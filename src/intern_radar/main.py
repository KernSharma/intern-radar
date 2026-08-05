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
from intern_radar.http import FetchError
from intern_radar.jd import JDError, fetch_jd
from intern_radar.models import Posting
from intern_radar.notify import (
    NotifyError,
    notify_console,
    notify_discord,
    notify_github_issue,
)
from intern_radar.sources import (
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_simplify,
    fetch_smartrecruiters,
    fetch_workday,
)
from intern_radar.state import SeenStore, append_inbox
from intern_radar.tracker import (
    STATUSES,
    Tracker,
    TrackerError,
    render_dashboard,
    write_postings_cache,
)

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
    for board in config.sources.workday_boards:
        jobs.append((f"workday:{board}", functools.partial(fetch_workday, board)))
    for company in config.sources.smartrecruiters_companies:
        jobs.append(
            (f"smartrecruiters:{company}", functools.partial(fetch_smartrecruiters, company))
        )
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

    # Snapshot matched postings so `track add <url>` can resolve metadata.
    write_postings_cache(state_path.parent / "postings.json", matched)

    # A missing state file means this is the first run: bootstrap implicitly,
    # otherwise the first cron tick after pushing floods one giant issue.
    if bootstrap or not store.path.exists():
        if failures:
            # A half-bootstrap would dump the failed source's whole board as
            # "new" on the next run — refuse instead.
            print("error: bootstrap needs every source healthy; fix failures and retry",
                  file=sys.stderr)
            return 1
        for p in matched:
            store.mark(p, today)
        store.prune(today)
        store.save()
        print(f"bootstrap: marked {len(matched)} current postings as seen; no notifications sent")
        return 0

    if new_postings:
        notify_console(new_postings)
        # The durable notifier runs before state is saved: if delivery fails we
        # exit nonzero without marking anything seen, and the next run retries.
        try:
            if config.notify.github_issues:
                notify_github_issue(new_postings, tuple(failures))
        except NotifyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        try:
            notify_discord(new_postings)
        except NotifyError as e:
            if config.notify.github_issues:
                # Best-effort when an issue was already created: a dead webhook
                # must not re-notify the same postings every run forever.
                print(f"warn: {e}", file=sys.stderr)
            else:
                # Discord is the only remote channel — failing it means the
                # notification was never delivered anywhere durable.
                print(f"error: {e}", file=sys.stderr)
                return 1

    if new_postings:
        # Notifications delivered — queue for the auto-tailor agent. Runs
        # before state save so a crash here re-delivers rather than drops.
        queued = append_inbox(state_path.parent / "inbox.json", new_postings, today)
        print(f"inbox: queued {queued} for auto-tailor")

    for p in matched:
        store.mark(p, today)
    store.prune(today)
    store.save()
    return 0


def run_track(args: argparse.Namespace) -> int:
    data_dir: Path = args.state.parent
    tracker = Tracker.load(data_dir / "applications.json")
    today = datetime.now(tz=UTC).date().isoformat()
    try:
        if args.action == "add":
            app = tracker.add(
                args.url, args.status, today, data_dir / "postings.json",
                company=args.company or "", title=args.title or "", note=args.note or "",
            )
            print(f"tracked: {app.company} — {app.title} [{app.status}]")
        elif args.action == "set":
            app = tracker.set_status(args.url, args.status, today, note=args.note or "")
            print(f"updated: {app.company} — {app.title} [{app.status}]")
        else:  # list
            for key in sorted(tracker.apps):
                app = tracker.apps[key]
                if args.status and app.status != args.status:
                    continue
                print(f"[{app.status:9}] {app.company} — {app.title}  {app.url}")
            return 0
    except TrackerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    tracker.save()
    args.dashboard.write_text(render_dashboard(tracker), encoding="utf-8", newline="\n")
    return 0


def run_jd(args: argparse.Namespace) -> int:
    try:
        jd = fetch_jd(args.url, board=args.board)
    except (JDError, FetchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rendered = jd.render()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {args.out} ({jd.company} — {jd.title})")
    else:
        print(rendered)
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
    sub = parser.add_subparsers(dest="command")
    track = sub.add_parser("track", help="track application pipeline state")
    track.add_argument("--dashboard", type=Path, default=Path("APPLICATIONS.md"))
    track_sub = track.add_subparsers(dest="action", required=True)
    add_p = track_sub.add_parser("add", help="start tracking a posting you applied to")
    add_p.add_argument("url")
    add_p.add_argument("--status", choices=STATUSES, default="applied")
    add_p.add_argument("--company")
    add_p.add_argument("--title")
    add_p.add_argument("--note")
    set_p = track_sub.add_parser("set", help="move an application to a new stage")
    set_p.add_argument("url")
    set_p.add_argument("status", choices=STATUSES)
    set_p.add_argument("--note")
    list_p = track_sub.add_parser("list", help="list tracked applications")
    list_p.add_argument("--status", choices=STATUSES)
    jd_p = sub.add_parser("jd", help="fetch the full job description for a posting URL")
    jd_p.add_argument("url")
    jd_p.add_argument("--out", type=Path, help="write to a file instead of stdout")
    jd_p.add_argument(
        "--board", default="",
        help="greenhouse board slug, needed only for custom-domain ?gh_jid= URLs",
    )

    args = parser.parse_args(argv)
    if args.command == "track":
        return run_track(args)
    if args.command == "jd":
        return run_jd(args)
    # Honor a bootstrap request coming from a workflow_dispatch input.
    bootstrap = args.bootstrap or os.environ.get("RADAR_BOOTSTRAP", "") == "true"
    return run(args.config, args.state, bootstrap=bootstrap, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
