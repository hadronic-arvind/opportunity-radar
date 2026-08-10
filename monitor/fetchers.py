"""Official-source adapters using Python's standard library."""

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple

from .models import FetchResult, MAX_OPPORTUNITIES_PER_SOURCE, Opportunity
from .text import (
    canonical_url,
    clean_text,
    infer_opportunity_type,
    iso_from_millis,
    stable_hash,
)


USER_AGENT = "OpportunityRadar/0.3 (public opportunity monitor)"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
MAX_JIBE_PAGES = 50
MAX_JIBE_JOBS = 5000
MAX_JIBE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_JIBE_WALL_SECONDS = 90


def _string_list(value: Any) -> List[str]:
    """Return clean, non-empty strings from a source taxonomy field."""

    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        text = clean_text(str(item))
        if text:
            cleaned.append(text)
    return cleaned


def _source_category(source: Dict[str, Any], *values: str) -> str:
    """Build the legacy searchable category text from structured source fields."""

    candidates = []
    legacy_category = clean_text(source.get("category", ""))
    if legacy_category:
        candidates.append(legacy_category)
    candidates.extend(value.replace("_", " ") for value in _string_list(source.get("domains")))
    candidates.extend(clean_text(value) for value in values if clean_text(value))
    return " ".join(dict.fromkeys(candidates))


def _source_metadata(source: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """Copy non-personal, structured source context onto an opportunity."""

    metadata: Dict[str, Any] = {}
    for key in ("domains", "packs", "career_levels", "regions"):
        values = _string_list(source.get(key))
        if values:
            metadata[key] = values
    if source.get("source_type"):
        metadata["source_type"] = clean_text(source["source_type"])
    if "official" in source:
        metadata["official"] = bool(source["official"])
    metadata.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
    return metadata


def _infer_opportunity_type(
    source: Dict[str, Any],
    title: Any,
    *structured_values: Any,
) -> str:
    """Classify a listing from a local override, its title, and ATS fields."""

    return infer_opportunity_type(
        title,
        tuple(structured_values),
        tuple(_string_list(source.get("opportunity_types"))),
        source.get("opportunity_type"),
        default="job",
    )


def _greenhouse_metadata_value(job: Dict[str, Any], *names: str) -> str:
    """Read an optional value from Greenhouse's documented metadata array."""

    wanted = {name.casefold() for name in names}
    for entry in job.get("metadata") or []:
        if not isinstance(entry, dict) or clean_text(entry.get("name")).casefold() not in wanted:
            continue
        value = entry.get("value")
        if isinstance(value, list):
            return " ".join(clean_text(item) for item in value if clean_text(item))
        return clean_text(value)
    return ""


class ResponseTooLargeError(ValueError):
    """Raised before an external response can consume unbounded memory."""


def _public_addresses(hostname: str, port: int) -> List[str]:
    hostname = hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Source URL must use a public host")
    addresses: List[Any] = []
    try:
        addresses.append(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as error:
            raise ValueError("Source hostname could not be resolved") from error
        for entry in resolved:
            try:
                addresses.append(ipaddress.ip_address(entry[4][0]))
            except (IndexError, TypeError, ValueError):
                raise ValueError("Source hostname returned an invalid address")
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Source URL must resolve only to public addresses")
    return list(dict.fromkeys(str(address) for address in addresses))


def _validate_remote_url(url: str) -> str:
    """Reject non-public or non-HTTPS collection targets before connecting."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Only absolute HTTPS source URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Source URLs must not contain credentials")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("Source URL contains an invalid port") from error
    if port != 443:
        raise ValueError("Source URLs must use the standard HTTPS port")
    _public_addresses(parsed.hostname, port)
    return parsed.geturl()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        _validate_remote_url(new_url)
        original_host = urllib.parse.urlsplit(request.full_url).hostname
        redirected_host = urllib.parse.urlsplit(new_url).hostname
        if not original_host or not redirected_host or original_host.casefold() != redirected_host.casefold():
            raise ValueError("Source redirects must remain on the configured host")
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated numeric address while retaining hostname TLS checks."""

    def __init__(self, host, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pinned_addresses = _public_addresses(self.host, self.port)
        self._create_connection = self._create_pinned_connection

    def _create_pinned_connection(self, address, timeout=None, source_address=None):
        last_error = None
        for numeric_address in self._pinned_addresses:
            try:
                return socket.create_connection(
                    (numeric_address, self.port),
                    timeout=timeout,
                    source_address=source_address,
                )
            except OSError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise OSError("Source hostname has no validated public address")


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(
            _PublicHTTPSConnection,
            request,
            context=self._context,
        )


_REMOTE_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _SafeRedirectHandler(),
    _PublicHTTPSHandler(),
)


def _open_remote(request: urllib.request.Request, timeout: int):
    return _REMOTE_OPENER.open(request, timeout=timeout)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href = ""
        self._text: List[str] = []
        self.overflow = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            if len(self.links) >= MAX_OPPORTUNITIES_PER_SOURCE:
                self.overflow = True
            else:
                self.links.append((self._href, clean_text(" ".join(self._text))))
            self._href = ""
            self._text = []


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _set_response_timeout(response: Any, seconds: float) -> None:
    for attributes in (
        ("fp", "raw", "_sock"),
        ("fp", "fp", "raw", "_sock"),
        ("fp", "_sock"),
        ("_sock",),
    ):
        candidate = response
        for attribute in attributes:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if candidate is not None and hasattr(candidate, "settimeout"):
            candidate.settimeout(max(0.1, seconds))
            return


def _request(
    url: str,
    timeout: int = 25,
    max_bytes: int = MAX_RESPONSE_BYTES,
    deadline: Any = None,
) -> bytes:
    validated_url = _validate_remote_url(url)
    if max_bytes < 1:
        raise ValueError("Response limit must be positive")
    if timeout <= 0:
        raise ValueError("Request timeout must be positive")
    request_deadline = min(
        float(deadline) if deadline is not None else float("inf"),
        time.monotonic() + timeout,
    )
    request = urllib.request.Request(
        validated_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    remaining = request_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Source request exceeded its wall-clock deadline")
    with _open_remote(request, timeout=max(0.1, remaining)) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else validated_url
        _validate_remote_url(final_url)
        headers = getattr(response, "headers", {})
        declared_length = headers.get("Content-Length") if hasattr(headers, "get") else None
        if declared_length:
            try:
                declared_size = int(declared_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                raise ResponseTooLargeError("Source response exceeds {} bytes".format(max_bytes))
        chunks = []
        total = 0
        while total <= max_bytes:
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Source request exceeded its wall-clock deadline")
            _set_response_timeout(response, remaining)
            chunk = response.read(min(READ_CHUNK_BYTES, max_bytes + 1 - total))
            if time.monotonic() > request_deadline:
                raise TimeoutError("Source request exceeded its wall-clock deadline")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError("Source response exceeds {} bytes".format(max_bytes))
        return b"".join(chunks)


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _url_with_page(url: str, page: int) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    updated = []
    replaced = False
    for key, value in query:
        if key == "page":
            updated.append((key, str(page)))
            replaced = True
        else:
            updated.append((key, value))
    if not replaced:
        updated.append(("page", str(page)))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(updated), parsed.fragment)
    )


def _jibe_pages(source: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """Fetch every advertised Jibe page within explicit aggregate bounds."""

    url = source["api_url"]
    deadline = time.monotonic() + MAX_JIBE_WALL_SECONDS
    payload = _request(url, deadline=deadline)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Jibe response did not contain a jobs list")

    jobs = list(data["jobs"])
    if len(jobs) > MAX_JIBE_JOBS:
        raise ResponseTooLargeError("Jibe source returned more than {} jobs".format(MAX_JIBE_JOBS))
    try:
        total = int(data.get("totalCount", len(jobs)))
    except (TypeError, ValueError):
        raise ValueError("Jibe response contained an invalid totalCount")
    total = max(total, len(jobs))
    if total > MAX_JIBE_JOBS:
        raise ResponseTooLargeError("Jibe source advertises more than {} jobs".format(MAX_JIBE_JOBS))

    try:
        current_page = int(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("page", 1))
    except (TypeError, ValueError):
        current_page = 1
    page_count = 1
    total_bytes = len(payload)
    combined_hash = hashlib.sha256()
    combined_hash.update(len(payload).to_bytes(8, "big"))
    combined_hash.update(payload)

    while len(jobs) < total:
        if page_count >= MAX_JIBE_PAGES:
            raise ResponseTooLargeError("Jibe source requires more than {} pages".format(MAX_JIBE_PAGES))
        current_page += 1
        next_payload = _request(_url_with_page(url, current_page), deadline=deadline)
        total_bytes += len(next_payload)
        if total_bytes > MAX_JIBE_TOTAL_BYTES:
            raise ResponseTooLargeError(
                "Paginated Jibe response exceeds {} bytes".format(MAX_JIBE_TOTAL_BYTES)
            )
        next_data = json.loads(next_payload.decode("utf-8"))
        if not isinstance(next_data, dict) or not isinstance(next_data.get("jobs"), list):
            raise ValueError("Jibe response did not contain a jobs list")
        next_jobs = next_data["jobs"]
        if not next_jobs:
            raise ValueError("Jibe pagination ended before totalCount jobs were returned")
        jobs.extend(next_jobs)
        if len(jobs) > MAX_JIBE_JOBS:
            raise ResponseTooLargeError(
                "Jibe source returned more than {} jobs".format(MAX_JIBE_JOBS)
            )
        page_count += 1
        combined_hash.update(len(next_payload).to_bytes(8, "big"))
        combined_hash.update(next_payload)

    content_hash = _hash(payload) if page_count == 1 else combined_hash.hexdigest()
    return jobs, content_hash


def fetch_source(source: Dict[str, Any]) -> FetchResult:
    kind = source["kind"]
    if kind == "greenhouse":
        result = fetch_greenhouse(source)
        return filter_items(source, result)
    if kind == "lever":
        result = fetch_lever(source)
        return filter_items(source, result)
    if kind == "jibe":
        result = fetch_jibe(source)
        return filter_items(source, result)
    if kind == "html_links":
        result = fetch_html_links(source)
        return filter_items(source, result)
    if kind == "watch_page":
        return fetch_watch_page(source)
    raise ValueError("Unsupported source kind: {}".format(kind))


def filter_items(source: Dict[str, Any], result: FetchResult) -> FetchResult:
    include = [term.lower() for term in source.get("item_include", [])]
    exclude = [term.lower() for term in source.get("item_exclude", [])]
    if not include and not exclude:
        return result
    filtered = []
    for item in result.opportunities:
        scope = source.get("item_filter_scope", "title")
        if scope == "full":
            text = "{} {} {}".format(item.title, item.description, item.location).lower()
        else:
            text = item.title.lower()
        if include and not any(term in text for term in include):
            continue
        if exclude and any(term in text for term in exclude):
            continue
        filtered.append(item)
    result.opportunities = filtered
    result.message = "{} relevant items after source filters".format(len(filtered))
    return result


def fetch_greenhouse(source: Dict[str, Any]) -> FetchResult:
    url = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true".format(source["board"])
    payload = _request(url)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Greenhouse response did not contain a jobs list")
    if len(data["jobs"]) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise ResponseTooLargeError("Greenhouse source returned too many jobs")
    items = []
    for job in data.get("jobs", []):
        departments = [entry.get("name", "") for entry in (job.get("departments") or [])]
        offices = [entry.get("name", "") for entry in (job.get("offices") or [])]
        employment_type = clean_text(
            job.get("employment_type")
            or _greenhouse_metadata_value(job, "employment type", "job type", "commitment")
        )
        title = clean_text(job.get("title"))
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("id") or stable_hash(job.get("absolute_url", ""))),
                title=title,
                organization=source["name"],
                url=canonical_url(job.get("absolute_url", "")),
                location=clean_text((job.get("location") or {}).get("name")),
                description=clean_text(job.get("content"))[:20000],
                category=_source_category(source, *departments),
                opportunity_type=_infer_opportunity_type(
                    source, title, employment_type, *departments
                ),
                posted_at=job.get("updated_at"),
                commitment=employment_type,
                metadata=_source_metadata(
                    source,
                    ats="greenhouse",
                    departments=[clean_text(value) for value in departments if clean_text(value)],
                    offices=[clean_text(value) for value in offices if clean_text(value)],
                ),
            )
        )
    return FetchResult(items, _hash(payload))


def fetch_lever(source: Dict[str, Any]) -> FetchResult:
    url = "https://api.lever.co/v0/postings/{}?mode=json".format(source["site"])
    payload = _request(url)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("Lever response was not a postings list")
    if len(data) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise ResponseTooLargeError("Lever source returned too many jobs")
    items = []
    for job in data:
        categories = job.get("categories") or {}
        lists = job.get("lists") or []
        title = clean_text(job.get("text"))
        commitment = clean_text(categories.get("commitment"))
        description = " ".join(
            [job.get("descriptionPlain", "")]
            + [clean_text(entry.get("content", "")) for entry in lists]
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("id") or stable_hash(job.get("hostedUrl", ""))),
                title=title,
                organization=source["name"],
                url=canonical_url(job.get("hostedUrl", "")),
                location=clean_text(categories.get("location")),
                description=clean_text(description)[:20000],
                category=_source_category(
                    source,
                    categories.get("team", ""),
                    categories.get("department", ""),
                ),
                opportunity_type=_infer_opportunity_type(
                    source,
                    title,
                    commitment,
                    categories.get("team", ""),
                    categories.get("department", ""),
                ),
                posted_at=iso_from_millis(job.get("createdAt")),
                commitment=commitment,
                metadata=_source_metadata(
                    source,
                    ats="lever",
                    workplace_type=job.get("workplaceType"),
                    country=job.get("country"),
                ),
            )
        )
    return FetchResult(items, _hash(payload))


def fetch_jibe(source: Dict[str, Any]) -> FetchResult:
    jobs, content_hash = _jibe_pages(source)
    items = []
    for entry in jobs:
        job = entry.get("data", entry)
        slug = str(job.get("slug") or job.get("req_id") or "")
        categories = [value.get("name", "") for value in job.get("categories", []) if isinstance(value, dict)]
        title = clean_text(job.get("title"))
        commitment = clean_text(
            job.get("employment_type") or job.get("job_type") or job.get("type")
        )
        template = source.get("job_url_template", "")
        job_url = (
            str(template).format(slug=urllib.parse.quote(slug), req_id=urllib.parse.quote(str(job.get("req_id") or "")))
            if template
            else canonical_url(str(job.get("apply_url") or source.get("url") or ""))
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("req_id") or slug or stable_hash(job.get("apply_url", ""))),
                title=title,
                organization=source["name"],
                url=job_url,
                location=clean_text(job.get("full_location") or job.get("short_location")),
                description=clean_text(job.get("description"))[:20000],
                category=_source_category(source, *categories),
                opportunity_type=_infer_opportunity_type(
                    source, title, commitment, *categories
                ),
                posted_at=job.get("posted_date") or job.get("create_date"),
                commitment=commitment,
                metadata=_source_metadata(
                    source, req_id=job.get("req_id"), ats="jibe"
                ),
            )
        )
    return FetchResult(items, content_hash, message="{} Jibe jobs".format(len(items)))


def fetch_html_links(source: Dict[str, Any]) -> FetchResult:
    payload = _request(source["url"])
    parser = LinkParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    if parser.overflow:
        raise ResponseTooLargeError("HTML source contained too many links")
    include = [value.lower() for value in source.get("include", [])]
    exclude = [value.lower() for value in source.get("exclude", [])]
    base_host = urllib.parse.urlparse(source["url"]).netloc
    items = []
    seen = set()
    for href, title in parser.links:
        url = canonical_url(urllib.parse.urljoin(source["url"], href))
        haystack = "{} {}".format(title, url).lower()
        if not title or not url.startswith("http"):
            continue
        if source.get("same_domain", False) and urllib.parse.urlparse(url).netloc != base_host:
            continue
        if include and not any(term in haystack for term in include):
            continue
        if any(term in haystack for term in exclude):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=stable_hash(url),
                title=title,
                organization=source["name"],
                url=url,
                category=_source_category(source),
                opportunity_type=_infer_opportunity_type(source, title),
                metadata=_source_metadata(source, adapter="html_links"),
            )
        )
    return FetchResult(items, _hash(payload), message="{} qualifying links".format(len(items)))


def fetch_watch_page(source: Dict[str, Any]) -> FetchResult:
    payload = _request(source["url"])
    parser = VisibleTextParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    page_text = clean_text(" ".join(parser.parts))
    if not page_text:
        raise ValueError("Watch page contained no visible text")
    semantic_hash = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
    if not source.get("publish_as_opportunity", False):
        return FetchResult(
            [],
            semantic_hash,
            message="watch page reachable; no listing records published",
        )

    title = clean_text(source.get("watch_title", source["name"]))
    item = Opportunity(
        source_id=source["id"],
        external_id=source["id"],
        title=title,
        organization=source["name"],
        url=source["url"],
        description=page_text[:10000],
        category=_source_category(source),
        opportunity_type=_infer_opportunity_type(source, title),
        metadata=_source_metadata(source, page_changed=True),
    )
    return FetchResult([item], semantic_hash, message="watch page reachable")


def polite_pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
