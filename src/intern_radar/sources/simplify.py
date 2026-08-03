from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from intern_radar.http import get_json
from intern_radar.models import Posting

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships"
    "/dev/.github/scripts/listings.json"
)


def _epoch_to_iso(raw: Any) -> str:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return ""
    return datetime.fromtimestamp(float(raw), tz=UTC).date().isoformat()


def parse_simplify(payload: Any) -> list[Posting]:
    if not isinstance(payload, list):
        raise ValueError("simplify: expected a top-level list")
    postings: list[Posting] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not (item.get("active") and item.get("is_visible")):
            continue
        listing_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not listing_id or not title or not url:
            continue
        postings.append(
            Posting(
                key=f"simplify:{listing_id}",
                source="simplify",
                company=str(item.get("company_name", "")).strip(),
                title=title,
                url=url,
                locations=tuple(
                    str(x).strip() for x in item.get("locations") or [] if str(x).strip()
                ),
                terms=tuple(str(x) for x in item.get("terms") or []),
                category=str(item.get("category", "")),
                degrees=tuple(str(x) for x in item.get("degrees") or []),
                posted_at=_epoch_to_iso(item.get("date_posted")),
            )
        )
    return postings


def fetch_simplify(url: str = LISTINGS_URL) -> list[Posting]:
    return parse_simplify(get_json(url))
