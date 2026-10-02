from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class Posting:
    key: str  # stable unique key, e.g. "greenhouse:stripe:7954688"
    source: str  # "simplify" | "greenhouse" | "lever" | "ashby"
    company: str
    title: str
    url: str
    locations: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()  # simplify only, e.g. ("Summer 2027",)
    category: str = ""  # simplify only
    degrees: tuple[str, ...] = ()  # simplify only
    posted_at: str = ""  # ISO date if the source provides one
    employment_type: str = ""  # source's own label, e.g. "Intern"/"Internship"

    @property
    def url_key(self) -> str:
        return "url:" + normalize_url(self.url)


def normalize_url(url: str) -> str:
    """Canonicalize a posting URL so the same job seen via two sources dedupes.

    Lowercases scheme/host, drops fragments and tracking params, strips a
    trailing slash. Keeps other query params — Greenhouse identifies jobs via
    ?gh_jid=.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    query: list[tuple[str, str]] = []
    for pair in parse_qsl(parts.query, keep_blank_values=True):
        k = pair[0].lower()
        if k.startswith("utm_") or k in {"ref", "source", "src"}:
            continue
        if pair not in query:  # some boards duplicate params (?gh_jid=X&gh_jid=X)
            query.append(pair)
    path = parts.path.rstrip("/")
    # Aggregators link Lever jobs as .../apply and Ashby jobs as
    # .../application?embed=true; the boards' own APIs use the bare posting
    # URL. Same job — normalize to the bare form.
    if host == "jobs.lever.co" and path.endswith("/apply"):
        path = path[: -len("/apply")]
    if host == "jobs.ashbyhq.com":
        if path.endswith("/application"):
            path = path[: -len("/application")]
        query = [(k, v) for k, v in query if k.lower() != "embed"]
    # This is a dedup key, never fetched, so case-fold the path too —
    # aggregators and board APIs disagree on org-slug casing (/Perplexity/ vs
    # /perplexity/) while job ids are numeric or UUIDs.
    return urlunsplit((parts.scheme.lower(), host, path.lower(), urlencode(query), ""))


_WORKDAY_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$")
_ICIMS_JOB = re.compile(r"^/jobs/(\d+)(?:/[^/]+)?/job$")


def canon_key(url: str) -> str:
    """Dedup-only key: folds URL variants the storage key keeps apart.

    `normalize_url` stays the storage key (seen.json, inbox, tracker all use
    it), so changing it would orphan existing entries. This key only decides
    "is this the same job?": Workday links carry optional locale segments
    (/en-US/) and iCIMS links carry an optional slug plus tracking query.
    """
    norm = normalize_url(url)
    parts = urlsplit(norm)
    host, path, query = parts.netloc, parts.path, parts.query
    if host.endswith(".myworkdayjobs.com"):
        segs = [s for s in path.split("/") if s and not _WORKDAY_LOCALE.match(s)]
        path = "/" + "/".join(segs)
    elif host == "icims.com" or host.endswith(".icims.com"):
        m = _ICIMS_JOB.match(path)
        if m:
            path, query = f"/jobs/{m.group(1)}/job", ""
    return "url:" + urlunsplit((parts.scheme, host, path, query, ""))
