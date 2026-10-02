"""Source health: per-run metrics, alert rules, weekly summary, delivery.

Everything here is derived from merged state (health.json runs, boards.json,
sightings.json), so a replayed run can never resurrect an alert the other
runner cleared. Issue numbers are never stored: an alert's issue is found by
its exact title. Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L4".
"""

from __future__ import annotations

import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from intern_radar.http import USER_AGENT

RUNS_KEPT = 200
LIST_FAMILIES = ("simplify", "vanshb03", "speedyapply")
NTFY_EVERY = timedelta(hours=6)
MAX_OPEN_ISSUES = 10
LABEL = "health"


def empty_health() -> dict[str, Any]:
    return {"version": 1, "last_run_at": None, "runner": None, "runs": [], "alerts": {},
            "last_health_ntfy_at": None, "weekly_written_for": None}


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _runs_since(health: dict[str, Any], since: datetime) -> list[dict[str, Any]]:
    return [r for r in health["runs"] if (_ts(r["at"]) or since) >= since]


def evaluate(health: dict[str, Any], boards: dict[str, Any], now: datetime
             ) -> dict[str, str]:
    """Active alerts as {"<rule>|<subject>": detail}. Rules R1-R3, R5, R6.

    R4 (search-feed staleness) belongs to phase 3 and is added there.
    """
    active: dict[str, str] = {}
    last_24h = _runs_since(health, now - timedelta(hours=24))
    prior = [r for r in _runs_since(health, now - timedelta(days=7)) if r not in last_24h]
    families = {f for r in health["runs"] for f in r.get("families", {})}
    for fam in sorted(families):
        had = any(r["families"].get(fam, {}).get("matches", 0) > 0 for r in prior)
        recent = [r["families"][fam] for r in last_24h if fam in r["families"]]
        if had and recent and all(e.get("matches", 0) == 0 for e in recent):
            active[f"R1|{fam}"] = "0 matches across every run in the last 24 h"
        last_6h = [r["families"][fam] for r in _runs_since(health, now - timedelta(hours=6))
                   if fam in r["families"]]
        if len(last_6h) >= 2 and all(e.get("ok", 0) == 0 and e.get("errors", 0) > 0
                                     for e in last_6h):
            active[f"R2|{fam}"] = "every job failed on every run in the last 6 h"
    if health["runs"]:
        latest = health["runs"][-1]
        for fam in LIST_FAMILIES:
            newest = latest.get("families", {}).get(fam, {}).get("newest")
            if newest and now.date() - datetime.fromisoformat(newest).date() > timedelta(days=7):
                active[f"R3|{fam}"] = f"newest item is from {newest}"
        if latest.get("seconds", 0) > 720:
            active["R5|run-time"] = f"run took {latest['seconds']:.0f} s"
    last_3h = _runs_since(health, now - timedelta(hours=3))
    if last_3h and all(r.get("boards_skipped_budget", 0) > 0 for r in last_3h):
        active["R5|budget"] = "boards skipped for time budget on every run in the last 3 h"
    for key, row in sorted(boards.items()):
        if row.get("deep") and not row.get("pinned"):
            anchor = _ts(row.get("last_deep_crawl")) or _ts(row.get("deep_since"))
            if anchor and now - anchor > timedelta(hours=36):
                active[f"R5|deep-crawl {key}"] = "no full crawl in 36 h"
        if row.get("disabled"):
            active[f"R6|{key}"] = "disabled after 10 consecutive errors"
    # Deep boards are listed in the weekly summary, not alerted: seeding marks
    # dozens at once and each would open an issue (rev-5 ruling, live run).
    return active


def refresh_alerts(health: dict[str, Any], active: dict[str, str], now: datetime) -> None:
    kept = {k: v for k, v in health.get("alerts", {}).items() if k in active}
    for key in active:
        kept.setdefault(key, {"opened_at": now.isoformat()})
    health["alerts"] = kept


def ntfy_due(health: dict[str, Any], active: dict[str, str], now: datetime,
             enabled: bool) -> bool:
    if not enabled or not active:
        return False
    last = _ts(health.get("last_health_ntfy_at"))
    return last is None or now - last >= NTFY_EVERY


def iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def head_starts(sightings: dict[str, Any]) -> dict[str, tuple[int, float]]:
    """{family: (n, median hours that family saw a posting before simplify)}."""
    gaps: dict[str, list[float]] = {}
    for fams in sightings.get("keys", {}).values():
        base = _ts(fams.get("simplify"))
        if base is None:
            continue
        for fam, stamp in fams.items():
            if fam == "simplify":
                continue
            seen = _ts(stamp)
            if seen is not None:
                gaps.setdefault(fam, []).append((base - seen).total_seconds() / 3600)
    return {f: (len(v), statistics.median(v)) for f, v in gaps.items()}


def weekly_markdown(health: dict[str, Any], boards: dict[str, Any],
                    sightings: dict[str, Any], now: datetime) -> str:
    week_runs = _runs_since(health, now - timedelta(days=7))
    matches: dict[str, int] = {}
    for run in week_runs:
        for fam, entry in run.get("families", {}).items():
            matches[fam] = matches.get(fam, 0) + int(entry.get("matches", 0))
    unique: dict[str, int] = {}
    for fams in sightings.get("keys", {}).values():
        if len(fams) == 1:
            (only,) = fams
            unique[only] = unique.get(only, 0) + 1
    lines = [f"# intern-radar weekly health — {iso_week(now)}", "",
             f"Runs this week: {len(week_runs)}", "",
             "| family | matched postings | unique finds (30 d) |", "|---|---|---|"]
    for fam in sorted(set(matches) | set(unique)):
        lines.append(f"| {fam} | {matches.get(fam, 0)} | {unique.get(fam, 0)} |")
    lines += ["", "## Head start over simplify (median hours, n ≥ 5)", ""]
    for fam, (n, hours) in sorted(head_starts(sightings).items()):
        if n >= 5:
            lines.append(f"- {fam}: {hours:+.1f} h (n={n})")
    for label, flag in (("Disabled", "disabled"), ("Deep (consider pinning)", "deep"),
                        ("Needs config", "needs_config")):
        keys = sorted(k for k, r in boards.items() if r.get(flag))
        lines += ["", f"## {label} boards ({len(keys)})", ""] + [f"- {k}" for k in keys]
    return "\n".join(lines) + "\n"


# --- delivery (after a successful push) -------------------------------------


def _api(method: str, path: str, token: str, body: Any = None) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def issue_title(key: str) -> str:
    rule, subject = key.split("|", 1)
    return f"health: {rule} {subject}"


def sync_issues(active: dict[str, str], now: datetime, *, token: str, repo: str,
                api: Any = _api) -> None:
    """Open, comment on, and close health issues to match `active`."""
    try:
        api("GET", f"/repos/{repo}/labels/{LABEL}", token)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        api("POST", f"/repos/{repo}/labels", token, {"name": LABEL, "color": "d73a4a"})
    open_issues = api("GET", f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=100",
                      token) or []
    by_title = {i["title"]: i for i in open_issues}
    wanted = {issue_title(k): (k, v) for k, v in active.items()}
    overflow_title = "health: R0 alert overflow"
    for title, issue in by_title.items():
        if title not in wanted and title != overflow_title:
            api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token,
                {"body": f"Cleared at {now.isoformat()}."})
            api("PATCH", f"/repos/{repo}/issues/{issue['number']}", token, {"state": "closed"})
    open_count = sum(1 for t in by_title if t in wanted)
    extra: list[str] = []
    for title, (_key, detail) in sorted(wanted.items()):
        issue = by_title.get(title)
        if issue is not None:
            updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
            if now - updated >= timedelta(hours=24):
                api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token,
                    {"body": f"Still active at {now.isoformat()}: {detail}"})
            continue
        if open_count >= MAX_OPEN_ISSUES:
            extra.append(f"- {title}: {detail}")
            continue
        api("POST", f"/repos/{repo}/issues", token,
            {"title": title, "body": f"{detail}\n\nOpened by intern-radar health at "
             f"{now.isoformat()}. Closes automatically when the condition clears.",
             "labels": [LABEL]})
        open_count += 1
    overflow = by_title.get(overflow_title)
    if extra:
        body = "Alerts beyond the open-issue cap:\n" + "\n".join(extra)
        if overflow is None:
            api("POST", f"/repos/{repo}/issues", token,
                {"title": overflow_title, "body": body, "labels": [LABEL]})
        else:
            api("PATCH", f"/repos/{repo}/issues/{overflow['number']}", token, {"body": body})
    elif overflow is not None:
        api("PATCH", f"/repos/{repo}/issues/{overflow['number']}", token, {"state": "closed"})


def send_ntfy(text: str, topic: str | None = None) -> None:
    topic = topic if topic is not None else os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return  # no topic => no POST: https://ntfy.sh/None is a public topic
    request = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(topic, safe='')}",
        data=text.encode("utf-8"), headers={"User-Agent": USER_AGENT}, method="POST")
    with urllib.request.urlopen(request, timeout=15):
        pass
