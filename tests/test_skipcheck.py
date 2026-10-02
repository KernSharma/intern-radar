import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from intern_radar import skipcheck

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_skipcheck_counts_only_mac_runs(tmp_path: Path) -> None:
    h = tmp_path / "health.json"
    assert not skipcheck.should_skip(h, NOW)

    def write(*runs: tuple[str, int]) -> None:
        h.write_text(json.dumps({"runs": [
            {"at": (NOW - timedelta(minutes=m)).isoformat(), "runner": r} for r, m in runs]}),
            encoding="utf-8")

    write(("mac", 20))
    assert skipcheck.should_skip(h, NOW)
    assert not skipcheck.should_skip(h, NOW + timedelta(minutes=40))
    write(("mac", 300), ("actions", 10))  # Mac off: Actions' own run must not cause a skip
    assert not skipcheck.should_skip(h, NOW)
