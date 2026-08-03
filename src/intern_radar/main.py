from __future__ import annotations

import argparse
import functools
import io
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from intern_radar.config import Config, load_config
from intern_radar.filters import apply_filters
from intern_radar.models import Posting
from intern_radar.notify import notify_console, notify_discord, notify_github_issue
from intern_radar.sources import fetch_ashby, fetch_greenhouse, fetch_lever, fetch_simplify
from intern_radar.state import SeenStore

FetchJob = tuple[str, Callable[[], list[Posting]]]


def build_fetch_jobs(config: Config) -> list[FetchJob]:
    jobs: list[FetchJob] = []
    if config.sources.simplify:
        jobs.append(("simplify", fetch_simplify))
    for board in config.sources.greenhouse_boards:
        jobs.append((f"greenhouse:{board}", functools.partial(fetch_greenhouse, board)))
    for company in config.sources.lever_companies:
        jobs.append((f"lever:{company}", functools.partial(fetch_lever, company)))
    for org in config.sources.ashby_orgs:
        jobs.append((f"ashby:{org}", functools.partial(fetch_ashby, org)))
    return jobs


def run(config_path: Path, state_path: Path, *, bootstrap: bool, dry_run: bool) -> int:
    config = load_config(config_path)
    store = SeenStore.load(state_path)
    today = datetime.now(tz=UTC).date().isoformat()

    postings: list[Posting] = []
    failures: list[str] = []
    jobs = build_fetch_jobs(config)
    for name, fetch in jobs:
        try:
            fetched = fetch()
            postings.extend(fetched)
            print(f"{name}: {len(fetched)} postings")
        except Exception as e:  # one bad source must not kill the run
            failures.append(f"{name}: {e}")
            print(f"error: {name}: {e}", file=sys.stderr)

    if failures and len(failures) == len(jobs):
        print("error: every source failed", file=sys.stderr)
        return 1

    matched = apply_filters(postings, config.filters)
    fresh = [p for p in matched if not store.is_seen(p)]
    # Within-run dedup: the same job often appears via Simplify and a direct
    # ATS board in the same batch.
    unique: dict[str, Posting] = {}
    for p in fresh:
        unique.setdefault(p.url_key, p)
    new_postings = list(unique.values())

    print(
        f"fetched {len(postings)} | matched filters {len(matched)} | new {len(new_postings)}"
        + (f" | source failures {len(failures)}" if failures else "")
    )

    if dry_run:
        notify_console(new_postings)
        return 0

    if bootstrap:
        for p in matched:
            store.mark(p, today)
        store.prune(today)
        store.save()
        print(f"bootstrap: marked {len(matched)} current postings as seen; no notifications sent")
        return 0

    if new_postings:
        notify_console(new_postings)
        # Remote notifiers run before state is saved: if delivery fails we exit
        # nonzero without marking anything seen, and the next run retries.
        if config.notify.github_issues:
            notify_github_issue(new_postings)
        notify_discord(new_postings)

    for p in matched:
        store.mark(p, today)
    store.prune(today)
    store.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Posting titles carry arbitrary Unicode; don't let a cp1252 Windows
    # console turn one odd character into a crashed run.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="intern-radar")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--state", type=Path, default=Path("data/seen.json"))
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="mark everything currently live as seen without notifying (first run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, filter, and print; no notifications, no state writes",
    )
    args = parser.parse_args(argv)
    # Honor a bootstrap request coming from a workflow_dispatch input.
    bootstrap = args.bootstrap or os.environ.get("RADAR_BOOTSTRAP", "") == "true"
    return run(args.config, args.state, bootstrap=bootstrap, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
