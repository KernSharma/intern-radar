"""Run fetch jobs concurrently, politely, within a time budget.

8 workers overall, at most 2 in flight per host. Once the run passes the
budget, jobs that have not started yet are skipped (pinned ones still run),
so the whole run stays well under the 15-minute Actions timeout.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from intern_radar.models import Posting

MAX_WORKERS = 8
PER_HOST = 2
BUDGET_SECONDS = 600.0


@dataclass
class PollJob:
    name: str            # e.g. "simplify", "workday:nvidia.wd5/NVIDIAExternalCareerSite"
    family: str          # health family: list name or ATS name
    host: str            # politeness key for the per-host limit
    fetch: Callable[[], Any]  # returns list[Posting] or (list[Posting], total)
    board_key: str | None = None
    pinned: bool = True
    deep_crawl: bool = False


@dataclass
class SourceResult:
    job: PollJob
    ok: bool = False
    skipped: bool = False
    error: str = ""
    postings: list[Posting] = field(default_factory=list)
    total: int | None = None
    seconds: float = 0.0


def run_jobs(
    jobs: list[PollJob],
    *,
    budget: float = BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    workers: int = MAX_WORKERS,
) -> list[SourceResult]:
    start = clock()
    locks: dict[str, threading.Semaphore] = {}
    guard = threading.Lock()

    def host_lock(host: str) -> threading.Semaphore:
        with guard:
            return locks.setdefault(host, threading.Semaphore(PER_HOST))

    def one(job: PollJob) -> SourceResult:
        result = SourceResult(job=job)
        if not job.pinned and clock() - start >= budget:
            result.skipped = True
            return result
        with host_lock(job.host):
            if not job.pinned and clock() - start >= budget:
                result.skipped = True  # waited past the budget for a busy host
                return result
            began = clock()
            try:
                out = job.fetch()
                if isinstance(out, tuple):
                    result.postings, result.total = list(out[0]), int(out[1])
                else:
                    result.postings = list(out)
                result.ok = True
            except Exception as e:  # one bad source must not kill the run
                result.error = f"{type(e).__name__}: {e}"[:300]
            result.seconds = clock() - began
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, jobs))
