"""Replay semantics: two runners' change sets merge without loss or doubles."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from intern_radar import boards
from intern_radar.changeset import BoardUpdate, ChangeSet, apply
from intern_radar.models import Posting, canon_key

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def post(n: int, url: str | None = None) -> Posting:
    return Posting(key=f"simplify:{n}", source="simplify", company=f"Co{n}", title="SWE Intern",
                   url=url or f"https://x.example/{n}", terms=("Summer 2027",))


def cs_for(posts: list[Posting], now: datetime = NOW, runner: str = "mac") -> ChangeSet:
    day = now.date().isoformat()
    marks = {}
    for p in posts:
        marks[p.key] = day
        marks[p.url_key] = day
    return ChangeSet(now=now, runner=runner, new_postings=posts, seen_marks=marks, cache=posts,
                     canon_add={canon_key(p.url): p.url_key for p in posts},
                     sightings_add={canon_key(p.url): {p.source: now.isoformat()} for p in posts},
                     run_entry={"at": now.isoformat(), "runner": runner, "seconds": 1.0,
                                "families": {}, "boards_polled": 0, "boards_skipped_budget": 0})


def read(tmp: Path, name: str) -> object:
    return json.loads((tmp / name).read_text(encoding="utf-8"))


def test_competing_runs_merge_without_loss_or_duplicates(tmp_path: Path) -> None:
    mac = cs_for([post(1), post(2)], runner="mac")
    actions = cs_for([post(2), post(3)], now=NOW + timedelta(minutes=1), runner="actions")
    assert [p.key for p in apply(tmp_path, mac).appended] == ["simplify:1", "simplify:2"]
    # Actions' push was rejected; it resets to origin (= Mac's state) and replays.
    replay = apply(tmp_path, actions)
    assert [p.key for p in replay.appended] == ["simplify:3"]  # 2 already recorded by Mac
    inbox = read(tmp_path, "inbox.json")
    assert [e["url"] for e in inbox] == [f"https://x.example/{n}" for n in (1, 2, 3)]
    seen = read(tmp_path, "seen.json")["seen"]
    assert all(f"simplify:{n}" in seen for n in (1, 2, 3))
    cache = read(tmp_path, "postings.json")
    assert set(cache) == {f"url:https://x.example/{n}" for n in (1, 2, 3)}
    health = read(tmp_path, "health.json")
    assert [r["runner"] for r in health["runs"]] == ["mac", "actions"]
    assert health["runner"] == "actions"


def test_replaying_the_same_changeset_is_idempotent(tmp_path: Path) -> None:
    cs = cs_for([post(1)])
    apply(tmp_path, cs)
    assert apply(tmp_path, cs).appended == []
    assert len(read(tmp_path, "inbox.json")) == 1


def test_canon_variant_recorded_by_other_runner_is_not_appended(tmp_path: Path) -> None:
    a = post(1, "https://t.wd1.myworkdayjobs.com/en-US/Site/job/x_1")
    b = Posting(key="workday:t.wd1/Site:/job/x_1", source="workday", company="t",
                title="SWE Intern", url="https://t.wd1.myworkdayjobs.com/Site/job/x_1")
    apply(tmp_path, cs_for([a]))
    assert apply(tmp_path, cs_for([b])).appended == []


def test_seen_takes_max_and_prune_runs_on_merged_state(tmp_path: Path) -> None:
    (tmp_path / "seen.json").write_text(json.dumps({"version": 1, "seen": {
        "simplify:old": "2025-01-01", "url:https://x.example/old": "2025-01-01",
        "simplify:1": "2026-09-01"}}), encoding="utf-8")
    (tmp_path / "inbox.json").write_text(json.dumps([
        {"url": "https://x.example/old", "company": "Old", "title": "t", "locations": "",
         "source": "simplify", "added": "2025-01-01"}]), encoding="utf-8")
    cs = cs_for([])
    cs.seen_marks = {"simplify:1": "2026-08-01"}
    apply(tmp_path, cs)
    seen = read(tmp_path, "seen.json")["seen"]
    assert seen["simplify:1"] == "2026-09-01"          # max, not overwrite
    assert "simplify:old" not in seen                   # 365-day prune
    assert read(tmp_path, "inbox.json") == []           # inbox follows the prune


def test_board_merge_rules(tmp_path: Path) -> None:
    key = boards.board_key("workday", "t.wd1/S")
    cs = cs_for([])
    cs.board_rows_new = {key: boards.new_row("workday", "t.wd1/S", pinned=False,
                                             discovered_via="simplify", now=NOW.isoformat())}
    cs.board_updates = {key: BoardUpdate(polled=True, ok=False, deep=True)}
    for _ in range(10):
        apply(tmp_path, cs)
    row = read(tmp_path, "boards.json")["boards"][key]
    assert row["consecutive_errors"] == 10 and row["disabled"] and row["deep"]
    cs.board_updates = {key: BoardUpdate(reenable=True, matched=True)}
    apply(tmp_path, cs)
    row = read(tmp_path, "boards.json")["boards"][key]
    assert not row["disabled"] and row["consecutive_errors"] == 0 and row["last_match"]


def test_pins_follow_config(tmp_path: Path) -> None:
    key = boards.board_key("greenhouse", "stripe")
    cs = cs_for([])
    cs.board_rows_new = {key: boards.new_row("greenhouse", "stripe", pinned=True,
                                             discovered_via=None, now=NOW.isoformat())}
    cs.pinned_keys = {key}
    apply(tmp_path, cs)
    assert read(tmp_path, "boards.json")["boards"][key]["pinned"] is True
    apply(tmp_path, cs_for([]))  # removed from config: unpinned, not deleted
    assert read(tmp_path, "boards.json")["boards"][key]["pinned"] is False


def test_closed_alert_is_not_resurrected_by_replay(tmp_path: Path) -> None:
    health = {"version": 1, "last_run_at": None, "runner": None, "runs": [],
              "alerts": {"R3|vanshb03": {"opened_at": "2026-09-01T00:00:00+00:00"}},
              "last_health_ntfy_at": None, "weekly_written_for": None}
    (tmp_path / "health.json").write_text(json.dumps(health), encoding="utf-8")
    result = apply(tmp_path, cs_for([]))
    assert "R3|vanshb03" not in result.active_alerts
    assert read(tmp_path, "health.json")["alerts"] == {}


def test_health_ntfy_decided_on_merged_state(tmp_path: Path) -> None:
    cs = cs_for([])
    cs.run_entry["seconds"] = 900  # R5 run-time alert
    first = apply(tmp_path, cs, ntfy_enabled=True)
    assert first.ntfy_due and "R5|run-time" in first.active_alerts
    later = cs_for([], now=NOW + timedelta(hours=1))
    later.run_entry["seconds"] = 900
    assert not apply(tmp_path, later, ntfy_enabled=True).ntfy_due  # within 6 h


def test_weekly_written_once_per_iso_week(tmp_path: Path) -> None:
    apply(tmp_path, cs_for([]))
    md = tmp_path / "health-weekly.md"
    assert md.exists()
    md.write_text("sentinel", encoding="utf-8")
    apply(tmp_path, cs_for([], now=NOW + timedelta(hours=2)))
    assert md.read_text(encoding="utf-8") == "sentinel"
