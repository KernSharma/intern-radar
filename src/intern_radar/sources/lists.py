"""Community internship lists besides Simplify: vanshb03 and speedyapply.

Both are independent of Simplify, so the watcher keeps working if any one
list goes stale. Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L1".
"""

from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from intern_radar.http import get_json, get_text
from intern_radar.models import Posting

VANSH_URL = (
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships"
    "/dev/.github/scripts/listings.json"
)
SPEEDY_URL = (
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md"
)
# vanshb03 "Summer" rows carry no year; rows posted before June 2026 are
# plausibly the Summer 2026 cycle (72 such rows were measured).
VANSH_SUMMER_CUTOFF = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
_YEAR = re.compile(r"\b(20\d\d)\b")
_HEADERS = (
    "| Company | Position | Location | Salary | Posting | Age |",
    "| Company | Position | Location | Posting | Age |",
)
_STRONG = re.compile(r"<strong>(.*?)</strong>", re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r'<a\s+href="([^"]+)"', re.IGNORECASE)
_AGE = re.compile(r"^(\d+)d$")
_TAGS = re.compile(r"<[^>]+>")


def _epoch_to_iso(raw: Any) -> str:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return ""
    return datetime.fromtimestamp(float(raw), tz=UTC).date().isoformat()


def _vansh_terms(item: dict[str, Any], title: str) -> tuple[str, ...]:
    if item.get("season") != "Summer":
        return ()
    posted = item.get("date_posted")
    if not isinstance(posted, (int, float)) or posted < VANSH_SUMMER_CUTOFF:
        return ()
    if any(year != "2027" for year in _YEAR.findall(title)):
        return ()
    return ("Summer 2027",)


def parse_vansh(payload: Any) -> list[Posting]:
    if not isinstance(payload, list):
        raise ValueError("vanshb03: expected a top-level list")
    postings: list[Posting] = []
    for item in payload:
        if not isinstance(item, dict) or not (item.get("active") and item.get("is_visible")):
            continue
        listing_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not listing_id or not title or not url:
            continue
        postings.append(Posting(
            key=f"vanshb03:{listing_id}",
            source="vanshb03",
            company=str(item.get("company_name", "")).strip(),
            title=title,
            url=url,
            locations=tuple(str(x).strip() for x in item.get("locations") or [] if str(x).strip()),
            terms=_vansh_terms(item, title),
            posted_at=_epoch_to_iso(item.get("date_posted")),
        ))
    return postings


def fetch_vansh(url: str = VANSH_URL) -> list[Posting]:
    return parse_vansh(get_json(url))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_speedyapply(markdown: str, today: date) -> list[Posting]:
    """Rows of every table whose header is one of the two known layouts."""
    postings: list[Posting] = []
    layout: tuple[str, ...] | None = None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped in _HEADERS:
            layout = tuple(_cells(stripped))
            continue
        if layout is None:
            continue
        if not stripped.startswith("|"):
            layout = None
            continue
        if set(stripped) <= set("|-: "):
            continue  # the |---|---| separator row
        cells = _cells(stripped)
        if len(cells) != len(layout):
            continue
        row = dict(zip(layout, cells, strict=True))
        company_m = _STRONG.search(row["Company"])
        href_m = _HREF.search(row["Posting"])
        title = html.unescape(_TAGS.sub("", row["Position"])).strip()
        if not company_m or not href_m or not title:
            continue
        age_m = _AGE.match(row["Age"])
        posted = (today - timedelta(days=int(age_m.group(1)))).isoformat() if age_m else ""
        location = html.unescape(_TAGS.sub("", row["Location"])).strip()
        url = html.unescape(href_m.group(1))
        postings.append(Posting(
            key=f"speedyapply:{url}",
            source="speedyapply",
            company=html.unescape(_TAGS.sub("", company_m.group(1))).strip(),
            title=title,
            url=url,
            locations=(location,) if location else (),
            posted_at=posted,
        ))
    return postings


def fetch_speedyapply(url: str = SPEEDY_URL) -> list[Posting]:
    return parse_speedyapply(get_text(url), datetime.now(tz=UTC).date())
