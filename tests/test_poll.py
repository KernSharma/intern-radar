import threading
import time
from typing import Any

from intern_radar.models import Posting
from intern_radar.poll import PollJob, run_jobs


def p(n: int) -> Posting:
    return Posting(key=f"k{n}", source="x", company="c", title="t", url=f"https://x/{n}")


def test_results_errors_and_tuple_outputs() -> None:
    def boom() -> list[Posting]:
        raise ValueError("nope")

    jobs = [PollJob("a", "fam", "h1", lambda: [p(1)]),
            PollJob("b", "fam", "h2", boom),
            PollJob("c", "workday", "h3", lambda: ([p(2), p(3)], 250))]
    a, b, c = run_jobs(jobs)
    assert a.ok and [x.key for x in a.postings] == ["k1"]
    assert not b.ok and "ValueError: nope" in b.error
    assert c.ok and c.total == 250 and len(c.postings) == 2


def test_budget_skips_unpinned_jobs_not_started() -> None:
    ticks = iter([0.0] + [700.0] * 20)
    jobs = [PollJob("pinned", "f", "h", lambda: [p(1)], pinned=True),
            PollJob("discovered", "f", "h", lambda: [p(2)], pinned=False)]
    results = run_jobs(jobs, budget=600.0, clock=lambda: next(ticks), workers=1)
    assert results[0].ok and not results[0].skipped
    assert results[1].skipped and not results[1].ok


def test_per_host_limit() -> None:
    active: dict[str, int] = {"n": 0, "max": 0}
    lock = threading.Lock()

    def slow() -> list[Any]:
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return []

    run_jobs([PollJob(str(i), "f", "same-host", slow) for i in range(8)], workers=8)
    assert active["max"] == 2


def test_budget_rechecked_after_waiting_for_a_busy_host() -> None:
    ticks = iter([0.0, 0.0, 700.0, 700.0, 700.0])  # start, pre-lock ok, then past budget
    jobs = [PollJob("late", "f", "h", lambda: [p(1)], pinned=False)]
    (result,) = run_jobs(jobs, budget=600.0, clock=lambda: next(ticks), workers=1)
    assert result.skipped and not result.ok
