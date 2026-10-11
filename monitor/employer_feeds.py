"""Bounded public employer feeds beyond the hosted startup job boards."""

import hashlib
import html
import json
import re
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

from .fetchers import (
    MAX_JIBE_TOTAL_BYTES,
    MAX_JIBE_WALL_SECONDS,
    ResponseTooLargeError,
    _infer_opportunity_type,
    _listing_dates,
    _remote_url_parts,
    _request,
    _source_category,
    _source_metadata,
)
from .models import FetchResult, MAX_OPPORTUNITIES_PER_SOURCE, Opportunity
from .text import canonical_url, clean_text, stable_hash

WORKDAY_HOST_RE = re.compile(r"^([a-zA-Z0-9_-]+)\.wd[0-9]+\.myworkdayjobs\.com$")


def workday_board(url: str) -> Tuple[str, str]:
    """Resolve only a public Workday tenant and its external site."""
    url, host, _port = _remote_url_parts(url)
    match = WORKDAY_HOST_RE.fullmatch(host)
    parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts.pop(0)
    if not match or not parts or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", parts[0]):
        raise ValueError("Workday source must name a public external tenant and site")
    return match.group(1), parts[0]


def fetch_workday(source: Dict[str, Any]) -> FetchResult:
    """Collect every public search page; enrich descriptions within one budget.

    Workday search uses read-only JSON POST requests, with no authentication or
    application submission. Missing detail responses retain the real listing
    with an empty description and explicit provenance rather than inventing
    qualifications or discarding the complete search snapshot.
    """
    tenant, site = workday_board(source["url"])
    origin = "https://" + urllib.parse.urlsplit(source["url"]).netloc
    base = origin + "/wday/cxs/{}/{}".format(tenant, site)
    board_url = origin + "/en-US/" + site
    deadline = time.monotonic() + MAX_JIBE_WALL_SECONDS
    digest = hashlib.sha256()
    total_bytes = 0

    def request(url: str, search: Any = None) -> Dict[str, Any]:
        nonlocal total_bytes
        kwargs: Dict[str, Any] = {"deadline": deadline}
        if search is not None:
            kwargs["json_body"] = search
        body = _request(url, **kwargs)
        total_bytes += len(body)
        if total_bytes > MAX_JIBE_TOTAL_BYTES:
            raise ResponseTooLargeError(
                "Workday responses exceed the aggregate byte limit"
            )
        digest.update(body)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Workday response must be an object")
        return data

    jobs: List[Dict[str, Any]] = []
    seen = set()
    total = None
    while total is None or len(jobs) < total:
        page = request(
            base + "/jobs",
            {
                "limit": 20,
                "offset": len(jobs),
                "searchText": "",
                "appliedFacets": {},
            },
        )
        count = page.get("total")
        entries = page.get("jobPostings")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or not isinstance(entries, list)
        ):
            raise ValueError(
                "Workday search must contain an integer total and jobPostings list"
            )
        if count > MAX_OPPORTUNITIES_PER_SOURCE:
            raise ResponseTooLargeError("Workday board exceeds the listing limit")
        # Some Workday tenants report the total only on the first page and
        # use zero on subsequent pages even when jobPostings is nonempty.
        if total is not None and count not in (0, total):
            raise ValueError(
                "Workday search changed during pagination; retry next scan"
            )
        if total is None:
            total = count
        if (not entries and len(jobs) < total) or len(jobs) + len(entries) > total:
            raise ValueError(
                "Workday search returned an incomplete or inconsistent page"
            )
        for job in entries:
            if not isinstance(job, dict):
                raise ValueError("Workday job must be an object")
            path = str(job.get("externalPath", ""))
            if (
                not path.startswith("/job/")
                or "?" in path
                or "#" in path
                or ".." in path
                or "\\" in path
            ):
                raise ValueError("Workday job must have a safe external job path")
            if path in seen or not clean_text(job.get("title")):
                raise ValueError("Workday search returned duplicate or untitled jobs")
            seen.add(path)
            jobs.append(job)
        if not total:
            break

    items = []
    detailed = 0
    detail_unavailable = False
    for job in jobs:
        info: Dict[str, Any] = {}
        # Search is complete before optional detail enrichment starts. Preserve
        # the complete set even when one detail fails or the budget expires.
        if not detail_unavailable and time.monotonic() < deadline - 1:
            try:
                details = request(base + job["externalPath"])
                info = details.get("jobPostingInfo", {})
                if not isinstance(info, dict):
                    raise ValueError("Workday jobPostingInfo must be an object")
            except ResponseTooLargeError:
                detail_unavailable = True
            except (OSError, ValueError, TimeoutError):
                info = {}
        description = clean_text(info.get("jobDescription"))
        detailed += bool(description)
        title = clean_text(info.get("title") or job["title"])
        location = clean_text(info.get("location") or job.get("locationsText"))
        other_locations = info.get("additionalLocations", [])
        if isinstance(other_locations, list):
            location = "; ".join(
                dict.fromkeys(
                    [location]
                    + [clean_text(x) for x in other_locations if isinstance(x, str)]
                )
            )
        commitment = clean_text(info.get("timeType"))
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
            posted_value=info.get("startDate"),
            posted_provenance="workday.startDate",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=stable_hash(job["externalPath"]),
                title=title,
                organization=source["name"],
                url=canonical_url(board_url + job["externalPath"]),
                location=location,
                description=description,
                category=_source_category(source),
                commitment=commitment,
                opportunity_type=_infer_opportunity_type(source, title, commitment),
                posted_at=posted_at,
                deadline_at=deadline_at,
                metadata=_source_metadata(
                    source,
                    ats="workday",
                    dates=dates,
                    description_availability="full" if description else "title_only",
                ),
            )
        )
    return FetchResult(
        items,
        digest.hexdigest(),
        message="{} Workday jobs; {} full descriptions".format(len(items), detailed),
    )


def fetch_smartrecruiters(source: Dict[str, Any]) -> FetchResult:
    """Collect public SmartRecruiters postings without department filtering."""
    company = str(source.get("company", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", company):
        raise ValueError("SmartRecruiters company must be a valid identifier")
    base = "https://api.smartrecruiters.com/v1/companies/{}/postings".format(company)
    deadline = time.monotonic() + MAX_JIBE_WALL_SECONDS
    digest = hashlib.sha256()
    total_bytes = 0

    def request(url: str) -> Dict[str, Any]:
        nonlocal total_bytes
        body = _request(url, deadline=deadline)
        total_bytes += len(body)
        if total_bytes > MAX_JIBE_TOTAL_BYTES:
            raise ResponseTooLargeError(
                "SmartRecruiters aggregate response exceeds its limit"
            )
        digest.update(body)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("SmartRecruiters response must be an object")
        return data

    jobs = []
    seen = set()
    total = None
    while total is None or len(jobs) < total:
        data = request(base + "?limit=100&offset={}".format(len(jobs)))
        count = data.get("totalFound")
        entries = data.get("content")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or not isinstance(entries, list)
        ):
            raise ValueError(
                "SmartRecruiters search has an invalid total or content list"
            )
        if count > MAX_OPPORTUNITIES_PER_SOURCE:
            raise ResponseTooLargeError(
                "SmartRecruiters board exceeds the listing limit"
            )
        if total is not None and count != total:
            raise ValueError("SmartRecruiters board changed during pagination")
        total = count
        if (not entries and len(jobs) < total) or len(jobs) + len(entries) > total:
            raise ValueError("SmartRecruiters search returned an incomplete page")
        for job in entries:
            if (
                not isinstance(job, dict)
                or not str(job.get("id", "")).isdigit()
                or not clean_text(job.get("name"))
            ):
                raise ValueError("SmartRecruiters job must have an ID and title")
            if str(job["id"]) in seen:
                raise ValueError("SmartRecruiters search returned duplicate jobs")
            seen.add(str(job["id"]))
            jobs.append(job)
        if total == 0:
            break

    items = []
    for job in jobs:
        details = {}
        if time.monotonic() < deadline - 1 and total_bytes < MAX_JIBE_TOTAL_BYTES:
            try:
                details = request(base + "/" + str(job["id"]))
            except (OSError, ValueError, TimeoutError):
                details = {}
        ad = details.get("jobAd")
        sections = ad.get("sections", {}) if isinstance(ad, dict) else {}
        description = (
            clean_text(
                "\n".join(
                    str(section.get("text", ""))
                    for section in sections.values()
                    if isinstance(section, dict)
                )
            )
            if isinstance(sections, dict)
            else ""
        )
        location = job.get("location")
        location = location if isinstance(location, dict) else {}
        title = clean_text(job["name"])
        employment_data = job.get("typeOfEmployment")
        employment = (
            clean_text(employment_data.get("label"))
            if isinstance(employment_data, dict)
            else ""
        )
        function = job.get("function")
        category = (
            clean_text(function.get("label"))
            if isinstance(function, dict)
            else _source_category(source)
        )
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
            posted_value=job.get("releasedDate"),
            posted_provenance="smartrecruiters.releasedDate",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=str(job["id"]),
                organization=source["name"],
                title=title,
                url="https://jobs.smartrecruiters.com/{}/{}".format(company, job["id"]),
                location=clean_text(
                    location.get("fullLocation")
                    or ", ".join(
                        str(location.get(k) or "")
                        for k in ["city", "region", "country"]
                    )
                ),
                description=description,
                category=category,
                commitment=employment,
                opportunity_type=_infer_opportunity_type(source, title, employment),
                posted_at=posted_at,
                deadline_at=deadline_at,
                metadata=_source_metadata(
                    source,
                    ats="smartrecruiters",
                    dates=dates,
                    description_availability="full" if description else "title_only",
                ),
            )
        )
    return FetchResult(
        items, digest.hexdigest(), message="{} SmartRecruiters jobs".format(len(items))
    )


def fetch_rippling(source: Dict[str, Any]) -> FetchResult:
    """Read the public board's embedded JSON and advertised pages, without JS."""
    board = str(source.get("board", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", board):
        raise ValueError("Rippling board must be a valid slug")
    base = "https://ats.rippling.com/{}/jobs".format(board)
    digest = hashlib.sha256()
    deadline = time.monotonic() + MAX_JIBE_WALL_SECONDS
    total_bytes = 0
    page = 0
    total = None
    pages = 1
    items = []
    seen = set()
    while page < pages:
        body = _request(base + "?page={}".format(page), deadline=deadline)
        total_bytes += len(body)
        if total_bytes > MAX_JIBE_TOTAL_BYTES:
            raise ResponseTooLargeError("Rippling aggregate response exceeds its limit")
        digest.update(body)
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            body.decode("utf-8"),
            re.DOTALL,
        )
        if not match:
            raise ValueError("Rippling board omitted its public job data")
        data = json.loads(match.group(1))
        queries = (
            data.get("props", {})
            .get("pageProps", {})
            .get("dehydratedState", {})
            .get("queries", [])
        )
        searches = [
            q.get("state", {}).get("data")
            for q in queries
            if isinstance(q, dict) and "job-posts" in q.get("queryKey", [])
        ]
        if len(searches) != 1 or not isinstance(searches[0], dict):
            raise ValueError("Rippling board has no unique job search snapshot")
        search = searches[0]
        count, advertised = search.get("totalItems"), search.get("totalPages")
        entries = search.get("items")
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in [count, advertised]
            )
            or not isinstance(entries, list)
            or search.get("page") != page
        ):
            raise ValueError("Rippling board returned invalid pagination")
        if count > MAX_OPPORTUNITIES_PER_SOURCE or advertised > 250:
            raise ResponseTooLargeError("Rippling board exceeds its listing/page limit")
        if total is not None and (total != count or pages != max(1, advertised)):
            raise ValueError("Rippling board changed during pagination")
        total, pages = count, max(1, advertised)
        if not entries and len(items) < total:
            raise ValueError("Rippling board returned an incomplete page")
        for job in entries:
            if (
                not isinstance(job, dict)
                or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", str(job.get("id", "")))
                or not clean_text(job.get("name"))
            ):
                raise ValueError("Rippling job must have a valid ID and title")
            if job["id"] in seen:
                raise ValueError("Rippling board returned duplicate jobs")
            seen.add(job["id"])
            locations = job.get("locations", [])
            if not isinstance(locations, list):
                raise ValueError("Rippling job locations must be a list")
            department = job.get("department")
            category = (
                clean_text(department.get("name"))
                if isinstance(department, dict)
                else _source_category(source)
            )
            title = clean_text(job["name"])
            items.append(
                Opportunity(
                    source_id=source["id"],
                    external_id=job["id"],
                    title=title,
                    organization=source["name"],
                    url=base + "/" + job["id"],
                    location="; ".join(
                        clean_text(x.get("name"))
                        for x in locations
                        if isinstance(x, dict)
                    ),
                    category=category,
                    opportunity_type=_infer_opportunity_type(source, title),
                    metadata=_source_metadata(
                        source, ats="rippling", description_availability="title_only"
                    ),
                )
            )
        page += 1
    if len(items) != total:
        raise ValueError("Rippling board returned an incomplete job snapshot")
    return FetchResult(
        items, digest.hexdigest(), message="{} Rippling jobs".format(len(items))
    )


def fetch_recruiterbox(source: Dict[str, Any]) -> FetchResult:
    """Parse the public widget's literal JSON job list, never execute its JS."""
    widget = str(source.get("widget", ""))
    company = str(source.get("company", ""))
    if not widget.isdigit() or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", company):
        raise ValueError("Recruiterbox source requires a widget ID and company slug")
    body = _request(
        "https://w.recruiterbox.com/static/client-src-served/widget/{}/rbox_api.js".format(
            widget
        )
    )
    content = body.decode("utf-8")
    marker = re.search(r"\brbox\s*=\s*new Rbox\(", content)
    if not marker:
        raise ValueError("Recruiterbox widget omitted its public openings")
    jobs, _end = json.JSONDecoder().raw_decode(content[marker.end() :].lstrip())
    if not isinstance(jobs, list):
        raise ValueError("Recruiterbox openings must be a list")
    if len(jobs) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise ResponseTooLargeError("Recruiterbox board exceeds its listing limit")
    seen = set()
    items = []
    for job in jobs:
        if (
            not isinstance(job, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(job.get("hash_id", "")))
            or not clean_text(job.get("title"))
        ):
            raise ValueError("Recruiterbox job must have an ID and title")
        identifier = job["hash_id"]
        if identifier in seen:
            raise ValueError("Recruiterbox widget returned duplicate jobs")
        seen.add(identifier)
        location = job.get("location")
        location = location if isinstance(location, dict) else {}
        title = clean_text(job["title"])
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=identifier,
                title=title,
                organization=source["name"],
                url="https://{}.hire.trakstar.com/jobs/{}".format(company, identifier),
                location=", ".join(
                    clean_text(location.get(k))
                    for k in ["city", "state", "country"]
                    if location.get(k)
                ),
                commitment=clean_text(job.get("position_type")),
                category=clean_text(job.get("team")),
                opportunity_type=_infer_opportunity_type(
                    source, title, job.get("position_type")
                ),
                metadata=_source_metadata(
                    source, ats="recruiterbox", description_availability="title_only"
                ),
            )
        )
    return FetchResult(
        items,
        hashlib.sha256(body).hexdigest(),
        message="{} Recruiterbox openings".format(len(items)),
    )


def fetch_sap_public(source: Dict[str, Any]) -> FetchResult:
    """Collect the anonymous SAP career-site search, including every page."""
    url, _host, _port = _remote_url_parts(source["url"])
    origin = "https://" + urllib.parse.urlsplit(url).netloc
    deadline = time.monotonic() + MAX_JIBE_WALL_SECONDS
    digest = hashlib.sha256()
    total_bytes = 0
    total = None
    page = 0
    items = []
    seen = set()
    while total is None or len(items) < total:
        body = _request(
            origin + "/services/recruiting/v1/jobs",
            deadline=deadline,
            json_body={
                "keywords": "",
                "location": "",
                "locale": "en_US",
                "pageNumber": page,
                "sortBy": source.get("sort_by", "recent"),
            },
        )
        total_bytes += len(body)
        if total_bytes > MAX_JIBE_TOTAL_BYTES:
            raise ResponseTooLargeError(
                "SAP public search aggregate response exceeds its limit"
            )
        digest.update(body)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("SAP public search must return an object")
        count, entries = data.get("totalJobs"), data.get("jobSearchResult")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or not isinstance(entries, list)
        ):
            raise ValueError("SAP public search has an invalid total or job list")
        if count > MAX_OPPORTUNITIES_PER_SOURCE or page >= 500:
            raise ResponseTooLargeError(
                "SAP public search exceeds its listing/page limit"
            )
        if total is not None and count != total:
            raise ValueError("SAP public job search changed during pagination")
        total = count
        if (not entries and len(items) < total) or len(items) + len(entries) > total:
            raise ValueError("SAP public search returned an incomplete page")
        for entry in entries:
            job = entry.get("response") if isinstance(entry, dict) else None
            if (
                not isinstance(job, dict)
                or not str(job.get("id", "")).isdigit()
                or not clean_text(job.get("unifiedStandardTitle"))
            ):
                raise ValueError("SAP public job must have an ID and title")
            identifier = str(job["id"])
            if identifier in seen:
                raise ValueError("SAP public search returned duplicate jobs")
            seen.add(identifier)
            slug = str(job.get("unifiedUrlTitle") or "")
            if (
                not slug
                or len(slug) > 500
                or any(value in slug for value in ["/", "\\", "?", "#", ".."])
                or re.search(r"%(?![0-9A-Fa-f]{2})", slug)
            ):
                raise ValueError("SAP public job must have a safe URL title")
            title = clean_text(job["unifiedStandardTitle"])
            locations = job.get("jobLocationShort", [])
            locations = locations if isinstance(locations, list) else []
            items.append(
                Opportunity(
                    source_id=source["id"],
                    external_id=identifier,
                    title=title,
                    organization=source["name"],
                    url=origin
                    + "/job/{}/{}/".format(
                        urllib.parse.quote(html.unescape(slug), safe="%_-"), identifier
                    ),
                    location="; ".join(
                        clean_text(x) for x in locations if isinstance(x, str)
                    ),
                    category=_source_category(source),
                    opportunity_type=_infer_opportunity_type(source, title),
                    metadata=_source_metadata(
                        source, ats="sap_public", description_availability="title_only"
                    ),
                )
            )
        page += 1
        if not total:
            break
    return FetchResult(
        items, digest.hexdigest(), message="{} SAP public jobs".format(len(items))
    )


def fetch_comeet(source: Dict[str, Any]) -> FetchResult:
    """Read the literal public position snapshot embedded by Comeet."""
    url, host, _port = _remote_url_parts(source["url"])
    if host != "www.comeet.com" or not urllib.parse.urlsplit(url).path.startswith(
        "/jobs/"
    ):
        raise ValueError("Comeet source must name an official hosted job board")
    body = _request(url)
    text = body.decode("utf-8")
    marker = re.search(r"\bCOMPANY_POSITIONS_DATA\s*=\s*", text)
    if not marker:
        raise ValueError("Comeet board omitted its public position data")
    jobs, _end = json.JSONDecoder().raw_decode(text[marker.end() :])
    if not isinstance(jobs, list):
        raise ValueError("Comeet positions must be a list")
    if len(jobs) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise ResponseTooLargeError("Comeet board exceeds its listing limit")
    items = []
    seen = set()
    for job in jobs:
        if (
            not isinstance(job, dict)
            or not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", str(job.get("uid", "")))
            or not clean_text(job.get("name"))
        ):
            raise ValueError("Comeet job must have an ID and title")
        identifier = job["uid"]
        job_url = str(job.get("url_comeet_hosted_page") or "")
        if not job_url.startswith(url.rstrip("/") + "/") or identifier in seen:
            raise ValueError("Comeet job has an unsafe URL or duplicate ID")
        _remote_url_parts(job_url)
        seen.add(identifier)
        fields = job.get("custom_fields")
        details = fields.get("details", []) if isinstance(fields, dict) else []
        description = (
            clean_text(
                "\n".join(
                    str(x.get("value") or "") for x in details if isinstance(x, dict)
                )
            )
            if isinstance(details, list)
            else ""
        )
        location = job.get("location")
        location = location if isinstance(location, dict) else {}
        title = clean_text(job["name"])
        commitment = clean_text(job.get("employment_type"))
        posted_at, deadline_at, dates = _listing_dates(
            source,
            description,
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=identifier,
                title=title,
                organization=source["name"],
                url=canonical_url(job_url),
                location=clean_text(location.get("name")),
                description=description,
                commitment=commitment,
                category=clean_text(job.get("department")),
                opportunity_type=_infer_opportunity_type(source, title, commitment),
                posted_at=posted_at,
                deadline_at=deadline_at,
                metadata=_source_metadata(
                    source,
                    ats="comeet",
                    dates=dates,
                    description_availability="full" if description else "title_only",
                ),
            )
        )
    return FetchResult(
        items,
        hashlib.sha256(body).hexdigest(),
        message="{} Comeet jobs".format(len(items)),
    )


def fetch_workable(source: Dict[str, Any]) -> FetchResult:
    """Read the public all-jobs widget also used by employer career sites."""
    board = str(source.get("board", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", board):
        raise ValueError("Workable board must be a valid slug")
    body = _request("https://apply.workable.com/api/v1/widget/accounts/" + board)
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Workable widget must contain a jobs list")
    if len(data["jobs"]) > MAX_OPPORTUNITIES_PER_SOURCE:
        raise ResponseTooLargeError("Workable board exceeds its listing limit")
    items = []
    seen = set()
    for job in data["jobs"]:
        if (
            not isinstance(job, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(job.get("shortcode", "")))
            or not clean_text(job.get("title"))
        ):
            raise ValueError("Workable job must have an ID and title")
        identifier = job["shortcode"]
        if identifier in seen:
            raise ValueError("Workable widget returned duplicate jobs")
        seen.add(identifier)
        title = clean_text(job["title"])
        employment = clean_text(job.get("employment_type"))
        posted_at, deadline_at, dates = _listing_dates(
            source,
            "",
            posted_value=job.get("published_on"),
            posted_provenance="workable.published_on",
        )
        items.append(
            Opportunity(
                source_id=source["id"],
                external_id=identifier,
                title=title,
                organization=source["name"],
                url="https://apply.workable.com/j/" + identifier,
                location=", ".join(
                    clean_text(job.get(k))
                    for k in ["city", "state", "country"]
                    if job.get(k)
                ),
                commitment=employment,
                category=clean_text(job.get("department")),
                opportunity_type=_infer_opportunity_type(source, title, employment),
                posted_at=posted_at,
                deadline_at=deadline_at,
                metadata=_source_metadata(
                    source,
                    ats="workable",
                    dates=dates,
                    description_availability="title_only",
                ),
            )
        )
    return FetchResult(
        items,
        hashlib.sha256(body).hexdigest(),
        message="{} Workable jobs".format(len(items)),
    )
