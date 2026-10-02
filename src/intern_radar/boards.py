"""Board registry: which company job boards exist, and when each is due.

`board_of` turns any posting URL into (ats, board). Every URL any source ever
produces feeds discovery, so a company Simplify lists once becomes a board we
poll directly afterwards. The registry lives in data/boards.json.

Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L2".
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

_LABEL = re.compile(r"^[a-z0-9-]{1,63}$")
_WD_TENANT = re.compile(r"^[a-z0-9_-]+$")
_WD_INSTANCE = re.compile(r"^wd\d{1,2}$")
_WD_SITE = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCALE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
_ORACLE_SITE = re.compile(r"/hcmui/candidateexperience/[^/]+/sites/([^/]+)/", re.IGNORECASE)

# ATS families that have a poller in this phase. Seeded boards on other ATSes
# wait (never due) until their reader ships.
READERS = frozenset({"workday", "greenhouse", "ashby", "lever", "smartrecruiters"})

HOT_DAYS = 60
WARM_DAYS = 180
NEW_DAYS = 14
DELETE_DAYS = 400
MAX_UNPINNED = 2000
DISABLE_AFTER_ERRORS = 10
WARM_INTERVAL = timedelta(hours=6)
COLD_INTERVAL = timedelta(hours=24)
DEEP_INTERVAL = timedelta(hours=24)


def _host_ok(host: str) -> bool:
    labels = host.split(".")
    return len(labels) >= 2 and all(_LABEL.match(label) for label in labels)


def _suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def board_of(url: str) -> tuple[str, str] | None:
    """(ats, board) for a posting URL on a supported ATS, else None.

    Host matching is exact or a dot-boundary suffix, so a lookalike such as
    evil-oraclecloud.com never becomes a board we poll. Greenhouse postings
    on a company's own domain (?gh_jid=) are returned as ("gh_custom", host);
    the caller resolves the real board name separately.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not _host_ok(host):
        return None
    segs = [s for s in parts.path.split("/") if s]

    if host.endswith(".myworkdayjobs.com"):
        labels = host.split(".")
        if len(labels) != 4:
            return None
        tenant, instance = labels[0], labels[1]
        if not _WD_TENANT.match(tenant) or not _WD_INSTANCE.match(instance):
            return None
        site = next((s for s in segs if not _LOCALE.match(s)), "")
        if not _WD_SITE.match(site):
            return None
        return "workday", f"{tenant}.{instance}/{site}"
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        if segs[:1] == ["embed"]:  # /embed/job_app?for=<board>&token=<id>
            board = parse_qs(parts.query).get("for", [""])[0]
            return ("greenhouse", board.lower()) if _SLUG.match(board) else None
        return ("greenhouse", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
    if host == "jobs.ashbyhq.com":
        return ("ashby", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
    if host == "jobs.lever.co":
        return ("lever", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
    if host == "jobs.smartrecruiters.com":
        return ("smartrecruiters", segs[0]) if segs and _SLUG.match(segs[0]) else None
    if _suffix(host, "oraclecloud.com"):
        m = _ORACLE_SITE.search(parts.path + "/")
        return ("oracle", f"{host}|{m.group(1)}") if m else None
    if _suffix(host, "icims.com"):
        return "icims", host
    if _suffix(host, "eightfold.ai"):
        return "eightfold", host
    gh = parse_qs(parts.query).get("gh_jid", [""])[0]
    if gh.isdigit():
        return "gh_custom", host
    return None


def board_key(ats: str, board: str) -> str:
    return f"{ats}:{board}".casefold()


def bucket(key: str) -> int:
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)


def empty_registry() -> dict[str, Any]:
    return {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}


def new_row(ats: str, board: str, *, pinned: bool, discovered_via: str | None,
            now: str) -> dict[str, Any]:
    return {
        "ats": ats, "board": board, "pinned": pinned, "discovered_via": discovered_via,
        "first_seen": now, "last_polled": None, "last_deep_crawl": None, "deep": False,
        "deep_since": None, "last_ok": None, "last_match": None, "consecutive_errors": 0,
        "disabled": False, "needs_config": False,
    }


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def interval(row: dict[str, Any], now: datetime) -> timedelta | None:
    """How long after `last_polled` this board is due again; None = never."""
    if row.get("disabled") or row.get("needs_config") or row["ats"] not in READERS:
        return None
    last_match = _parse(row.get("last_match"))
    first_seen = _parse(row.get("first_seen"))
    if (row.get("pinned")
            or (last_match and now - last_match <= timedelta(days=HOT_DAYS))
            or (first_seen and now - first_seen <= timedelta(days=NEW_DAYS))):
        return timedelta(0)
    jitter = timedelta(minutes=bucket(board_key(row["ats"], row["board"])) % 60)
    if last_match and now - last_match <= timedelta(days=WARM_DAYS):
        return WARM_INTERVAL - jitter
    return COLD_INTERVAL - jitter


def due(row: dict[str, Any], now: datetime) -> bool:
    gap = interval(row, now)
    if gap is None:
        return False
    last = _parse(row.get("last_polled"))
    return last is None or now - last >= gap


def deep_crawl_due(row: dict[str, Any], now: datetime) -> bool:
    if not row.get("deep") or row.get("pinned"):
        return False
    last = _parse(row.get("last_deep_crawl"))
    return last is None or now - last >= DEEP_INTERVAL


def prune_and_evict(reg: dict[str, Any], now: datetime) -> list[str]:
    """Delete unpinned boards silent for DELETE_DAYS, then cap the unpinned set.

    Runs on the merged registry after every apply (see changeset), so a
    deletion is always derived from current data, never replayed.
    """
    boards: dict[str, Any] = reg["boards"]
    removed: list[str] = []
    cutoff = now - timedelta(days=DELETE_DAYS)
    for key, row in list(boards.items()):
        if row.get("pinned"):
            continue
        anchor = _parse(row.get("last_match")) or _parse(row.get("first_seen"))
        if anchor and anchor < cutoff:
            removed.append(key)
            del boards[key]
    unpinned = [k for k, r in boards.items() if not r.get("pinned")]
    if len(unpinned) > MAX_UNPINNED:
        unpinned.sort(key=lambda k: (boards[k].get("last_match") or "",
                                     boards[k].get("first_seen") or ""))
        for key in unpinned[: len(unpinned) - MAX_UNPINNED]:
            removed.append(key)
            del boards[key]
    return removed


MAX_DEEP_CRAWLS_PER_RUN = 20
