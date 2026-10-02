from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from intern_radar.http import post_json
from intern_radar.models import Posting

PAGE_SIZE = 20
# searchText=intern is FUZZY (matches "internal", "international", ...) and
# results are newest-first, so a small cap silently hides older intern
# postings on big boards (measured: 25/26 intern roles beyond offset 200 on
# paloaltonetworks). Paginate to the full match count; this cap is only a
# runaway guard.
MAX_RESULTS = 1000


def _split_board(board: str) -> tuple[str, str, str]:
    if board.count("/") != 1:
        raise ValueError(f"workday board must be 'tenant.instance/site', got {board!r}")
    host_part, _, site = board.partition("/")
    tenant = host_part.split(".")[0]
    if not host_part or not site or not tenant:
        raise ValueError(f"workday board must be 'tenant.instance/site', got {board!r}")
    return host_part, tenant, site


_POSTED_DAYS = re.compile(r"^Posted (\d+)\+? Days? Ago$", re.IGNORECASE)
DISCOVERED_MAX_RESULTS = 100  # 5 pages; see fetch_workday_info


def posted_date(posted_on: str, today: date) -> str:
    """ISO date from Workday's relative 'postedOn' text; '' if unrecognized."""
    text = posted_on.strip()
    if text.lower() == "posted today":
        return today.isoformat()
    if text.lower() == "posted yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = _POSTED_DAYS.match(text)
    return (today - timedelta(days=int(m.group(1)))).isoformat() if m else ""


def parse_workday(board: str, payload: Any, today: date | None = None) -> list[Posting]:
    """Parse one page of a Workday CXS jobs response.

    `board` is "tenant.instance/site", e.g. "arrowstreetcapital.wd5/Campus_Careers".
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
        raise ValueError(f"workday:{board}: expected a dict with a 'jobPostings' list")
    host_part, tenant, site = _split_board(board)
    postings: list[Posting] = []
    for job in payload["jobPostings"]:
        if not isinstance(job, dict):
            continue
        title = str(job.get("title") or "").strip()
        external_path = str(job.get("externalPath") or "").strip()
        if not title or not external_path:
            continue
        location = str(job.get("locationsText") or "").strip()
        postings.append(
            Posting(
                key=f"workday:{board}:{external_path}",
                source="workday",
                company=tenant,
                title=title,
                url=f"https://{host_part}.myworkdayjobs.com/{site}{external_path}",
                locations=(location,) if location else (),
                posted_at=posted_date(str(job.get("postedOn") or ""), today) if today else "",
            )
        )
    return postings


def fetch_workday(board: str) -> list[Posting]:
    return fetch_workday_info(board)[0]


def fetch_workday_info(
    board: str, max_results: int = MAX_RESULTS, today: date | None = None
) -> tuple[list[Posting], int]:
    """Postings plus the server's total match count.

    Pinned boards page to MAX_RESULTS (intern roles hide deep in the fuzzy
    search). Discovered boards pass DISCOVERED_MAX_RESULTS; a total above it
    marks the board `deep` so it gets a daily full crawl instead.
    """
    host_part, tenant, site = _split_board(board)
    api = f"https://{host_part}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    postings: list[Posting] = []
    offset = 0
    total = 0
    while offset < max_results:
        payload = post_json(
            api, {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
                  "searchText": "intern"},
        )
        postings.extend(parse_workday(board, payload, today))
        raw_count = len(payload["jobPostings"])
        total = int(payload.get("total") or 0)
        offset += PAGE_SIZE
        # Stop on the server's raw page, not the parsed count — a page of
        # unparseable entries must not end pagination early.
        if offset >= total or raw_count == 0:
            break
    return postings, total
