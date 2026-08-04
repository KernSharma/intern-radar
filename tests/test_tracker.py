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
