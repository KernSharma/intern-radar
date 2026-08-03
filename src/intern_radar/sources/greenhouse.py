from __future__ import annotations

from typing import Any

from intern_radar.http import get_json
from intern_radar.models import Posting


def parse_greenhouse(board: str, payload: Any) -> list[Posting]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"greenhouse:{board}: expected a dict with a 'jobs' list")
    postings: list[Posting] = []
    for job in payload["jobs"]:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id")
        title = str(job.get("title", "")).strip()
        url = str(job.get("absolute_url", "")).strip()
        if job_id is None or not title or not url:
            continue
        location = job.get("location")
        location_name = ""
        if isinstance(location, dict):
            location_name = str(location.get("name", "")).strip()
        first_published = str(job.get("first_published", ""))
        postings.append(
            Posting(
                key=f"greenhouse:{board}:{job_id}",
                source="greenhouse",
                company=str(job.get("company_name", "")).strip() or board,
                title=title,
                url=url,
                locations=(location_name,) if location_name else (),
                posted_at=first_published[:10],
            )
        )
    return postings


def fetch_greenhouse(board: str) -> list[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    return parse_greenhouse(board, get_json(url))
