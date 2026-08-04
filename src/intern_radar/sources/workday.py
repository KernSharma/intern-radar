from __future__ import annotations

from typing import Any

from intern_radar.http import post_json
from intern_radar.models import Posting

PAGE_SIZE = 20
MAX_RESULTS = 200  # searchText narrows server-side; this is a runaway guard


def parse_workday(board: str, payload: Any) -> list[Posting]:
    """Parse one page of a Workday CXS jobs response.

    `board` is "tenant.instance/site", e.g. "arrowstreetcapital.wd5/Campus_Careers".
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
        raise ValueError(f"workday:{board}: expected a dict with a 'jobPostings' list")
    host_part, _, site = board.partition("/")
    tenant = host_part.split(".")[0]
    postings: list[Posting] = []
    for job in payload["jobPostings"]:
        if not isinstance(job, dict):
            continue
        title = str(job.get("title", "")).strip()
        external_path = str(job.get("externalPath", "")).strip()
        if not title or not external_path:
            continue
        location = str(job.get("locationsText", "")).strip()
        postings.append(
            Posting(
                key=f"workday:{board}:{external_path}",
                source="workday",
                company=tenant,
                title=title,
                url=f"https://{host_part}.myworkdayjobs.com/{site}{external_path}",
                locations=(location,) if location else (),
                # postedOn is relative text ("Posted 18 Days Ago") — no real date.
            )
        )
    return postings


def fetch_workday(board: str) -> list[Posting]:
    host_part, _, site = board.partition("/")
    tenant = host_part.split(".")[0]
    api = f"https://{host_part}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    postings: list[Posting] = []
    offset = 0
    while offset < MAX_RESULTS:
        payload = post_json(
            api, {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
                  "searchText": "intern"},
        )
        page = parse_workday(board, payload)
        postings.extend(page)
        total = payload.get("total", 0) if isinstance(payload, dict) else 0
        offset += PAGE_SIZE
        if offset >= int(total) or not page:
            break
    return postings
