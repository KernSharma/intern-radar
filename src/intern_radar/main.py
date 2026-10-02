from __future__ import annotations

import argparse
import functools
import http.client
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from intern_radar import boards as boards_mod
from intern_radar import health as health_mod
from intern_radar.changeset import ApplyResult, BoardUpdate, ChangeSet
from intern_radar.changeset import apply as apply_changes
from intern_radar.config import Config, load_config
from intern_radar.filters import apply_filters, passes_discovered_gate
from intern_radar.gitsync import GitSync
from intern_radar.http import USER_AGENT, FetchError
from intern_radar.jd import JDError, fetch_jd
from intern_radar.models import Posting, canon_key, normalize_url
from intern_radar.notify import (
    NotifyError,
    notify_console,
    notify_discord,
    notify_github_issue,
)
from intern_radar.poll import PollJob, SourceResult, run_jobs
from intern_radar.sources import (
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_simplify,
    fetch_smartrecruiters,
    fetch_workday,
)
from intern_radar.sources import workday as workday_mod
from intern_radar.sources.lists import fetch_speedyapply, fetch_vansh
from intern_radar.sources.workday import fetch_workday_info
from intern_radar.state import SeenStore
from intern_radar.tracker import (
    STATUSES,
    Tracker,
    TrackerError,
    render_dashboard,
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
            (
                f"smartrecruiters:{company}",
                functools.partial(
                    fetch_smartrecruiters, company, config.sources.smartrecruiters_country
                ),
            )
        )
    return jobs


RUNNER_ENV = "RADAR_RUNNER"          # "mac" | "actions"; anything else = "local"
GIT_ENV = "RADAR_GIT"                # "1": the run owns fetch/reset/commit/push
ISSUES_ENV = "RADAR_GITHUB_ISSUES"   # "0": never open new-posting issues (Mac)
PUSH_ATTEMPTS = 3
# Sources the pre-health watcher already ran: on the first run under this code
# (no health.json yet) any other source's backlog is queued quietly.
LEGACY_FAMILIES = ("simplify", "greenhouse", "lever", "ashby", "workday", "smartrecruiters")
GH_CUSTOM_PER_RUN = 10


def _config_pins(config: Config) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    for ats, names in (
        ("greenhouse", config.sources.greenhouse_boards),
        ("lever", config.sources.lever_companies),
        ("ashby", config.sources.ashby_orgs),
        ("workday", config.sources.workday_boards),
        ("smartrecruiters", config.sources.smartrecruiters_companies),
    ):
        for name in names:
            pins[boards_mod.board_key(ats, name)] = (ats, name)
    return pins


def _load_registry(data_dir: Path, config: Config, now: datetime) -> tuple[
        dict[str, Any], dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    """Current boards.json plus rows to add this run (seed + config pins).

    With no boards.json yet, every URL already in inbox.json seeds discovery
    (441 boards on 2026-09-30) and seeds canon.json for existing entries.
    Returns (registry, new_rows, gh_pending_add, canon_seed).
    """
    path = data_dir / "boards.json"
    registry: dict[str, Any] = (json.loads(path.read_text(encoding="utf-8"))
                                if path.exists() else boards_mod.empty_registry())
    now_iso = now.isoformat()
    new_rows: dict[str, dict[str, Any]] = {}
    pending: dict[str, str] = {}
    canon_seed: dict[str, str] = {}
    for key, (ats, name) in _config_pins(config).items():
        if key not in registry["boards"]:
            new_rows[key] = boards_mod.new_row(ats, name, pinned=True, discovered_via=None,
                                               now=now_iso)
    if not path.exists():
        inbox_path = data_dir / "inbox.json"
        entries = (json.loads(inbox_path.read_text(encoding="utf-8-sig"))
                   if inbox_path.exists() else [])
        for e in entries if isinstance(entries, list) else []:
            url = str(e.get("url", "")) if isinstance(e, dict) else ""
            if not url:
                continue
            canon_seed.setdefault(canon_key(url), "url:" + normalize_url(url))
            _discover(url, str(e.get("source", "")), registry, new_rows, pending, now_iso)
    return registry, new_rows, pending, canon_seed


def _discover(url: str, via: str, registry: dict[str, Any],
              new_rows: dict[str, dict[str, Any]], pending: dict[str, str],
              now_iso: str) -> str | None:
    """Record the board behind `url`; returns its key when it is a known ATS."""
    found = boards_mod.board_of(url)
    if found is None:
        return None
    ats, board = found
    if ats == "gh_custom":
        resolved = registry["gh_custom"].get(board)
        if resolved is None:
            token = parse_qs(urlsplit(url).query).get("gh_jid", [""])[0]
            if board not in registry.get("gh_custom_pending", {}):
                pending.setdefault(board, token)
            return None
        ats, board = "greenhouse", resolved
    key = boards_mod.board_key(ats, board)
    if key not in registry["boards"] and key not in new_rows:
        new_rows[key] = boards_mod.new_row(ats, board, pinned=False, discovered_via=via,
                                           now=now_iso)
    return key


def resolve_gh_custom(token: str) -> str | None:
    """Board slug for a custom-domain Greenhouse job, from the embed redirect.

    Returns the slug, "" when Greenhouse answered without one (lookup done,
    nothing to add), or None on a transport failure (retry on a later run).
    """
    request = urllib.request.Request(
        f"https://boards.greenhouse.io/embed/job_app?token={quote(token, safe='')}",
        headers={"User-Agent": USER_AGENT}, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(request, timeout=15)
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location", "") if e.code in (301, 302) else ""
        board = parse_qs(urlsplit(location).query).get("for", [""])[0].lower()
        return board if boards_mod.valid_slug(board) else ""
    except OSError:
        return None
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def build_poll_jobs(config: Config, registry: dict[str, Any],
                    new_rows: dict[str, dict[str, Any]], now: datetime) -> list[PollJob]:
    jobs: list[PollJob] = []
    if config.sources.simplify:
        jobs.append(PollJob("simplify", "simplify", "raw.githubusercontent.com", fetch_simplify))
    if config.sources.vanshb03:
        jobs.append(PollJob("vanshb03", "vanshb03", "raw.githubusercontent.com", fetch_vansh))
    if config.sources.speedyapply:
        jobs.append(PollJob("speedyapply", "speedyapply", "raw.githubusercontent.com",
                            fetch_speedyapply))
    today = now.date()
    rows = {**registry["boards"], **new_rows}
    deep_budget = boards_mod.MAX_DEEP_CRAWLS_PER_RUN
    for key in sorted(rows):
        row = rows[key]
        if not boards_mod.due(row, now):
            continue
        ats, board, pinned = row["ats"], row["board"], bool(row.get("pinned"))
        deep_crawl = False
        fetch: Callable[[], Any]
        if ats == "workday":
            depth = workday_mod.MAX_RESULTS
            if not pinned:
                depth = workday_mod.DISCOVERED_MAX_RESULTS
                if deep_budget > 0 and boards_mod.deep_crawl_due(row, now):
                    depth, deep_crawl = workday_mod.MAX_RESULTS, True
                    deep_budget -= 1
            fetch = functools.partial(fetch_workday_info, board, depth, today)
            host = f"{board.split('/')[0]}.myworkdayjobs.com"
        elif ats == "greenhouse":
            fetch, host = functools.partial(fetch_greenhouse, board), "boards-api.greenhouse.io"
        elif ats == "lever":
            fetch, host = functools.partial(fetch_lever, board), "api.lever.co"
        elif ats == "ashby":
            fetch, host = functools.partial(fetch_ashby, board), "api.ashbyhq.com"
        elif ats == "smartrecruiters":
            fetch = functools.partial(fetch_smartrecruiters, board,
                                      config.sources.smartrecruiters_country)
            host = "api.smartrecruiters.com"
        else:
            continue
        jobs.append(PollJob(f"{ats}:{board}", ats, host, fetch, board_key=key,
                            pinned=pinned, deep_crawl=deep_crawl))
    return jobs


def _family_stats(results: list[SourceResult], matched_ids: set[int]) -> dict[str, Any]:
    fams: dict[str, dict[str, Any]] = {}
    for r in results:
        if r.skipped:
            continue
        e = fams.setdefault(r.job.family, {"jobs": 0, "ok": 0, "errors": 0, "postings": 0,
                                           "matches": 0, "seconds": 0.0, "newest": None})
        e["jobs"] += 1
        e["ok" if r.ok else "errors"] += 1
        e["postings"] += len(r.postings)
        e["matches"] += sum(1 for p in r.postings if id(p) in matched_ids)
        e["seconds"] = round(e["seconds"] + r.seconds, 2)
        dates = [p.posted_at for p in r.postings if p.posted_at]
        if dates:
            e["newest"] = max([d for d in (e["newest"], *dates) if d])
    return fams


def run(
    config_path: Path,
    state_path: Path,
    *,
    bootstrap: bool,
    dry_run: bool,
    git: GitSync | None = None,
    now: datetime | None = None,
) -> int:
    started = time.monotonic()
    now = now or datetime.now(tz=UTC).replace(microsecond=0)
    now_iso = now.isoformat()
    today = now.date().isoformat()
    runner = os.environ.get(RUNNER_ENV, "local")
    data_dir = state_path.parent
    if git is not None and not dry_run:
        git.reset_to_origin()

    config = load_config(config_path)
    store = SeenStore.load(state_path)
    first_run = not store.path.exists()
    registry, new_rows, gh_pending, canon_seed = _load_registry(data_dir, config, now)
    canon_path = data_dir / "canon.json"
    canon: dict[str, str] = (json.loads(canon_path.read_text(encoding="utf-8"))["canon"]
                             if canon_path.exists() else {})
    canon = {**canon_seed, **canon}

    jobs = build_poll_jobs(config, registry, new_rows, now)
    results = run_jobs(jobs)
    ran = [r for r in results if not r.skipped]
    failures = [f"{r.job.name}: {r.error}" for r in ran if not r.ok]
    for r in ran:
        if not r.ok:
            print(f"error: {r.job.name}: {r.error}", file=sys.stderr)
        elif r.job.board_key is None:
            print(f"{r.job.name}: {len(r.postings)} postings")
    if ran and not any(r.ok for r in ran):
        print("error: every source failed", file=sys.stderr)
        return 1

    postings = [p for r in ran for p in r.postings]
    discovered_ids = {id(p) for r in ran if r.job.board_key is not None and not r.job.pinned
                      for p in r.postings}
    matched = [p for p in apply_filters(postings, config.filters)
               if id(p) not in discovered_ids or passes_discovered_gate(p, config.filters)]
    matched_ids = {id(p) for p in matched}
    # A board's first poll surfaces its whole current backlog: queue it, but
    # don't notify (owner, 2026-09-30). Only later arrivals notify.
    # A deep board's first full-depth crawl surfaces results past the top 100
    # for the first time: also quiet. So is a list source's first run (vanshb03/speedyapply when
    # first enabled): its whole backlog would otherwise notify at once.
    all_rows = {**registry["boards"], **new_rows}
    health_path = data_dir / "health.json"
    # Only families that have succeeded count as known: a first run that failed
    # must not un-quiet the backlog on the next, successful run.
    known_families = ({f for run in json.loads(health_path.read_text(encoding="utf-8"))["runs"]
                       for f, e in run.get("families", {}).items() if e.get("ok", 0) > 0}
                      if health_path.exists() else set(LEGACY_FAMILIES))
    quiet_ids = {id(p) for r in ran
                 if (r.job.board_key is not None
                     and (all_rows.get(r.job.board_key, {}).get("last_ok") is None
                          or (r.job.deep_crawl
                              and all_rows[r.job.board_key].get("last_deep_crawl") is None)))
                 or (r.job.board_key is None and r.job.family not in known_families)
                 for p in r.postings}

    marks: dict[str, str] = {}
    canon_add: dict[str, str] = {}
    sightings_add: dict[str, dict[str, str]] = {}
    fresh: dict[str, Posting] = {}
    for p in matched:
        marks[p.key] = today
        marks[p.url_key] = today
        ck = canon_key(p.url)
        sightings_add.setdefault(ck, {}).setdefault(p.source, now_iso)
        stored = canon.get(ck)
        if stored is None:
            canon_add.setdefault(ck, p.url_key)
            canon[ck] = p.url_key  # later variants of this job in THIS run dedupe against it
        if store.is_seen(p):
            continue
        if stored is not None and stored != p.url_key:
            marks[stored] = today  # same job under another URL variant: keep it live
            continue
        fresh.setdefault(p.url_key, p)
    new_postings = list(fresh.values())
    quiet_urls = {p.url for p in new_postings if id(p) in quiet_ids}
    canon_add = {**canon_seed, **canon_add}

    updates: dict[str, BoardUpdate] = {}
    matched_boards: set[str] = set()
    for p in matched:
        key = _discover(p.url, p.source, registry, new_rows, gh_pending, now_iso)
        if key is not None:
            matched_boards.add(key)
            if registry["boards"].get(key, {}).get("disabled"):
                updates.setdefault(key, BoardUpdate()).reenable = True
    gh_custom_add: dict[str, str] = {}
    gh_done: set[str] = set()
    for host, token in list({**registry.get("gh_custom_pending", {}),
                             **gh_pending}.items())[:GH_CUSTOM_PER_RUN]:
        board = resolve_gh_custom(token) if token else ""
        if board is None:
            continue  # transport failure: keep the host pending for a later run
        gh_done.add(host)
        if board:
            gh_custom_add[host] = board
            key = boards_mod.board_key("greenhouse", board)
            if key not in registry["boards"]:
                new_rows.setdefault(key, boards_mod.new_row(
                    "greenhouse", board, pinned=False, discovered_via="gh_custom", now=now_iso))
    for r in results:
        key = r.job.board_key
        if key is None:
            continue
        up = updates.setdefault(key, BoardUpdate())
        up.polled = not r.skipped
        up.ok = r.ok
        up.matched = any(id(p) in matched_ids for p in r.postings) or key in matched_boards
        if (r.job.family == "workday" and not r.job.pinned and r.total is not None
                and r.total > workday_mod.DISCOVERED_MAX_RESULTS):
            up.deep = True
        up.deep_crawled = r.job.deep_crawl and r.ok
    for key in matched_boards:
        updates.setdefault(key, BoardUpdate()).matched = True

    skipped = sum(1 for r in results if r.skipped)
    polled = sum(1 for r in ran if r.job.board_key is not None)
    print(f"fetched {len(postings)} | matched filters {len(matched)} | new {len(new_postings)}"
          + (f" | source failures {len(failures)}" if failures else "")
          + f" | boards polled {polled}" + (f" | skipped (budget) {skipped}" if skipped else ""))

    if dry_run:
        notify_console(new_postings)
        return 0

    is_bootstrap = bootstrap or first_run
    if is_bootstrap and failures:
        print("error: bootstrap needs every source healthy; fix failures and retry",
              file=sys.stderr)
        return 1

    run_entry = {"at": now_iso, "runner": runner, "seconds": round(time.monotonic() - started, 1),
                 "families": _family_stats(results, matched_ids),
                 "boards_polled": polled, "boards_skipped_budget": skipped}
    cs = ChangeSet(
        now=now, runner=runner, new_postings=[] if is_bootstrap else new_postings,
        seen_marks=marks, cache=matched, canon_add=canon_add, sightings_add=sightings_add,
        board_rows_new=new_rows, board_updates=updates,
        pinned_keys=set(_config_pins(config)), gh_custom_add=gh_custom_add,
        gh_pending_add=gh_pending, gh_pending_done=gh_done, run_entry=run_entry,
    )
    ntfy_on = bool(os.environ.get("NTFY_TOPIC"))
    result = None
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        result = apply_changes(data_dir, cs, ntfy_enabled=ntfy_on)
        if git is None:
            break
        if git.commit_push(["data"], "chore: update seen state [skip ci]"):
            break
        print(f"warn: push rejected (attempt {attempt}); replaying on latest origin",
              file=sys.stderr)
        git.reset_to_origin()
    else:
        print("error: push failed after replay attempts; next run re-finds these postings",
              file=sys.stderr)
        return 1
    assert result is not None

    if is_bootstrap:
        print(f"bootstrap: marked {len(matched)} current postings as seen; "
              "no notifications sent")
    elif result.appended:
        loud = [p for p in result.appended if p.url not in quiet_urls]
        if loud:
            _notify_new(config, loud, tuple(failures))
        print(f"inbox: queued {len(result.appended)} for triage"
              + (f" ({len(result.appended) - len(loud)} quietly, first board poll)"
                 if len(loud) != len(result.appended) else ""))
    if not is_bootstrap:  # a bootstrap notifies nothing, health included
        _deliver_health(result, now)
    return 0


def _notify_new(config: Config, postings: list[Posting], failures: tuple[str, ...]) -> None:
    """Best-effort delivery after a successful push; inbox.json is durable."""
    notify_console(postings)
    issues = config.notify.github_issues and os.environ.get(ISSUES_ENV, "1") != "0"
    for name, send in (
        ("github issue", (lambda: notify_github_issue(postings, failures)) if issues else None),
        ("discord", lambda: notify_discord(postings)),
        ("ntfy", None if issues else (
            lambda: health_mod.send_ntfy(f"intern-radar: {len(postings)} new postings"))),
    ):
        if send is None:
            continue
        try:
            send()
        except (NotifyError, OSError) as e:
            print(f"warn: {name} notification failed: {e}", file=sys.stderr)


def _deliver_health(result: ApplyResult, now: datetime) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if token and repo:
        try:
            health_mod.sync_issues(result.active_alerts, now, token=token, repo=repo)
        except (OSError, http.client.HTTPException, ValueError) as e:
            print(f"warn: health issue sync failed: {e}", file=sys.stderr)
    if result.ntfy_due:
        try:
            health_mod.send_ntfy(f"intern-radar health: {len(result.active_alerts)} open alerts")
        except (OSError, http.client.HTTPException) as e:
            print(f"warn: health ntfy failed: {e}", file=sys.stderr)


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


def _guarded_run(*args: Any, **kwargs: Any) -> int:
    """`run`, with a corrupt committed data file reported plainly.

    Only the watcher writes data/*.json, always whole files, so corruption
    means a manual edit went wrong. Fail loudly every run (never fall back to
    empty state: an empty seen.json would re-notify every posting).
    """
    try:
        return run(*args, **kwargs)
    except json.JSONDecodeError as e:
        print(f"error: a data/*.json file is corrupt ({e}); fix or restore it from git",
              file=sys.stderr)
        return 1


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
    git = GitSync(Path.cwd()) if os.environ.get(GIT_ENV) == "1" else None
    return _guarded_run(args.config, args.state, bootstrap=bootstrap, dry_run=args.dry_run,
                        git=git)


if __name__ == "__main__":
    sys.exit(main())
