"""Bounded, opt-in public collection with per-observation provenance."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit

from monitor import fetchers
from monitor.public_feeds import GRANTS_API_URL, fetch_public_page
from monitor.text import canonical_url, normalize_opportunity_type, stable_hash

BOARD_ADAPTERS = {"greenhouse", "lever", "ashby"}
SUPPORTED_KINDS = BOARD_ADAPTERS | {
    "arbeitnow",
    "grants_gov",
    "html_links",
    "json_ld",
    "public_page",
    "syndication",
}
CATALOG_ONLY = {"arbeitnow", "grants_gov", "html_links"}
MAX_ROWS = 1500
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
MAX_SECONDS = 120
TERMINAL_STATES = {"complete", "partial", "failed", "timed_out", "cancelled"}
WORKER_CODE = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from radar.collection import _worker; _worker(int(sys.argv[2]))"
)


@lru_cache(maxsize=1)
def _catalog_data():
    payload = Path(__file__).with_name("catalog.json").read_text(encoding="utf-8")
    return json.loads(payload)


def load_catalog() -> list[dict]:
    """Return independent source definitions; network access requires opt-in."""
    return [validate_source(source) for source in _catalog_data()]


def _public_url(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in value)
    ):
        raise ValueError("Use an absolute public HTTPS URL without spaces")
    url, hostname, _port = fetchers._remote_url_parts(value)
    parsed = urlsplit(url)
    if parsed.port not in (None, 443):
        raise ValueError("Source URLs must use the standard HTTPS port")
    if (
        parsed.fragment
        or "%" in hostname
        or ("." not in hostname and ":" not in hostname)
    ):
        raise ValueError("Use a public HTTPS URL without a fragment")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
        last_label = hostname.rsplit(".", 1)[-1]
        if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", last_label):
            raise ValueError("Source URL must use a public host")
    if address is not None and (address.is_multicast or address.is_reserved):
        raise ValueError("Source URL must use a public unicast address")
    for key, _value in parse_qsl(
        parsed.query, keep_blank_values=True, max_num_fields=30
    ):
        if re.search(
            r"token|secret|signature|password|credential|session|^auth|api.?key|^key$",
            key,
            re.I,
        ):
            raise ValueError("Public URLs must not contain credential parameters")
    return url


def _known_source(source):
    for known in _catalog_data():
        if all(
            source.get(key) == known.get(key) for key in ("id", "kind", "url", "board")
        ):
            return known
    return None


def _list_field(value, label):
    if (
        not isinstance(value, list)
        or len(value) > 20
        or any(not isinstance(item, str) or not 1 <= len(item) <= 120 for item in value)
    ):
        raise ValueError(f"{label} must be a list of up to 20 short strings")
    return list(dict.fromkeys(value))


def validate_source(source: dict) -> dict:
    """Accept only fields used by explicitly supported public read adapters."""
    if not isinstance(source, dict):
        raise ValueError("Source must be an object")
    source_id, name, kind = (source.get(key) for key in ("id", "name", "kind"))
    if not isinstance(source_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,99}", source_id
    ):
        raise ValueError(
            "Source ID must use lowercase letters, digits, hyphens, or underscores"
        )
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 160
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in name)
    ):
        raise ValueError("Source name must contain 1 to 160 characters")
    if not isinstance(kind, str) or kind not in SUPPORTED_KINDS:
        raise ValueError("Use a supported public source adapter")
    url = _public_url(source.get("url"))
    enabled = source.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("Source enabled value must be true or false")
    normalized = {
        "id": source_id,
        "name": name.strip(),
        "kind": kind,
        "url": url,
        "enabled": enabled,
    }
    if kind in BOARD_ADAPTERS:
        board = source.get("board") or (source.get("site") if kind == "lever" else None)
        if not isinstance(board, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", board
        ):
            raise ValueError("This adapter requires a board slug")
        normalized["board"] = board
    known = _known_source(normalized)
    if kind in CATALOG_ONLY and known is None:
        raise ValueError("This adapter is available through the public catalog")
    if known:
        # Publisher facts and parser selectors only come from the catalog.
        normalized.update(
            {key: known[key] for key in ("name", "publisher_url") if key in known}
        )
    fields = known or source
    for key in ("regions", "opportunity_types"):
        normalized[key] = _list_field(fields.get(key, []), key)
    for key in ("opportunity_type", "default_opportunity_type"):
        if key in fields:
            value = (
                "postdoc"
                if fields[key] == "postdoc"
                else normalize_opportunity_type(fields[key])
            )
            if not value:
                raise ValueError(f"{key} must be a known opportunity form")
            normalized[key] = value
    if kind == "lever":
        region = fields.get("api_region", "us")
        if region not in ("eu", "us"):
            raise ValueError("Lever API region must be us or eu")
        normalized["api_region"] = region
    if kind == "html_links":
        normalized.update(
            include=list(known["include"]),
            exclude=list(known.get("exclude", [])),
            pages=known.get("pages", 1),
            same_domain=True,
            rolling_window=True,
            source_type="listing_feed",
        )
    return normalized


def _observations(source, items, request_url):
    known = _known_source(source)
    for item in items:
        if not item.title.strip() or not item.external_id.strip():
            continue
        try:
            url = _public_url(item.url)
        except ValueError:
            continue
        external_id = item.external_id
        if source["kind"] == "html_links" and source["pages"] > 1:
            url = canonical_url(fetchers._url_without_query_key(url, "page"))
            external_id = stable_hash(url)
        details = item.metadata.get("collection", {})
        format_name = details.get("evidence", "publisher_api")
        is_listing = format_name != "directory_link"
        authoritative = (
            known is not None and is_listing and source["kind"] != "arbeitnow"
        )
        evidence = {
            "format": format_name,
            "source_url": request_url,
            "publisher_url": source.get("publisher_url", source["url"]),
            "authority_scope": "published fields"
            if authoritative
            else "reference only",
        }
        for key in ("dates", "listing"):
            if item.metadata.get(key):
                evidence[key] = item.metadata[key]
        kinds = [item.opportunity_type]
        if (
            source["id"] == "nrc_canada_postdoctoral"
            and item.opportunity_type == "postdoc"
        ):
            kinds.append("fellowship")
        requirements = (
            item.eligibility if is_listing and source["kind"] != "grants_gov" else ""
        )
        yield {
            "external_id": external_id,
            "title": item.title,
            "organization": item.organization
            if is_listing or source["id"] != "euraxess"
            else "",
            "url": url,
            "location": item.location,
            "description": item.description,
            "kinds": kinds,
            "posted_at": item.posted_at,
            "deadline_at": item.deadline_at,
            "requirements": requirements,
            "authority": authoritative,
            "evidence": evidence,
        }


def _batches(source):
    if source["kind"] == "html_links":
        observed_pages = set()
        for offset in range(source["pages"]):
            page_url = fetchers._html_page_url(source, offset)
            result = fetchers.fetch_source({**source, "url": page_url, "pages": 1})
            page_ids = frozenset(item.external_id for item in result.opportunities)
            if page_ids in observed_pages:
                raise ValueError("Publisher returned a repeated result page")
            observed_pages.add(page_ids)
            yield result, page_url
        return
    adapter_source = dict(source)
    for key in ("opportunity_type", "default_opportunity_type"):
        if adapter_source.get(key) == "postdoc":
            adapter_source[key] = "postdoctoral"
    if source["kind"] == "lever":
        adapter_source["site"] = source["board"]
    if source["kind"] == "public_page":
        payload = fetchers._request(source["url"])
        prefix = (
            payload[:1000].decode("utf-8-sig", errors="replace").lstrip().casefold()
        )
        adapter_source["kind"] = (
            "syndication"
            if prefix.startswith(("<?xml", "<rss", "<feed", "<rdf:rdf"))
            else "json_ld"
        )
        result = fetch_public_page(adapter_source, prefetched=payload)
    else:
        result = fetchers.fetch_source(adapter_source)
    request_url = GRANTS_API_URL if source["kind"] == "grants_gov" else source["url"]
    if source["kind"] in BOARD_ADAPTERS:
        endpoints = {
            "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
            "lever": "https://api.lever.co/v0/postings/{}",
            "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
        }
        request_url = endpoints[source["kind"]].format(source["board"])
        if source["kind"] == "lever" and source.get("api_region") == "eu":
            request_url = request_url.replace("api.lever.co", "api.eu.lever.co", 1)
    yield result, request_url


def _failure_message(error):
    if isinstance(error, HTTPError):
        if error.code == 429:
            return "Publisher rate limited the request (HTTP 429). Retry later."
        return f"Publisher returned HTTP {error.code}."
    if isinstance(error, TimeoutError):
        return "The source request reached its time limit."
    if isinstance(error, fetchers.ResponseTooLargeError):
        return "The source response exceeded a size or listing limit."
    if isinstance(error.__cause__, socket.gaierror):
        return "Could not resolve the public publisher."
    if isinstance(error, URLError):
        return "Could not connect to the public publisher."
    if str(error) == "Publisher returned a repeated result page":
        return "The publisher repeated a page. Previously observed listings were kept."
    return "The source returned an invalid or unsupported response."


def _perform(source, deadline, emit):
    count = 0
    complete = True
    output_size = 0
    seen = set()
    try:
        with fetchers.collection_request_deadline(deadline):
            for result, request_url in _batches(source):
                if result.status != "ok":
                    raise ValueError("Unusable source response")
                complete = complete and result.complete_snapshot
                usable = 0
                for row in _observations(source, result.opportunities, request_url):
                    usable += 1
                    if row["external_id"] in seen:
                        continue
                    encoded = json.dumps(
                        row, ensure_ascii=True, separators=(",", ":")
                    ).encode()
                    if (
                        count >= MAX_ROWS
                        or output_size + len(encoded) + 1 > MAX_OUTPUT_BYTES
                    ):
                        return "partial", "Observation limit reached."
                    emit(encoded)
                    seen.add(row["external_id"])
                    count += 1
                    output_size += len(encoded) + 1
                if usable < len(result.opportunities):
                    complete = False
                    if not usable:
                        raise ValueError("Source page has no usable listing identities")
        return (
            ("complete", "Publisher snapshot observed.")
            if complete
            else (
                "partial",
                "Observed a rolling window, directory, or incomplete snapshot.",
            )
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        AttributeError,
    ) as error:
        if count:
            return "partial", _failure_message(error)
        if isinstance(error, TimeoutError):
            return "timed_out", _failure_message(error)
        return "failed", _failure_message(error)


def _exit_with_owner(owner_fd):
    try:
        # The writer is held only by the caller. Parent death closes it at once.
        os.read(owner_fd, 1)
    finally:
        os._exit(1)


def _worker(owner_fd):
    """Write each accepted observation before starting the next request."""
    Thread(target=_exit_with_owner, args=(owner_fd,), daemon=True).start()
    request = json.loads(sys.stdin.buffer.read(16384))
    source = validate_source(request["source"])
    deadline = time.monotonic() + request["seconds"]
    output = sys.stdout.buffer

    def emit(encoded):
        output.write(encoded + b"\n")

    # Flush each result page by line so an interrupted later page cannot erase it.
    def emit_observation(encoded):
        emit(encoded)
        output.flush()

    state, message = _perform(source, deadline, emit_observation)
    emit(json.dumps({"state": state, "message": message}).encode())
    output.flush()


def _decode_worker_output(output):
    rows = []
    state, message = "failed", "The collector exited before reporting its result."
    # A killed process may leave one incomplete JSON line in the pipe.
    for line in output.split(b"\n")[:-1]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if "external_id" in record:
            rows.append(record)
        elif record.get("state") in TERMINAL_STATES:
            state, message = record["state"], record["message"]
    if rows and state == "failed":
        state = "partial"
    return rows, state, message


def collect(source: dict, seconds: float, cancel=None) -> tuple[list[dict], str, str]:
    """Run one bounded burst in an isolated child with no inherited secrets."""
    source = validate_source(source)
    if (
        type(seconds) not in {float, int}
        or not math.isfinite(seconds)
        or not 0 < seconds <= MAX_SECONDS
    ):
        raise ValueError(f"Collection seconds must be between 0 and {MAX_SECONDS}")
    if cancel is not None and cancel.is_set():
        return [], "cancelled", "Collection cancelled before the source request."
    if sys.platform not in {"darwin", "linux"}:
        return [], "failed", "Public collection requires macOS or Linux."
    deadline = time.monotonic() + seconds
    request = json.dumps({"source": source, "seconds": seconds}).encode()
    try:
        with _owner_pipe() as owner_fd:
            output, stopped = _run_worker(request, deadline, cancel, owner_fd)
    except OSError:
        return [], "failed", "Could not start or communicate with the public collector."
    rows, state, message = _decode_worker_output(output)
    if stopped is not None:
        state, message = stopped
    return rows, state, message


@contextmanager
def _owner_pipe():
    reader, writer = os.pipe()
    try:
        yield reader
    finally:
        os.close(reader)
        os.close(writer)


def _run_worker(request, deadline, cancel, owner_fd):
    worker = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            WORKER_CODE,
            str(Path(__file__).resolve().parent.parent),
            str(owner_fd),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={},
        pass_fds=(owner_fd,),
    )
    output = None
    stopped = None
    try:
        while True:
            if cancel is not None and cancel.is_set():
                stopped = ("cancelled", "Collection cancelled.")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stopped = ("timed_out", "Collection reached its time limit.")
                break
            try:
                output, _ = worker.communicate(request, timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                request = None
    finally:
        if worker.poll() is None:
            worker.kill()
        if output is None:
            output, _ = worker.communicate()
    # A terminal interrupt can stop both the caller and child during communicate.
    if stopped is None and cancel is not None and cancel.is_set():
        stopped = ("cancelled", "Collection cancelled.")
    return output, stopped


def _smoke():
    parser = argparse.ArgumentParser(
        description="Test selected public sources without a local profile or database."
    )
    parser.add_argument("--smoke", nargs="*", required=True, metavar="SOURCE")
    parser.add_argument("--seconds", type=float, default=7)
    args = parser.parse_args()
    catalog = {source["id"]: source for source in load_catalog()}
    requested = args.smoke or list(catalog)
    unknown = set(requested) - catalog.keys()
    if unknown:
        parser.error("Unknown catalog source: " + ", ".join(sorted(unknown)))
    failed = False
    for source_id in dict.fromkeys(requested):
        started = time.monotonic()
        rows, state, message = collect(catalog[source_id], args.seconds)
        print(
            json.dumps(
                {
                    "source": source_id,
                    "state": state,
                    "observed": len(rows),
                    "seconds": round(time.monotonic() - started, 3),
                    "message": message,
                }
            ),
            flush=True,
        )
        failed = failed or state not in {"complete", "partial"} or not rows
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(_smoke())
