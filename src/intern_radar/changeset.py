"""A run's state changes, applied idempotently to whatever is on disk.

Two runners (the owner's Mac and GitHub Actions) commit to the same repo. A
run never writes files directly: it builds a ChangeSet, and `apply` merges it
into the files as they are *now*. When a push is rejected the runner resets
to origin and calls `apply` again with the same ChangeSet, so nothing either
runner found is lost. Prunes, evictions and alert state are re-derived from
the merged data on every apply, never replayed.

Spec: docs/specs/2026-09-30-wider-watcher-design.md, "Write protocol".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from intern_radar import boards as boards_mod
from intern_radar import health as health_mod
from intern_radar.models import Posting, canon_key, normalize_url
from intern_radar.state import PRUNE_AFTER_DAYS, SeenStore

SIGHTINGS_DAYS = 30
DATA_FILES = ("seen.json", "inbox.json", "postings.json", "boards.json", "canon.json",
              "sightings.json", "health.json", "health-weekly.md")


@dataclass
class BoardUpdate:
    polled: bool = False
    ok: bool = False
    matched: bool = False
    deep: bool = False
    deep_crawled: bool = False
    reenable: bool = False


@dataclass
class ChangeSet:
    now: datetime
    runner: str
    new_postings: list[Posting] = field(default_factory=list)
    seen_marks: dict[str, str] = field(default_factory=dict)
    cache: list[Posting] = field(default_factory=list)
    canon_add: dict[str, str] = field(default_factory=dict)
    sightings_add: dict[str, dict[str, str]] = field(default_factory=dict)
    board_rows_new: dict[str, dict[str, Any]] = field(default_factory=dict)
    board_updates: dict[str, BoardUpdate] = field(default_factory=dict)
    pinned_keys: set[str] = field(default_factory=set)
    gh_custom_add: dict[str, str] = field(default_factory=dict)
    gh_pending_add: dict[str, str] = field(default_factory=dict)
    gh_pending_done: set[str] = field(default_factory=set)
    run_entry: dict[str, Any] | None = None


@dataclass
class ApplyResult:
    appended: list[Posting]
    active_alerts: dict[str, str]
    ntfy_due: bool


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any, *, sort_keys: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False, sort_keys=sort_keys)
        f.write("\n")


def _max_ts(a: str | None, b: str | None) -> str | None:
    return max((x for x in (a, b) if x), default=None)


def _entry(p: Posting, today: str) -> dict[str, str]:
    return {"url": p.url, "company": p.company, "title": p.title,
            "locations": ", ".join(p.locations), "source": p.source, "added": today}


def apply(data_dir: Path, cs: ChangeSet, *, ntfy_enabled: bool = False) -> ApplyResult:
    now = cs.now
    now_iso = now.isoformat()
    today = now.date().isoformat()

    store = SeenStore.load(data_dir / "seen.json")
    prior_seen = dict(store.seen)
    raw_inbox = _read_json(data_dir / "inbox.json", [])
    inbox: list[dict[str, str]] = [
        {str(k): str(v) for k, v in e.items()} for e in raw_inbox if isinstance(e, dict)
    ] if isinstance(raw_inbox, list) else []
    cache: dict[str, Any] = _read_json(data_dir / "postings.json", {})
    registry: dict[str, Any] = _read_json(data_dir / "boards.json",
                                          boards_mod.empty_registry())
    canon: dict[str, str] = _read_json(data_dir / "canon.json",
                                       {"version": 1, "canon": {}})["canon"]
    sightings: dict[str, Any] = _read_json(data_dir / "sightings.json",
                                           {"version": 1, "keys": {}})
    health: dict[str, Any] = _read_json(data_dir / "health.json", health_mod.empty_health())

    # Inbox: append only postings no runner has recorded yet.
    urls = {e.get("url", "") for e in inbox}
    appended: list[Posting] = []
    for p in cs.new_postings:
        if (p.url in urls or p.key in prior_seen or p.url_key in prior_seen
                or canon.get(canon_key(p.url), p.url_key) != p.url_key):
            continue
        inbox.append(_entry(p, today))
        urls.add(p.url)
        appended.append(p)

    for key, day in cs.seen_marks.items():
        store.seen[key] = max(day, store.seen.get(key, ""))
    for p in cs.cache:
        cache[p.url_key] = {"company": p.company, "title": p.title, "url": p.url,
                            "locations": list(p.locations), "posted_at": p.posted_at,
                            "source": p.source}
    for ck, stored in cs.canon_add.items():
        canon.setdefault(ck, stored)
    for ck, fams in cs.sightings_add.items():
        slot = sightings["keys"].setdefault(ck, {})
        for fam, stamp in fams.items():
            slot[fam] = min(stamp, slot.get(fam, stamp))

    # Boards.
    rows: dict[str, Any] = registry["boards"]
    for key, row in cs.board_rows_new.items():
        if key in rows:
            rows[key]["first_seen"] = min(rows[key]["first_seen"], row["first_seen"])
        else:
            rows[key] = dict(row)
    for key, row in rows.items():
        row["pinned"] = key in cs.pinned_keys
    for key, up in cs.board_updates.items():
        found = rows.get(key)
        if found is None:
            continue
        row = found
        if up.polled:
            row["last_polled"] = _max_ts(row.get("last_polled"), now_iso)
            if up.ok:
                row["last_ok"] = _max_ts(row.get("last_ok"), now_iso)
                row["consecutive_errors"] = 0
            else:
                row["consecutive_errors"] = int(row.get("consecutive_errors", 0)) + 1
        if up.matched:
            row["last_match"] = _max_ts(row.get("last_match"), now_iso)
        if up.deep and not row.get("deep"):
            row["deep"], row["deep_since"] = True, now_iso
        if up.deep_crawled:
            row["last_deep_crawl"] = _max_ts(row.get("last_deep_crawl"), now_iso)
        if up.reenable:
            row["disabled"], row["consecutive_errors"] = False, 0
        elif row["consecutive_errors"] >= boards_mod.DISABLE_AFTER_ERRORS:
            row["disabled"] = True
    for host, board in cs.gh_custom_add.items():
        registry["gh_custom"].setdefault(host, board)
    pending = dict(registry.get("gh_custom_pending") or {})
    for host, token in cs.gh_pending_add.items():
        if host not in registry["gh_custom"]:
            pending.setdefault(host, token)
    for host in cs.gh_pending_done | set(registry["gh_custom"]):
        pending.pop(host, None)
    registry["gh_custom_pending"] = pending

    # Health run record.
    if cs.run_entry is not None:
        health["runs"].append(cs.run_entry)
        health["runs"] = sorted(health["runs"], key=lambda r: r["at"])[-health_mod.RUNS_KEPT:]
        if cs.run_entry["at"] >= (health.get("last_run_at") or ""):
            health["last_run_at"], health["runner"] = cs.run_entry["at"], cs.runner

    # Derived on the merged state: prunes, evictions, alerts, weekly, ntfy.
    before = set(store.seen)
    store.prune(today)
    pruned = before - set(store.seen)  # only keys that aged out, never merely absent ones
    inbox = [e for e in inbox if "url:" + normalize_url(e.get("url", "")) not in pruned]
    canon = {ck: stored for ck, stored in canon.items() if stored not in pruned}
    cutoff = (now - timedelta(days=SIGHTINGS_DAYS)).isoformat()
    sightings["keys"] = {ck: f for ck, f in sightings["keys"].items()
                         if max(f.values()) >= cutoff}
    boards_mod.prune_and_evict(registry, now)
    active = health_mod.evaluate(health, rows, now)
    health_mod.refresh_alerts(health, active, now)
    due = health_mod.ntfy_due(health, active, now, ntfy_enabled)
    if due:
        health["last_health_ntfy_at"] = now_iso
    week = health_mod.iso_week(now)
    if health.get("weekly_written_for") != week:
        (data_dir / "health-weekly.md").write_text(
            health_mod.weekly_markdown(health, rows, sightings, now),
            encoding="utf-8", newline="\n")
        health["weekly_written_for"] = week

    store.save()
    # inbox.json and postings.json keep their pre-existing layouts (entry key
    # order), so the first run under this code doesn't rewrite every line.
    _write_json(data_dir / "inbox.json", inbox, sort_keys=False)
    _write_json(data_dir / "postings.json", dict(sorted(cache.items())), sort_keys=False)
    _write_json(data_dir / "boards.json", registry)
    _write_json(data_dir / "canon.json", {"version": 1, "canon": canon})
    _write_json(data_dir / "sightings.json", sightings)
    _write_json(data_dir / "health.json", health)
    return ApplyResult(appended=appended, active_alerts=active, ntfy_due=due)


__all__ = ["DATA_FILES", "PRUNE_AFTER_DAYS", "ApplyResult", "BoardUpdate", "ChangeSet",
           "apply"]
