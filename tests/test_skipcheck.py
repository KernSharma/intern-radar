import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from intern_radar import skipcheck

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_skipcheck(tmp_path: Path) -> None:
    h = tmp_path / "health.json"
    assert not skipcheck.should_skip(h, NOW)
    h.write_text(json.dumps({"last_run_at": (NOW - timedelta(minutes=20)).isoformat()}),
                 encoding="utf-8")
    assert skipcheck.should_skip(h, NOW)
    assert not skipcheck.should_skip(h, NOW + timedelta(minutes=40))
