"""Live check: the new pipeline finds everything the old watcher found.

    PYTHONPATH=src python3 scripts/regression_replay.py

Runs the legacy source set (config boards + simplify, sequential, as before
this change) and the new poll set side by side, read-only, and reports any
matched posting the legacy run found that the new run did not. Postings from
sources that errored in either run are excluded. Exit 0 = superset holds.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from intern_radar import main as m
from intern_radar.boards import empty_registry
from intern_radar.config import load_config
from intern_radar.filters import apply_filters, passes_discovered_gate
from intern_radar.poll import run_jobs


def main() -> int:
    config = load_config(Path("config.toml"))
    now = datetime.now(tz=UTC)
    legacy: set[str] = set()
    legacy_failed: set[str] = set()
    for name, fetch in m.build_fetch_jobs(config):
        try:
            legacy |= {p.url_key for p in apply_filters(fetch(), config.filters)}
        except Exception as e:  # report and exclude
            legacy_failed.add(name)
            print(f"legacy error {name}: {e}", file=sys.stderr)
    reg_path = Path("data/boards.json")
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() \
        else empty_registry()
    pins = {k: m.boards_mod.new_row(a, b, pinned=True, discovered_via=None, now=now.isoformat())
            for k, (a, b) in m._config_pins(config).items() if k not in registry["boards"]}
    results = run_jobs(m.build_poll_jobs(config, registry, pins, now))
    new: set[str] = set()
    new_failed = {r.job.name for r in results if not r.ok}
    for r in results:
        keep = apply_filters(r.postings, config.filters)
        if r.job.board_key is not None and not r.job.pinned:
            keep = [p for p in keep if passes_discovered_gate(p, config.filters)]
        new |= {p.url_key for p in keep}
    missing = sorted(legacy - new)
    print(f"legacy matched {len(legacy)} | new matched {len(new)} | missing {len(missing)}"
          f" | failed legacy {sorted(legacy_failed)} new {sorted(new_failed)}")
    for key in missing[:20]:
        print(f"  missing: {key}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
