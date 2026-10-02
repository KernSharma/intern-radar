"""Phase gates as PASS/FAIL lines (wider-watcher spec, "Phased rollout").

    PYTHONPATH=src python3 scripts/gate_check.py --phase 1 [--expect "R3|vanshb03"]

Reads data/health.json, data/sightings.json and data/boards.json. With
GITHUB_TOKEN + GITHUB_REPOSITORY set, also checks open health issues. Exits
nonzero if any criterion fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from intern_radar.health import head_starts
from intern_radar.http import USER_AGENT


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def phase1(data: Path, now: datetime, expect: set[str]) -> list[tuple[bool, str]]:
    health = _load(data / "health.json")
    runs = [r for r in health.get("runs", [])
            if datetime.fromisoformat(r["at"]) >= now - timedelta(days=7)]
    out: list[tuple[bool, str]] = []
    span = (datetime.fromisoformat(runs[0]["at"]) if runs else now)
    out.append((bool(runs) and now - span >= timedelta(days=6, hours=12),
                f"7 days of runs recorded ({len(runs)} runs since {span:%Y-%m-%d %H:%M})"))
    slow = [r for r in runs if r.get("seconds", 0) >= 720]
    slowest = max((r["seconds"] for r in runs), default=0)
    out.append((not slow, f"every run under 12 min (slowest {slowest:.0f} s)"))
    clean = sum(1 for r in runs if r.get("boards_skipped_budget", 0) == 0)
    out.append((bool(runs) and clean / len(runs) >= 0.9,
                f"skipped_budget = 0 on >= 90% of runs ({clean}/{len(runs)})"))
    by_day: dict[str, list[datetime]] = defaultdict(list)
    for r in runs:
        if r.get("runner") == "mac":
            by_day[r["at"][:10]].append(datetime.fromisoformat(r["at"]))
    coverage_ok = bool(by_day)
    details = []
    for day, stamps in sorted(by_day.items()):
        first, last = min(stamps), max(stamps)
        expected = max(1, int((last - first).total_seconds() // 1800) + 1)
        ratio = min(1.0, len(stamps) / expected)
        coverage_ok &= ratio >= 0.9
        details.append(f"{day[5:]} {len(stamps)}/{expected}")
    out.append((coverage_ok,
                "Mac covers >= 90% of :07/:37 slots each day (" + ", ".join(details) + ")"))
    pairs = {f: v for f, v in head_starts(_load(data / "sightings.json")).items() if v[0] >= 10}
    paired = ", ".join(f"{f}: n={n}, {h:+.1f} h" for f, (n, h) in sorted(pairs.items()))
    out.append((bool(pairs),
                f"a direct family has n >= 10 sightings paired with simplify ({paired})"))
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues?state=open&labels=health&per_page=100",
            headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            issues = json.loads(resp.read())
        bad = [i["title"] for i in issues
               if "false-positive" not in {lbl["name"] for lbl in i.get("labels", [])}
               and i["title"].removeprefix("health: ").replace(" ", "|", 1) not in expect]
        out.append((not bad, f"no unexplained open health issues ({len(bad)}: {bad[:5]})"))
    else:
        alerts = [k for k in health.get("alerts", {}) if k not in expect]
        out.append((not alerts, f"no unexpected active alerts in health.json ({alerts[:5]})"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, default=1, choices=[1])
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--expect", action="append", default=[],
                    help="alert key that is expected, e.g. 'R3|vanshb03'")
    args = ap.parse_args(argv)
    results = phase1(args.data, datetime.now(tz=UTC), set(args.expect))
    for ok, text in results:
        print(f"{'PASS' if ok else 'FAIL'}  {text}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
