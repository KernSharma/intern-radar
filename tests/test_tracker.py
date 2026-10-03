import json
from pathlib import Path

import pytest

from intern_radar.models import Posting
from intern_radar.tracker import Tracker, TrackerError, render_dashboard, write_postings_cache


def posting(company: str, title: str, url: str) -> Posting:
    return Posting(key=f"t:{url}", source="greenhouse", company=company, title=title, url=url)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    path = tmp_path / "postings.json"
    write_postings_cache(
        path, [posting("Acme", "SWE Intern", "https://jobs.lever.co/acme/123")]
    )
    return path


def test_add_resolves_metadata_from_cache(tmp_path: Path, cache: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    # The /apply variant of the same URL must still resolve via normalization.
    app = tracker.add(
        "https://jobs.lever.co/acme/123/apply", "applied", "2026-08-03", cache
    )
    assert app.company == "Acme"
    assert app.title == "SWE Intern"
    assert app.history == {"applied": "2026-08-03"}


def test_add_unknown_url_requires_explicit_metadata(tmp_path: Path, cache: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    with pytest.raises(TrackerError, match="--company"):
        tracker.add("https://elsewhere.example/job/9", "applied", "2026-08-03", cache)
    app = tracker.add(
        "https://elsewhere.example/job/9", "applied", "2026-08-03", cache,
        company="Elsewhere", title="Intern",
    )
    assert app.company == "Elsewhere"


def test_add_twice_rejected_set_transitions(tmp_path: Path, cache: Path) -> None:
    path = tmp_path / "applications.json"
    tracker = Tracker.load(path)
    tracker.add("https://jobs.lever.co/acme/123", "applied", "2026-08-01", cache)
    with pytest.raises(TrackerError, match="already tracked"):
        tracker.add("https://jobs.lever.co/acme/123/apply", "applied", "2026-08-02", cache)
    tracker.set_status("https://jobs.lever.co/acme/123", "oa", "2026-08-03", note="HackerRank")
    tracker.save()

    reloaded = Tracker.load(path)
    app = next(iter(reloaded.apps.values()))
    assert app.status == "oa"
    assert app.history == {"applied": "2026-08-01", "oa": "2026-08-03"}
    assert app.notes == ["HackerRank"]


def test_set_untracked_rejected(tmp_path: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    with pytest.raises(TrackerError, match="track add"):
        tracker.set_status("https://x.example/1", "oa", "2026-08-03")


def test_cache_merge_survives_partial_runs(tmp_path: Path) -> None:
    # A run where one source failed must not evict metadata cached earlier,
    # and a closed posting stays resolvable after the fact.
    path = tmp_path / "postings.json"
    write_postings_cache(path, [posting("Acme", "SWE Intern", "https://a.example/1")])
    write_postings_cache(path, [posting("Beta", "Data Intern", "https://b.example/2")])
    tracker = Tracker.load(tmp_path / "applications.json")
    assert tracker.add("https://a.example/1", "applied", "2026-08-03", path).company == "Acme"
    assert tracker.add("https://b.example/2", "applied", "2026-08-03", path).company == "Beta"


def test_dashboard_escapes_pipes_and_newlines(tmp_path: Path, cache: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    tracker.add(
        "https://x.example/9", "applied", "2026-08-03", cache,
        company="Pipe|Co", title="Intern | Summer 2027", note="line1\nline2",
    )
    md = render_dashboard(tracker)
    assert "| Pipe\\|Co | [Intern \\| Summer 2027](https://x.example/9)" in md
    assert "line1 line2" in md


def test_dashboard_groups_by_stage(tmp_path: Path, cache: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    tracker.add("https://jobs.lever.co/acme/123", "applied", "2026-08-01", cache)
    tracker.add(
        "https://x.example/2", "interview", "2026-08-02", cache,
        company="Beta", title="SWE Intern", note="onsite 8/10",
    )
    md = render_dashboard(tracker)
    assert "**2 tracked** — interview: 1 · applied: 1" in md
    # Interview section renders before applied.
    assert md.index("## interview (1)") < md.index("## applied (1)")
    assert "| Beta | [SWE Intern](https://x.example/2) | 2026-08-02 | onsite 8/10 |" in md


def test_interested_queue_stage(tmp_path: Path, cache: Path) -> None:
    tracker = Tracker.load(tmp_path / "applications.json")
    tracker.add(
        "https://x.example/3", "interested", "2026-08-04", cache,
        company="Gamma", title="SWE Intern", note="stretch",
    )
    tracker.add("https://jobs.lever.co/acme/123", "applied", "2026-08-04", cache)
    md = render_dashboard(tracker)
    assert "**2 tracked** — applied: 1 · interested: 1" in md
    # The queue renders below the live pipeline, above dead applications.
    assert md.index("## applied (1)") < md.index("## interested (1)")
    # Applying promotes the queued posting and keeps the queue date.
    app = tracker.set_status("https://x.example/3", "applied", "2026-08-05")
    assert app.status == "applied"
    assert app.history == {"interested": "2026-08-04", "applied": "2026-08-05"}


def test_cli_track_round_trip(
    tmp_path: Path, cache: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from intern_radar.main import main

    monkeypatch.chdir(tmp_path)
    state = str(tmp_path / "postings.json").replace("postings.json", "seen.json")
    base = ["--state", state, "track", "--dashboard", str(tmp_path / "APPLICATIONS.md")]
    assert main([*base, "add", "https://jobs.lever.co/acme/123"]) == 0
    assert main([*base, "set", "https://jobs.lever.co/acme/123", "oa"]) == 0
    assert main([*base, "list"]) == 0
    out = capsys.readouterr().out
    assert "tracked: Acme — SWE Intern [applied]" in out
    assert "[oa       ] Acme — SWE Intern" in out
    assert (tmp_path / "APPLICATIONS.md").exists()
    # Unknown URL exits 2 with guidance.
    assert main([*base, "add", "https://nowhere.example/1"]) == 2


def test_cli_tracker_dir_separates_tracker_from_watcher_data(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from intern_radar.main import main

    monkeypatch.chdir(tmp_path)
    tracker_dir = tmp_path / "private" / "tracker"
    # Options go before the action; postings.json is still read next to --state.
    base = ["--state", str(tmp_path / "seen.json"), "track",
            "--tracker-dir", str(tracker_dir),
            "--dashboard", str(tracker_dir / "APPLICATIONS.md")]
    assert main([*base, "add", "https://jobs.lever.co/acme/123", "--status", "interested"]) == 0
    assert (tracker_dir / "applications.json").exists()
    assert (tracker_dir / "APPLICATIONS.md").exists()
    assert not (tmp_path / "applications.json").exists()
    saved = json.loads((tracker_dir / "applications.json").read_text(encoding="utf-8"))
    (row,) = saved["applications"].values()
    assert (row["company"], row["status"]) == ("Acme", "interested")


def test_run_track_holds_exclusive_lock_from_load_through_save(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl

    import intern_radar.main as main_mod

    events: list[str] = []
    real_flock = fcntl.flock

    def spy_flock(fd: int, op: int) -> None:
        events.append("lock" if op == fcntl.LOCK_EX else "unlock")
        real_flock(fd, op)

    real_load, real_save = main_mod.Tracker.load, main_mod.Tracker.save
    monkeypatch.setattr(main_mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(main_mod.Tracker, "load",
                        classmethod(lambda cls, p: (events.append("load"), real_load(p))[1]))
    monkeypatch.setattr(main_mod.Tracker, "save",
                        lambda self: (events.append("save"), real_save(self))[1])
    base = ["--state", str(tmp_path / "seen.json"), "track",
            "--dashboard", str(tmp_path / "APPLICATIONS.md")]
    assert main_mod.main([*base, "add", "https://jobs.lever.co/acme/123"]) == 0
    assert events == ["lock", "load", "save", "unlock"]
    assert (tmp_path / ".lock").exists()
    # Errors release the lock too.
    events.clear()
    assert main_mod.main([*base, "add", "https://jobs.lever.co/acme/123"]) == 2
    assert events == ["lock", "load", "unlock"]


def test_save_is_atomic_and_leaves_no_temp_file(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import intern_radar.tracker as tracker_mod

    tracker = Tracker.load(tmp_path / "applications.json")
    tracker.add("https://jobs.lever.co/acme/123", "applied", "2026-08-04", cache)
    tracker.save()
    before = (tmp_path / "applications.json").read_text(encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    tracker.set_status("https://jobs.lever.co/acme/123", "oa", "2026-08-05")
    monkeypatch.setattr(tracker_mod.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        tracker.save()
    # The old file is intact and no temp file is left behind.
    assert (tmp_path / "applications.json").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".applications")) == []
