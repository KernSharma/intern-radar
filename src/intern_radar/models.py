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
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "src"}]
    path = parts.path.rstrip("/")
    # Aggregators link Lever jobs as .../apply; Lever's own API uses the bare
    # posting URL. Same job — normalize to the bare form.
    if parts.netloc.lower() == "jobs.lever.co" and path.endswith("/apply"):
        path = path[: -len("/apply")]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )
