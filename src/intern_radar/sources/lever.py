from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from intern_radar.http import get_json
from intern_radar.models import Posting


def _ms_to_iso(raw: Any) -> str:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return ""
    return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC).date().isoformat()


def parse_lever(company: str, payload: Any) -> list[Posting]:
    if not isinstance(payload, list):
        raise ValueError(f"lever:{company}: expected a top-level list")
    postings: list[Posting] = []
    for job in payload:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id", "")).strip()
        title = str(job.get("text", "")).strip()
        url = str(job.get("hostedUrl", "")).strip()
        if not job_id or not title or not url:
            continue
        categories = job.get("categories")
        locations: tuple[str, ...] = ()
        if isinstance(categories, dict):
            all_locations = categories.get("allLocations")
            if isinstance(all_locations, list) and all_locations:
                locations = tuple(str(x).strip() for x in all_locations if str(x).strip())
            elif categories.get("location"):
                locations = (str(categories["location"]).strip(),)
        postings.append(
            Posting(
                key=f"lever:{company}:{job_id}",
                source="lever",
                company=company,
                title=title,
                url=url,
                locations=locations,
                posted_at=_ms_to_iso(job.get("createdAt")),
            )
        )
    return postings


def fetch_lever(company: str) -> list[Posting]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    return parse_lever(company, get_json(url))
