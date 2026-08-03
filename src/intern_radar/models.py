from __future__ import annotations

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
