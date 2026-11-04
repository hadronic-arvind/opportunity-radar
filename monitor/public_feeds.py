"""Bounded public listing discovery using common publication formats."""

import json
import math
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional

from . import fetchers
from .dates import normalize_timestamp
from .models import (
    MAX_DESCRIPTION_CHARS,
    MAX_OPPORTUNITIES_PER_SOURCE,
    MAX_URL_CHARS,
    FetchResult,
    Opportunity,
)
from .text import canonical_url, clean_text, infer_opportunity_type, stable_hash


SCHEMA_TYPES = {
    "JobPosting": "job",
    "Event": "event",
    "BusinessEvent": "event",
    "EducationEvent": "event",
    "Hackathon": "hackathon",
    "Course": "course",
    "EducationalOccupationalProgram": "training",
    "Grant": "grant",
    "MonetaryGrant": "grant",
}
SCHEMA_CHILDREN = ("@graph", "mainEntity", "itemListElement", "item", "subEvent")
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
ARBEITNOW_HOSTS = {"www.arbeitnow.com", "www.arbeitnow.co.uk"}
GRANTS_SEARCH_URL = "https://www.grants.gov/search-grants"
GRANTS_API_URL = "https://api.grants.gov/v1/api/search2"
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _ListingMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: List[str] = []
        self.microdata: List[Dict[str, Any]] = []
        self._current: Optional[List[str]] = None
        self._frames = []

    @staticmethod
    def _set_property(scope, names, value) -> None:
        for name in names:
            if name not in scope:
                scope[name] = value
            elif isinstance(scope[name], list):
                scope[name].append(value)
            else:
                scope[name] = [scope[name], value]

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        media_type = str(attributes.get("type") or "").split(";", 1)[0].strip().lower()
        if tag == "script" and media_type == "application/ld+json":
            self._current = []
        parent = self._frames[-1][1] if self._frames else None
        inherited_text = self._frames[-1][2] if self._frames else None
        properties = (attributes.get("itemprop") or "").split()
        scope = parent
        capture = None
        if "itemscope" in attributes:
            scope = {"@type": (attributes.get("itemtype") or "").split()}
            if parent is not None and properties:
                self._set_property(parent, properties, scope)
            elif _schema_kind(scope):
                scope["_scope_href"] = attributes.get("href")
                self.microdata.append(scope)
                if len(self.microdata) > MAX_OPPORTUNITIES_PER_SOURCE:
                    raise fetchers.ResponseTooLargeError(
                        "Page contains too many structured listings"
                    )
        elif parent is not None and properties:
            value = next(
                (
                    attributes[key]
                    for key in ("content", "href", "datetime", "src", "value")
                    if key in attributes
                ),
                None,
            )
            if value is not None:
                self._set_property(parent, properties, value)
            elif tag not in VOID_ELEMENTS:
                capture = (parent, properties, [])
        elif parent is not None and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if "_heading" not in parent and _schema_kind(parent):
                capture = (parent, ["_heading"], [])
        if tag not in VOID_ELEMENTS:
            if len(self._frames) >= 128:
                raise ValueError("Structured listing HTML is nested too deeply")
            self._frames.append((tag, scope, capture or inherited_text, capture))

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None
        for index in range(len(self._frames) - 1, -1, -1):
            if self._frames[index][0] == tag:
                for frame in self._frames[index:]:
                    if frame[3] is not None:
                        parent, properties, text = frame[3]
                        self._set_property(parent, properties, " ".join(text))
                del self._frames[index:]
                break

    def handle_data(self, data) -> None:
        if self._current is not None:
            self._current.append(data)
        elif self._frames and self._frames[-1][2] is not None:
            self._frames[-1][2][2].append(data)


class _NoDocumentTypes(ET.TreeBuilder):
    def doctype(self, name, pubid, system) -> None:
        raise ValueError("Syndication feeds must not declare XML document types")


def _text(value: Any, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    if isinstance(value, list):
        return " · ".join(filter(None, (_text(item, limit) for item in value[:30])))[
            :limit
        ]
    if isinstance(value, dict):
        return _text(
            value.get("@value") or value.get("name") or value.get("value"), limit
        )
    return clean_text(value[: limit * 2])[:limit] if isinstance(value, str) else ""


def _values(value: Any) -> List[Any]:
    return value if isinstance(value, list) else [value] if value is not None else []


def _https_link(base: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_URL_CHARS:
        return ""
    if any(ord(character) < 32 or character == "\\" for character in value):
        return ""
    try:
        validated, _host, _port = fetchers._remote_url_parts(
            urllib.parse.urljoin(base, value.strip())
        )
    except ValueError:
        return ""
    return canonical_url(validated)


def _collection(source: Dict[str, Any], evidence: str) -> Dict[str, Any]:
    return {
        "evidence": evidence,
        "source_url": source["url"],
        "publisher_url": source.get("publisher_url") or source["url"],
        "coverage": "partial",
        "context": "listing",
    }


def _schema_nodes(value: Any, depth: int = 0) -> Iterator[Dict[str, Any]]:
    if depth > 24:
        raise ValueError("Structured listing markup is nested too deeply")
    if isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child, depth + 1)
    elif isinstance(value, dict):
        yield value
        for key in SCHEMA_CHILDREN:
            if key in value:
                yield from _schema_nodes(value[key], depth + 1)


def _schema_local_name(value: str) -> str:
    for prefix in ("https://schema.org/", "http://schema.org/", "schema:"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _schema_kind(node: Dict[str, Any]) -> str:
    kinds = [
        _schema_local_name(value)
        for value in _values(node.get("@type"))
        if isinstance(value, str)
    ]
    return next((kind for kind in SCHEMA_TYPES if kind in kinds), "")


def _schema_location(value: Any) -> str:
    locations = []
    for place in _values(value)[:30]:
        if not isinstance(place, dict):
            locations.append(_text(place, 500))
            continue
        address = place.get("address", place)
        if isinstance(address, dict):
            parts = [
                _text(address.get(key), 200)
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            location = ", ".join(dict.fromkeys(filter(None, parts)))
        else:
            location = _text(address, 500)
        locations.append(location or _text(place.get("name"), 500))
    return " · ".join(dict.fromkeys(filter(None, locations)))


def _money_number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and len(value) > 50:
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _schema_money(amount: Any, currency: Any = None) -> Dict[str, Any]:
    """Retain explicit amounts and units without treating estimates as offers."""
    fields = amount if isinstance(amount, dict) else {"value": amount}
    values = fields.get("value")
    if not isinstance(values, dict):
        values = fields
    result = {
        key: number
        for key, field in (
            ("value", "value"),
            ("minimum", "minValue"),
            ("maximum", "maxValue"),
        )
        for number in [_money_number(values.get(field))]
        if number is not None
    }
    if not result or result.get("minimum", 0) > result.get("maximum", math.inf):
        return {}
    currency_text = _text(fields.get("currency") or currency, 30)
    unit = _text(values.get("unitText") or values.get("unitCode"), 50)
    if currency_text:
        result["currency"] = currency_text
    if unit:
        result["unit"] = unit
    return result


def _schema_items(source: Dict[str, Any], payload: bytes) -> List[Opportunity]:
    parser = _ListingMarkup()
    parser.feed(payload.decode("utf-8-sig", errors="replace"))
    parser.close()
    candidates = []
    references = {}
    for block in parser.blocks:
        try:
            body = json.loads(block.lstrip("\ufeff"))
        except (ValueError, RecursionError):
            continue
        for node in _schema_nodes(body):
            node_id = node.get("@id")
            if isinstance(node_id, str) and len(node) > 1:
                references[node_id] = node
            kind = _schema_kind(node)
            if kind:
                candidates.append((kind, node, "json_ld"))
                if len(candidates) > MAX_OPPORTUNITIES_PER_SOURCE:
                    raise fetchers.ResponseTooLargeError(
                        "Page contains too many structured listings"
                    )

    candidates.extend(
        (_schema_kind(node), node, "microdata") for node in parser.microdata
    )

    def resolve(value):
        if isinstance(value, dict):
            reference = value.get("@id")
            if isinstance(reference, str):
                return {**references.get(reference, {}), **value}
        if isinstance(value, list):
            return [resolve(child) for child in value[:30]]
        return value

    results = []
    seen = set()
    for kind, node, adapter in candidates:
        fallback = SCHEMA_TYPES[kind]
        title = _text(
            node.get("title") or node.get("name") or node.get("_heading"), 500
        )
        link = node.get("url") or node.get("_scope_href") or node.get("@id")
        url = _https_link(source["url"], link)
        if not url and len(candidates) == 1 and not link:
            url = canonical_url(source["url"])
        if not title or not url or url in seen:
            continue
        organization = _text(
            resolve(
                node.get("hiringOrganization")
                or node.get("organizer")
                or node.get("provider")
                or node.get("funder")
                or node.get("sponsor")
            ),
            300,
        )
        description = _text(node.get("description"))
        employment = _text(node.get("employmentType"), 300)
        remote = _text(node.get("jobLocationType"), 100).casefold() == "telecommute"
        applicant_locations = _schema_location(
            resolve(node.get("applicantLocationRequirements"))
        )
        location = _schema_location(
            resolve(node.get("jobLocation") or node.get("location"))
        )
        attendance = _schema_local_name(_text(node.get("eventAttendanceMode"), 120))
        if remote or attendance == "OnlineEventAttendanceMode":
            location = "Remote" + (
                " · " + applicant_locations if applicant_locations else ""
            )
        posted, deadline, dates = fetchers._listing_dates(
            source,
            description,
            node.get("datePosted"),
            "schema.org datePosted",
            deadline_value=node.get("applicationDeadline")
            or (node.get("validThrough") if kind == "JobPosting" else None),
            deadline_provenance="schema.org applicationDeadline"
            if node.get("applicationDeadline")
            else "schema.org validThrough",
        )
        eligibility = " · ".join(
            filter(
                None,
                (
                    _text(node.get("eligibilityToWorkRequirement"), 1000),
                    _text(
                        node.get("programPrerequisites")
                        or node.get("coursePrerequisites"),
                        1000,
                    ),
                    _text(node.get("eligibilityRequirements"), 1000),
                ),
            )
        )
        free = node.get("isAccessibleForFree")
        if isinstance(free, str):
            free = {"true": True, "false": False}.get(free.casefold())
        listing = {
            "schema_type": kind,
            "starts_at": normalize_timestamp(node.get("startDate")),
            "ends_at": normalize_timestamp(node.get("endDate")),
            "applications_open_at": normalize_timestamp(
                node.get("applicationStartDate")
            ),
            "attendance": attendance,
            "event_status": _schema_local_name(_text(node.get("eventStatus"), 120)),
            "is_free": free if isinstance(free, bool) else None,
            "applicant_location_requirements": applicant_locations,
            "credential": _text(node.get("educationalCredentialAwarded"), 300),
            "duration": _text(node.get("timeToComplete") or node.get("duration"), 120),
            "eligibility": eligibility,
        }
        if kind == "JobPosting":
            listing["base_salary"] = _schema_money(
                resolve(node.get("baseSalary")), node.get("salaryCurrency")
            )
        elif kind in {"Grant", "MonetaryGrant"}:
            listing["grant_amount"] = _schema_money(resolve(node.get("amount")))
        results.append(
            Opportunity(
                source_id=source["id"],
                external_id=stable_hash(url),
                title=title,
                organization=organization or source["name"],
                url=url,
                location=location,
                description=description,
                category=fetchers._source_category(
                    source, _text(node.get("occupationalCategory"), 500)
                ),
                opportunity_type=infer_opportunity_type(
                    title,
                    (_text(node.get("employmentType"), 300),),
                    explicit=source.get("opportunity_type"),
                    default=source.get("default_opportunity_type", fallback),
                    employment_feed=kind == "JobPosting",
                ),
                posted_at=posted,
                deadline_at=deadline,
                commitment=employment,
                eligibility=eligibility,
                metadata=fetchers._source_metadata(
                    source,
                    adapter=adapter,
                    collection=_collection(source, "structured_listing"),
                    listing={
                        key: value
                        for key, value in listing.items()
                        if value not in (None, "", {})
                    },
                    dates=dates,
                ),
            )
        )
        seen.add(url)
    return results


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold()


def _xml_value(element: ET.Element, *names: str) -> str:
    for name in names:
        for child in element:
            if _local_tag(child) == name:
                value = " ".join(child.itertext()).strip()
                if value:
                    return value
    return ""


def _rss_timestamp(value: str) -> Optional[str]:
    iso = normalize_timestamp(value)
    if iso:
        return iso
    try:
        return parsedate_to_datetime(value).isoformat() if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def _syndication_items(source: Dict[str, Any], payload: bytes) -> List[Opportunity]:
    try:
        root = ET.fromstring(payload, parser=ET.XMLParser(target=_NoDocumentTypes()))
    except ET.ParseError as error:
        raise ValueError("Source did not return a valid RSS or Atom feed") from error
    root_type = _local_tag(root)
    container = root
    if root_type == "rss":
        container = next(
            (child for child in root if _local_tag(child) == "channel"), None
        )
        if container is None:
            raise ValueError("RSS feed did not contain a channel")
    elif root_type not in {"rdf", "feed"}:
        raise ValueError("Source did not return an RSS or Atom feed")
    expected_tag = "entry" if root_type == "feed" else "item"
    entries = [child for child in container if _local_tag(child) == expected_tag]
    if len(entries) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise fetchers.ResponseTooLargeError(
            "Syndication feed contains too many listings"
        )
    base = urllib.parse.urljoin(source["url"], root.get(XML_BASE, ""))
    if container is not root:
        base = urllib.parse.urljoin(base, container.get(XML_BASE, ""))
    results = []
    seen = set()
    for entry in entries:
        title = _text(_xml_value(entry, "title"), 500)
        entry_base = urllib.parse.urljoin(base, entry.get(XML_BASE, ""))
        url = ""
        for child in entry:
            if (
                _local_tag(child) != "link"
                or child.get("rel", "alternate") != "alternate"
            ):
                continue
            link_base = urllib.parse.urljoin(entry_base, child.get(XML_BASE, ""))
            url = _https_link(link_base, child.get("href") or child.text)
            if url:
                break
        if not title or not url or url in seen:
            continue
        description = _text(
            _xml_value(entry, "encoded", "content", "description", "summary")
        )
        category = _text(
            [
                child.get("term") or " ".join(child.itertext())
                for child in entry
                if _local_tag(child) == "category"
            ],
            500,
        )
        posted_value = _rss_timestamp(_xml_value(entry, "published", "pubdate", "date"))
        posted, deadline, dates = fetchers._listing_dates(
            source,
            description,
            posted_value,
            "syndication publication date",
            updated_value=_rss_timestamp(_xml_value(entry, "updated")),
            updated_provenance="Atom updated",
        )
        results.append(
            Opportunity(
                source_id=source["id"],
                external_id=stable_hash(url),
                title=title,
                organization=_text(
                    _xml_value(entry, "company", "organization", "employer"), 300
                )
                or source["name"],
                url=url,
                location=_text(_xml_value(entry, "location"), 500),
                description=description,
                category=fetchers._source_category(source, category),
                opportunity_type=fetchers._infer_opportunity_type(
                    source, title, category
                ),
                posted_at=posted,
                deadline_at=deadline,
                metadata=fetchers._source_metadata(
                    source,
                    adapter="syndication",
                    collection=_collection(source, "feed_entry"),
                    feed_entry_id=_text(_xml_value(entry, "guid", "id"), 300),
                    dates=dates,
                ),
            )
        )
        seen.add(url)
    if entries and not results:
        raise ValueError("Feed entries had no usable HTTPS listing links")
    return results


def fetch_public_page(
    source: Dict[str, Any], prefetched: Optional[bytes] = None
) -> FetchResult:
    kind = source["kind"]
    payload = fetchers._request(source["url"]) if prefetched is None else prefetched
    prefix = payload[:1000].decode("utf-8-sig", errors="replace").lstrip().casefold()
    if kind == "syndication" or (
        kind == "public_page"
        and prefix.startswith(("<?xml", "<rss", "<feed", "<rdf:rdf"))
    ):
        items = _syndication_items(source, payload)
        message = "{} syndicated listings".format(len(items))
    else:
        items = _schema_items(source, payload)
        message = "{} structured listings".format(len(items))
        if not items and kind == "public_page":
            links = fetchers.fetch_html_links(source, prefetched=payload)
            items = links.opportunities
            message = "{} directory links; listing details unavailable".format(
                len(items)
            )
        if not items:
            raise ValueError(
                "Page did not publish any recognizable opportunity listings"
            )
    return FetchResult(
        items, fetchers._hash(payload), message=message, complete_snapshot=False
    )


def arbeitnow_publisher(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname not in ARBEITNOW_HOSTS or parsed.path != "/api/job-board-api":
        raise ValueError("Arbeitnow requires its published EU or UK job-board API URL")
    return "https://{}/".format(parsed.hostname)


def _unix_timestamp(value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def fetch_arbeitnow(source: Dict[str, Any]) -> FetchResult:
    publisher_url = arbeitnow_publisher(source["url"])
    payload = fetchers._request(source["url"])
    feed = json.loads(payload)
    records = feed.get("data") if isinstance(feed, dict) else None
    if not isinstance(records, list):
        raise ValueError("Arbeitnow returned an unexpected jobs response")
    if len(records) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise fetchers.ResponseTooLargeError(
            "Arbeitnow feed contains too many listings"
        )
    items = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        url = _https_link(publisher_url, record.get("url"))
        title = _text(record.get("title"), 500)
        company = _text(record.get("company_name"), 300)
        if not title or not company or not url or url in seen:
            continue
        location = _text(record.get("location"), 500)
        if record.get("remote") is True and "remote" not in location.casefold().split():
            location = "Remote" + (" · " + location if location else "")
        description = _text(record.get("description"))
        posted, deadline, dates = fetchers._listing_dates(
            source,
            description,
            _unix_timestamp(record.get("created_at")),
            "Arbeitnow created_at",
        )
        employment = _text(record.get("job_types"), 300)
        tags = _text(record.get("tags"), 500)
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=stable_hash(url),
                title=title,
                organization=company,
                url=url,
                location=location,
                description=description,
                category=fetchers._source_category(source, tags),
                opportunity_type=fetchers._infer_opportunity_type(
                    source, title, employment
                ),
                posted_at=posted,
                deadline_at=deadline,
                commitment=employment,
                metadata=fetchers._source_metadata(
                    source,
                    adapter="arbeitnow",
                    collection={
                        **_collection(source, "syndicated_listing"),
                        "publisher_url": publisher_url,
                    },
                    dates=dates,
                ),
            )
        )
        seen.add(url)
    if records and not items:
        raise ValueError("Arbeitnow returned listings without usable identities")
    return FetchResult(
        items,
        fetchers._hash(payload),
        message="{} recent listings via Arbeitnow".format(len(items)),
        complete_snapshot=False,
    )


def _grants_date(value: Any) -> Optional[str]:
    try:
        return (
            datetime.strptime(value, "%m/%d/%Y").date().isoformat() if value else None
        )
    except (ValueError, TypeError):
        return None


def fetch_grants_gov(source: Dict[str, Any]) -> FetchResult:
    """Query posted U.S. federal awards with the agency's Individuals filter."""
    payload = fetchers._request(
        GRANTS_API_URL,
        json_body={"rows": 100, "eligibilities": "21", "oppStatuses": "posted"},
    )
    feed = json.loads(payload)
    data = (
        feed.get("data")
        if isinstance(feed, dict) and feed.get("errorcode") == 0
        else None
    )
    records = data.get("oppHits") if isinstance(data, dict) else None
    count = data.get("hitCount") if isinstance(data, dict) else None
    if (
        not isinstance(records, list)
        or type(count) is not int
        or count < len(records)
        or len(records) > 100
    ):
        raise ValueError("Grants.gov returned an unexpected opportunities response")
    if count and not records:
        raise ValueError("Grants.gov returned an empty page of available opportunities")
    complete = count == len(records)
    items = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or record.get("oppStatus") != "posted":
            raise ValueError(
                "Grants.gov returned a record outside the requested status"
            )
        identifier = str(record.get("id", ""))
        title = _text(record.get("title"), 500)
        if (
            not identifier.isascii()
            or not identifier.isdecimal()
            or identifier in seen
            or not title
        ):
            raise ValueError("Grants.gov returned a duplicate or invalid opportunity")
        seen.add(identifier)
        _posted, deadline, dates = fetchers._listing_dates(
            source,
            "",
            deadline_value=_grants_date(record.get("closeDate")),
            deadline_provenance="Grants.gov closeDate",
        )
        listing = {
            "funding_number": _text(record.get("number"), 120),
            "applications_open_at": _grants_date(record.get("openDate")),
            "applicant_category": "Individuals",
        }
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=identifier,
                title=title,
                organization=_text(
                    record.get("agency") or record.get("agencyName"), 300
                )
                or source["name"],
                url="https://www.grants.gov/search-results-detail/" + identifier,
                category="Federal funding",
                opportunity_type=infer_opportunity_type(
                    title,
                    explicit=source.get("opportunity_type"),
                    default=source.get("default_opportunity_type", "grant"),
                ),
                deadline_at=deadline,
                eligibility="Grants.gov lists this opportunity under Individuals. The official notice contains any additional eligibility requirements.",
                metadata=fetchers._source_metadata(
                    source,
                    adapter="grants_gov",
                    collection={
                        **_collection(source, "structured_listing"),
                        "coverage": "complete" if complete else "partial",
                    },
                    listing={key: value for key, value in listing.items() if value},
                    dates=dates,
                ),
            )
        )
    # The response includes an ephemeral token. Hash only the listings.
    digest = fetchers._hash(json.dumps([count, records], sort_keys=True).encode())
    return FetchResult(
        items,
        digest,
        message="{} open federal funding opportunities".format(len(items)),
        complete_snapshot=complete,
    )
