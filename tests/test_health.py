import urllib.error
from datetime import UTC, datetime, timedelta
from typing import Any

from intern_radar import health

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def run_at(hours_ago: float, **fams: dict[str, Any]) -> dict[str, Any]:
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(), "runner": "mac", "seconds": 60,
            "families": fams, "boards_polled": 10, "boards_skipped_budget": 0}


def state(*runs: dict[str, Any]) -> dict[str, Any]:
    h = health.empty_health()
    h["runs"] = list(runs)
    return h


def test_r1_family_went_silent() -> None:
    h = state(run_at(72, workday={"matches": 5, "ok": 3}),
              run_at(10, workday={"matches": 0, "ok": 3}),
              run_at(1, workday={"matches": 0, "ok": 3}))
    assert "R1|workday" in health.evaluate(h, {}, NOW)


def test_r2_family_failing() -> None:
    h = state(run_at(4, ashby={"ok": 0, "errors": 2}), run_at(1, ashby={"ok": 0, "errors": 2}))
    assert "R2|ashby" in health.evaluate(h, {}, NOW)
    h["runs"][-1]["families"]["ashby"]["ok"] = 1
    assert "R2|ashby" not in health.evaluate(h, {}, NOW)


def test_r3_stale_list_and_r5() -> None:
    h = state(run_at(0.5, vanshb03={"newest": "2026-08-23", "ok": 1}))
    h["runs"][-1]["seconds"] = 800
    h["runs"][-1]["boards_skipped_budget"] = 4
    active = health.evaluate(h, {}, NOW)
    assert {"R3|vanshb03", "R5|run-time", "R5|budget"} <= set(active)


def test_r5_deep_crawl_starved_and_r6() -> None:
    boards = {
        "workday:a": {"deep": True, "pinned": False,
                      "deep_since": (NOW - timedelta(days=3)).isoformat(),
                      "last_deep_crawl": (NOW - timedelta(hours=40)).isoformat()},
        "workday:b": {"disabled": True, "disabled_at": (NOW - timedelta(hours=5)).isoformat()},
        "workday:c": {"disabled": True, "disabled_at": (NOW - timedelta(days=3)).isoformat()},
    }
    active = health.evaluate(state(), boards, NOW)
    assert "R5|deep-crawl workday:a" in active
    assert "R6|workday:b" in active       # newly disabled boards alert...
    assert "R6|workday:c" not in active   # ...for 48 h only (weekly summary keeps them)
    assert "R6|workday:a" not in active   # deep boards go to the weekly summary only


def test_head_starts() -> None:
    s = {"keys": {f"k{i}": {"simplify": (NOW - timedelta(hours=1)).isoformat(),
                            "workday": (NOW - timedelta(hours=5)).isoformat()} for i in range(6)}}
    n, hours = health.head_starts(s)["workday"]
    assert n == 6 and hours == 4.0


class FakeApi:
    def __init__(self, issues: list[dict[str, Any]], label: bool = True) -> None:
        self.issues, self.label, self.calls = issues, label, []

    def __call__(self, method: str, path: str, token: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        if path.endswith("/labels/health") and method == "GET":
            if not self.label:
                raise urllib.error.HTTPError(path, 404, "nf", None, None)  # type: ignore[arg-type]
            return {}
        if method == "GET" and "/issues?" in path:
            return self.issues
        return {}


def test_sync_opens_closes_and_creates_label() -> None:
    old = {"number": 7, "title": "health: R2 ashby", "updated_at": "2026-10-01T11:00:00Z"}
    api = FakeApi([old], label=False)
    health.sync_issues({"R3|vanshb03": "stale"}, NOW, token="t", repo="o/r", api=api)
    methods = [(m, p) for m, p, _ in api.calls]
    assert ("POST", "/repos/o/r/labels") in methods
    assert ("PATCH", "/repos/o/r/issues/7") in methods
    created = [b for m, p, b in api.calls if m == "POST" and p == "/repos/o/r/issues"]
    assert created[0]["title"] == "health: R3 vanshb03"


def test_sync_never_repeats_comments_and_caps() -> None:
    stale = {"number": 3, "title": "health: R3 vanshb03", "updated_at": "2026-09-29T00:00:00Z"}
    api = FakeApi([stale])
    active = {"R3|vanshb03": "stale", **{f"R6|workday:b{i}": "x" for i in range(12)}}
    health.sync_issues(active, NOW, token="t", repo="o/r", api=api)
    comments = [p for m, p, _ in api.calls if p.endswith("/issues/3/comments")]
    assert comments == []  # an issue that is already open gets no "still active" noise
    created = [b["title"] for m, p, b in api.calls if m == "POST" and p == "/repos/o/r/issues"]
    assert len([t for t in created if t != "health: R0 alert overflow"]) == 9
    assert "health: R0 alert overflow" in created


def test_ntfy_no_topic_no_post(monkeypatch: Any) -> None:
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    health.send_ntfy("hi")  # must not raise or POST


def test_ntfy_only_when_an_alert_newly_opens() -> None:
    h = health.empty_health()
    health.refresh_alerts(h, {"R3|vanshb03": "stale"}, NOW)
    assert health.ntfy_due(h, {"R3|vanshb03": "stale"}, NOW, True)
    later = NOW + timedelta(hours=7)
    health.refresh_alerts(h, {"R3|vanshb03": "stale"}, later)
    assert not health.ntfy_due(h, {"R3|vanshb03": "stale"}, later, True)  # standing: no repeat
    health.refresh_alerts(h, {"R3|vanshb03": "stale", "R2|ashby": "down"}, later)
    assert health.ntfy_due(h, {"R3|vanshb03": "stale", "R2|ashby": "down"}, later, True)
