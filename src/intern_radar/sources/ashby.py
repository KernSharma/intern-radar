from __future__ import annotations

from typing import Any

from intern_radar.http import get_json
from intern_radar.models import Posting


def parse_ashby(org: str, payload: Any) -> list[Posting]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"ashby:{org}: expected a dict with a 'jobs' list")
    postings: list[Posting] = []
    for job in payload["jobs"]:
        if not isinstance(job, dict):
            continue
        if job.get("isListed") is False:
            continue
        job_id = str(job.get("id", "")).strip()
        # Ashby titles ship with stray whitespace (seen in live payloads).
        title = str(job.get("title", "")).strip()
        url = str(job.get("jobUrl", "")).strip()
        if not job_id or not title or not url:
            continue
        locations = [str(job.get("location", "")).strip()]
        secondary = job.get("secondaryLocations")
        if isinstance(secondary, list):
            for entry in secondary:
                if isinstance(entry, dict) and entry.get("location"):
                    locations.append(str(entry["location"]).strip())
        published = str(job.get("publishedAt", ""))
        postings.append(
            Posting(
                key=f"ashby:{org}:{job_id}",
                source="ashby",
                company=org,
                title=title,
                url=url,
                locations=tuple(x for x in locations if x),
                posted_at=published[:10],
            )
        )
    return postings


def fetch_ashby(org: str) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    return parse_ashby(org, get_json(url))
