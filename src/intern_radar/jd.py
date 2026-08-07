"""Fetch the full job description for a single posting URL.

Resolves which ATS a posting URL belongs to, hits that ATS's per-job (or
board) API, and flattens the description HTML to readable plain text —
the input for tailoring a resume against the posting.

Supported URL shapes:
    https://boards.greenhouse.io/{board}/jobs/{id}   (also job-boards.)
    https://jobs.lever.co/{company}/{uuid}
    https://jobs.ashbyhq.com/{org}/{uuid}
    https://{tenant}.{inst}.myworkdayjobs.com/[{locale}/]{site}/job/.../{slug}
    https://jobs.smartrecruiters.com/{company}/{id}[-{slug}]
    https://{tenant}.icims.com/jobs/{id}/[{slug}/]job
    https://{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/
        {locale}/sites/{site}/job/{id}
"""

from __future__ import annotations

import html as html_mod
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

from intern_radar.http import FetchError, get_json, get_text

_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_SR_ID_RE = re.compile(r"^(\d+)")
_LD_JSON_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


class JDError(Exception):
    pass


@dataclass(frozen=True)
class JobDescription:
    source: str
    company: str
    title: str
    location: str
    url: str
    text: str

    def render(self) -> str:
        header = f"# {self.title}\n{self.company}"
        if self.location:
            header += f" — {self.location}"
        return f"{header}\n{self.url}\n\n{self.text}\n"


_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "ul", "ol", "table", "tr", "blockquote", "section",
     "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6"}
)
_CELL_TAGS = frozenset({"td", "th"})
_SKIP_TAGS = frozenset({"script", "style"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _CELL_TAGS:
            self.parts.append(" ")  # keep adjacent table cells from fusing
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag != "br" and (tag == "li" or tag in _BLOCK_TAGS):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(fragment: str) -> str:
    """Flatten an HTML fragment to plain text: block tags break lines, <li> -> '- '."""
    extractor = _TextExtractor()
    extractor.feed(fragment)
    extractor.close()
    raw = "".join(extractor.parts)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Block tags inside <li> (Workday ships <li><p>text</p></li>) leave the
    # bullet marker orphaned on its own line — the per-line strip above has
    # already reduced it to a bare "-", so rejoin it with its text.
    text = re.sub(r"^-\n+(?=\S)", "- ", text, flags=re.MULTILINE)
    # </li>-then-<li> leaves a blank line between bullets — keep lists tight.
    text = re.sub(r"\n{2,}(?=- )", "\n", text)
    return text.strip()


def _segment_after(parts: list[str], marker: str) -> str:
    """The path segment following `marker`, or "" — locale prefixes move it."""
    for i, part in enumerate(parts[:-1]):
        if part.lower() == marker:
            return parts[i + 1]
    return ""


def _require_text(source: str, url: str, text: str) -> str:
    """Empty JD text must be an error, never a silently header-only file."""
    if not text.strip():
        raise JDError(f"{source}: posting returned an empty job description: {url}")
    return text


def parse_workday_jd(url: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostingInfo"), dict):
        raise JDError(f"workday: expected a dict with 'jobPostingInfo': {url}")
    info = payload["jobPostingInfo"]
    tenant = urlparse(url).netloc.split(".")[0]
    locations = [str(info.get("location") or "").strip()]
    extra = info.get("additionalLocations")
    if isinstance(extra, list):
        locations.extend(str(x).strip() for x in extra)
    org = payload.get("hiringOrganization")
    company = ""
    if isinstance(org, dict):
        company = str(org.get("name", "")).strip()
    company = company or tenant
    return JobDescription(
        source="workday",
        company=company,
        title=str(info.get("title") or "").strip(),
        location="; ".join(x for x in locations if x),
        url=url,
        text=_require_text("workday", url, html_to_text(str(info.get("jobDescription") or ""))),
    )


def parse_greenhouse_jd(url: str, board: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or "content" not in payload:
        raise JDError(f"greenhouse: expected a dict with 'content': {url}")
    location = payload.get("location")
    location_name = ""
    if isinstance(location, dict):
        location_name = str(location.get("name", "")).strip()
    # Greenhouse ships `content` as entity-escaped HTML (&lt;p&gt;...).
    content = html_mod.unescape(str(payload.get("content") or ""))
    return JobDescription(
        source="greenhouse",
        company=str(payload.get("company_name", "")).strip() or board,
        title=str(payload.get("title") or "").strip(),
        location=location_name,
        url=url,
        text=_require_text("greenhouse", url, html_to_text(content)),
    )


def parse_lever_jd(url: str, company: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or not payload.get("text"):
        raise JDError(f"lever: expected a posting dict with 'text': {url}")
    chunks = [str(payload.get("descriptionPlain") or "").strip()]
    lists = payload.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("text") or "").strip()
            body = html_to_text(str(section.get("content") or ""))
            if heading:
                chunks.append(f"{heading}\n{body}")
            elif body:
                chunks.append(body)
    chunks.append(str(payload.get("additionalPlain") or "").strip())
    categories = payload.get("categories")
    locations: list[str] = []
    if isinstance(categories, dict):
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [str(x).strip() for x in all_locations if str(x).strip()]
        elif categories.get("location"):
            locations = [str(categories["location"]).strip()]
    return JobDescription(
        source="lever",
        company=company,
        title=str(payload.get("text") or "").strip(),
        location="; ".join(locations),
        url=url,
        text=_require_text("lever", url, "\n\n".join(c for c in chunks if c)),
    )


def parse_ashby_jd(url: str, org: str, job_id: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise JDError(f"ashby: expected a dict with a 'jobs' list: {url}")
    for job in payload["jobs"]:
        if not isinstance(job, dict) or str(job.get("id", "")) != job_id:
            continue
        description = str(job.get("descriptionHtml") or "")
        text = html_to_text(description) if description else str(
            job.get("descriptionPlain") or ""
        ).strip()
        if not text:
            raise JDError(f"ashby: job {job_id} has no description in board payload: {url}")
        locations = [str(job.get("location", "")).strip()]
        secondary = job.get("secondaryLocations")
        if isinstance(secondary, list):
            for entry in secondary:
                if isinstance(entry, dict) and entry.get("location"):
                    locations.append(str(entry["location"]).strip())
        return JobDescription(
            source="ashby",
            company=org,
            title=str(job.get("title") or "").strip(),
            location="; ".join(x for x in locations if x),
            url=url,
            text=text,
        )
    raise JDError(f"ashby: job {job_id} not on the {org} board (unlisted or closed): {url}")


def parse_smartrecruiters_jd(url: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobAd"), dict):
        raise JDError(f"smartrecruiters: expected a dict with 'jobAd': {url}")
    company_info = payload.get("company")
    company = ""
    if isinstance(company_info, dict):
        company = str(company_info.get("name", "")).strip()
    location = payload.get("location")
    location_name = ""
    if isinstance(location, dict):
        location_name = str(location.get("fullLocation", "")).strip() or ", ".join(
            str(location.get(k, "")).strip()
            for k in ("city", "region", "country")
            if str(location.get(k, "")).strip()
        )
    sections = payload["jobAd"].get("sections")
    chunks: list[str] = []
    if isinstance(sections, dict):
        for section in sections.values():
            if not isinstance(section, dict):
                continue
            heading = str(section.get("title") or "").strip()
            body = html_to_text(str(section.get("text") or ""))
            if body:
                chunks.append(f"{heading}\n{body}" if heading else body)
    return JobDescription(
        source="smartrecruiters",
        company=company,
        title=str(payload.get("name") or "").strip(),
        location=location_name,
        url=url,
        text=_require_text("smartrecruiters", url, "\n\n".join(chunks)),
    )


# Substance first, then the boilerplate blocks. Oracle tenants disagree about
# which field the body lands in: some ship one ExternalDescriptionStr, others
# split it across responsibilities/qualifications, so every one is concatenated.
_ORACLE_BODY_FIELDS = (
    "ShortDescriptionStr",
    "ExternalDescriptionStr",
    "ExternalResponsibilitiesStr",
    "ExternalQualificationsStr",
)
# Company marketing and benefits copy. Excluded from the JD text — it dilutes
# the tailoring input — unless a tenant put the whole posting in there, in
# which case an empty body is worse than a boilerplate one.
_ORACLE_BOILERPLATE_FIELDS = ("CorporateDescriptionStr", "OrganizationDescriptionStr")


def parse_oracle_jd(url: str, tenant: str, payload: Any) -> JobDescription:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise JDError(f"oracle: expected a dict with an 'items' list: {url}")
    items = payload["items"]
    if not items or not isinstance(items[0], dict):
        # A wrong or retired requisition id is HTTP 200 with items: [].
        raise JDError(f"oracle: no requisition matched that id (closed or wrong site): {url}")
    item = items[0]

    def body(fields: tuple[str, ...]) -> str:
        chunks = [html_to_text(str(item.get(f) or "")) for f in fields]
        return "\n\n".join(c for c in chunks if c)

    text = body(_ORACLE_BODY_FIELDS) or body(_ORACLE_BOILERPLATE_FIELDS)

    locations = [str(item.get("PrimaryLocation") or "").strip()]
    for key in ("secondaryLocations", "otherWorkLocations"):
        extra = item.get(key)
        if isinstance(extra, list):
            locations.extend(
                str(e.get("Name") or "").strip() for e in extra if isinstance(e, dict)
            )
    # Oracle ships no company name anywhere in the requisition payload (its
    # LegalEmployer/Organization fields come back null), and the tenant is an
    # opaque code for all but a few employers — so `egug` stands in for
    # American Express. The JD body is what tailoring reads; this is a header.
    return JobDescription(
        source="oracle",
        company=tenant,
        title=str(item.get("Title") or "").strip(),
        location="; ".join(dict.fromkeys(x for x in locations if x)),
        url=url,
        text=_require_text("oracle", url, text),
    )


def parse_icims_jd(url: str, tenant: str, page: str) -> JobDescription:
    """Pull the schema.org JobPosting block out of a rendered iCIMS job page.

    iCIMS has no public per-job JSON API, but every job page embeds a
    JobPosting ld+json block whose `description` is the complete posting
    (verified against the rendered body: it adds no text the block lacks).
    """
    posting: dict[str, Any] | None = None
    for block in _LD_JSON_RE.findall(page):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            posting = data
            break
    if posting is None:
        raise JDError(
            f"icims: no JobPosting metadata on the page — the posting may be "
            f"closed, or behind a login: {url}"
        )

    org = posting.get("hiringOrganization")
    company = ""
    if isinstance(org, dict):
        company = str(org.get("name", "")).strip()

    raw_locations = posting.get("jobLocation")
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]
    locations: list[str] = []
    if isinstance(raw_locations, list):
        for place in raw_locations:
            if not isinstance(place, dict):
                continue
            address = place.get("address")
            if not isinstance(address, dict):
                continue
            # iCIMS writes the literal string "UNAVAILABLE" into address
            # fields it has no value for; those must not reach the header.
            parts = [
                str(address.get(k, "")).strip()
                for k in ("addressLocality", "addressRegion", "addressCountry")
            ]
            pretty = ", ".join(p for p in parts if p and p.upper() != "UNAVAILABLE")
            if pretty:
                locations.append(pretty)

    return JobDescription(
        source="icims",
        company=company or tenant,
        title=str(posting.get("title") or "").strip(),
        location="; ".join(dict.fromkeys(locations)),
        url=url,
        text=_require_text(
            "icims", url, html_to_text(str(posting.get("description") or ""))
        ),
    )


def fetch_jd(url: str, *, board: str = "") -> JobDescription:
    """Resolve the ATS from a posting URL and fetch its full job description.

    `board` is only needed for Greenhouse postings on a company's custom
    domain (".../jobs/search?gh_jid=123"), where the board slug isn't in
    the URL.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        # /{board}/jobs/{id}
        if len(parts) < 3 or parts[1] != "jobs":
            raise JDError(f"unrecognized greenhouse URL (want /board/jobs/id): {url}")
        board, job_id = parts[0], parts[2]
        api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
        return parse_greenhouse_jd(url, board, get_json(api))

    # Greenhouse on a company's own domain: watcher notifications carry
    # these for boards whose absolute_url isn't on boards.greenhouse.io.
    gh_jid = parse_qs(parsed.query).get("gh_jid", [""])[0]
    if gh_jid:
        if not board:
            raise JDError(
                f"greenhouse posting on a custom domain — pass --board <slug> "
                f"(usually the company name, e.g. --board stripe): {url}"
            )
        api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{gh_jid}"
        return parse_greenhouse_jd(url, board, get_json(api))

    if host == "jobs.lever.co":
        if len(parts) < 2:
            raise JDError(f"unrecognized lever URL (want /company/id): {url}")
        company, job_id = parts[0], parts[1]
        api = f"https://api.lever.co/v0/postings/{company}/{job_id}"
        return parse_lever_jd(url, company, get_json(api))

    if host == "jobs.ashbyhq.com":
        # /{org}/{uuid}[/application] — the posting API has no per-job
        # endpoint, so fetch the board and pick the job out.
        if len(parts) < 2:
            raise JDError(f"unrecognized ashby URL (want /org/id): {url}")
        org, job_id = parts[0], parts[1]
        api = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
        return parse_ashby_jd(url, org, job_id, get_json(api))

    if host.endswith(".myworkdayjobs.com"):
        # /[{locale}/]{site}/job/{location}/{slug}
        if parts and _LOCALE_RE.match(parts[0]):
            parts = parts[1:]
        # Copied-from-the-apply-page URLs carry a trailing /apply segment.
        if len(parts) > 3 and parts[-1].lower() == "apply":
            parts = parts[:-1]
        if len(parts) < 3 or parts[1] != "job":
            raise JDError(f"unrecognized workday URL (want /site/job/.../slug): {url}")
        tenant = host.split(".")[0]
        site, external_path = parts[0], "/".join(parts[1:])
        api = f"https://{host}/wday/cxs/{tenant}/{site}/{external_path}"
        return parse_workday_jd(url, get_json(api))

    if host == "jobs.smartrecruiters.com":
        # /{company}/{id}-{slug}
        if parts and parts[0] == "oneclick-ui":
            raise JDError(
                f"smartrecruiters apply-page URL — use the posting link "
                f"(jobs.smartrecruiters.com/<company>/<numeric-id>) instead: {url}"
            )
        if len(parts) < 2:
            raise JDError(f"unrecognized smartrecruiters URL (want /company/id): {url}")
        company = parts[0]
        id_match = _SR_ID_RE.match(parts[1])
        if not id_match:
            raise JDError(f"unrecognized smartrecruiters URL (no numeric id): {url}")
        api = (
            "https://api.smartrecruiters.com/v1/companies/"
            f"{company}/postings/{id_match.group(1)}"
        )
        return parse_smartrecruiters_jd(url, get_json(api))

    if host.endswith(".icims.com"):
        # /jobs/{id}[/{slug}]/job — the id is the only stable segment.
        if len(parts) < 2 or parts[0] != "jobs" or not parts[1].isdigit():
            raise JDError(f"unrecognized icims URL (want /jobs/<id>/job): {url}")
        tenant = host.split(".")[0].removeprefix("careers-")
        # Without these two params iCIMS serves a redirect shim: HTTP 200 with
        # no ld+json at all. They are forced on, not inherited from the URL.
        api = f"https://{host}/jobs/{parts[1]}/job?mobile=true&needsRedirect=false"
        try:
            page = get_text(api)
        except FetchError as e:
            if e.status == 410:
                raise JDError(
                    f"icims: posting {parts[1]} is closed — iCIMS returns HTTP 410 "
                    f"once a req is filled or expired: {url}"
                ) from e
            raise
        return parse_icims_jd(url, tenant, page)

    if host.endswith(".oraclecloud.com"):
        # /hcmUI/CandidateExperience/{locale}/sites/{site}/job/{id}[/apply]
        site = _segment_after(parts, "sites")
        job_id = _segment_after(parts, "job")
        if not site or not job_id:
            raise JDError(
                f"unrecognized oracle URL (want .../sites/<site>/job/<id>): {url}"
            )
        tenant = host.split(".")[0]
        api = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f'?expand=all&finder=ById;Id="{job_id}",siteNumber="{site}"'
        )
        return parse_oracle_jd(url, tenant, get_json(api))

    raise JDError(
        f"unsupported job board host {host!r} — supported: greenhouse, lever, "
        f"ashby, workday, smartrecruiters, icims, oracle"
    )
