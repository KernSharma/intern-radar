from __future__ import annotations

from typing import Any

from intern_radar.http import get_json
from intern_radar.models import Posting

PAGE_SIZE = 100
MAX_RESULTS = 500  # q=intern matches fuzzily; guard against huge boards


def parse_smartrecruiters(company: str, payload: Any) -> list[Posting]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ValueError(f"smartrecruiters:{company}: expected a dict with a 'content' list")
    postings: list[Posting] = []
    for job in payload["content"]:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id", "")).strip()
        title = str(job.get("name", "")).strip()
        if not job_id or not title:
            continue
        company_info = job.get("company")
        identifier = company
        display_name = company
        if isinstance(company_info, dict):
            identifier = str(company_info.get("identifier", "")).strip() or company
            display_name = str(company_info.get("name", "")).strip() or identifier
        location = job.get("location")
        full_location = ""
        if isinstance(location, dict):
            full_location = str(location.get("fullLocation", "")).strip()
        employment = job.get("typeOfEmployment")
        employment_label = ""
        if isinstance(employment, dict):
            employment_label = str(employment.get("label", ""))
        released = str(job.get("releasedDate", ""))
        postings.append(
            Posting(
                key=f"smartrecruiters:{company}:{job_id}",
                source="smartrecruiters",
                company=display_name,
                title=title,
                url=f"https://jobs.smartrecruiters.com/{identifier}/{job_id}",
                locations=(full_location,) if full_location else (),
                posted_at=released[:10],
                employment_type=employment_label,
            )
        )
    return postings


def fetch_smartrecruiters(company: str) -> list[Posting]:
    postings: list[Posting] = []
    offset = 0
    while offset < MAX_RESULTS:
        payload = get_json(
            "https://api.smartrecruiters.com/v1/companies/"
            f"{company}/postings?q=intern&limit={PAGE_SIZE}&offset={offset}"
        )
        page = parse_smartrecruiters(company, payload)
        postings.extend(page)
        total = payload.get("totalFound", 0) if isinstance(payload, dict) else 0
        offset += PAGE_SIZE
        if offset >= int(total) or not page:
            break
    return postings
