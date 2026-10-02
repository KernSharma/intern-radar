"""Actions backup gate: skip this run when the Mac runner is active.

Prints `skip=true|false` and appends it to $GITHUB_OUTPUT. The Mac is
"active" when the last committed run started within SKIP_WINDOW.

    python -m intern_radar.skipcheck data/health.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SKIP_WINDOW = timedelta(minutes=50)


def should_skip(health_path: Path, now: datetime) -> bool:
    if not health_path.exists():
        return False
    last = json.loads(health_path.read_text(encoding="utf-8")).get("last_run_at")
    return bool(last) and now - datetime.fromisoformat(last) < SKIP_WINDOW


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    skip = should_skip(Path(args[0] if args else "data/health.json"), datetime.now(tz=UTC))
    line = f"skip={'true' if skip else 'false'}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
