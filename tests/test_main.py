import json
from pathlib import Path

import pytest

import intern_radar.main as main_mod
from intern_radar.http import FetchError
from intern_radar.models import Posting

CONFIG = """
[filters]
terms = ["Summer 2027"]
title_require_any = ["intern"]

[sources]
simplify = true

[notify]
github_issues = false
"""


def simplify_posting(n: int) -> Posting:
    return Posting(
        key=f"simplify:{n}", source="simplify", company=f"Co{n}", title="SWE Intern",
        url=f"https://x.example/{n}", terms=("Summer 2027",), category="Software",
    )


def seed_empty_state(state: Path) -> None:
    state.write_text('{"version": 1, "seen": {}}', encoding="utf-8")


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    return config, tmp_path / "seen.json"


def test_bootstrap_then_incremental(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, state = paths
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    assert main_mod.run(config, state, bootstrap=True, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "bootstrap: marked 1" in out
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "simplify:1" in saved["seen"]

    # Next run: one previously-seen posting plus one new one -> notify only the new.
    monkeypatch.setattr(
        main_mod, "fetch_simplify", lambda: [simplify_posting(1), simplify_posting(2)]
    )
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "new 1" in out
    assert "**Co2**" in out
    assert "**Co1**" not in out

    # Third run, nothing new.
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
    assert "new 0" in capsys.readouterr().out


def test_all_sources_failing_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
) -> None:
    config, state = paths

    def boom() -> list[Posting]:
        raise FetchError("down")

    monkeypatch.setattr(main_mod, "fetch_simplify", boom)
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
    assert not state.exists()  # nothing marked seen on a failed run


def test_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, state = paths
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    assert main_mod.run(config, state, bootstrap=False, dry_run=True) == 0
    assert "**Co1**" in capsys.readouterr().out
    assert not state.exists()
    assert not (state.parent / "postings.json").exists()


def test_all_failed_run_skips_postings_cache(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
) -> None:
    config, state = paths

    def boom() -> list[Posting]:
        raise FetchError("down")

    monkeypatch.setattr(main_mod, "fetch_simplify", boom)
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
    assert not (state.parent / "postings.json").exists()


def test_successful_run_writes_postings_cache(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
) -> None:
    config, state = paths
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    assert main_mod.run(config, state, bootstrap=True, dry_run=False) == 0
    cache = json.loads((state.parent / "postings.json").read_text(encoding="utf-8"))
    assert any(entry["company"] == "Co1" for entry in cache.values())


def test_notify_failure_leaves_state_unsaved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # github_issues enabled but no creds: the run must fail without marking
    # anything seen, so the next run retries delivery.
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.replace("github_issues = false", "github_issues = true"),
                      encoding="utf-8")
    state = tmp_path / "seen.json"
    seed_empty_state(state)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
    assert json.loads(state.read_text(encoding="utf-8"))["seen"] == {}


def test_bootstrap_refuses_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        CONFIG + '\n[sources.greenhouse]\nboards = ["broken"]\n', encoding="utf-8"
    )
    state = tmp_path / "seen.json"

    def boom(board: str) -> list[Posting]:
        raise FetchError("down")

    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    monkeypatch.setattr(main_mod, "fetch_greenhouse", boom)
    assert main_mod.run(config, state, bootstrap=True, dry_run=False) == 1
    assert not state.exists()  # half a bootstrap would flood the next run


def test_within_run_url_dedup(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, state = paths
    seed_empty_state(state)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    duplicate = Posting(
        key="simplify:dup", source="simplify", company="Co1", title="SWE Intern",
        url="https://x.example/1", terms=("Summer 2027",), category="Software",
    )
    monkeypatch.setattr(
        main_mod, "fetch_simplify", lambda: [simplify_posting(1), duplicate]
    )
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
    assert "new 1" in capsys.readouterr().out


def test_first_run_auto_bootstraps(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Missing state file = first run: mark everything seen, notify nothing —
    # otherwise the first cron tick after pushing floods one giant issue.
    config, state = paths
    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "bootstrap: marked 1" in out
    assert "**Co1**" not in out
    assert "simplify:1" in json.loads(state.read_text(encoding="utf-8"))["seen"]


def test_discord_failure_is_best_effort_when_issue_succeeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from intern_radar.notify import NotifyError

    config = tmp_path / "config.toml"
    config.write_text(CONFIG.replace("github_issues = false", "github_issues = true"),
                      encoding="utf-8")
    state = tmp_path / "seen.json"
    seed_empty_state(state)

    def dead_webhook(postings: list[Posting]) -> None:
        raise NotifyError("webhook deleted")

    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): None)
    monkeypatch.setattr(main_mod, "notify_discord", dead_webhook)
    # Issue was delivered -> run succeeds, state saves, no duplicate-issue loop.
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
    assert "simplify:1" in json.loads(state.read_text(encoding="utf-8"))["seen"]


def test_discord_failure_is_fatal_when_it_is_the_only_channel(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
) -> None:
    from intern_radar.notify import NotifyError

    config, state = paths  # github_issues = false in CONFIG
    seed_empty_state(state)

    def dead_webhook(postings: list[Posting]) -> None:
        raise NotifyError("webhook deleted")

    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
    monkeypatch.setattr(main_mod, "notify_discord", dead_webhook)
    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
    assert json.loads(state.read_text(encoding="utf-8"))["seen"] == {}
