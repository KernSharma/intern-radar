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


def test_within_run_url_dedup(
    monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, state = paths
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
