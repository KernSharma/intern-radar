"""End-to-end runs of main.run: discovery, seeding, canon, runners, replay."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import intern_radar.main as main_mod
from intern_radar.models import Posting

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CONFIG = """
[filters]
terms = ["Summer 2027"]
title_require_any = ["intern"]

[sources]
simplify = true

[notify]
github_issues = true
"""


def sp(n: int, url: str) -> Posting:
    return Posting(key=f"simplify:{n}", source="simplify", company=f"Co{n}", title="SWE Intern",
                   url=url, terms=("Summer 2027",), category="Software")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for var in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "DISCORD_WEBHOOK_URL", "NTFY_TOPIC",
                "RADAR_GITHUB_ISSUES", "RADAR_RUNNER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(main_mod, "resolve_gh_custom", lambda token: "optiverus")
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    state = tmp_path / "seen.json"
    state.write_text('{"version": 1, "seen": {}}', encoding="utf-8")
    return config, state


def read(tmp: Path, name: str) -> Any:
    return json.loads((tmp / name).read_text(encoding="utf-8"))


def test_first_boards_run_seeds_from_inbox_and_discovers(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    (state.parent / "inbox.json").write_text(json.dumps([
        {"url": "https://nvidia.wd5.myworkdayjobs.com/en-US/Ext/job/US/Intern_1", "company": "n",
         "title": "t", "locations": "", "source": "simplify", "added": "2026-09-01"},
        {"url": "https://www.optiver.com/job?gh_jid=8027900", "company": "o", "title": "t",
         "locations": "", "source": "simplify", "added": "2026-09-01"},
    ]), encoding="utf-8")
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
        sp(1, "https://job-boards.greenhouse.io/stripe/jobs/1")])
    monkeypatch.setattr(main_mod, "fetch_workday_info", lambda b, d, t: ([], 0))
    monkeypatch.setattr(main_mod, "fetch_greenhouse", lambda b: [])
    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): None)
    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
    reg = read(state.parent, "boards.json")
    expected = {"workday:nvidia.wd5/ext", "greenhouse:stripe", "greenhouse:optiverus"}
    assert expected <= set(reg["boards"])
    assert reg["gh_custom"] == {"www.optiver.com": "optiverus"}
    canon = read(state.parent, "canon.json")["canon"]
    assert "url:https://nvidia.wd5.myworkdayjobs.com/ext/job/us/intern_1" in canon


def test_canon_variant_refreshes_stored_key_and_is_not_new(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    stored = "url:https://t.wd1.myworkdayjobs.com/en-us/site/job/x_1"
    state.write_text(json.dumps({"version": 1, "seen": {stored: "2026-09-01"}}), encoding="utf-8")
    (state.parent / "canon.json").write_text(json.dumps({"version": 1, "canon": {
        "url:https://t.wd1.myworkdayjobs.com/site/job/x_1": stored}}), encoding="utf-8")
    (state.parent / "boards.json").write_text(json.dumps(
        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
        sp(9, "https://t.wd1.myworkdayjobs.com/Site/job/x_1")])
    monkeypatch.setattr(main_mod, "fetch_workday_info", lambda b, d, t: ([], 0))
    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
    assert read(state.parent, "seen.json")["seen"][stored] == "2026-10-01"
    assert not (state.parent / "inbox.json").exists() or read(state.parent, "inbox.json") == []


def test_discovered_workday_board_goes_deep(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    (state.parent / "boards.json").write_text(json.dumps(
        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
    depths: list[int] = []

    def wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
        depths.append(depth)
        return [], 747

    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
        sp(1, "https://acme.wd5.myworkdayjobs.com/Ext/job/US/Intern_1")])
    monkeypatch.setattr(main_mod, "fetch_workday_info", wd)
    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): None)
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)  # discovers
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
    row = read(state.parent, "boards.json")["boards"]["workday:acme.wd5/ext"]
    assert depths[0] == 100 and row["deep"]
    issued: list[Any] = []
    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): issued.append(p))

    def deep_wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
        depths.append(depth)
        post = Posting(key="workday:acme:/job/999", source="workday", company="acme",
                       title="SWE Intern", url="https://acme.wd5.myworkdayjobs.com/Ext/job/999")
        return [post], 747

    monkeypatch.setattr(main_mod, "fetch_workday_info", deep_wd)
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=60))
    assert depths[-1] == 1000  # daily full crawl for a deep board
    assert issued == []        # its first full crawl is quiet


def test_mac_runner_uses_ntfy_not_issues(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    monkeypatch.setenv("RADAR_GITHUB_ISSUES", "0")
    monkeypatch.setenv("NTFY_TOPIC", "t")
    sent: list[str] = []
    monkeypatch.setattr(main_mod.health_mod, "send_ntfy",
                        lambda text, topic=None: sent.append(text))
    monkeypatch.setattr(main_mod, "notify_github_issue",
                        lambda p, f=(): (_ for _ in ()).throw(AssertionError("no issues on Mac")))
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
    assert "intern-radar: 1 new postings" in sent


class ReplayGit:
    """First push is rejected after a competing runner commits new data."""

    def __init__(self, data_dir: Path, competitor: Any) -> None:
        self.data_dir, self.competitor, self.pushes, self.snapshot = data_dir, competitor, 0, {}

    def reset_to_origin(self) -> None:
        for name, text in self.snapshot.items():
            (self.data_dir / name).write_text(text, encoding="utf-8")

    def commit_push(self, paths: list[str], message: str) -> bool:
        self.pushes += 1
        if self.pushes == 1:
            self.competitor()  # other runner pushed first: origin moved
            self.snapshot = {p.name: p.read_text(encoding="utf-8")
                             for p in self.data_dir.iterdir() if p.suffix in (".json", ".md")}
            return False
        return True


def test_replay_keeps_both_runners_postings_and_notifies_once(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    notified: list[list[str]] = []
    monkeypatch.setattr(main_mod, "notify_github_issue",
                        lambda p, f=(): notified.append([x.url for x in p]))

    def competitor() -> None:
        other = main_mod.ChangeSet(now=NOW, runner="actions",
                                   new_postings=[sp(2, "https://x.example/2")],
                                   seen_marks={"simplify:2": "2026-10-01",
                                               "url:https://x.example/2": "2026-10-01"})
        # origin = state before this run + the competitor's commit
        for name in ("inbox.json", "seen.json"):
            (state.parent / name).unlink(missing_ok=True)
        state.write_text('{"version": 1, "seen": {}}', encoding="utf-8")
        main_mod.apply_changes(state.parent, other)

    git = ReplayGit(state.parent, competitor)
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
        sp(1, "https://x.example/1"), sp(2, "https://x.example/2")])
    assert main_mod.run(config, state, bootstrap=False, dry_run=False, git=git, now=NOW) == 0
    urls = [e["url"] for e in read(state.parent, "inbox.json")]
    assert urls == ["https://x.example/2", "https://x.example/1"]
    assert notified == [["https://x.example/1"]]  # 2 was the competitor's to announce


def test_push_failing_three_times_exits_1_without_notifying(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world

    class DeadGit:
        def reset_to_origin(self) -> None:
            pass

        def commit_push(self, paths: list[str], message: str) -> bool:
            return False

    called: list[Any] = []
    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): called.append(p))
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
    assert main_mod.run(config, state, bootstrap=False, dry_run=False, git=DeadGit(),
                        now=NOW) == 1
    assert called == []


def test_discovered_board_tech_gate_and_quiet_first_poll(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    gate = ('title_require_any = ["intern"]\n'
            'discovered_title_require_any = ["software", "data"]\n'
            'discovered_title_exclude = ["winter 2027"]')
    config.write_text(CONFIG.replace('title_require_any = ["intern"]', gate), encoding="utf-8")
    (state.parent / "boards.json").write_text(json.dumps(
        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
    board_posts: list[list[Posting]] = [[]]

    def wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
        return board_posts[0], len(board_posts[0])

    def wdp(n: int, title: str) -> Posting:
        return Posting(key=f"workday:acme.wd5/Ext:/job/{n}", source="workday", company="acme",
                       title=title, url=f"https://acme.wd5.myworkdayjobs.com/Ext/job/{n}")

    issued: list[list[str]] = []
    monkeypatch.setattr(main_mod, "notify_github_issue",
                        lambda p, f=(): issued.append([x.title for x in p]))
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
        sp(1, "https://acme.wd5.myworkdayjobs.com/Ext/job/0")])
    monkeypatch.setattr(main_mod, "fetch_workday_info", wd)
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)  # discovers acme
    issued.clear()
    board_posts[0] = [wdp(1, "Software Intern"), wdp(2, "Labor Relations Intern"),
                      wdp(3, "Data Intern - Winter 2027")]
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
    inbox_titles = [e["title"] for e in read(state.parent, "inbox.json")]
    assert "Software Intern" in inbox_titles                  # gate passes
    assert "Labor Relations Intern" not in inbox_titles       # no tech keyword
    assert "Data Intern - Winter 2027" not in inbox_titles    # off-cycle term
    assert issued == []                                       # first poll: quiet
    board_posts[0] = [*board_posts[0], wdp(4, "Data Science Intern")]
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=60))
    assert issued == [["Data Science Intern"]]                # later arrivals notify


def test_list_source_first_run_is_quiet(
        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    config, state = world
    issued: list[list[str]] = []
    monkeypatch.setattr(main_mod, "notify_github_issue",
                        lambda p, f=(): issued.append([x.url for x in p]))
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)
    issued.clear()
    config.write_text(CONFIG.replace("simplify = true", "simplify = true\nspeedyapply = true"),
                      encoding="utf-8")
    speedy = Posting(key="speedyapply:u", source="speedyapply", company="Z", title="SWE Intern",
                     url="https://x.example/9")
    monkeypatch.setattr(main_mod, "fetch_speedyapply", lambda: [speedy])
    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
    assert issued == []  # speedyapply's first run: backlog queued quietly
    assert "https://x.example/9" in [e["url"] for e in read(state.parent, "inbox.json")]
