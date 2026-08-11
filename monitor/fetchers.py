"""Official-source adapters using Python's standard library."""

import hashlib
import http.client
import ipaddress
import json
import re
import signal
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager, nullcontext
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .dates import (
    extract_deadline,
    posting_evidence,
    structured_deadline,
)
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
MAX_HTML_LINK_PAGES = 20
MAX_HTML_LINK_TOTAL_BYTES = 32 * 1024 * 1024
MAX_HTML_LINK_WALL_SECONDS = 60
MAX_RESOLVED_ADDRESSES = 8


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
    """Build searchable category text from listing-specific taxonomy."""

    candidates = []
    legacy_category = clean_text(source.get("category", ""))
    if legacy_category:
        candidates.append(legacy_category)
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


def _listing_dates(
    source: Dict[str, Any],
    description: str,
    posted_value: Any = None,
    posted_provenance: str = "none",
    posted_confidence: str = "high",
    deadline_value: Any = None,
    deadline_provenance: str = "none",
    updated_value: Any = None,
    updated_provenance: str = "none",
) -> Tuple[Any, Any, Dict[str, Any]]:
    """Normalize source dates and retain concise confidence/provenance metadata."""
    posted_at, posted = posting_evidence(
        posted_value,
        posted_provenance,
        confidence=posted_confidence,
    )
    if deadline_value not in (None, ""):
        deadline_at, deadline = structured_deadline(
            deadline_value,
            deadline_provenance,
        )
        if deadline_at is None:
            deadline_at, deadline = extract_deadline(
                description,
                date_order=str(source.get("date_order", "")).lower(),
                allow_generic_deadline=source.get("kind") == "watch_page",
            )
    else:
        deadline_at, deadline = extract_deadline(
            description,
            date_order=str(source.get("date_order", "")).lower(),
            allow_generic_deadline=source.get("kind") == "watch_page",
        )
    dates: Dict[str, Any] = {"posted": posted, "deadline": deadline}
    if updated_value not in (None, ""):
        _updated_at, updated = posting_evidence(
            updated_value,
            updated_provenance,
            kind="updated",
        )
        dates["source_updated"] = updated
    return posted_at, deadline_at, dates


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


class _ResolutionTimeout(TimeoutError):
    """Internal signal exception for a bounded synchronous resolver call."""


class _WallClockTimeout(TimeoutError):
    """Internal signal exception for an aggregate network deadline."""


@contextmanager
def _unix_wall_clock_guard(timeout: float, message: str):
    """Interrupt a blocking Unix main-thread operation at one aggregate deadline."""
    if timeout <= 0:
        raise TimeoutError(message)
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        raise RuntimeError("Source network operations must run on the Unix main thread")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0] > 0:
        raise RuntimeError("Source network operations cannot replace an active process timer")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum, _frame):
        raise _WallClockTimeout(message)

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, timeout))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _bounded_getaddrinfo(hostname: str, port: int, timeout: float) -> List[Any]:
    if timeout <= 0:
        raise TimeoutError("Source hostname resolution exceeded its deadline")
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        raise RuntimeError("Source DNS resolution must run on the Unix main thread")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0] > 0:
        return socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum, _frame):
        raise _ResolutionTimeout("Source hostname resolution exceeded its deadline")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, timeout))
    try:
        return socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _public_addresses(hostname: str, port: int, timeout: float = 25) -> List[str]:
    hostname = hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Source URL must use a public host")
    addresses: List[Any] = []
    try:
        addresses.append(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            resolved = _bounded_getaddrinfo(hostname, port, timeout)
        except _ResolutionTimeout as error:
            raise TimeoutError("Source hostname resolution exceeded its deadline") from error
        except socket.gaierror as error:
            raise ValueError("Source hostname could not be resolved") from error
        for entry in resolved:
            try:
                addresses.append(ipaddress.ip_address(entry[4][0]))
            except (IndexError, TypeError, ValueError):
                raise ValueError("Source hostname returned an invalid address")
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Source URL must resolve only to public addresses")
    return list(dict.fromkeys(str(address) for address in addresses))[:MAX_RESOLVED_ADDRESSES]


def _remote_url_parts(url: str) -> Tuple[str, str, int]:
    """Validate URL syntax and reject obviously non-public literal targets."""
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
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Source URL must use a public host")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("Source URL must resolve only to public addresses")
    return parsed.geturl(), hostname, port


def _validate_remote_url(url: str, resolution_timeout: float = 25) -> str:
    """Reject non-public or non-HTTPS collection targets before connecting."""
    validated, hostname, port = _remote_url_parts(url)
    _public_addresses(hostname, port, timeout=resolution_timeout)
    return validated


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, request, file_pointer, code, message, headers):
        bounded_response = _BoundedRedirectResponse(file_pointer, MAX_RESPONSE_BYTES)
        return super().http_error_302(
            request,
            bounded_response,
            code,
            message,
            headers,
        )

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        validated_url, redirected_host, _port = _remote_url_parts(new_url)
        original_host = urllib.parse.urlsplit(request.full_url).hostname
        if not original_host or not redirected_host or original_host.casefold() != redirected_host.casefold():
            raise ValueError("Source redirects must remain on the configured host")
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            validated_url,
        )


class _BoundedRedirectResponse:
    """Let urllib drain an intermediate redirect without an unbounded read."""

    def __init__(self, response: Any, max_bytes: int) -> None:
        self._response = response
        self._max_bytes = max_bytes
        self._consumed = 0

    def _reject_oversize(self) -> None:
        try:
            self._response.close()
        finally:
            raise ResponseTooLargeError(
                "Source redirect response exceeds {} bytes".format(self._max_bytes)
            )

    def read(self, amount: Any = None) -> bytes:
        headers = getattr(self._response, "headers", {})
        declared_length = headers.get("Content-Length") if hasattr(headers, "get") else None
        if declared_length:
            try:
                declared_size = int(declared_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > self._max_bytes:
                self._reject_oversize()

        if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
            allowed = min(amount, self._max_bytes + 1 - self._consumed)
            chunk = self._response.read(max(0, allowed))
            self._consumed += len(chunk)
            if self._consumed > self._max_bytes:
                self._reject_oversize()
            return chunk

        while self._consumed <= self._max_bytes:
            allowed = min(
                READ_CHUNK_BYTES,
                self._max_bytes + 1 - self._consumed,
            )
            chunk = self._response.read(allowed)
            if not chunk:
                return b""
            self._consumed += len(chunk)
            if self._consumed > self._max_bytes:
                self._reject_oversize()
        self._reject_oversize()
        return b""

    def close(self) -> None:
        self._response.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated numeric address while retaining hostname TLS checks."""

    def __init__(self, host, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        resolution_timeout = self.timeout if isinstance(self.timeout, (int, float)) else 25
        self._pinned_addresses = _public_addresses(
            self.host,
            self.port,
            timeout=max(0.1, float(resolution_timeout)),
        )
        self._create_connection = self._create_pinned_connection

    def _create_pinned_connection(self, address, timeout=None, source_address=None):
        last_error = None
        numeric_timeout = float(timeout) if isinstance(timeout, (int, float)) else 25.0
        deadline = time.monotonic() + max(0.001, numeric_timeout)
        active_guard = signal.getitimer(signal.ITIMER_REAL)[0] > 0
        guard = nullcontext() if active_guard else _unix_wall_clock_guard(
            numeric_timeout,
            "Source connection exceeded its wall-clock deadline",
        )
        with guard:
            for numeric_address in self._pinned_addresses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Source connection exceeded its wall-clock deadline")
                try:
                    return socket.create_connection(
                        (numeric_address, self.port),
                        timeout=remaining,
                        source_address=source_address,
                    )
                except _WallClockTimeout:
                    raise
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
    def __init__(self, title_prefix: str = "") -> None:
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href = ""
        self._text: List[str] = []
        self._fallback_title = ""
        self._title_prefix = clean_text(title_prefix)[:120]
        self.overflow = False
        self.base_href: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "base" and self.base_href is None:
            self.base_href = dict(attrs).get("href", "") or ""
        if normalized_tag == "a":
            attributes = dict(attrs)
            self._href = attributes.get("href", "") or ""
            self._text = []
            self._fallback_title = clean_text(
                attributes.get("aria-label") or attributes.get("title") or ""
            )[:500]

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            title = clean_text(" ".join(self._text)) or self._fallback_title
            for prefix in (self._title_prefix, "Learn more about"):
                if prefix and title.casefold().startswith(prefix.casefold()):
                    stripped = title[len(prefix):].lstrip(" :-")
                    if stripped:
                        title = stripped
                    break
            if len(self.links) >= MAX_OPPORTUNITIES_PER_SOURCE:
                self.overflow = True
            else:
                self.links.append((self._href, title[:500]))
            self._href = ""
            self._text = []
            self._fallback_title = ""


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
    if max_bytes < 1:
        raise ValueError("Response limit must be positive")
    if timeout <= 0:
        raise ValueError("Request timeout must be positive")
    request_deadline = min(
        float(deadline) if deadline is not None else float("inf"),
        time.monotonic() + timeout,
    )
    remaining = request_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Source request exceeded its wall-clock deadline")
    validated_url = _validate_remote_url(url, resolution_timeout=remaining)
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
    with _unix_wall_clock_guard(
        remaining,
        "Source request exceeded its wall-clock deadline",
    ):
        with _open_remote(request, timeout=max(0.1, remaining)) as response:
            reported_url = response.geturl() if hasattr(response, "geturl") else ""
            final_url = reported_url or validated_url
            _final_url, final_host, _final_port = _remote_url_parts(final_url)
            configured_host = urllib.parse.urlsplit(validated_url).hostname
            if not configured_host or final_host != configured_host.casefold():
                raise ValueError("Source redirects must remain on the configured host")
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


def _html_link_payloads(source: Dict[str, Any]) -> Tuple[List[bytes], str]:
    """Fetch a configured bounded run of page-numbered HTML result pages."""
    pages = source.get("pages", 1)
    if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_HTML_LINK_PAGES:
        raise ValueError(
            "HTML link source pages must be an integer from 1 to {}".format(
                MAX_HTML_LINK_PAGES
            )
        )
    url = str(source["url"])
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    try:
        first_page = int(query.get("page", 1))
    except (TypeError, ValueError):
        raise ValueError("HTML link source page query must be an integer")

    deadline = time.monotonic() + MAX_HTML_LINK_WALL_SECONDS
    payloads: List[bytes] = []
    total_bytes = 0
    combined_hash = hashlib.sha256()
    for offset in range(pages):
        page_url = url if offset == 0 else _url_with_page(url, first_page + offset)
        payload = _request(page_url, deadline=deadline)
        total_bytes += len(payload)
        if total_bytes > MAX_HTML_LINK_TOTAL_BYTES:
            raise ResponseTooLargeError(
                "Paginated HTML response exceeds {} bytes".format(
                    MAX_HTML_LINK_TOTAL_BYTES
                )
            )
        payloads.append(payload)
        combined_hash.update(len(payload).to_bytes(8, "big"))
        combined_hash.update(payload)
    return payloads, _hash(payloads[0]) if pages == 1 else combined_hash.hexdigest()


def _html_link_base(
    value: Any,
    page_url: str,
    source_host: str,
    strict: bool = False,
) -> str:
    """Resolve a link base while keeping remote HTML on its configured host."""
    if value in (None, ""):
        return ""
    candidate = urllib.parse.urljoin(page_url, str(value))
    try:
        validated, hostname, _port = _remote_url_parts(candidate)
        if hostname != source_host:
            raise ValueError("HTML link base URL must remain on the source host")
    except (TypeError, ValueError) as error:
        if strict:
            raise ValueError(
                "HTML link_base_url must resolve to public HTTPS on the source host"
            ) from error
        return ""
    return validated


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
    url = "https://boards-api.greenhouse.io/v1/boards/{}/jobs".format(source["board"])
    if source.get("include_content", True):
        url += "?content=true"
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
        category_metadata = {
            name: value
            for name in _string_list(source.get("category_metadata_names"))
            for value in [_greenhouse_metadata_value(job, name)]
            if value
        }
        category_values = departments + list(category_metadata.values())
        employment_type = clean_text(
            job.get("employment_type")
            or _greenhouse_metadata_value(job, "employment type", "job type", "commitment")
        )
        title = clean_text(job.get("title"))
        description = clean_text(job.get("content"))[:20000]
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
            posted_value=job.get("first_published"),
            posted_provenance="greenhouse.first_published",
            deadline_value=(
                job.get("application_deadline")
                or _greenhouse_metadata_value(
                    job,
                    "application deadline",
                    "closing date",
                )
            ),
            deadline_provenance="greenhouse.application_deadline",
            updated_value=job.get("updated_at"),
            updated_provenance="greenhouse.updated_at",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("id") or stable_hash(job.get("absolute_url", ""))),
                title=title,
                organization=source["name"],
                url=canonical_url(job.get("absolute_url", "")),
                location=clean_text((job.get("location") or {}).get("name")),
                description=description,
                category=_source_category(source, *category_values),
                opportunity_type=_infer_opportunity_type(
                    source, title, employment_type, *category_values
                ),
                posted_at=posted_at,
                deadline_at=deadline_at,
                commitment=employment_type,
                metadata=_source_metadata(
                    source,
                    ats="greenhouse",
                    departments=[clean_text(value) for value in departments if clean_text(value)],
                    offices=[clean_text(value) for value in offices if clean_text(value)],
                    category_metadata=category_metadata,
                    dates=dates,
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
        description = clean_text(" ".join(
            [job.get("descriptionPlain", "")]
            + [clean_text(entry.get("content", "")) for entry in lists]
        ))[:20000]
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
            posted_value=iso_from_millis(job.get("createdAt")),
            posted_provenance="lever.createdAt",
            posted_confidence="medium",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("id") or stable_hash(job.get("hostedUrl", ""))),
                title=title,
                organization=source["name"],
                url=canonical_url(job.get("hostedUrl", "")),
                location=clean_text(categories.get("location")),
                description=description,
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
                posted_at=posted_at,
                deadline_at=deadline_at,
                commitment=commitment,
                metadata=_source_metadata(
                    source,
                    ats="lever",
                    team=clean_text(categories.get("team")),
                    department=clean_text(categories.get("department")),
                    workplace_type=job.get("workplaceType"),
                    country=job.get("country"),
                    dates=dates,
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
        description = clean_text(job.get("description"))[:20000]
        posted_value = job.get("posted_date") or job.get("create_date")
        posted_provenance = (
            "jibe.posted_date" if job.get("posted_date") else "jibe.create_date"
        )
        deadline_value = next(
            (
                job.get(name)
                for name in (
                    "application_deadline",
                    "apply_end_date",
                    "expiration_date",
                    "valid_through",
                    "closing_date",
                    "close_date",
                )
                if job.get(name) not in (None, "")
            ),
            None,
        )
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
            posted_value=posted_value,
            posted_provenance=posted_provenance,
            deadline_value=deadline_value,
            deadline_provenance="jibe.structured_deadline",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job.get("req_id") or slug or stable_hash(job.get("apply_url", ""))),
                title=title,
                organization=source["name"],
                url=job_url,
                location=clean_text(job.get("full_location") or job.get("short_location")),
                description=description,
                category=_source_category(source, *categories),
                opportunity_type=_infer_opportunity_type(
                    source, title, commitment, *categories
                ),
                posted_at=posted_at,
                deadline_at=deadline_at,
                commitment=commitment,
                metadata=_source_metadata(
                    source,
                    req_id=job.get("req_id"),
                    ats="jibe",
                    categories=[
                        clean_text(value) for value in categories if clean_text(value)
                    ],
                    dates=dates,
                ),
            )
        )
    return FetchResult(items, content_hash, message="{} Jibe jobs".format(len(items)))


def fetch_html_links(source: Dict[str, Any]) -> FetchResult:
    source_url, source_host, _source_port = _remote_url_parts(str(source["url"]))
    configured_link_base = _html_link_base(
        source.get("link_base_url"),
        source_url,
        source_host,
        strict=source.get("link_base_url") not in (None, ""),
    )
    payloads, content_hash = _html_link_payloads(source)
    links: List[Tuple[str, str, str]] = []
    parsed_source = urllib.parse.urlsplit(source_url)
    source_query = dict(
        urllib.parse.parse_qsl(parsed_source.query, keep_blank_values=True)
    )
    first_page = int(source_query.get("page", 1))
    for page_index, payload in enumerate(payloads):
        page_url = (
            source_url
            if page_index == 0
            else _url_with_page(source_url, first_page + page_index)
        )
        parser = LinkParser(str(source.get("link_title_prefix", "")))
        parser.feed(payload.decode("utf-8", errors="replace"))
        if parser.overflow or len(links) + len(parser.links) > MAX_OPPORTUNITIES_PER_SOURCE:
            raise ResponseTooLargeError("HTML source contained too many links")
        declared_link_base = _html_link_base(
            parser.base_href,
            page_url,
            source_host,
        )
        resolution_base = configured_link_base or declared_link_base or page_url
        links.extend(
            (href, title, resolution_base) for href, title in parser.links
        )
    include = [value.lower() for value in source.get("include", [])]
    exclude = [value.lower() for value in source.get("exclude", [])]
    items = []
    seen = set()
    for href, title, resolution_base in links:
        url = canonical_url(urllib.parse.urljoin(resolution_base, href))
        haystack = "{} {}".format(title, url).lower()
        if not title or not url.startswith("http"):
            continue
        if (
            source.get("same_domain", False)
            and (urllib.parse.urlsplit(url).hostname or "").casefold() != source_host
        ):
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
                metadata=_source_metadata(
                    source,
                    adapter="html_links",
                    page_count=len(payloads),
                ),
            )
        )
    return FetchResult(items, content_hash, message="{} qualifying links".format(len(items)))


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
    _posted_at, deadline_at, dates = _listing_dates(source, page_text)
    item = Opportunity(
        source_id=source["id"],
        external_id=source["id"],
        title=title,
        organization=source["name"],
        url=source["url"],
        description=page_text[:10000],
        category=_source_category(source),
        opportunity_type=_infer_opportunity_type(source, title),
        deadline_at=deadline_at,
        metadata=_source_metadata(source, page_changed=True, dates=dates),
    )
    return FetchResult([item], semantic_hash, message="watch page reachable")


def polite_pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
