# Wider watcher, phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 1 of `docs/specs/2026-09-30-wider-watcher-design.md` (rev 6): the Mac becomes the primary 30-minute runner with GitHub Actions as a self-skipping backup; the watcher gains two independent internship lists, auto-discovered company boards (441 seeded), wall-clock tiered polling with a time budget, replay-safe commits, dedupe across URL variants, "who saw it first" sightings, and source-health alerts.

**Architecture:** `python -m intern_radar` now owns git: it resets to origin, polls (lists + due boards, 8 workers, ≤2 per host, 10-minute budget), builds a `ChangeSet`, and `changeset.apply` merges it into whatever is on disk — re-deriving prunes, evictions and alerts on the merged state. On a rejected push it resets and re-applies the same change set (up to 3 times), and only then notifies. New modules: `boards.py`, `poll.py`, `changeset.py`, `health.py`, `gitsync.py`, `skipcheck.py`, `sources/lists.py`.

**Tech Stack:** Python stdlib (3.12 on Actions, 3.13 on the Mac), pytest, ruff, mypy, bash, launchd, GitHub Actions.

**Pre-verified (2026-10-01):** every patch below was produced from a scratch implementation and replayed in order on a fresh clone of `main` at `38956e6`: each task's tests fail before its code patch and the whole suite passes after (final: 204 passed; ruff and mypy clean). Live runs of the finished code against real data (read-only, no push): first run 442 boards in 1:45 with 1,895 postings queued quietly and 0 notifications; steady state 624 boards in 2:22 with 9 genuine new notifications; `regression_replay.py` found all 1,785 of the old watcher's matches among 4,674 (0 missing).

**How to apply a patch step:** save the diff block to a file and run `git apply <file>` from `/Users/kernsharma/projects/intern-radar` (the patches are exact; do not retype them). If `git apply` refuses, stop and report — the base has moved.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `src/intern_radar/models.py` | modify | add `canon_key` (dedupe-only key; storage key unchanged) |
| `src/intern_radar/boards.py` | create | `board_of`, registry rows, `due`/`deep_crawl_due`, prune/evict |
| `src/intern_radar/sources/lists.py` | create | vanshb03 + speedyapply readers |
| `src/intern_radar/sources/workday.py` | modify | `posted_date`, `fetch_workday_info(board, max_results, today)` |
| `src/intern_radar/poll.py` | create | `PollJob`, `SourceResult`, `run_jobs` |
| `src/intern_radar/health.py` | create | alert rules R1–R3/R5/R6, weekly summary, issue sync, ntfy |
| `src/intern_radar/changeset.py` | create | `ChangeSet`, `apply` (merge + re-derive) |
| `src/intern_radar/gitsync.py` | create | reset-to-origin, commit+push |
| `src/intern_radar/skipcheck.py` | create | Actions skip gate |
| `src/intern_radar/config.py`, `filters.py`, `notify.py` | modify | list flags, discovered gate, markdown escaping |
| `src/intern_radar/main.py` | modify | new `run()` loop |
| `config.toml` | modify | enable lists + tech gate |
| `.github/workflows/watch.yml` | modify | backup runner with skip step |
| `scripts/mac-watch.sh`, `scripts/git-askpass.sh`, `launchd/com.kernsharma.radar-watch.plist`, `docs/mac-runner.md` | create | Mac runner |
| `scripts/gate_check.py`, `scripts/regression_replay.py` | create | mechanical gates |
| kern-sharma-resume `auto-tailor.md`, `auto-tailor-runner.ps1` | modify / delete | retire the inbox-consuming playbook step |

---

### Task 1: Branch and baseline

- [ ] **Step 1:** `cd /Users/kernsharma/projects/intern-radar && git status --short && git pull --ff-only && git switch -c wider-watcher-p1`
  Expected: clean tree (the bot's data commits may have fast-forwarded).
- [ ] **Step 2:** `python3 -m pytest -q && python3 -m ruff check src tests && python3 -m mypy src`
  Expected: `141 passed`, `All checks passed!`, `Success: no issues found in 18 source files`.

---
### Task 2: Dedupe key and board discovery (`canon_key`, `boards.py`)

**Files:** `tests/test_boards.py`, `src/intern_radar/models.py`, `src/intern_radar/boards.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_boards.py b/tests/test_boards.py
new file mode 100644
index 0000000..0161360
--- /dev/null
+++ b/tests/test_boards.py
@@ -0,0 +1,110 @@
+from datetime import UTC, datetime, timedelta
+
+import pytest
+
+from intern_radar import boards
+from intern_radar.models import canon_key
+
+NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
+
+
+@pytest.mark.parametrize(("url", "expected"), [
+    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US/Intern_JR1",
+     ("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
+    ("https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/x/y_R1",
+     ("workday", "capitalone.wd12/Capital_One")),
+    ("https://bank.wd1.myworkdayjobs.com/fr-CA/Ext/job/z", ("workday", "bank.wd1/Ext")),
+    ("https://evil.wd5.myworkdayjobs.com.attacker.io/Site/job/x", None),
+    ("https://a.b.wd5.myworkdayjobs.com/Site/job/x", None),
+    ("https://job-boards.greenhouse.io/Stripe/jobs/123", ("greenhouse", "stripe")),
+    ("https://boards.greenhouse.io/anduril/jobs/5", ("greenhouse", "anduril")),
+    ("https://boards.greenhouse.io/embed/job_app?for=Jumptrading&token=8027900",
+     ("greenhouse", "jumptrading")),
+    ("https://boards.greenhouse.io/embed/job_app?token=1", None),
+    ("https://jobs.ashbyhq.com/Perplexity/abc/application?embed=true", ("ashby", "perplexity")),
+    ("https://jobs.lever.co/palantir/uuid/apply", ("lever", "palantir")),
+    ("https://jobs.smartrecruiters.com/BoschGroup/7439", ("smartrecruiters", "BoschGroup")),
+    ("https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/9",
+     ("oracle", "eofe.fa.us2.oraclecloud.com|CX_1001")),
+    ("https://evil-oraclecloud.com/hcmUI/CandidateExperience/en/sites/X/job/1", None),
+    ("https://oraclecloud.com.evil.io/hcmUI/CandidateExperience/en/sites/X/job/1", None),
+    ("https://careers-schwab.icims.com/jobs/26869/intern/job",
+     ("icims", "careers-schwab.icims.com")),
+    ("https://paypal.eightfold.ai/careers/job/1", ("eightfold", "paypal.eightfold.ai")),
+    ("https://www.optiver.com/job?gh_jid=8027900", ("gh_custom", "www.optiver.com")),
+    ("https://www.optiver.com/job?gh_jid=abc", None),
+    ("https://example.com/careers/1", None),
+    ("javascript:alert(1)", None),
+    ("https://bad_host.example.com/x", None),
+])
+def test_board_of(url: str, expected: tuple[str, str] | None) -> None:
+    assert boards.board_of(url) == expected
+
+
+def test_canon_key_folds_workday_locale_and_icims_variants() -> None:
+    assert canon_key("https://x.wd1.myworkdayjobs.com/en-US/Site/job/a_1") == \
+        canon_key("https://x.wd1.myworkdayjobs.com/Site/job/a_1")
+    assert canon_key("https://careers-y.icims.com/jobs/12886/job?mobile=true&needsRedirect=false") \
+        == canon_key("https://careers-y.icims.com/jobs/12886/software-intern/job?in_iframe=1")
+    assert canon_key("https://job-boards.greenhouse.io/a/jobs/1") != \
+        canon_key("https://job-boards.greenhouse.io/a/jobs/2")
+
+
+def row(**kw: object) -> dict:
+    base = boards.new_row("workday", "t.wd1/S", pinned=False, discovered_via="simplify",
+                          now=(NOW - timedelta(days=100)).isoformat())
+    base.update(kw)
+    return base
+
+
+def test_due_tiers() -> None:
+    assert boards.due(row(pinned=True, last_polled=NOW.isoformat()), NOW)
+    hot = row(last_match=(NOW - timedelta(days=10)).isoformat(), last_polled=NOW.isoformat())
+    assert boards.due(hot, NOW)
+    new = row(first_seen=(NOW - timedelta(days=3)).isoformat(), last_polled=NOW.isoformat())
+    assert boards.due(new, NOW)
+    warm = row(last_match=(NOW - timedelta(days=90)).isoformat(),
+               last_polled=(NOW - timedelta(hours=2)).isoformat())
+    assert not boards.due(warm, NOW)
+    warm["last_polled"] = (NOW - timedelta(hours=6)).isoformat()
+    assert boards.due(warm, NOW)
+    cold = row(last_polled=(NOW - timedelta(hours=12)).isoformat())
+    assert not boards.due(cold, NOW)
+    cold["last_polled"] = (NOW - timedelta(hours=24)).isoformat()
+    assert boards.due(cold, NOW)
+    assert boards.due(row(), NOW)  # never polled
+    assert not boards.due(row(disabled=True), NOW)
+    assert not boards.due(row(needs_config=True), NOW)
+    assert not boards.due(row(ats="oracle"), NOW)  # no reader until phase 2
+
+
+def test_jitter_is_bounded_and_spread() -> None:
+    keys = [f"workday:t{i}.wd1/s" for i in range(400)]
+    minutes = {boards.bucket(k) % 60 for k in keys}
+    assert len(minutes) > 50 and max(minutes) < 60
+
+
+def test_deep_crawl_due() -> None:
+    assert not boards.deep_crawl_due(row(), NOW)
+    assert boards.deep_crawl_due(row(deep=True), NOW)
+    assert not boards.deep_crawl_due(row(deep=True, last_deep_crawl=NOW.isoformat()), NOW)
+    assert not boards.deep_crawl_due(row(deep=True, pinned=True), NOW)
+
+
+def test_prune_and_evict() -> None:
+    reg = boards.empty_registry()
+    old = (NOW - timedelta(days=500)).isoformat()
+    reg["boards"]["workday:old"] = row(first_seen=old)
+    reg["boards"]["workday:pinned-old"] = row(first_seen=old, pinned=True)
+    reg["boards"]["workday:fresh"] = row(first_seen=NOW.isoformat())
+    assert boards.prune_and_evict(reg, NOW) == ["workday:old"]
+    assert set(reg["boards"]) == {"workday:pinned-old", "workday:fresh"}
+
+
+def test_evicts_oldest_beyond_cap(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr(boards, "MAX_UNPINNED", 2)
+    reg = boards.empty_registry()
+    for i, days in enumerate((1, 5, 9)):
+        reg["boards"][f"workday:b{i}"] = row(
+            last_match=(NOW - timedelta(days=days)).isoformat(), first_seen=NOW.isoformat())
+    assert boards.prune_and_evict(reg, NOW) == ["workday:b2"]
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_boards.py`
  Expected: 1 error during collection (`cannot import name 'canon_key'` / no module `intern_radar.boards`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/boards.py b/src/intern_radar/boards.py
new file mode 100644
index 0000000..171d47b
--- /dev/null
+++ b/src/intern_radar/boards.py
@@ -0,0 +1,187 @@
+"""Board registry: which company job boards exist, and when each is due.
+
+`board_of` turns any posting URL into (ats, board). Every URL any source ever
+produces feeds discovery, so a company Simplify lists once becomes a board we
+poll directly afterwards. The registry lives in data/boards.json.
+
+Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L2".
+"""
+
+from __future__ import annotations
+
+import hashlib
+import re
+from datetime import datetime, timedelta
+from typing import Any
+from urllib.parse import parse_qs, urlsplit
+
+_LABEL = re.compile(r"^[a-z0-9-]{1,63}$")
+_WD_TENANT = re.compile(r"^[a-z0-9_-]+$")
+_WD_INSTANCE = re.compile(r"^wd\d{1,2}$")
+_WD_SITE = re.compile(r"^[A-Za-z0-9_-]+$")
+_LOCALE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
+_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
+_ORACLE_SITE = re.compile(r"/hcmui/candidateexperience/[^/]+/sites/([^/]+)/", re.IGNORECASE)
+
+# ATS families that have a poller in this phase. Seeded boards on other ATSes
+# wait (never due) until their reader ships.
+READERS = frozenset({"workday", "greenhouse", "ashby", "lever", "smartrecruiters"})
+
+HOT_DAYS = 60
+WARM_DAYS = 180
+NEW_DAYS = 14
+DELETE_DAYS = 400
+MAX_UNPINNED = 2000
+DISABLE_AFTER_ERRORS = 10
+WARM_INTERVAL = timedelta(hours=6)
+COLD_INTERVAL = timedelta(hours=24)
+DEEP_INTERVAL = timedelta(hours=24)
+
+
+def _host_ok(host: str) -> bool:
+    labels = host.split(".")
+    return len(labels) >= 2 and all(_LABEL.match(label) for label in labels)
+
+
+def _suffix(host: str, suffix: str) -> bool:
+    return host == suffix or host.endswith("." + suffix)
+
+
+def board_of(url: str) -> tuple[str, str] | None:
+    """(ats, board) for a posting URL on a supported ATS, else None.
+
+    Host matching is exact or a dot-boundary suffix, so a lookalike such as
+    evil-oraclecloud.com never becomes a board we poll. Greenhouse postings
+    on a company's own domain (?gh_jid=) are returned as ("gh_custom", host);
+    the caller resolves the real board name separately.
+    """
+    try:
+        parts = urlsplit(url.strip())
+    except ValueError:
+        return None
+    if parts.scheme not in ("http", "https"):
+        return None
+    host = (parts.hostname or "").lower()
+    if not _host_ok(host):
+        return None
+    segs = [s for s in parts.path.split("/") if s]
+
+    if host.endswith(".myworkdayjobs.com"):
+        labels = host.split(".")
+        if len(labels) != 4:
+            return None
+        tenant, instance = labels[0], labels[1]
+        if not _WD_TENANT.match(tenant) or not _WD_INSTANCE.match(instance):
+            return None
+        site = next((s for s in segs if not _LOCALE.match(s)), "")
+        if not _WD_SITE.match(site):
+            return None
+        return "workday", f"{tenant}.{instance}/{site}"
+    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
+        if segs[:1] == ["embed"]:  # /embed/job_app?for=<board>&token=<id>
+            board = parse_qs(parts.query).get("for", [""])[0]
+            return ("greenhouse", board.lower()) if _SLUG.match(board) else None
+        return ("greenhouse", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
+    if host == "jobs.ashbyhq.com":
+        return ("ashby", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
+    if host == "jobs.lever.co":
+        return ("lever", segs[0].lower()) if segs and _SLUG.match(segs[0]) else None
+    if host == "jobs.smartrecruiters.com":
+        return ("smartrecruiters", segs[0]) if segs and _SLUG.match(segs[0]) else None
+    if _suffix(host, "oraclecloud.com"):
+        m = _ORACLE_SITE.search(parts.path + "/")
+        return ("oracle", f"{host}|{m.group(1)}") if m else None
+    if _suffix(host, "icims.com"):
+        return "icims", host
+    if _suffix(host, "eightfold.ai"):
+        return "eightfold", host
+    gh = parse_qs(parts.query).get("gh_jid", [""])[0]
+    if gh.isdigit():
+        return "gh_custom", host
+    return None
+
+
+def board_key(ats: str, board: str) -> str:
+    return f"{ats}:{board}".casefold()
+
+
+def bucket(key: str) -> int:
+    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
+
+
+def empty_registry() -> dict[str, Any]:
+    return {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}
+
+
+def new_row(ats: str, board: str, *, pinned: bool, discovered_via: str | None,
+            now: str) -> dict[str, Any]:
+    return {
+        "ats": ats, "board": board, "pinned": pinned, "discovered_via": discovered_via,
+        "first_seen": now, "last_polled": None, "last_deep_crawl": None, "deep": False,
+        "deep_since": None, "last_ok": None, "last_match": None, "consecutive_errors": 0,
+        "disabled": False, "needs_config": False,
+    }
+
+
+def _parse(ts: str | None) -> datetime | None:
+    return datetime.fromisoformat(ts) if ts else None
+
+
+def interval(row: dict[str, Any], now: datetime) -> timedelta | None:
+    """How long after `last_polled` this board is due again; None = never."""
+    if row.get("disabled") or row.get("needs_config") or row["ats"] not in READERS:
+        return None
+    last_match = _parse(row.get("last_match"))
+    first_seen = _parse(row.get("first_seen"))
+    if (row.get("pinned")
+            or (last_match and now - last_match <= timedelta(days=HOT_DAYS))
+            or (first_seen and now - first_seen <= timedelta(days=NEW_DAYS))):
+        return timedelta(0)
+    jitter = timedelta(minutes=bucket(board_key(row["ats"], row["board"])) % 60)
+    if last_match and now - last_match <= timedelta(days=WARM_DAYS):
+        return WARM_INTERVAL - jitter
+    return COLD_INTERVAL - jitter
+
+
+def due(row: dict[str, Any], now: datetime) -> bool:
+    gap = interval(row, now)
+    if gap is None:
+        return False
+    last = _parse(row.get("last_polled"))
+    return last is None or now - last >= gap
+
+
+def deep_crawl_due(row: dict[str, Any], now: datetime) -> bool:
+    if not row.get("deep") or row.get("pinned"):
+        return False
+    last = _parse(row.get("last_deep_crawl"))
+    return last is None or now - last >= DEEP_INTERVAL
+
+
+def prune_and_evict(reg: dict[str, Any], now: datetime) -> list[str]:
+    """Delete unpinned boards silent for DELETE_DAYS, then cap the unpinned set.
+
+    Runs on the merged registry after every apply (see changeset), so a
+    deletion is always derived from current data, never replayed.
+    """
+    boards: dict[str, Any] = reg["boards"]
+    removed: list[str] = []
+    cutoff = now - timedelta(days=DELETE_DAYS)
+    for key, row in list(boards.items()):
+        if row.get("pinned"):
+            continue
+        anchor = _parse(row.get("last_match")) or _parse(row.get("first_seen"))
+        if anchor and anchor < cutoff:
+            removed.append(key)
+            del boards[key]
+    unpinned = [k for k, r in boards.items() if not r.get("pinned")]
+    if len(unpinned) > MAX_UNPINNED:
+        unpinned.sort(key=lambda k: (boards[k].get("last_match") or "",
+                                     boards[k].get("first_seen") or ""))
+        for key in unpinned[: len(unpinned) - MAX_UNPINNED]:
+            removed.append(key)
+            del boards[key]
+    return removed
+
+
+MAX_DEEP_CRAWLS_PER_RUN = 20
diff --git a/src/intern_radar/models.py b/src/intern_radar/models.py
index 5e31050..f3a1a31 100644
--- a/src/intern_radar/models.py
+++ b/src/intern_radar/models.py
@@ -1,5 +1,6 @@
 from __future__ import annotations
 
+import re
 from dataclasses import dataclass
 from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
 
@@ -53,3 +54,28 @@ def normalize_url(url: str) -> str:
     # aggregators and board APIs disagree on org-slug casing (/Perplexity/ vs
     # /perplexity/) while job ids are numeric or UUIDs.
     return urlunsplit((parts.scheme.lower(), host, path.lower(), urlencode(query), ""))
+
+
+_WORKDAY_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$")
+_ICIMS_JOB = re.compile(r"^/jobs/(\d+)(?:/[^/]+)?/job$")
+
+
+def canon_key(url: str) -> str:
+    """Dedup-only key: folds URL variants the storage key keeps apart.
+
+    `normalize_url` stays the storage key (seen.json, inbox, tracker all use
+    it), so changing it would orphan existing entries. This key only decides
+    "is this the same job?": Workday links carry optional locale segments
+    (/en-US/) and iCIMS links carry an optional slug plus tracking query.
+    """
+    norm = normalize_url(url)
+    parts = urlsplit(norm)
+    host, path, query = parts.netloc, parts.path, parts.query
+    if host.endswith(".myworkdayjobs.com"):
+        segs = [s for s in path.split("/") if s and not _WORKDAY_LOCALE.match(s)]
+        path = "/" + "/".join(segs)
+    elif host == "icims.com" or host.endswith(".icims.com"):
+        m = _ICIMS_JOB.match(path)
+        if m:
+            path, query = f"/jobs/{m.group(1)}/job", ""
+    return "url:" + urlunsplit((parts.scheme, host, path, query, ""))
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `169 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_boards.py src/intern_radar/models.py src/intern_radar/boards.py
git commit -m "feat(boards): canon_key and board registry with wall-clock due rule" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: List sources: vanshb03 and speedyapply

**Files:** `tests/fixtures/speedyapply_readme.md`, `tests/test_lists.py`, `src/intern_radar/sources/lists.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/fixtures/speedyapply_readme.md b/tests/fixtures/speedyapply_readme.md
new file mode 100644
index 0000000..40e73c2
--- /dev/null
+++ b/tests/fixtures/speedyapply_readme.md
@@ -0,0 +1,13 @@
+
+Intro text
+
+| Company | Position | Location | Salary | Posting | Age |
+|---|---|---|---|---|---|
+| <a href="https://www.microsoft.com"><strong>Microsoft</strong></a> | Software Engineer: Intern | Atlanta, GA | $52/hr | <a href="https://apply.careers.microsoft.com/careers/job/1970393557008714"><img src="x" alt="Apply" width="70"/></a> | 4d |
+| <strong>AT&amp;T</strong> | Data Intern | Dallas, TX | $40/hr | <a href="https://att.wd1.myworkdayjobs.com/ATTCollege/job/x_1"><img/></a> | 0d |
+
+More text
+
+| Company | Position | Location | Posting | Age |
+|---|---|---|---|---|
+| <a href="https://z.com"><strong>Zeta</strong></a> | ML Intern | Remote | <a href="https://jobs.lever.co/zeta/1"><img/></a> | 2w |
diff --git a/tests/test_lists.py b/tests/test_lists.py
new file mode 100644
index 0000000..9ec5e2e
--- /dev/null
+++ b/tests/test_lists.py
@@ -0,0 +1,52 @@
+from datetime import UTC, date, datetime
+from pathlib import Path
+
+from intern_radar.sources.lists import parse_speedyapply, parse_vansh
+
+
+def ts(y: int, m: int, d: int) -> int:
+    return int(datetime(y, m, d, tzinfo=UTC).timestamp())
+
+
+def vansh_row(i: int, **kw: object) -> dict:
+    base = {"id": f"id{i}", "active": True, "is_visible": True, "company_name": "Acme",
+            "title": "Software Engineer Intern", "url": f"https://jobs.lever.co/acme/{i}",
+            "locations": ["NYC"], "season": "Summer", "date_posted": ts(2026, 7, 1),
+            "date_updated": ts(2026, 8, 23)}
+    base.update(kw)
+    return base
+
+
+def test_vansh_summer_term_rules() -> None:
+    rows = [
+        vansh_row(1),
+        vansh_row(2, date_posted=ts(2026, 4, 10)),
+        vansh_row(3, title="Summer 2026 Engineer Intern"),
+        vansh_row(4, title="Summer 2027 SWE Intern"),
+        vansh_row(5, season="Fall"),
+        vansh_row(6, active=False),
+    ]
+    by_id = {p.key: p for p in parse_vansh(rows)}
+    assert by_id["vanshb03:id1"].terms == ("Summer 2027",)
+    assert by_id["vanshb03:id2"].terms == ()
+    assert by_id["vanshb03:id3"].terms == ()
+    assert by_id["vanshb03:id4"].terms == ("Summer 2027",)
+    assert by_id["vanshb03:id5"].terms == ()
+    assert "vanshb03:id6" not in by_id
+    assert by_id["vanshb03:id1"].source == "vanshb03"
+    assert by_id["vanshb03:id1"].posted_at == "2026-07-01"
+
+
+README = (Path(__file__).parent / "fixtures" / "speedyapply_readme.md").read_text(
+    encoding="utf-8")
+
+
+def test_speedyapply_both_layouts() -> None:
+    posts = parse_speedyapply(README, date(2026, 9, 30))
+    assert [(p.company, p.title, p.posted_at) for p in posts] == [
+        ("Microsoft", "Software Engineer: Intern", "2026-09-26"),
+        ("AT&T", "Data Intern", "2026-09-30"),
+        ("Zeta", "ML Intern", ""),
+    ]
+    assert posts[1].url == "https://att.wd1.myworkdayjobs.com/ATTCollege/job/x_1"
+    assert posts[0].source == "speedyapply" and posts[0].terms == ()
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_lists.py`
  Expected: 1 error during collection (no module `intern_radar.sources.lists`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/sources/lists.py b/src/intern_radar/sources/lists.py
new file mode 100644
index 0000000..33923ed
--- /dev/null
+++ b/src/intern_radar/sources/lists.py
@@ -0,0 +1,130 @@
+"""Community internship lists besides Simplify: vanshb03 and speedyapply.
+
+Both are independent of Simplify, so the watcher keeps working if any one
+list goes stale. Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L1".
+"""
+
+from __future__ import annotations
+
+import html
+import re
+from datetime import UTC, date, datetime, timedelta
+from typing import Any
+
+from intern_radar.http import get_json, get_text
+from intern_radar.models import Posting
+
+VANSH_URL = (
+    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships"
+    "/dev/.github/scripts/listings.json"
+)
+SPEEDY_URL = (
+    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md"
+)
+# vanshb03 "Summer" rows carry no year; rows posted before June 2026 are
+# plausibly the Summer 2026 cycle (72 such rows were measured).
+VANSH_SUMMER_CUTOFF = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
+_YEAR = re.compile(r"\b(20\d\d)\b")
+_HEADERS = (
+    "| Company | Position | Location | Salary | Posting | Age |",
+    "| Company | Position | Location | Posting | Age |",
+)
+_STRONG = re.compile(r"<strong>(.*?)</strong>", re.IGNORECASE | re.DOTALL)
+_HREF = re.compile(r'<a\s+href="([^"]+)"', re.IGNORECASE)
+_AGE = re.compile(r"^(\d+)d$")
+_TAGS = re.compile(r"<[^>]+>")
+
+
+def _epoch_to_iso(raw: Any) -> str:
+    if not isinstance(raw, (int, float)) or raw <= 0:
+        return ""
+    return datetime.fromtimestamp(float(raw), tz=UTC).date().isoformat()
+
+
+def _vansh_terms(item: dict[str, Any], title: str) -> tuple[str, ...]:
+    if item.get("season") != "Summer":
+        return ()
+    posted = item.get("date_posted")
+    if not isinstance(posted, (int, float)) or posted < VANSH_SUMMER_CUTOFF:
+        return ()
+    if any(year != "2027" for year in _YEAR.findall(title)):
+        return ()
+    return ("Summer 2027",)
+
+
+def parse_vansh(payload: Any) -> list[Posting]:
+    if not isinstance(payload, list):
+        raise ValueError("vanshb03: expected a top-level list")
+    postings: list[Posting] = []
+    for item in payload:
+        if not isinstance(item, dict) or not (item.get("active") and item.get("is_visible")):
+            continue
+        listing_id = str(item.get("id", "")).strip()
+        title = str(item.get("title", "")).strip()
+        url = str(item.get("url", "")).strip()
+        if not listing_id or not title or not url:
+            continue
+        postings.append(Posting(
+            key=f"vanshb03:{listing_id}",
+            source="vanshb03",
+            company=str(item.get("company_name", "")).strip(),
+            title=title,
+            url=url,
+            locations=tuple(str(x).strip() for x in item.get("locations") or [] if str(x).strip()),
+            terms=_vansh_terms(item, title),
+            posted_at=_epoch_to_iso(item.get("date_posted")),
+        ))
+    return postings
+
+
+def fetch_vansh(url: str = VANSH_URL) -> list[Posting]:
+    return parse_vansh(get_json(url))
+
+
+def _cells(line: str) -> list[str]:
+    return [c.strip() for c in line.strip().strip("|").split("|")]
+
+
+def parse_speedyapply(markdown: str, today: date) -> list[Posting]:
+    """Rows of every table whose header is one of the two known layouts."""
+    postings: list[Posting] = []
+    layout: tuple[str, ...] | None = None
+    for line in markdown.splitlines():
+        stripped = line.strip()
+        if stripped in _HEADERS:
+            layout = tuple(_cells(stripped))
+            continue
+        if layout is None:
+            continue
+        if not stripped.startswith("|"):
+            layout = None
+            continue
+        if set(stripped) <= set("|-: "):
+            continue  # the |---|---| separator row
+        cells = _cells(stripped)
+        if len(cells) != len(layout):
+            continue
+        row = dict(zip(layout, cells, strict=True))
+        company_m = _STRONG.search(row["Company"])
+        href_m = _HREF.search(row["Posting"])
+        title = html.unescape(_TAGS.sub("", row["Position"])).strip()
+        if not company_m or not href_m or not title:
+            continue
+        age_m = _AGE.match(row["Age"])
+        posted = (today - timedelta(days=int(age_m.group(1)))).isoformat() if age_m else ""
+        location = html.unescape(_TAGS.sub("", row["Location"])).strip()
+        url = html.unescape(href_m.group(1))
+        postings.append(Posting(
+            key=f"speedyapply:{url}",
+            source="speedyapply",
+            company=html.unescape(_TAGS.sub("", company_m.group(1))).strip(),
+            title=title,
+            url=url,
+            locations=(location,) if location else (),
+            posted_at=posted,
+        ))
+    return postings
+
+
+def fetch_speedyapply(url: str = SPEEDY_URL) -> list[Posting]:
+    return parse_speedyapply(get_text(url), datetime.now(tz=UTC).date())
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `171 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/fixtures/speedyapply_readme.md tests/test_lists.py src/intern_radar/sources/lists.py
git commit -m "feat(sources): vanshb03 and speedyapply list readers" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Workday: posted dates and depth-capped fetch

**Files:** `tests/test_workday_depth.py`, `src/intern_radar/sources/workday.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_workday_depth.py b/tests/test_workday_depth.py
new file mode 100644
index 0000000..6741b68
--- /dev/null
+++ b/tests/test_workday_depth.py
@@ -0,0 +1,33 @@
+from datetime import date
+from typing import Any
+
+import pytest
+
+from intern_radar.sources import workday
+
+
+def test_posted_date_variants() -> None:
+    today = date(2026, 9, 30)
+    assert workday.posted_date("Posted Today", today) == "2026-09-30"
+    assert workday.posted_date("Posted Yesterday", today) == "2026-09-29"
+    assert workday.posted_date("Posted 3 Days Ago", today) == "2026-09-27"
+    assert workday.posted_date("Posted 30+ Days Ago", today) == "2026-08-31"
+    assert workday.posted_date("Something else", today) == ""
+
+
+def test_fetch_info_respects_depth_and_returns_total(monkeypatch: pytest.MonkeyPatch) -> None:
+    calls: list[int] = []
+
+    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
+        calls.append(payload["offset"])
+        assert payload["limit"] == 20
+        base = payload["offset"]
+        jobs = [{"title": f"Intern {base + i}", "externalPath": f"/job/{base + i}",
+                 "postedOn": "Posted Today"} for i in range(20)]
+        return {"total": 747, "jobPostings": jobs}
+
+    monkeypatch.setattr(workday, "post_json", fake_post)
+    posts, total = workday.fetch_workday_info("acme.wd5/Ext", workday.DISCOVERED_MAX_RESULTS,
+                                              date(2026, 9, 30))
+    assert total == 747 and calls == [0, 20, 40, 60, 80] and len(posts) == 100
+    assert posts[0].posted_at == "2026-09-30"
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_workday_depth.py`
  Expected: 2 failed (`posted_date` / `fetch_workday_info` missing).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/sources/workday.py b/src/intern_radar/sources/workday.py
index 1d21e46..a173703 100644
--- a/src/intern_radar/sources/workday.py
+++ b/src/intern_radar/sources/workday.py
@@ -1,5 +1,7 @@
 from __future__ import annotations
 
+import re
+from datetime import date, timedelta
 from typing import Any
 
 from intern_radar.http import post_json
@@ -24,7 +26,22 @@ def _split_board(board: str) -> tuple[str, str, str]:
     return host_part, tenant, site
 
 
-def parse_workday(board: str, payload: Any) -> list[Posting]:
+_POSTED_DAYS = re.compile(r"^Posted (\d+)\+? Days? Ago$", re.IGNORECASE)
+DISCOVERED_MAX_RESULTS = 100  # 5 pages; see fetch_workday_info
+
+
+def posted_date(posted_on: str, today: date) -> str:
+    """ISO date from Workday's relative 'postedOn' text; '' if unrecognized."""
+    text = posted_on.strip()
+    if text.lower() == "posted today":
+        return today.isoformat()
+    if text.lower() == "posted yesterday":
+        return (today - timedelta(days=1)).isoformat()
+    m = _POSTED_DAYS.match(text)
+    return (today - timedelta(days=int(m.group(1)))).isoformat() if m else ""
+
+
+def parse_workday(board: str, payload: Any, today: date | None = None) -> list[Posting]:
     """Parse one page of a Workday CXS jobs response.
 
     `board` is "tenant.instance/site", e.g. "arrowstreetcapital.wd5/Campus_Careers".
@@ -49,23 +66,36 @@ def parse_workday(board: str, payload: Any) -> list[Posting]:
                 title=title,
                 url=f"https://{host_part}.myworkdayjobs.com/{site}{external_path}",
                 locations=(location,) if location else (),
-                # postedOn is relative text ("Posted 18 Days Ago") — no real date.
+                posted_at=posted_date(str(job.get("postedOn") or ""), today) if today else "",
             )
         )
     return postings
 
 
 def fetch_workday(board: str) -> list[Posting]:
+    return fetch_workday_info(board)[0]
+
+
+def fetch_workday_info(
+    board: str, max_results: int = MAX_RESULTS, today: date | None = None
+) -> tuple[list[Posting], int]:
+    """Postings plus the server's total match count.
+
+    Pinned boards page to MAX_RESULTS (intern roles hide deep in the fuzzy
+    search). Discovered boards pass DISCOVERED_MAX_RESULTS; a total above it
+    marks the board `deep` so it gets a daily full crawl instead.
+    """
     host_part, tenant, site = _split_board(board)
     api = f"https://{host_part}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
     postings: list[Posting] = []
     offset = 0
-    while offset < MAX_RESULTS:
+    total = 0
+    while offset < max_results:
         payload = post_json(
             api, {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
                   "searchText": "intern"},
         )
-        postings.extend(parse_workday(board, payload))
+        postings.extend(parse_workday(board, payload, today))
         raw_count = len(payload["jobPostings"])
         total = int(payload.get("total") or 0)
         offset += PAGE_SIZE
@@ -73,4 +103,4 @@ def fetch_workday(board: str) -> list[Posting]:
         # unparseable entries must not end pagination early.
         if offset >= total or raw_count == 0:
             break
-    return postings
+    return postings, total
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `173 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_workday_depth.py src/intern_radar/sources/workday.py
git commit -m "feat(workday): postedOn dates and depth-capped fetch_workday_info" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Concurrent polling with per-host limit and budget (`poll.py`)

**Files:** `tests/test_poll.py`, `src/intern_radar/poll.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_poll.py b/tests/test_poll.py
new file mode 100644
index 0000000..ff9bc46
--- /dev/null
+++ b/tests/test_poll.py
@@ -0,0 +1,49 @@
+import threading
+import time
+from typing import Any
+
+from intern_radar.models import Posting
+from intern_radar.poll import PollJob, run_jobs
+
+
+def p(n: int) -> Posting:
+    return Posting(key=f"k{n}", source="x", company="c", title="t", url=f"https://x/{n}")
+
+
+def test_results_errors_and_tuple_outputs() -> None:
+    def boom() -> list[Posting]:
+        raise ValueError("nope")
+
+    jobs = [PollJob("a", "fam", "h1", lambda: [p(1)]),
+            PollJob("b", "fam", "h2", boom),
+            PollJob("c", "workday", "h3", lambda: ([p(2), p(3)], 250))]
+    a, b, c = run_jobs(jobs)
+    assert a.ok and [x.key for x in a.postings] == ["k1"]
+    assert not b.ok and "ValueError: nope" in b.error
+    assert c.ok and c.total == 250 and len(c.postings) == 2
+
+
+def test_budget_skips_unpinned_jobs_not_started() -> None:
+    ticks = iter([0.0] + [700.0] * 20)
+    jobs = [PollJob("pinned", "f", "h", lambda: [p(1)], pinned=True),
+            PollJob("discovered", "f", "h", lambda: [p(2)], pinned=False)]
+    results = run_jobs(jobs, budget=600.0, clock=lambda: next(ticks), workers=1)
+    assert results[0].ok and not results[0].skipped
+    assert results[1].skipped and not results[1].ok
+
+
+def test_per_host_limit() -> None:
+    active: dict[str, int] = {"n": 0, "max": 0}
+    lock = threading.Lock()
+
+    def slow() -> list[Any]:
+        with lock:
+            active["n"] += 1
+            active["max"] = max(active["max"], active["n"])
+        time.sleep(0.05)
+        with lock:
+            active["n"] -= 1
+        return []
+
+    run_jobs([PollJob(str(i), "f", "same-host", slow) for i in range(8)], workers=8)
+    assert active["max"] == 2
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_poll.py`
  Expected: 1 error during collection (no module `intern_radar.poll`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/poll.py b/src/intern_radar/poll.py
new file mode 100644
index 0000000..9f9127b
--- /dev/null
+++ b/src/intern_radar/poll.py
@@ -0,0 +1,81 @@
+"""Run fetch jobs concurrently, politely, within a time budget.
+
+8 workers overall, at most 2 in flight per host. Once the run passes the
+budget, jobs that have not started yet are skipped (pinned ones still run),
+so the whole run stays well under the 15-minute Actions timeout.
+"""
+
+from __future__ import annotations
+
+import threading
+import time
+from collections.abc import Callable
+from concurrent.futures import ThreadPoolExecutor
+from dataclasses import dataclass, field
+from typing import Any
+
+from intern_radar.models import Posting
+
+MAX_WORKERS = 8
+PER_HOST = 2
+BUDGET_SECONDS = 600.0
+
+
+@dataclass
+class PollJob:
+    name: str            # e.g. "simplify", "workday:nvidia.wd5/NVIDIAExternalCareerSite"
+    family: str          # health family: list name or ATS name
+    host: str            # politeness key for the per-host limit
+    fetch: Callable[[], Any]  # returns list[Posting] or (list[Posting], total)
+    board_key: str | None = None
+    pinned: bool = True
+    deep_crawl: bool = False
+
+
+@dataclass
+class SourceResult:
+    job: PollJob
+    ok: bool = False
+    skipped: bool = False
+    error: str = ""
+    postings: list[Posting] = field(default_factory=list)
+    total: int | None = None
+    seconds: float = 0.0
+
+
+def run_jobs(
+    jobs: list[PollJob],
+    *,
+    budget: float = BUDGET_SECONDS,
+    clock: Callable[[], float] = time.monotonic,
+    workers: int = MAX_WORKERS,
+) -> list[SourceResult]:
+    start = clock()
+    locks: dict[str, threading.Semaphore] = {}
+    guard = threading.Lock()
+
+    def host_lock(host: str) -> threading.Semaphore:
+        with guard:
+            return locks.setdefault(host, threading.Semaphore(PER_HOST))
+
+    def one(job: PollJob) -> SourceResult:
+        result = SourceResult(job=job)
+        if not job.pinned and clock() - start >= budget:
+            result.skipped = True
+            return result
+        with host_lock(job.host):
+            began = clock()
+            try:
+                out = job.fetch()
+                if isinstance(out, tuple):
+                    result.postings, result.total = list(out[0]), int(out[1])
+                else:
+                    result.postings = list(out)
+                result.ok = True
+            except Exception as e:  # one bad source must not kill the run
+                result.error = f"{type(e).__name__}: {e}"[:300]
+            result.seconds = clock() - began
+        return result
+
+    with ThreadPoolExecutor(max_workers=workers) as pool:
+        return list(pool.map(one, jobs))
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `176 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_poll.py src/intern_radar/poll.py
git commit -m "feat(poll): concurrent fetch jobs with per-host limit and time budget" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Health: alert rules, weekly summary, issue sync, ntfy (`health.py`)

**Files:** `tests/test_health.py`, `src/intern_radar/health.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_health.py b/tests/test_health.py
new file mode 100644
index 0000000..e57f631
--- /dev/null
+++ b/tests/test_health.py
@@ -0,0 +1,103 @@
+import urllib.error
+from datetime import UTC, datetime, timedelta
+from typing import Any
+
+from intern_radar import health
+
+NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
+
+
+def run_at(hours_ago: float, **fams: dict[str, Any]) -> dict[str, Any]:
+    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(), "runner": "mac", "seconds": 60,
+            "families": fams, "boards_polled": 10, "boards_skipped_budget": 0}
+
+
+def state(*runs: dict[str, Any]) -> dict[str, Any]:
+    h = health.empty_health()
+    h["runs"] = list(runs)
+    return h
+
+
+def test_r1_family_went_silent() -> None:
+    h = state(run_at(72, workday={"matches": 5, "ok": 3}),
+              run_at(10, workday={"matches": 0, "ok": 3}),
+              run_at(1, workday={"matches": 0, "ok": 3}))
+    assert "R1|workday" in health.evaluate(h, {}, NOW)
+
+
+def test_r2_family_failing() -> None:
+    h = state(run_at(4, ashby={"ok": 0, "errors": 2}), run_at(1, ashby={"ok": 0, "errors": 2}))
+    assert "R2|ashby" in health.evaluate(h, {}, NOW)
+    h["runs"][-1]["families"]["ashby"]["ok"] = 1
+    assert "R2|ashby" not in health.evaluate(h, {}, NOW)
+
+
+def test_r3_stale_list_and_r5() -> None:
+    h = state(run_at(0.5, vanshb03={"newest": "2026-08-23", "ok": 1}))
+    h["runs"][-1]["seconds"] = 800
+    h["runs"][-1]["boards_skipped_budget"] = 4
+    active = health.evaluate(h, {}, NOW)
+    assert {"R3|vanshb03", "R5|run-time", "R5|budget"} <= set(active)
+
+
+def test_r5_deep_crawl_starved_and_r6() -> None:
+    boards = {
+        "workday:a": {"deep": True, "pinned": False,
+                      "deep_since": (NOW - timedelta(days=3)).isoformat(),
+                      "last_deep_crawl": (NOW - timedelta(hours=40)).isoformat()},
+        "workday:b": {"disabled": True},
+    }
+    active = health.evaluate(state(), boards, NOW)
+    assert "R5|deep-crawl workday:a" in active
+    assert "R6|workday:b" in active       # disabled boards alert
+    assert "R6|workday:a" not in active   # deep boards go to the weekly summary only
+
+
+def test_head_starts() -> None:
+    s = {"keys": {f"k{i}": {"simplify": (NOW - timedelta(hours=1)).isoformat(),
+                            "workday": (NOW - timedelta(hours=5)).isoformat()} for i in range(6)}}
+    n, hours = health.head_starts(s)["workday"]
+    assert n == 6 and hours == 4.0
+
+
+class FakeApi:
+    def __init__(self, issues: list[dict[str, Any]], label: bool = True) -> None:
+        self.issues, self.label, self.calls = issues, label, []
+
+    def __call__(self, method: str, path: str, token: str, body: Any = None) -> Any:
+        self.calls.append((method, path, body))
+        if path.endswith("/labels/health") and method == "GET":
+            if not self.label:
+                raise urllib.error.HTTPError(path, 404, "nf", None, None)  # type: ignore[arg-type]
+            return {}
+        if method == "GET" and "/issues?" in path:
+            return self.issues
+        return {}
+
+
+def test_sync_opens_closes_and_creates_label() -> None:
+    old = {"number": 7, "title": "health: R2 ashby", "updated_at": "2026-10-01T11:00:00Z"}
+    api = FakeApi([old], label=False)
+    health.sync_issues({"R3|vanshb03": "stale"}, NOW, token="t", repo="o/r", api=api)
+    methods = [(m, p) for m, p, _ in api.calls]
+    assert ("POST", "/repos/o/r/labels") in methods
+    assert ("PATCH", "/repos/o/r/issues/7") in methods
+    created = [b for m, p, b in api.calls if m == "POST" and p == "/repos/o/r/issues"]
+    assert created[0]["title"] == "health: R3 vanshb03"
+
+
+def test_sync_comments_daily_and_caps() -> None:
+    stale = {"number": 3, "title": "health: R3 vanshb03", "updated_at": "2026-09-29T00:00:00Z"}
+    api = FakeApi([stale])
+    active = {"R3|vanshb03": "stale", **{f"R6|workday:b{i}": "x" for i in range(12)}}
+    health.sync_issues(active, NOW, token="t", repo="o/r", api=api)
+    comments = [p for m, p, _ in api.calls if p.endswith("/issues/3/comments")]
+    assert comments
+    created = [b["title"] for m, p, b in api.calls if m == "POST" and p == "/repos/o/r/issues"]
+    assert len([t for t in created if t != "health: R0 alert overflow"]) == 9
+    assert "health: R0 alert overflow" in created
+
+
+def test_ntfy_no_topic_no_post(monkeypatch: Any) -> None:
+    monkeypatch.delenv("NTFY_TOPIC", raising=False)
+    health.send_ntfy("hi")  # must not raise or POST
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_health.py`
  Expected: 1 error during collection (no module `intern_radar.health`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/health.py b/src/intern_radar/health.py
new file mode 100644
index 0000000..ef062ba
--- /dev/null
+++ b/src/intern_radar/health.py
@@ -0,0 +1,227 @@
+"""Source health: per-run metrics, alert rules, weekly summary, delivery.
+
+Everything here is derived from merged state (health.json runs, boards.json,
+sightings.json), so a replayed run can never resurrect an alert the other
+runner cleared. Issue numbers are never stored: an alert's issue is found by
+its exact title. Spec: docs/specs/2026-09-30-wider-watcher-design.md, "L4".
+"""
+
+from __future__ import annotations
+
+import json
+import os
+import statistics
+import urllib.error
+import urllib.parse
+import urllib.request
+from datetime import datetime, timedelta
+from typing import Any
+
+from intern_radar.http import USER_AGENT
+
+RUNS_KEPT = 200
+LIST_FAMILIES = ("simplify", "vanshb03", "speedyapply")
+NTFY_EVERY = timedelta(hours=6)
+MAX_OPEN_ISSUES = 10
+LABEL = "health"
+
+
+def empty_health() -> dict[str, Any]:
+    return {"version": 1, "last_run_at": None, "runner": None, "runs": [], "alerts": {},
+            "last_health_ntfy_at": None, "weekly_written_for": None}
+
+
+def _ts(value: str | None) -> datetime | None:
+    return datetime.fromisoformat(value) if value else None
+
+
+def _runs_since(health: dict[str, Any], since: datetime) -> list[dict[str, Any]]:
+    return [r for r in health["runs"] if (_ts(r["at"]) or since) >= since]
+
+
+def evaluate(health: dict[str, Any], boards: dict[str, Any], now: datetime
+             ) -> dict[str, str]:
+    """Active alerts as {"<rule>|<subject>": detail}. Rules R1-R3, R5, R6.
+
+    R4 (search-feed staleness) belongs to phase 3 and is added there.
+    """
+    active: dict[str, str] = {}
+    last_24h = _runs_since(health, now - timedelta(hours=24))
+    prior = [r for r in _runs_since(health, now - timedelta(days=7)) if r not in last_24h]
+    families = {f for r in health["runs"] for f in r.get("families", {})}
+    for fam in sorted(families):
+        had = any(r["families"].get(fam, {}).get("matches", 0) > 0 for r in prior)
+        recent = [r["families"][fam] for r in last_24h if fam in r["families"]]
+        if had and recent and all(e.get("matches", 0) == 0 for e in recent):
+            active[f"R1|{fam}"] = "0 matches across every run in the last 24 h"
+        last_6h = [r["families"][fam] for r in _runs_since(health, now - timedelta(hours=6))
+                   if fam in r["families"]]
+        if len(last_6h) >= 2 and all(e.get("ok", 0) == 0 and e.get("errors", 0) > 0
+                                     for e in last_6h):
+            active[f"R2|{fam}"] = "every job failed on every run in the last 6 h"
+    if health["runs"]:
+        latest = health["runs"][-1]
+        for fam in LIST_FAMILIES:
+            newest = latest.get("families", {}).get(fam, {}).get("newest")
+            if newest and now.date() - datetime.fromisoformat(newest).date() > timedelta(days=7):
+                active[f"R3|{fam}"] = f"newest item is from {newest}"
+        if latest.get("seconds", 0) > 720:
+            active["R5|run-time"] = f"run took {latest['seconds']:.0f} s"
+    last_3h = _runs_since(health, now - timedelta(hours=3))
+    if last_3h and all(r.get("boards_skipped_budget", 0) > 0 for r in last_3h):
+        active["R5|budget"] = "boards skipped for time budget on every run in the last 3 h"
+    for key, row in sorted(boards.items()):
+        if row.get("deep") and not row.get("pinned"):
+            anchor = _ts(row.get("last_deep_crawl")) or _ts(row.get("deep_since"))
+            if anchor and now - anchor > timedelta(hours=36):
+                active[f"R5|deep-crawl {key}"] = "no full crawl in 36 h"
+        if row.get("disabled"):
+            active[f"R6|{key}"] = "disabled after 10 consecutive errors"
+    # Deep boards are listed in the weekly summary, not alerted: seeding marks
+    # dozens at once and each would open an issue (rev-5 ruling, live run).
+    return active
+
+
+def refresh_alerts(health: dict[str, Any], active: dict[str, str], now: datetime) -> None:
+    kept = {k: v for k, v in health.get("alerts", {}).items() if k in active}
+    for key in active:
+        kept.setdefault(key, {"opened_at": now.isoformat()})
+    health["alerts"] = kept
+
+
+def ntfy_due(health: dict[str, Any], active: dict[str, str], now: datetime,
+             enabled: bool) -> bool:
+    if not enabled or not active:
+        return False
+    last = _ts(health.get("last_health_ntfy_at"))
+    return last is None or now - last >= NTFY_EVERY
+
+
+def iso_week(now: datetime) -> str:
+    year, week, _ = now.isocalendar()
+    return f"{year}-W{week:02d}"
+
+
+def head_starts(sightings: dict[str, Any]) -> dict[str, tuple[int, float]]:
+    """{family: (n, median hours that family saw a posting before simplify)}."""
+    gaps: dict[str, list[float]] = {}
+    for fams in sightings.get("keys", {}).values():
+        base = _ts(fams.get("simplify"))
+        if base is None:
+            continue
+        for fam, stamp in fams.items():
+            if fam == "simplify":
+                continue
+            seen = _ts(stamp)
+            if seen is not None:
+                gaps.setdefault(fam, []).append((base - seen).total_seconds() / 3600)
+    return {f: (len(v), statistics.median(v)) for f, v in gaps.items()}
+
+
+def weekly_markdown(health: dict[str, Any], boards: dict[str, Any],
+                    sightings: dict[str, Any], now: datetime) -> str:
+    week_runs = _runs_since(health, now - timedelta(days=7))
+    matches: dict[str, int] = {}
+    for run in week_runs:
+        for fam, entry in run.get("families", {}).items():
+            matches[fam] = matches.get(fam, 0) + int(entry.get("matches", 0))
+    unique: dict[str, int] = {}
+    for fams in sightings.get("keys", {}).values():
+        if len(fams) == 1:
+            (only,) = fams
+            unique[only] = unique.get(only, 0) + 1
+    lines = [f"# intern-radar weekly health — {iso_week(now)}", "",
+             f"Runs this week: {len(week_runs)}", "",
+             "| family | matched postings | unique finds (30 d) |", "|---|---|---|"]
+    for fam in sorted(set(matches) | set(unique)):
+        lines.append(f"| {fam} | {matches.get(fam, 0)} | {unique.get(fam, 0)} |")
+    lines += ["", "## Head start over simplify (median hours, n ≥ 5)", ""]
+    for fam, (n, hours) in sorted(head_starts(sightings).items()):
+        if n >= 5:
+            lines.append(f"- {fam}: {hours:+.1f} h (n={n})")
+    for label, flag in (("Disabled", "disabled"), ("Deep (consider pinning)", "deep"),
+                        ("Needs config", "needs_config")):
+        keys = sorted(k for k, r in boards.items() if r.get(flag))
+        lines += ["", f"## {label} boards ({len(keys)})", ""] + [f"- {k}" for k in keys]
+    return "\n".join(lines) + "\n"
+
+
+# --- delivery (after a successful push) -------------------------------------
+
+
+def _api(method: str, path: str, token: str, body: Any = None) -> Any:
+    request = urllib.request.Request(
+        f"https://api.github.com{path}",
+        data=json.dumps(body).encode("utf-8") if body is not None else None,
+        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}",
+                 "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
+        method=method,
+    )
+    with urllib.request.urlopen(request, timeout=30) as response:
+        raw = response.read()
+    return json.loads(raw) if raw else None
+
+
+def issue_title(key: str) -> str:
+    rule, subject = key.split("|", 1)
+    return f"health: {rule} {subject}"
+
+
+def sync_issues(active: dict[str, str], now: datetime, *, token: str, repo: str,
+                api: Any = _api) -> None:
+    """Open, comment on, and close health issues to match `active`."""
+    try:
+        api("GET", f"/repos/{repo}/labels/{LABEL}", token)
+    except urllib.error.HTTPError as e:
+        if e.code != 404:
+            raise
+        api("POST", f"/repos/{repo}/labels", token, {"name": LABEL, "color": "d73a4a"})
+    open_issues = api("GET", f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=100",
+                      token) or []
+    by_title = {i["title"]: i for i in open_issues}
+    wanted = {issue_title(k): (k, v) for k, v in active.items()}
+    overflow_title = "health: R0 alert overflow"
+    for title, issue in by_title.items():
+        if title not in wanted and title != overflow_title:
+            api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token,
+                {"body": f"Cleared at {now.isoformat()}."})
+            api("PATCH", f"/repos/{repo}/issues/{issue['number']}", token, {"state": "closed"})
+    open_count = sum(1 for t in by_title if t in wanted)
+    extra: list[str] = []
+    for title, (_key, detail) in sorted(wanted.items()):
+        issue = by_title.get(title)
+        if issue is not None:
+            updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
+            if now - updated >= timedelta(hours=24):
+                api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token,
+                    {"body": f"Still active at {now.isoformat()}: {detail}"})
+            continue
+        if open_count >= MAX_OPEN_ISSUES:
+            extra.append(f"- {title}: {detail}")
+            continue
+        api("POST", f"/repos/{repo}/issues", token,
+            {"title": title, "body": f"{detail}\n\nOpened by intern-radar health at "
+             f"{now.isoformat()}. Closes automatically when the condition clears.",
+             "labels": [LABEL]})
+        open_count += 1
+    overflow = by_title.get(overflow_title)
+    if extra:
+        body = "Alerts beyond the open-issue cap:\n" + "\n".join(extra)
+        if overflow is None:
+            api("POST", f"/repos/{repo}/issues", token,
+                {"title": overflow_title, "body": body, "labels": [LABEL]})
+        else:
+            api("PATCH", f"/repos/{repo}/issues/{overflow['number']}", token, {"body": body})
+    elif overflow is not None:
+        api("PATCH", f"/repos/{repo}/issues/{overflow['number']}", token, {"state": "closed"})
+
+
+def send_ntfy(text: str, topic: str | None = None) -> None:
+    topic = topic if topic is not None else os.environ.get("NTFY_TOPIC", "")
+    if not topic:
+        return  # no topic => no POST: https://ntfy.sh/None is a public topic
+    request = urllib.request.Request(
+        f"https://ntfy.sh/{urllib.parse.quote(topic, safe='')}",
+        data=text.encode("utf-8"), headers={"User-Agent": USER_AGENT}, method="POST")
+    with urllib.request.urlopen(request, timeout=15):
+        pass
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `184 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_health.py src/intern_radar/health.py
git commit -m "feat(health): alert rules, weekly summary, issue sync, ntfy" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Change set and idempotent apply (`changeset.py`)

**Files:** `tests/test_changeset.py`, `src/intern_radar/changeset.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_changeset.py b/tests/test_changeset.py
new file mode 100644
index 0000000..7176e55
--- /dev/null
+++ b/tests/test_changeset.py
@@ -0,0 +1,139 @@
+"""Replay semantics: two runners' change sets merge without loss or doubles."""
+
+import json
+from datetime import UTC, datetime, timedelta
+from pathlib import Path
+
+from intern_radar import boards
+from intern_radar.changeset import BoardUpdate, ChangeSet, apply
+from intern_radar.models import Posting, canon_key
+
+NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
+
+
+def post(n: int, url: str | None = None) -> Posting:
+    return Posting(key=f"simplify:{n}", source="simplify", company=f"Co{n}", title="SWE Intern",
+                   url=url or f"https://x.example/{n}", terms=("Summer 2027",))
+
+
+def cs_for(posts: list[Posting], now: datetime = NOW, runner: str = "mac") -> ChangeSet:
+    day = now.date().isoformat()
+    marks = {}
+    for p in posts:
+        marks[p.key] = day
+        marks[p.url_key] = day
+    return ChangeSet(now=now, runner=runner, new_postings=posts, seen_marks=marks, cache=posts,
+                     canon_add={canon_key(p.url): p.url_key for p in posts},
+                     sightings_add={canon_key(p.url): {p.source: now.isoformat()} for p in posts},
+                     run_entry={"at": now.isoformat(), "runner": runner, "seconds": 1.0,
+                                "families": {}, "boards_polled": 0, "boards_skipped_budget": 0})
+
+
+def read(tmp: Path, name: str) -> object:
+    return json.loads((tmp / name).read_text(encoding="utf-8"))
+
+
+def test_competing_runs_merge_without_loss_or_duplicates(tmp_path: Path) -> None:
+    mac = cs_for([post(1), post(2)], runner="mac")
+    actions = cs_for([post(2), post(3)], now=NOW + timedelta(minutes=1), runner="actions")
+    assert [p.key for p in apply(tmp_path, mac).appended] == ["simplify:1", "simplify:2"]
+    # Actions' push was rejected; it resets to origin (= Mac's state) and replays.
+    replay = apply(tmp_path, actions)
+    assert [p.key for p in replay.appended] == ["simplify:3"]  # 2 already recorded by Mac
+    inbox = read(tmp_path, "inbox.json")
+    assert [e["url"] for e in inbox] == [f"https://x.example/{n}" for n in (1, 2, 3)]
+    seen = read(tmp_path, "seen.json")["seen"]
+    assert all(f"simplify:{n}" in seen for n in (1, 2, 3))
+    cache = read(tmp_path, "postings.json")
+    assert set(cache) == {f"url:https://x.example/{n}" for n in (1, 2, 3)}
+    health = read(tmp_path, "health.json")
+    assert [r["runner"] for r in health["runs"]] == ["mac", "actions"]
+    assert health["runner"] == "actions"
+
+
+def test_replaying_the_same_changeset_is_idempotent(tmp_path: Path) -> None:
+    cs = cs_for([post(1)])
+    apply(tmp_path, cs)
+    assert apply(tmp_path, cs).appended == []
+    assert len(read(tmp_path, "inbox.json")) == 1
+
+
+def test_canon_variant_recorded_by_other_runner_is_not_appended(tmp_path: Path) -> None:
+    a = post(1, "https://t.wd1.myworkdayjobs.com/en-US/Site/job/x_1")
+    b = Posting(key="workday:t.wd1/Site:/job/x_1", source="workday", company="t",
+                title="SWE Intern", url="https://t.wd1.myworkdayjobs.com/Site/job/x_1")
+    apply(tmp_path, cs_for([a]))
+    assert apply(tmp_path, cs_for([b])).appended == []
+
+
+def test_seen_takes_max_and_prune_runs_on_merged_state(tmp_path: Path) -> None:
+    (tmp_path / "seen.json").write_text(json.dumps({"version": 1, "seen": {
+        "simplify:old": "2025-01-01", "url:https://x.example/old": "2025-01-01",
+        "simplify:1": "2026-09-01"}}), encoding="utf-8")
+    (tmp_path / "inbox.json").write_text(json.dumps([
+        {"url": "https://x.example/old", "company": "Old", "title": "t", "locations": "",
+         "source": "simplify", "added": "2025-01-01"}]), encoding="utf-8")
+    cs = cs_for([])
+    cs.seen_marks = {"simplify:1": "2026-08-01"}
+    apply(tmp_path, cs)
+    seen = read(tmp_path, "seen.json")["seen"]
+    assert seen["simplify:1"] == "2026-09-01"          # max, not overwrite
+    assert "simplify:old" not in seen                   # 365-day prune
+    assert read(tmp_path, "inbox.json") == []           # inbox follows the prune
+
+
+def test_board_merge_rules(tmp_path: Path) -> None:
+    key = boards.board_key("workday", "t.wd1/S")
+    cs = cs_for([])
+    cs.board_rows_new = {key: boards.new_row("workday", "t.wd1/S", pinned=False,
+                                             discovered_via="simplify", now=NOW.isoformat())}
+    cs.board_updates = {key: BoardUpdate(polled=True, ok=False, deep=True)}
+    for _ in range(10):
+        apply(tmp_path, cs)
+    row = read(tmp_path, "boards.json")["boards"][key]
+    assert row["consecutive_errors"] == 10 and row["disabled"] and row["deep"]
+    cs.board_updates = {key: BoardUpdate(reenable=True, matched=True)}
+    apply(tmp_path, cs)
+    row = read(tmp_path, "boards.json")["boards"][key]
+    assert not row["disabled"] and row["consecutive_errors"] == 0 and row["last_match"]
+
+
+def test_pins_follow_config(tmp_path: Path) -> None:
+    key = boards.board_key("greenhouse", "stripe")
+    cs = cs_for([])
+    cs.board_rows_new = {key: boards.new_row("greenhouse", "stripe", pinned=True,
+                                             discovered_via=None, now=NOW.isoformat())}
+    cs.pinned_keys = {key}
+    apply(tmp_path, cs)
+    assert read(tmp_path, "boards.json")["boards"][key]["pinned"] is True
+    apply(tmp_path, cs_for([]))  # removed from config: unpinned, not deleted
+    assert read(tmp_path, "boards.json")["boards"][key]["pinned"] is False
+
+
+def test_closed_alert_is_not_resurrected_by_replay(tmp_path: Path) -> None:
+    health = {"version": 1, "last_run_at": None, "runner": None, "runs": [],
+              "alerts": {"R3|vanshb03": {"opened_at": "2026-09-01T00:00:00+00:00"}},
+              "last_health_ntfy_at": None, "weekly_written_for": None}
+    (tmp_path / "health.json").write_text(json.dumps(health), encoding="utf-8")
+    result = apply(tmp_path, cs_for([]))
+    assert "R3|vanshb03" not in result.active_alerts
+    assert read(tmp_path, "health.json")["alerts"] == {}
+
+
+def test_health_ntfy_decided_on_merged_state(tmp_path: Path) -> None:
+    cs = cs_for([])
+    cs.run_entry["seconds"] = 900  # R5 run-time alert
+    first = apply(tmp_path, cs, ntfy_enabled=True)
+    assert first.ntfy_due and "R5|run-time" in first.active_alerts
+    later = cs_for([], now=NOW + timedelta(hours=1))
+    later.run_entry["seconds"] = 900
+    assert not apply(tmp_path, later, ntfy_enabled=True).ntfy_due  # within 6 h
+
+
+def test_weekly_written_once_per_iso_week(tmp_path: Path) -> None:
+    apply(tmp_path, cs_for([]))
+    md = tmp_path / "health-weekly.md"
+    assert md.exists()
+    md.write_text("sentinel", encoding="utf-8")
+    apply(tmp_path, cs_for([], now=NOW + timedelta(hours=2)))
+    assert md.read_text(encoding="utf-8") == "sentinel"
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_changeset.py`
  Expected: 1 error during collection (no module `intern_radar.changeset`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/changeset.py b/src/intern_radar/changeset.py
new file mode 100644
index 0000000..3704bcd
--- /dev/null
+++ b/src/intern_radar/changeset.py
@@ -0,0 +1,216 @@
+"""A run's state changes, applied idempotently to whatever is on disk.
+
+Two runners (the owner's Mac and GitHub Actions) commit to the same repo. A
+run never writes files directly: it builds a ChangeSet, and `apply` merges it
+into the files as they are *now*. When a push is rejected the runner resets
+to origin and calls `apply` again with the same ChangeSet, so nothing either
+runner found is lost. Prunes, evictions and alert state are re-derived from
+the merged data on every apply, never replayed.
+
+Spec: docs/specs/2026-09-30-wider-watcher-design.md, "Write protocol".
+"""
+
+from __future__ import annotations
+
+import json
+from dataclasses import dataclass, field
+from datetime import datetime, timedelta
+from pathlib import Path
+from typing import Any
+
+from intern_radar import boards as boards_mod
+from intern_radar import health as health_mod
+from intern_radar.models import Posting, canon_key, normalize_url
+from intern_radar.state import PRUNE_AFTER_DAYS, SeenStore
+
+SIGHTINGS_DAYS = 30
+DATA_FILES = ("seen.json", "inbox.json", "postings.json", "boards.json", "canon.json",
+              "sightings.json", "health.json", "health-weekly.md")
+
+
+@dataclass
+class BoardUpdate:
+    polled: bool = False
+    ok: bool = False
+    matched: bool = False
+    deep: bool = False
+    deep_crawled: bool = False
+    reenable: bool = False
+
+
+@dataclass
+class ChangeSet:
+    now: datetime
+    runner: str
+    new_postings: list[Posting] = field(default_factory=list)
+    seen_marks: dict[str, str] = field(default_factory=dict)
+    cache: list[Posting] = field(default_factory=list)
+    canon_add: dict[str, str] = field(default_factory=dict)
+    sightings_add: dict[str, dict[str, str]] = field(default_factory=dict)
+    board_rows_new: dict[str, dict[str, Any]] = field(default_factory=dict)
+    board_updates: dict[str, BoardUpdate] = field(default_factory=dict)
+    pinned_keys: set[str] = field(default_factory=set)
+    gh_custom_add: dict[str, str] = field(default_factory=dict)
+    gh_pending_add: dict[str, str] = field(default_factory=dict)
+    gh_pending_done: set[str] = field(default_factory=set)
+    run_entry: dict[str, Any] | None = None
+
+
+@dataclass
+class ApplyResult:
+    appended: list[Posting]
+    active_alerts: dict[str, str]
+    ntfy_due: bool
+
+
+def _read_json(path: Path, default: Any) -> Any:
+    if not path.exists():
+        return default
+    with path.open("r", encoding="utf-8-sig") as f:
+        return json.load(f)
+
+
+def _write_json(path: Path, payload: Any, *, sort_keys: bool = True) -> None:
+    path.parent.mkdir(parents=True, exist_ok=True)
+    with path.open("w", encoding="utf-8", newline="\n") as f:
+        json.dump(payload, f, indent=1, ensure_ascii=False, sort_keys=sort_keys)
+        f.write("\n")
+
+
+def _max_ts(a: str | None, b: str | None) -> str | None:
+    return max((x for x in (a, b) if x), default=None)
+
+
+def _entry(p: Posting, today: str) -> dict[str, str]:
+    return {"url": p.url, "company": p.company, "title": p.title,
+            "locations": ", ".join(p.locations), "source": p.source, "added": today}
+
+
+def apply(data_dir: Path, cs: ChangeSet, *, ntfy_enabled: bool = False) -> ApplyResult:
+    now = cs.now
+    now_iso = now.isoformat()
+    today = now.date().isoformat()
+
+    store = SeenStore.load(data_dir / "seen.json")
+    prior_seen = dict(store.seen)
+    raw_inbox = _read_json(data_dir / "inbox.json", [])
+    inbox: list[dict[str, str]] = [
+        {str(k): str(v) for k, v in e.items()} for e in raw_inbox if isinstance(e, dict)
+    ] if isinstance(raw_inbox, list) else []
+    cache: dict[str, Any] = _read_json(data_dir / "postings.json", {})
+    registry: dict[str, Any] = _read_json(data_dir / "boards.json",
+                                          boards_mod.empty_registry())
+    canon: dict[str, str] = _read_json(data_dir / "canon.json",
+                                       {"version": 1, "canon": {}})["canon"]
+    sightings: dict[str, Any] = _read_json(data_dir / "sightings.json",
+                                           {"version": 1, "keys": {}})
+    health: dict[str, Any] = _read_json(data_dir / "health.json", health_mod.empty_health())
+
+    # Inbox: append only postings no runner has recorded yet.
+    urls = {e.get("url", "") for e in inbox}
+    appended: list[Posting] = []
+    for p in cs.new_postings:
+        if (p.url in urls or p.key in prior_seen or p.url_key in prior_seen
+                or canon.get(canon_key(p.url), p.url_key) != p.url_key):
+            continue
+        inbox.append(_entry(p, today))
+        urls.add(p.url)
+        appended.append(p)
+
+    for key, day in cs.seen_marks.items():
+        store.seen[key] = max(day, store.seen.get(key, ""))
+    for p in cs.cache:
+        cache[p.url_key] = {"company": p.company, "title": p.title, "url": p.url,
+                            "locations": list(p.locations), "posted_at": p.posted_at,
+                            "source": p.source}
+    for ck, stored in cs.canon_add.items():
+        canon.setdefault(ck, stored)
+    for ck, fams in cs.sightings_add.items():
+        slot = sightings["keys"].setdefault(ck, {})
+        for fam, stamp in fams.items():
+            slot[fam] = min(stamp, slot.get(fam, stamp))
+
+    # Boards.
+    rows: dict[str, Any] = registry["boards"]
+    for key, row in cs.board_rows_new.items():
+        if key in rows:
+            rows[key]["first_seen"] = min(rows[key]["first_seen"], row["first_seen"])
+        else:
+            rows[key] = dict(row)
+    for key, row in rows.items():
+        row["pinned"] = key in cs.pinned_keys
+    for key, up in cs.board_updates.items():
+        found = rows.get(key)
+        if found is None:
+            continue
+        row = found
+        if up.polled:
+            row["last_polled"] = _max_ts(row.get("last_polled"), now_iso)
+            if up.ok:
+                row["last_ok"] = _max_ts(row.get("last_ok"), now_iso)
+                row["consecutive_errors"] = 0
+            else:
+                row["consecutive_errors"] = int(row.get("consecutive_errors", 0)) + 1
+        if up.matched:
+            row["last_match"] = _max_ts(row.get("last_match"), now_iso)
+        if up.deep and not row.get("deep"):
+            row["deep"], row["deep_since"] = True, now_iso
+        if up.deep_crawled:
+            row["last_deep_crawl"] = _max_ts(row.get("last_deep_crawl"), now_iso)
+        if up.reenable:
+            row["disabled"], row["consecutive_errors"] = False, 0
+        elif row["consecutive_errors"] >= boards_mod.DISABLE_AFTER_ERRORS:
+            row["disabled"] = True
+    for host, board in cs.gh_custom_add.items():
+        registry["gh_custom"].setdefault(host, board)
+    pending = dict(registry.get("gh_custom_pending") or {})
+    for host, token in cs.gh_pending_add.items():
+        if host not in registry["gh_custom"]:
+            pending.setdefault(host, token)
+    for host in cs.gh_pending_done | set(registry["gh_custom"]):
+        pending.pop(host, None)
+    registry["gh_custom_pending"] = pending
+
+    # Health run record.
+    if cs.run_entry is not None:
+        health["runs"].append(cs.run_entry)
+        health["runs"] = sorted(health["runs"], key=lambda r: r["at"])[-health_mod.RUNS_KEPT:]
+        if cs.run_entry["at"] >= (health.get("last_run_at") or ""):
+            health["last_run_at"], health["runner"] = cs.run_entry["at"], cs.runner
+
+    # Derived on the merged state: prunes, evictions, alerts, weekly, ntfy.
+    before = set(store.seen)
+    store.prune(today)
+    pruned = before - set(store.seen)  # only keys that aged out, never merely absent ones
+    inbox = [e for e in inbox if "url:" + normalize_url(e.get("url", "")) not in pruned]
+    canon = {ck: stored for ck, stored in canon.items() if stored not in pruned}
+    cutoff = (now - timedelta(days=SIGHTINGS_DAYS)).isoformat()
+    sightings["keys"] = {ck: f for ck, f in sightings["keys"].items()
+                         if max(f.values()) >= cutoff}
+    boards_mod.prune_and_evict(registry, now)
+    active = health_mod.evaluate(health, rows, now)
+    health_mod.refresh_alerts(health, active, now)
+    due = health_mod.ntfy_due(health, active, now, ntfy_enabled)
+    if due:
+        health["last_health_ntfy_at"] = now_iso
+    week = health_mod.iso_week(now)
+    if health.get("weekly_written_for") != week:
+        (data_dir / "health-weekly.md").write_text(
+            health_mod.weekly_markdown(health, rows, sightings, now),
+            encoding="utf-8", newline="\n")
+        health["weekly_written_for"] = week
+
+    store.save()
+    # inbox.json and postings.json keep their pre-existing layouts (entry key
+    # order), so the first run under this code doesn't rewrite every line.
+    _write_json(data_dir / "inbox.json", inbox, sort_keys=False)
+    _write_json(data_dir / "postings.json", dict(sorted(cache.items())), sort_keys=False)
+    _write_json(data_dir / "boards.json", registry)
+    _write_json(data_dir / "canon.json", {"version": 1, "canon": canon})
+    _write_json(data_dir / "sightings.json", sightings)
+    _write_json(data_dir / "health.json", health)
+    return ApplyResult(appended=appended, active_alerts=active, ntfy_due=due)
+
+
+__all__ = ["DATA_FILES", "PRUNE_AFTER_DAYS", "ApplyResult", "BoardUpdate", "ChangeSet",
+           "apply"]
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `193 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_changeset.py src/intern_radar/changeset.py
git commit -m "feat(changeset): replayable change sets applied to merged state" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Git sync and the Actions skip check

**Files:** `tests/test_gitsync.py`, `tests/test_skipcheck.py`, `src/intern_radar/gitsync.py`, `src/intern_radar/skipcheck.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_gitsync.py b/tests/test_gitsync.py
new file mode 100644
index 0000000..e5033fc
--- /dev/null
+++ b/tests/test_gitsync.py
@@ -0,0 +1,35 @@
+import subprocess
+from pathlib import Path
+from typing import Any
+
+from intern_radar.gitsync import GitSync
+
+
+class FakeGit:
+    def __init__(self, staged: bool, push_ok: bool) -> None:
+        self.staged, self.push_ok, self.cmds = staged, push_ok, []
+
+    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
+        self.cmds.append(cmd[3:])
+        args, rc, out = cmd[3:], 0, ""
+        if args[:2] == ["rev-parse", "--abbrev-ref"]:
+            out = "main\n"
+        elif args[:2] == ["diff", "--cached"]:
+            rc = 1 if self.staged else 0
+        elif args[0] == "push":
+            rc = 0 if self.push_ok else 1
+        return subprocess.CompletedProcess(cmd, rc, out, "")
+
+
+def test_nothing_staged_counts_as_pushed() -> None:
+    fake = FakeGit(staged=False, push_ok=False)
+    assert GitSync(Path("/r"), fake).commit_push(["data"], "m")
+    assert not any(c[0] == "push" for c in fake.cmds)
+
+
+def test_rejected_push_returns_false_and_reset_targets_origin() -> None:
+    fake = FakeGit(staged=True, push_ok=False)
+    g = GitSync(Path("/r"), fake)
+    assert not g.commit_push(["data"], "m")
+    g.reset_to_origin()
+    assert ["reset", "-q", "--hard", "origin/main"] in fake.cmds
diff --git a/tests/test_skipcheck.py b/tests/test_skipcheck.py
new file mode 100644
index 0000000..073bda9
--- /dev/null
+++ b/tests/test_skipcheck.py
@@ -0,0 +1,16 @@
+import json
+from datetime import UTC, datetime, timedelta
+from pathlib import Path
+
+from intern_radar import skipcheck
+
+NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
+
+
+def test_skipcheck(tmp_path: Path) -> None:
+    h = tmp_path / "health.json"
+    assert not skipcheck.should_skip(h, NOW)
+    h.write_text(json.dumps({"last_run_at": (NOW - timedelta(minutes=20)).isoformat()}),
+                 encoding="utf-8")
+    assert skipcheck.should_skip(h, NOW)
+    assert not skipcheck.should_skip(h, NOW + timedelta(minutes=40))
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_gitsync.py tests/test_skipcheck.py`
  Expected: 2 errors during collection (no modules `gitsync`, `skipcheck`).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/gitsync.py b/src/intern_radar/gitsync.py
new file mode 100644
index 0000000..a4f7091
--- /dev/null
+++ b/src/intern_radar/gitsync.py
@@ -0,0 +1,47 @@
+"""The only place the watcher runs git.
+
+The run itself owns sync and push (both runners): reset to origin, apply the
+change set, commit data/, push; on rejection reset and let the caller
+re-apply. A run never merges; replay replaces merging.
+"""
+
+from __future__ import annotations
+
+import subprocess
+from collections.abc import Callable, Sequence
+from pathlib import Path
+
+Runner = Callable[..., "subprocess.CompletedProcess[str]"]
+
+
+class GitError(Exception):
+    pass
+
+
+class GitSync:
+    def __init__(self, repo: Path, runner: Runner = subprocess.run) -> None:
+        self.repo = repo
+        self._run = runner
+
+    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
+        result = self._run(["git", "-C", str(self.repo), *args],
+                           capture_output=True, text=True, timeout=120, check=False)
+        if check and result.returncode != 0:
+            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
+        return result
+
+    def branch(self) -> str:
+        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
+
+    def reset_to_origin(self) -> None:
+        branch = self.branch()
+        self._git("fetch", "-q", "origin", branch)
+        self._git("reset", "-q", "--hard", f"origin/{branch}")
+
+    def commit_push(self, paths: Sequence[str], message: str) -> bool:
+        """True when origin holds this run's state (pushed, or nothing to push)."""
+        self._git("add", "--", *paths)
+        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
+            return True
+        self._git("commit", "-q", "-m", message)
+        return self._git("push", "-q", "origin", "HEAD", check=False).returncode == 0
diff --git a/src/intern_radar/skipcheck.py b/src/intern_radar/skipcheck.py
new file mode 100644
index 0000000..5afe572
--- /dev/null
+++ b/src/intern_radar/skipcheck.py
@@ -0,0 +1,40 @@
+"""Actions backup gate: skip this run when the Mac runner is active.
+
+Prints `skip=true|false` and appends it to $GITHUB_OUTPUT. The Mac is
+"active" when the last committed run started within SKIP_WINDOW.
+
+    python -m intern_radar.skipcheck data/health.json
+"""
+
+from __future__ import annotations
+
+import json
+import os
+import sys
+from datetime import UTC, datetime, timedelta
+from pathlib import Path
+
+SKIP_WINDOW = timedelta(minutes=50)
+
+
+def should_skip(health_path: Path, now: datetime) -> bool:
+    if not health_path.exists():
+        return False
+    last = json.loads(health_path.read_text(encoding="utf-8")).get("last_run_at")
+    return bool(last) and now - datetime.fromisoformat(last) < SKIP_WINDOW
+
+
+def main(argv: list[str] | None = None) -> int:
+    args = argv if argv is not None else sys.argv[1:]
+    skip = should_skip(Path(args[0] if args else "data/health.json"), datetime.now(tz=UTC))
+    line = f"skip={'true' if skip else 'false'}"
+    print(line)
+    out = os.environ.get("GITHUB_OUTPUT")
+    if out:
+        with open(out, "a", encoding="utf-8") as f:
+            f.write(line + "\n")
+    return 0
+
+
+if __name__ == "__main__":
+    sys.exit(main())
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `196 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_gitsync.py tests/test_skipcheck.py src/intern_radar/gitsync.py src/intern_radar/skipcheck.py
git commit -m "feat(git): gitsync replay primitives and Actions skip check" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Main loop: poll, dedupe, discover, apply, push with replay, notify after push

**Files:** `tests/test_main.py`, `tests/test_main_wider.py`, `src/intern_radar/config.py`, `src/intern_radar/filters.py`, `src/intern_radar/notify.py`, `src/intern_radar/main.py`

- [ ] **Step 1: Tests first.** Apply:

```diff
diff --git a/tests/test_main.py b/tests/test_main.py
index 4393f4f..68a019e 100644
--- a/tests/test_main.py
+++ b/tests/test_main.py
@@ -115,11 +115,11 @@ def test_successful_run_writes_postings_cache(
     assert any(entry["company"] == "Co1" for entry in cache.values())
 
 
-def test_notify_failure_leaves_state_unsaved(
+def test_notify_failure_is_best_effort_and_state_saves(
     monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
 ) -> None:
-    # github_issues enabled but no creds: the run must fail without marking
-    # anything seen, so the next run retries delivery.
+    # Notifications go out only after state is committed, and never block it:
+    # inbox.json is the durable channel (spec: Write protocol, step 5).
     config = tmp_path / "config.toml"
     config.write_text(CONFIG.replace("github_issues = false", "github_issues = true"),
                       encoding="utf-8")
@@ -129,8 +129,10 @@ def test_notify_failure_leaves_state_unsaved(
     monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
     monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
     monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
-    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
-    assert json.loads(state.read_text(encoding="utf-8"))["seen"] == {}
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
+    assert "simplify:1" in json.loads(state.read_text(encoding="utf-8"))["seen"]
+    inbox = json.loads((tmp_path / "inbox.json").read_text(encoding="utf-8"))
+    assert [e["url"] for e in inbox] == ["https://x.example/1"]
 
 
 def test_bootstrap_refuses_partial_failure(
@@ -206,7 +208,7 @@ def test_discord_failure_is_best_effort_when_issue_succeeded(
     assert "simplify:1" in json.loads(state.read_text(encoding="utf-8"))["seen"]
 
 
-def test_discord_failure_is_fatal_when_it_is_the_only_channel(
+def test_discord_failure_is_non_fatal_even_as_the_only_channel(
     monkeypatch: pytest.MonkeyPatch, paths: tuple[Path, Path],
 ) -> None:
     from intern_radar.notify import NotifyError
@@ -219,5 +221,5 @@ def test_discord_failure_is_fatal_when_it_is_the_only_channel(
 
     monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [simplify_posting(1)])
     monkeypatch.setattr(main_mod, "notify_discord", dead_webhook)
-    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 1
-    assert json.loads(state.read_text(encoding="utf-8"))["seen"] == {}
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False) == 0
+    assert "simplify:1" in json.loads(state.read_text(encoding="utf-8"))["seen"]
diff --git a/tests/test_main_wider.py b/tests/test_main_wider.py
new file mode 100644
index 0000000..dbda4dc
--- /dev/null
+++ b/tests/test_main_wider.py
@@ -0,0 +1,259 @@
+"""End-to-end runs of main.run: discovery, seeding, canon, runners, replay."""
+
+import json
+from datetime import UTC, datetime, timedelta
+from pathlib import Path
+from typing import Any
+
+import pytest
+
+import intern_radar.main as main_mod
+from intern_radar.models import Posting
+
+NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
+CONFIG = """
+[filters]
+terms = ["Summer 2027"]
+title_require_any = ["intern"]
+
+[sources]
+simplify = true
+
+[notify]
+github_issues = true
+"""
+
+
+def sp(n: int, url: str) -> Posting:
+    return Posting(key=f"simplify:{n}", source="simplify", company=f"Co{n}", title="SWE Intern",
+                   url=url, terms=("Summer 2027",), category="Software")
+
+
+@pytest.fixture
+def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
+    for var in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "DISCORD_WEBHOOK_URL", "NTFY_TOPIC",
+                "RADAR_GITHUB_ISSUES", "RADAR_RUNNER"):
+        monkeypatch.delenv(var, raising=False)
+    monkeypatch.setattr(main_mod, "resolve_gh_custom", lambda token: "optiverus")
+    config = tmp_path / "config.toml"
+    config.write_text(CONFIG, encoding="utf-8")
+    state = tmp_path / "seen.json"
+    state.write_text('{"version": 1, "seen": {}}', encoding="utf-8")
+    return config, state
+
+
+def read(tmp: Path, name: str) -> Any:
+    return json.loads((tmp / name).read_text(encoding="utf-8"))
+
+
+def test_first_boards_run_seeds_from_inbox_and_discovers(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    (state.parent / "inbox.json").write_text(json.dumps([
+        {"url": "https://nvidia.wd5.myworkdayjobs.com/en-US/Ext/job/US/Intern_1", "company": "n",
+         "title": "t", "locations": "", "source": "simplify", "added": "2026-09-01"},
+        {"url": "https://www.optiver.com/job?gh_jid=8027900", "company": "o", "title": "t",
+         "locations": "", "source": "simplify", "added": "2026-09-01"},
+    ]), encoding="utf-8")
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
+        sp(1, "https://job-boards.greenhouse.io/stripe/jobs/1")])
+    monkeypatch.setattr(main_mod, "fetch_workday_info", lambda b, d, t: ([], 0))
+    monkeypatch.setattr(main_mod, "fetch_greenhouse", lambda b: [])
+    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): None)
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
+    reg = read(state.parent, "boards.json")
+    expected = {"workday:nvidia.wd5/ext", "greenhouse:stripe", "greenhouse:optiverus"}
+    assert expected <= set(reg["boards"])
+    assert reg["gh_custom"] == {"www.optiver.com": "optiverus"}
+    canon = read(state.parent, "canon.json")["canon"]
+    assert "url:https://nvidia.wd5.myworkdayjobs.com/ext/job/us/intern_1" in canon
+
+
+def test_canon_variant_refreshes_stored_key_and_is_not_new(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    stored = "url:https://t.wd1.myworkdayjobs.com/en-us/site/job/x_1"
+    state.write_text(json.dumps({"version": 1, "seen": {stored: "2026-09-01"}}), encoding="utf-8")
+    (state.parent / "canon.json").write_text(json.dumps({"version": 1, "canon": {
+        "url:https://t.wd1.myworkdayjobs.com/site/job/x_1": stored}}), encoding="utf-8")
+    (state.parent / "boards.json").write_text(json.dumps(
+        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
+        sp(9, "https://t.wd1.myworkdayjobs.com/Site/job/x_1")])
+    monkeypatch.setattr(main_mod, "fetch_workday_info", lambda b, d, t: ([], 0))
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
+    assert read(state.parent, "seen.json")["seen"][stored] == "2026-10-01"
+    assert not (state.parent / "inbox.json").exists() or read(state.parent, "inbox.json") == []
+
+
+def test_discovered_workday_board_goes_deep(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    (state.parent / "boards.json").write_text(json.dumps(
+        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
+    depths: list[int] = []
+
+    def wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
+        depths.append(depth)
+        return [], 747
+
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
+        sp(1, "https://acme.wd5.myworkdayjobs.com/Ext/job/US/Intern_1")])
+    monkeypatch.setattr(main_mod, "fetch_workday_info", wd)
+    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): None)
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)  # discovers
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
+    row = read(state.parent, "boards.json")["boards"]["workday:acme.wd5/ext"]
+    assert depths[0] == 100 and row["deep"]
+    issued: list[Any] = []
+    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): issued.append(p))
+
+    def deep_wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
+        depths.append(depth)
+        post = Posting(key="workday:acme:/job/999", source="workday", company="acme",
+                       title="SWE Intern", url="https://acme.wd5.myworkdayjobs.com/Ext/job/999")
+        return [post], 747
+
+    monkeypatch.setattr(main_mod, "fetch_workday_info", deep_wd)
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=60))
+    assert depths[-1] == 1000  # daily full crawl for a deep board
+    assert issued == []        # its first full crawl is quiet
+
+
+def test_mac_runner_uses_ntfy_not_issues(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    monkeypatch.setenv("RADAR_GITHUB_ISSUES", "0")
+    monkeypatch.setenv("NTFY_TOPIC", "t")
+    sent: list[str] = []
+    monkeypatch.setattr(main_mod.health_mod, "send_ntfy",
+                        lambda text, topic=None: sent.append(text))
+    monkeypatch.setattr(main_mod, "notify_github_issue",
+                        lambda p, f=(): (_ for _ in ()).throw(AssertionError("no issues on Mac")))
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW) == 0
+    assert "intern-radar: 1 new postings" in sent
+
+
+class ReplayGit:
+    """First push is rejected after a competing runner commits new data."""
+
+    def __init__(self, data_dir: Path, competitor: Any) -> None:
+        self.data_dir, self.competitor, self.pushes, self.snapshot = data_dir, competitor, 0, {}
+
+    def reset_to_origin(self) -> None:
+        for name, text in self.snapshot.items():
+            (self.data_dir / name).write_text(text, encoding="utf-8")
+
+    def commit_push(self, paths: list[str], message: str) -> bool:
+        self.pushes += 1
+        if self.pushes == 1:
+            self.competitor()  # other runner pushed first: origin moved
+            self.snapshot = {p.name: p.read_text(encoding="utf-8")
+                             for p in self.data_dir.iterdir() if p.suffix in (".json", ".md")}
+            return False
+        return True
+
+
+def test_replay_keeps_both_runners_postings_and_notifies_once(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    notified: list[list[str]] = []
+    monkeypatch.setattr(main_mod, "notify_github_issue",
+                        lambda p, f=(): notified.append([x.url for x in p]))
+
+    def competitor() -> None:
+        other = main_mod.ChangeSet(now=NOW, runner="actions",
+                                   new_postings=[sp(2, "https://x.example/2")],
+                                   seen_marks={"simplify:2": "2026-10-01",
+                                               "url:https://x.example/2": "2026-10-01"})
+        # origin = state before this run + the competitor's commit
+        for name in ("inbox.json", "seen.json"):
+            (state.parent / name).unlink(missing_ok=True)
+        state.write_text('{"version": 1, "seen": {}}', encoding="utf-8")
+        main_mod.apply_changes(state.parent, other)
+
+    git = ReplayGit(state.parent, competitor)
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
+        sp(1, "https://x.example/1"), sp(2, "https://x.example/2")])
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False, git=git, now=NOW) == 0
+    urls = [e["url"] for e in read(state.parent, "inbox.json")]
+    assert urls == ["https://x.example/2", "https://x.example/1"]
+    assert notified == [["https://x.example/1"]]  # 2 was the competitor's to announce
+
+
+def test_push_failing_three_times_exits_1_without_notifying(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+
+    class DeadGit:
+        def reset_to_origin(self) -> None:
+            pass
+
+        def commit_push(self, paths: list[str], message: str) -> bool:
+            return False
+
+    called: list[Any] = []
+    monkeypatch.setattr(main_mod, "notify_github_issue", lambda p, f=(): called.append(p))
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
+    assert main_mod.run(config, state, bootstrap=False, dry_run=False, git=DeadGit(),
+                        now=NOW) == 1
+    assert called == []
+
+
+def test_discovered_board_tech_gate_and_quiet_first_poll(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    gate = ('title_require_any = ["intern"]\n'
+            'discovered_title_require_any = ["software", "data"]\n'
+            'discovered_title_exclude = ["winter 2027"]')
+    config.write_text(CONFIG.replace('title_require_any = ["intern"]', gate), encoding="utf-8")
+    (state.parent / "boards.json").write_text(json.dumps(
+        {"version": 1, "gh_custom": {}, "gh_custom_pending": {}, "boards": {}}), encoding="utf-8")
+    board_posts: list[list[Posting]] = [[]]
+
+    def wd(board: str, depth: int, today: Any) -> tuple[list[Posting], int]:
+        return board_posts[0], len(board_posts[0])
+
+    def wdp(n: int, title: str) -> Posting:
+        return Posting(key=f"workday:acme.wd5/Ext:/job/{n}", source="workday", company="acme",
+                       title=title, url=f"https://acme.wd5.myworkdayjobs.com/Ext/job/{n}")
+
+    issued: list[list[str]] = []
+    monkeypatch.setattr(main_mod, "notify_github_issue",
+                        lambda p, f=(): issued.append([x.title for x in p]))
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [
+        sp(1, "https://acme.wd5.myworkdayjobs.com/Ext/job/0")])
+    monkeypatch.setattr(main_mod, "fetch_workday_info", wd)
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)  # discovers acme
+    issued.clear()
+    board_posts[0] = [wdp(1, "Software Intern"), wdp(2, "Labor Relations Intern"),
+                      wdp(3, "Data Intern - Winter 2027")]
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
+    inbox_titles = [e["title"] for e in read(state.parent, "inbox.json")]
+    assert "Software Intern" in inbox_titles                  # gate passes
+    assert "Labor Relations Intern" not in inbox_titles       # no tech keyword
+    assert "Data Intern - Winter 2027" not in inbox_titles    # off-cycle term
+    assert issued == []                                       # first poll: quiet
+    board_posts[0] = [*board_posts[0], wdp(4, "Data Science Intern")]
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=60))
+    assert issued == [["Data Science Intern"]]                # later arrivals notify
+
+
+def test_list_source_first_run_is_quiet(
+        world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
+    config, state = world
+    issued: list[list[str]] = []
+    monkeypatch.setattr(main_mod, "notify_github_issue",
+                        lambda p, f=(): issued.append([x.url for x in p]))
+    monkeypatch.setattr(main_mod, "fetch_simplify", lambda: [sp(1, "https://x.example/1")])
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW)
+    issued.clear()
+    config.write_text(CONFIG.replace("simplify = true", "simplify = true\nspeedyapply = true"),
+                      encoding="utf-8")
+    speedy = Posting(key="speedyapply:u", source="speedyapply", company="Z", title="SWE Intern",
+                     url="https://x.example/9")
+    monkeypatch.setattr(main_mod, "fetch_speedyapply", lambda: [speedy])
+    main_mod.run(config, state, bootstrap=False, dry_run=False, now=NOW + timedelta(minutes=30))
+    assert issued == []  # speedyapply's first run: backlog queued quietly
+    assert "https://x.example/9" in [e["url"] for e in read(state.parent, "inbox.json")]
```

- [ ] **Step 2: Confirm they fail.** Run: `python3 -m pytest -q tests/test_main.py tests/test_main_wider.py`
  Expected: 2 failed, 9 passed, 8 errors (new `run()` parameters and `ChangeSet` missing; the two updated notify tests fail against the old notify-before-save code).

- [ ] **Step 3: Implement.** Apply:

```diff
diff --git a/src/intern_radar/config.py b/src/intern_radar/config.py
index 963f18c..d65ac24 100644
--- a/src/intern_radar/config.py
+++ b/src/intern_radar/config.py
@@ -21,11 +21,18 @@ class FilterConfig:
     # postings can never become an application: an unreachable apply flow, or
     # a lifetime cap on applications you have already spent.
     company_exclude: tuple[str, ...] = ()
+    # Extra gate for postings from auto-discovered boards only (owner,
+    # 2026-09-30). Those boards carry no job category, so without this the
+    # fuzzy "intern" search admits every labor-relations and finance intern.
+    discovered_title_require_any: tuple[str, ...] = ()
+    discovered_title_exclude: tuple[str, ...] = ()
 
 
 @dataclass(frozen=True)
 class SourcesConfig:
     simplify: bool = True
+    vanshb03: bool = False
+    speedyapply: bool = False
     greenhouse_boards: tuple[str, ...] = ()
     lever_companies: tuple[str, ...] = ()
     ashby_orgs: tuple[str, ...] = ()
@@ -70,11 +77,15 @@ def load_config(path: Path) -> Config:
         untermed_title_exclude=_str_tuple(f_raw.get("untermed_title_exclude", [])),
         location_exclude=_str_tuple(f_raw.get("location_exclude", [])),
         company_exclude=_str_tuple(f_raw.get("company_exclude", [])),
+        discovered_title_require_any=_str_tuple(f_raw.get("discovered_title_require_any", [])),
+        discovered_title_exclude=_str_tuple(f_raw.get("discovered_title_exclude", [])),
     )
 
     s_raw = data.get("sources", {})
     sources = SourcesConfig(
         simplify=bool(s_raw.get("simplify", True)),
+        vanshb03=bool(s_raw.get("vanshb03", False)),
+        speedyapply=bool(s_raw.get("speedyapply", False)),
         greenhouse_boards=_str_tuple(s_raw.get("greenhouse", {}).get("boards", [])),
         lever_companies=_str_tuple(s_raw.get("lever", {}).get("companies", [])),
         ashby_orgs=_str_tuple(s_raw.get("ashby", {}).get("orgs", [])),
diff --git a/src/intern_radar/filters.py b/src/intern_radar/filters.py
index 491680f..eb6dd2a 100644
--- a/src/intern_radar/filters.py
+++ b/src/intern_radar/filters.py
@@ -82,3 +82,13 @@ def matches(posting: Posting, filters: FilterConfig) -> bool:
 
 def apply_filters(postings: list[Posting], filters: FilterConfig) -> list[Posting]:
     return [p for p in postings if matches(p, filters)]
+
+
+def passes_discovered_gate(posting: Posting, filters: FilterConfig) -> bool:
+    """Tech gate for postings from auto-discovered (unpinned) boards."""
+    title_lower = posting.title.lower()
+    if any(kw.lower() in title_lower for kw in filters.discovered_title_exclude):
+        return False
+    return not filters.discovered_title_require_any or _matches_keyword(
+        posting.title, filters.discovered_title_require_any
+    )
diff --git a/src/intern_radar/main.py b/src/intern_radar/main.py
index 1dedea7..066033f 100644
--- a/src/intern_radar/main.py
+++ b/src/intern_radar/main.py
@@ -3,23 +3,35 @@ from __future__ import annotations
 import argparse
 import functools
 import io
+import json
 import os
 import sys
+import time
+import urllib.error
+import urllib.request
 from collections.abc import Callable
 from datetime import UTC, datetime
 from pathlib import Path
+from typing import Any
+from urllib.parse import parse_qs, urlsplit
 
+from intern_radar import boards as boards_mod
+from intern_radar import health as health_mod
+from intern_radar.changeset import ApplyResult, BoardUpdate, ChangeSet
+from intern_radar.changeset import apply as apply_changes
 from intern_radar.config import Config, load_config
-from intern_radar.filters import apply_filters
-from intern_radar.http import FetchError
+from intern_radar.filters import apply_filters, passes_discovered_gate
+from intern_radar.gitsync import GitSync
+from intern_radar.http import USER_AGENT, FetchError
 from intern_radar.jd import JDError, fetch_jd
-from intern_radar.models import Posting
+from intern_radar.models import Posting, canon_key, normalize_url
 from intern_radar.notify import (
     NotifyError,
     notify_console,
     notify_discord,
     notify_github_issue,
 )
+from intern_radar.poll import PollJob, SourceResult, run_jobs
 from intern_radar.sources import (
     fetch_ashby,
     fetch_greenhouse,
@@ -28,13 +40,15 @@ from intern_radar.sources import (
     fetch_smartrecruiters,
     fetch_workday,
 )
-from intern_radar.state import SeenStore, append_inbox
+from intern_radar.sources import workday as workday_mod
+from intern_radar.sources.lists import fetch_speedyapply, fetch_vansh
+from intern_radar.sources.workday import fetch_workday_info
+from intern_radar.state import SeenStore
 from intern_radar.tracker import (
     STATUSES,
     Tracker,
     TrackerError,
     render_dashboard,
-    write_postings_cache,
 )
 
 FetchJob = tuple[str, Callable[[], list[Posting]]]
@@ -64,100 +78,379 @@ def build_fetch_jobs(config: Config) -> list[FetchJob]:
     return jobs
 
 
-def run(config_path: Path, state_path: Path, *, bootstrap: bool, dry_run: bool) -> int:
+RUNNER_ENV = "RADAR_RUNNER"          # "mac" | "actions"; anything else = "local"
+GIT_ENV = "RADAR_GIT"                # "1": the run owns fetch/reset/commit/push
+ISSUES_ENV = "RADAR_GITHUB_ISSUES"   # "0": never open new-posting issues (Mac)
+PUSH_ATTEMPTS = 3
+# Sources the pre-health watcher already ran: on the first run under this code
+# (no health.json yet) any other source's backlog is queued quietly.
+LEGACY_FAMILIES = ("simplify", "greenhouse", "lever", "ashby", "workday", "smartrecruiters")
+GH_CUSTOM_PER_RUN = 10
+
+
+def _config_pins(config: Config) -> dict[str, tuple[str, str]]:
+    pins: dict[str, tuple[str, str]] = {}
+    for ats, names in (
+        ("greenhouse", config.sources.greenhouse_boards),
+        ("lever", config.sources.lever_companies),
+        ("ashby", config.sources.ashby_orgs),
+        ("workday", config.sources.workday_boards),
+        ("smartrecruiters", config.sources.smartrecruiters_companies),
+    ):
+        for name in names:
+            pins[boards_mod.board_key(ats, name)] = (ats, name)
+    return pins
+
+
+def _load_registry(data_dir: Path, config: Config, now: datetime) -> tuple[
+        dict[str, Any], dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
+    """Current boards.json plus rows to add this run (seed + config pins).
+
+    With no boards.json yet, every URL already in inbox.json seeds discovery
+    (441 boards on 2026-09-30) and seeds canon.json for existing entries.
+    Returns (registry, new_rows, gh_pending_add, canon_seed).
+    """
+    path = data_dir / "boards.json"
+    registry: dict[str, Any] = (json.loads(path.read_text(encoding="utf-8"))
+                                if path.exists() else boards_mod.empty_registry())
+    now_iso = now.isoformat()
+    new_rows: dict[str, dict[str, Any]] = {}
+    pending: dict[str, str] = {}
+    canon_seed: dict[str, str] = {}
+    for key, (ats, name) in _config_pins(config).items():
+        if key not in registry["boards"]:
+            new_rows[key] = boards_mod.new_row(ats, name, pinned=True, discovered_via=None,
+                                               now=now_iso)
+    if not path.exists():
+        inbox_path = data_dir / "inbox.json"
+        entries = (json.loads(inbox_path.read_text(encoding="utf-8-sig"))
+                   if inbox_path.exists() else [])
+        for e in entries if isinstance(entries, list) else []:
+            url = str(e.get("url", "")) if isinstance(e, dict) else ""
+            if not url:
+                continue
+            canon_seed.setdefault(canon_key(url), "url:" + normalize_url(url))
+            _discover(url, str(e.get("source", "")), registry, new_rows, pending, now_iso)
+    return registry, new_rows, pending, canon_seed
+
+
+def _discover(url: str, via: str, registry: dict[str, Any],
+              new_rows: dict[str, dict[str, Any]], pending: dict[str, str],
+              now_iso: str) -> str | None:
+    """Record the board behind `url`; returns its key when it is a known ATS."""
+    found = boards_mod.board_of(url)
+    if found is None:
+        return None
+    ats, board = found
+    if ats == "gh_custom":
+        resolved = registry["gh_custom"].get(board)
+        if resolved is None:
+            token = parse_qs(urlsplit(url).query).get("gh_jid", [""])[0]
+            if board not in registry.get("gh_custom_pending", {}):
+                pending.setdefault(board, token)
+            return None
+        ats, board = "greenhouse", resolved
+    key = boards_mod.board_key(ats, board)
+    if key not in registry["boards"] and key not in new_rows:
+        new_rows[key] = boards_mod.new_row(ats, board, pinned=False, discovered_via=via,
+                                           now=now_iso)
+    return key
+
+
+def resolve_gh_custom(token: str) -> str | None:
+    """Board slug for a custom-domain Greenhouse job, from the embed redirect."""
+    request = urllib.request.Request(
+        f"https://boards.greenhouse.io/embed/job_app?token={token}",
+        headers={"User-Agent": USER_AGENT}, method="GET")
+    opener = urllib.request.build_opener(_NoRedirect)
+    try:
+        opener.open(request, timeout=15)
+    except urllib.error.HTTPError as e:
+        location = e.headers.get("Location", "") if e.code in (301, 302) else ""
+        board = parse_qs(urlsplit(location).query).get("for", [""])[0]
+        return board.lower() or None
+    except OSError:
+        return None
+    return None
+
+
+class _NoRedirect(urllib.request.HTTPRedirectHandler):
+    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
+        return None
+
+
+def build_poll_jobs(config: Config, registry: dict[str, Any],
+                    new_rows: dict[str, dict[str, Any]], now: datetime) -> list[PollJob]:
+    jobs: list[PollJob] = []
+    if config.sources.simplify:
+        jobs.append(PollJob("simplify", "simplify", "raw.githubusercontent.com", fetch_simplify))
+    if config.sources.vanshb03:
+        jobs.append(PollJob("vanshb03", "vanshb03", "raw.githubusercontent.com", fetch_vansh))
+    if config.sources.speedyapply:
+        jobs.append(PollJob("speedyapply", "speedyapply", "raw.githubusercontent.com",
+                            fetch_speedyapply))
+    today = now.date()
+    rows = {**registry["boards"], **new_rows}
+    deep_budget = boards_mod.MAX_DEEP_CRAWLS_PER_RUN
+    for key in sorted(rows):
+        row = rows[key]
+        if not boards_mod.due(row, now):
+            continue
+        ats, board, pinned = row["ats"], row["board"], bool(row.get("pinned"))
+        deep_crawl = False
+        fetch: Callable[[], Any]
+        if ats == "workday":
+            depth = workday_mod.MAX_RESULTS
+            if not pinned:
+                depth = workday_mod.DISCOVERED_MAX_RESULTS
+                if deep_budget > 0 and boards_mod.deep_crawl_due(row, now):
+                    depth, deep_crawl = workday_mod.MAX_RESULTS, True
+                    deep_budget -= 1
+            fetch = functools.partial(fetch_workday_info, board, depth, today)
+            host = f"{board.split('/')[0]}.myworkdayjobs.com"
+        elif ats == "greenhouse":
+            fetch, host = functools.partial(fetch_greenhouse, board), "boards-api.greenhouse.io"
+        elif ats == "lever":
+            fetch, host = functools.partial(fetch_lever, board), "api.lever.co"
+        elif ats == "ashby":
+            fetch, host = functools.partial(fetch_ashby, board), "api.ashbyhq.com"
+        elif ats == "smartrecruiters":
+            fetch = functools.partial(fetch_smartrecruiters, board,
+                                      config.sources.smartrecruiters_country)
+            host = "api.smartrecruiters.com"
+        else:
+            continue
+        jobs.append(PollJob(f"{ats}:{board}", ats, host, fetch, board_key=key,
+                            pinned=pinned, deep_crawl=deep_crawl))
+    return jobs
+
+
+def _family_stats(results: list[SourceResult], matched_ids: set[int]) -> dict[str, Any]:
+    fams: dict[str, dict[str, Any]] = {}
+    for r in results:
+        if r.skipped:
+            continue
+        e = fams.setdefault(r.job.family, {"jobs": 0, "ok": 0, "errors": 0, "postings": 0,
+                                           "matches": 0, "seconds": 0.0, "newest": None})
+        e["jobs"] += 1
+        e["ok" if r.ok else "errors"] += 1
+        e["postings"] += len(r.postings)
+        e["matches"] += sum(1 for p in r.postings if id(p) in matched_ids)
+        e["seconds"] = round(e["seconds"] + r.seconds, 2)
+        dates = [p.posted_at for p in r.postings if p.posted_at]
+        if dates:
+            e["newest"] = max([d for d in (e["newest"], *dates) if d])
+    return fams
+
+
+def run(
+    config_path: Path,
+    state_path: Path,
+    *,
+    bootstrap: bool,
+    dry_run: bool,
+    git: GitSync | None = None,
+    now: datetime | None = None,
+) -> int:
+    started = time.monotonic()
+    now = now or datetime.now(tz=UTC).replace(microsecond=0)
+    now_iso = now.isoformat()
+    today = now.date().isoformat()
+    runner = os.environ.get(RUNNER_ENV, "local")
+    data_dir = state_path.parent
+    if git is not None and not dry_run:
+        git.reset_to_origin()
+
     config = load_config(config_path)
     store = SeenStore.load(state_path)
-    today = datetime.now(tz=UTC).date().isoformat()
+    first_run = not store.path.exists()
+    registry, new_rows, gh_pending, canon_seed = _load_registry(data_dir, config, now)
+    canon_path = data_dir / "canon.json"
+    canon: dict[str, str] = (json.loads(canon_path.read_text(encoding="utf-8"))["canon"]
+                             if canon_path.exists() else {})
+    canon = {**canon_seed, **canon}
 
-    postings: list[Posting] = []
-    failures: list[str] = []
-    jobs = build_fetch_jobs(config)
-    for name, fetch in jobs:
-        try:
-            fetched = fetch()
-            postings.extend(fetched)
-            print(f"{name}: {len(fetched)} postings")
-        except Exception as e:  # one bad source must not kill the run
-            failures.append(f"{name}: {e}")
-            print(f"error: {name}: {e}", file=sys.stderr)
-
-    if failures and len(failures) == len(jobs):
+    jobs = build_poll_jobs(config, registry, new_rows, now)
+    results = run_jobs(jobs)
+    ran = [r for r in results if not r.skipped]
+    failures = [f"{r.job.name}: {r.error}" for r in ran if not r.ok]
+    for r in ran:
+        if r.ok:
+            print(f"{r.job.name}: {len(r.postings)} postings")
+        else:
+            print(f"error: {r.job.name}: {r.error}", file=sys.stderr)
+    if ran and not any(r.ok for r in ran):
         print("error: every source failed", file=sys.stderr)
         return 1
 
-    matched = apply_filters(postings, config.filters)
-    fresh = [p for p in matched if not store.is_seen(p)]
-    # Within-run dedup: the same job often appears via Simplify and a direct
-    # ATS board in the same batch.
-    unique: dict[str, Posting] = {}
-    for p in fresh:
-        unique.setdefault(p.url_key, p)
-    new_postings = list(unique.values())
-
-    print(
-        f"fetched {len(postings)} | matched filters {len(matched)} | new {len(new_postings)}"
-        + (f" | source failures {len(failures)}" if failures else "")
-    )
+    postings = [p for r in ran for p in r.postings]
+    discovered_ids = {id(p) for r in ran if r.job.board_key is not None and not r.job.pinned
+                      for p in r.postings}
+    matched = [p for p in apply_filters(postings, config.filters)
+               if id(p) not in discovered_ids or passes_discovered_gate(p, config.filters)]
+    matched_ids = {id(p) for p in matched}
+    # A board's first poll surfaces its whole current backlog: queue it, but
+    # don't notify (owner, 2026-09-30). Only later arrivals notify.
+    # A deep board's first full-depth crawl surfaces results past the top 100
+    # for the first time: also quiet. So is a list source's first run (vanshb03/speedyapply when
+    # first enabled): its whole backlog would otherwise notify at once.
+    all_rows = {**registry["boards"], **new_rows}
+    health_path = data_dir / "health.json"
+    known_families = ({f for run in json.loads(health_path.read_text(encoding="utf-8"))["runs"]
+                       for f in run.get("families", {})} if health_path.exists()
+                      else set(LEGACY_FAMILIES))
+    quiet_ids = {id(p) for r in ran
+                 if (r.job.board_key is not None
+                     and (all_rows.get(r.job.board_key, {}).get("last_polled") is None
+                          or (r.job.deep_crawl
+                              and all_rows[r.job.board_key].get("last_deep_crawl") is None)))
+                 or (r.job.board_key is None and r.job.family not in known_families)
+                 for p in r.postings}
+
+    marks: dict[str, str] = {}
+    canon_add: dict[str, str] = {}
+    sightings_add: dict[str, dict[str, str]] = {}
+    fresh: dict[str, Posting] = {}
+    for p in matched:
+        marks[p.key] = today
+        marks[p.url_key] = today
+        ck = canon_key(p.url)
+        sightings_add.setdefault(ck, {}).setdefault(p.source, now_iso)
+        stored = canon.get(ck)
+        if stored is None:
+            canon_add.setdefault(ck, p.url_key)
+        if store.is_seen(p):
+            continue
+        if stored is not None and stored != p.url_key:
+            marks[stored] = today  # same job under another URL variant: keep it live
+            continue
+        fresh.setdefault(p.url_key, p)
+    new_postings = list(fresh.values())
+    quiet_urls = {p.url for p in new_postings if id(p) in quiet_ids}
+    canon_add = {**canon_seed, **canon_add}
+
+    updates: dict[str, BoardUpdate] = {}
+    matched_boards: set[str] = set()
+    for p in matched:
+        key = _discover(p.url, p.source, registry, new_rows, gh_pending, now_iso)
+        if key is not None:
+            matched_boards.add(key)
+            if registry["boards"].get(key, {}).get("disabled"):
+                updates.setdefault(key, BoardUpdate()).reenable = True
+    gh_custom_add: dict[str, str] = {}
+    gh_done: set[str] = set()
+    for host, token in list({**registry.get("gh_custom_pending", {}),
+                             **gh_pending}.items())[:GH_CUSTOM_PER_RUN]:
+        board = resolve_gh_custom(token) if token else None
+        gh_done.add(host)
+        if board:
+            gh_custom_add[host] = board
+            key = boards_mod.board_key("greenhouse", board)
+            if key not in registry["boards"]:
+                new_rows.setdefault(key, boards_mod.new_row(
+                    "greenhouse", board, pinned=False, discovered_via="gh_custom", now=now_iso))
+    for r in results:
+        key = r.job.board_key
+        if key is None:
+            continue
+        up = updates.setdefault(key, BoardUpdate())
+        up.polled = not r.skipped
+        up.ok = r.ok
+        up.matched = any(id(p) in matched_ids for p in r.postings) or key in matched_boards
+        if (r.job.family == "workday" and not r.job.pinned and r.total is not None
+                and r.total > workday_mod.DISCOVERED_MAX_RESULTS):
+            up.deep = True
+        up.deep_crawled = r.job.deep_crawl and r.ok
+    for key in matched_boards:
+        updates.setdefault(key, BoardUpdate()).matched = True
+
+    skipped = sum(1 for r in results if r.skipped)
+    polled = sum(1 for r in ran if r.job.board_key is not None)
+    print(f"fetched {len(postings)} | matched filters {len(matched)} | new {len(new_postings)}"
+          + (f" | source failures {len(failures)}" if failures else "")
+          + f" | boards polled {polled}" + (f" | skipped (budget) {skipped}" if skipped else ""))
 
     if dry_run:
         notify_console(new_postings)
         return 0
 
-    # Snapshot matched postings so `track add <url>` can resolve metadata.
-    write_postings_cache(state_path.parent / "postings.json", matched)
-
-    # A missing state file means this is the first run: bootstrap implicitly,
-    # otherwise the first cron tick after pushing floods one giant issue.
-    if bootstrap or not store.path.exists():
-        if failures:
-            # A half-bootstrap would dump the failed source's whole board as
-            # "new" on the next run — refuse instead.
-            print("error: bootstrap needs every source healthy; fix failures and retry",
-                  file=sys.stderr)
-            return 1
-        for p in matched:
-            store.mark(p, today)
-        store.prune(today)
-        store.save()
-        print(f"bootstrap: marked {len(matched)} current postings as seen; no notifications sent")
-        return 0
+    is_bootstrap = bootstrap or first_run
+    if is_bootstrap and failures:
+        print("error: bootstrap needs every source healthy; fix failures and retry",
+              file=sys.stderr)
+        return 1
 
-    if new_postings:
-        notify_console(new_postings)
-        # The durable notifier runs before state is saved: if delivery fails we
-        # exit nonzero without marking anything seen, and the next run retries.
-        try:
-            if config.notify.github_issues:
-                notify_github_issue(new_postings, tuple(failures))
-        except NotifyError as e:
-            print(f"error: {e}", file=sys.stderr)
-            return 1
-        try:
-            notify_discord(new_postings)
-        except NotifyError as e:
-            if config.notify.github_issues:
-                # Best-effort when an issue was already created: a dead webhook
-                # must not re-notify the same postings every run forever.
-                print(f"warn: {e}", file=sys.stderr)
-            else:
-                # Discord is the only remote channel — failing it means the
-                # notification was never delivered anywhere durable.
-                print(f"error: {e}", file=sys.stderr)
-                return 1
-
-    if new_postings:
-        # Notifications delivered — queue for the auto-tailor agent. Runs
-        # before state save so a crash here re-delivers rather than drops.
-        queued = append_inbox(state_path.parent / "inbox.json", new_postings, today)
-        print(f"inbox: queued {queued} for auto-tailor")
+    run_entry = {"at": now_iso, "runner": runner, "seconds": round(time.monotonic() - started, 1),
+                 "families": _family_stats(results, matched_ids),
+                 "boards_polled": polled, "boards_skipped_budget": skipped}
+    cs = ChangeSet(
+        now=now, runner=runner, new_postings=[] if is_bootstrap else new_postings,
+        seen_marks=marks, cache=matched, canon_add=canon_add, sightings_add=sightings_add,
+        board_rows_new=new_rows, board_updates=updates,
+        pinned_keys=set(_config_pins(config)), gh_custom_add=gh_custom_add,
+        gh_pending_add=gh_pending, gh_pending_done=gh_done, run_entry=run_entry,
+    )
+    ntfy_on = bool(os.environ.get("NTFY_TOPIC"))
+    result = None
+    for attempt in range(1, PUSH_ATTEMPTS + 1):
+        result = apply_changes(data_dir, cs, ntfy_enabled=ntfy_on)
+        if git is None:
+            break
+        if git.commit_push(["data"], "chore: update seen state [skip ci]"):
+            break
+        print(f"warn: push rejected (attempt {attempt}); replaying on latest origin",
+              file=sys.stderr)
+        git.reset_to_origin()
+    else:
+        print("error: push failed after replay attempts; next run re-finds these postings",
+              file=sys.stderr)
+        return 1
+    assert result is not None
 
-    for p in matched:
-        store.mark(p, today)
-    store.prune(today)
-    store.save()
+    if is_bootstrap:
+        print(f"bootstrap: marked {len(matched)} current postings as seen; "
+              "no notifications sent")
+    elif result.appended:
+        loud = [p for p in result.appended if p.url not in quiet_urls]
+        if loud:
+            _notify_new(config, loud, tuple(failures))
+        print(f"inbox: queued {len(result.appended)} for triage"
+              + (f" ({len(result.appended) - len(loud)} quietly, first board poll)"
+                 if len(loud) != len(result.appended) else ""))
+    _deliver_health(result, now)
     return 0
 
 
+def _notify_new(config: Config, postings: list[Posting], failures: tuple[str, ...]) -> None:
+    """Best-effort delivery after a successful push; inbox.json is durable."""
+    notify_console(postings)
+    issues = config.notify.github_issues and os.environ.get(ISSUES_ENV, "1") != "0"
+    for name, send in (
+        ("github issue", (lambda: notify_github_issue(postings, failures)) if issues else None),
+        ("discord", lambda: notify_discord(postings)),
+        ("ntfy", None if issues else (
+            lambda: health_mod.send_ntfy(f"intern-radar: {len(postings)} new postings"))),
+    ):
+        if send is None:
+            continue
+        try:
+            send()
+        except (NotifyError, OSError) as e:
+            print(f"warn: {name} notification failed: {e}", file=sys.stderr)
+
+
+def _deliver_health(result: ApplyResult, now: datetime) -> None:
+    token = os.environ.get("GITHUB_TOKEN", "")
+    repo = os.environ.get("GITHUB_REPOSITORY", "")
+    try:
+        if token and repo:
+            health_mod.sync_issues(result.active_alerts, now, token=token, repo=repo)
+        if result.ntfy_due:
+            health_mod.send_ntfy(f"intern-radar health: {len(result.active_alerts)} open alerts")
+    except OSError as e:
+        print(f"warn: health delivery failed: {e}", file=sys.stderr)
+
+
 def run_track(args: argparse.Namespace) -> int:
     data_dir: Path = args.state.parent
     tracker = Tracker.load(data_dir / "applications.json")
@@ -253,7 +546,8 @@ def main(argv: list[str] | None = None) -> int:
         return run_jd(args)
     # Honor a bootstrap request coming from a workflow_dispatch input.
     bootstrap = args.bootstrap or os.environ.get("RADAR_BOOTSTRAP", "") == "true"
-    return run(args.config, args.state, bootstrap=bootstrap, dry_run=args.dry_run)
+    git = GitSync(Path.cwd()) if os.environ.get(GIT_ENV) == "1" else None
+    return run(args.config, args.state, bootstrap=bootstrap, dry_run=args.dry_run, git=git)
 
 
 if __name__ == "__main__":
diff --git a/src/intern_radar/notify.py b/src/intern_radar/notify.py
index 30be057..e15c684 100644
--- a/src/intern_radar/notify.py
+++ b/src/intern_radar/notify.py
@@ -16,13 +16,26 @@ class NotifyError(Exception):
     pass
 
 
+_MD_SPECIAL = str.maketrans({c: "\\" + c for c in "[]()<>!@`*_"})
+
+
+def md_escape(text: str) -> str:
+    """Neutralize markdown/mention syntax in untrusted board text.
+
+    Company and title strings come from third-party job boards; unescaped they
+    could inject links, images or @mentions into the issue body.
+    """
+    return "".join(ch for ch in text if ch.isprintable()).translate(_MD_SPECIAL)
+
+
 def format_lines(postings: list[Posting]) -> list[str]:
     lines: list[str] = []
     for p in sorted(postings, key=lambda p: (p.company.lower(), p.title.lower())):
         locations = ", ".join(p.locations[:3]) + (" …" if len(p.locations) > 3 else "")
         extras = " · ".join(x for x in (locations, ", ".join(p.terms), p.posted_at) if x)
-        suffix = f" ({extras})" if extras else ""
-        lines.append(f"- [ ] **{p.company}** — [{p.title}]({p.url}){suffix}")
+        suffix = f" ({md_escape(extras)})" if extras else ""
+        url = p.url.replace(")", "%29").replace(" ", "%20")
+        lines.append(f"- [ ] **{md_escape(p.company)}** — [{md_escape(p.title)}]({url}){suffix}")
     return lines
```

- [ ] **Step 4: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `204 passed`, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_main.py tests/test_main_wider.py src/intern_radar/config.py src/intern_radar/filters.py src/intern_radar/notify.py src/intern_radar/main.py
git commit -m "feat(main): wider watcher run loop with replay protocol" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Enable the lists and the discovered-board tech gate in `config.toml`

**Files:** `config.toml`

- [ ] **Step 1: Implement.** Apply:

```diff
diff --git a/config.toml b/config.toml
index e4f4257..c1cb04a 100644
--- a/config.toml
+++ b/config.toml
@@ -44,9 +44,28 @@ location_exclude = []
 # see them again.
 company_exclude = ["TikTok", "ByteDance"]
 
+# Auto-discovered boards have no job category: require a tech keyword and
+# drop off-cycle terms (owner, 2026-09-30). Hand-picked boards are exempt.
+discovered_title_require_any = [
+  "software", "engineer", "engineering", "developer", "development", "data",
+  "machine learning", "ml", "ai", "artificial intelligence", "quant",
+  "quantitative", "computer", "computing", "computational", "technology",
+  "technical", "tech", "it", "cyber", "cybersecurity", "security", "analytics",
+  "intelligence", "platform", "cloud", "devops", "sre", "infrastructure",
+  "backend", "frontend", "full stack", "full-stack", "mobile", "ios", "android",
+  "web", "programming", "programmer", "algorithm", "research", "python", "java",
+  "automation", "robotics", "maps", "mapping", "embedded", "firmware", "systems",
+]
+discovered_title_exclude = [
+  "fall 2027", "winter 2027", "spring 2027", "fall '27", "winter '27", "spring '27",
+]
+
 [sources]
 # Aggregator: SimplifyJobs/Summer2027-Internships listings.json (dev branch).
 simplify = true
+# Independent lists (wider-watcher spec, L1).
+vanshb03 = true
+speedyapply = true
 
 # Direct ATS boards, polled for speed — new postings show up here before
 # aggregators pick them up. Slugs below were verified live on 2026-08-03
```

- [ ] **Step 2: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `204 passed`, `All checks passed!`.

- [ ] **Step 3: Commit.**

```bash
git add config.toml
git commit -m "config: enable vanshb03/speedyapply and the discovered-board tech gate" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Actions backup workflow, Mac runner scripts, launchd plist, setup doc

**Files:** `.github/workflows/watch.yml`, `scripts/mac-watch.sh`, `scripts/git-askpass.sh`, `launchd/com.kernsharma.radar-watch.plist`, `docs/mac-runner.md`

- [ ] **Step 1: Implement.** Apply:

```diff
diff --git a/.github/workflows/watch.yml b/.github/workflows/watch.yml
index 8f95665..91fd678 100644
--- a/.github/workflows/watch.yml
+++ b/.github/workflows/watch.yml
@@ -1,8 +1,12 @@
 name: watch
 
+# Backup runner. The owner's Mac is the primary (launchd every 30 min); this
+# job runs only when the Mac hasn't committed a run in the last 50 minutes.
+# GitHub throttles this cron to ~4-7 runs a day. Never add pull_request /
+# pull_request_target triggers: this job holds write access and secrets.
 on:
   schedule:
-    - cron: "7,37 * * * *"  # every 30 min, offset to dodge top-of-hour congestion
+    - cron: "7,37 * * * *"
   workflow_dispatch:
     inputs:
       bootstrap:
@@ -29,30 +33,35 @@ jobs:
         with:
           python-version: "3.12"
 
+      - name: Skip if the Mac runner is active
+        id: skip
+        env:
+          PYTHONPATH: src
+        run: python -m intern_radar.skipcheck data/health.json
+
       - name: Run watcher
+        if: steps.skip.outputs.skip != 'true'
         env:
           PYTHONPATH: src
+          RADAR_GIT: "1"
+          RADAR_RUNNER: actions
           GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
           DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
+          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
           RADAR_BOOTSTRAP: ${{ inputs.bootstrap == true && 'true' || 'false' }}
-        run: python -m intern_radar
-
-      - name: Commit seen state
         run: |
           git config user.name "intern-radar[bot]"
           git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
-          git add data/
-          committed=0
-          if ! git diff --cached --quiet; then
-            git commit -m "chore: update seen state [skip ci]"
-            committed=1
-          elif [ $(( $(date +%s) - $(git log -1 --format=%ct) )) -gt 4320000 ]; then
-            # GitHub disables cron workflows after 60 days without repo
-            # activity; an empty commit at 50 days resets that clock.
+          python -m intern_radar
+
+      - name: Keepalive
+        if: steps.skip.outputs.skip != 'true'
+        run: |
+          # GitHub disables cron workflows after 60 days without repo activity;
+          # an empty commit at 50 days resets that clock. Mac commits count as
+          # activity too, so this only fires if the Mac has been off for weeks.
+          git pull -q --rebase origin "${GITHUB_REF_NAME}"
+          if [ $(( $(date +%s) - $(git log -1 --format=%ct) )) -gt 4320000 ]; then
             git commit --allow-empty -m "chore: keepalive [skip ci]"
-            committed=1
-          fi
-          if [ "$committed" = "1" ]; then
-            git pull --rebase origin "${GITHUB_REF_NAME}"
             git push
           fi
diff --git a/docs/mac-runner.md b/docs/mac-runner.md
new file mode 100644
index 0000000..1746a9e
--- /dev/null
+++ b/docs/mac-runner.md
@@ -0,0 +1,54 @@
+# Mac runner setup (one time)
+
+The Mac is intern-radar's primary runner (every 30 minutes while awake);
+GitHub Actions is the backup and skips itself while the Mac is active. Spec:
+`docs/specs/2026-09-30-wider-watcher-design.md`, "Runners and the write
+protocol".
+
+## 1. Fine-grained token
+
+GitHub → Settings → Developer settings → Fine-grained tokens → Generate:
+- Repository access: **only** `intern-radar`
+- Permissions: **Contents: Read and write**, **Issues: Read and write**
+- Expiry: 1 year (set a reminder)
+
+Store it (you are prompted for the value; it never appears on screen or in
+shell history):
+
+    security add-generic-password -s radar-mac-pat -a "$USER" -w
+
+## 2. Dedicated clone
+
+    mkdir -p ~/.local/share/intern-radar ~/.local/state/intern-radar
+    git clone https://github.com/KernSharma/intern-radar.git ~/.local/share/intern-radar/repo
+    cd ~/.local/share/intern-radar/repo
+    git config user.name "intern-radar[mac]"
+    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
+
+Never edit files in this clone; each run resets it to `origin/main`.
+
+## 3. Optional notifiers
+
+The ntfy topic is shared with the briefing system (`ntfy-topic` already
+exists in the Keychain). For Discord, store the webhook as
+`radar-discord-webhook`.
+
+## 4. Test by hand, then schedule
+
+    bash ~/.local/share/intern-radar/repo/scripts/mac-watch.sh
+    tail ~/.local/state/intern-radar/launchd.log   # after scheduling
+
+    cp ~/.local/share/intern-radar/repo/launchd/com.kernsharma.radar-watch.plist ~/Library/LaunchAgents/
+    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kernsharma.radar-watch.plist
+
+Stop it with `launchctl bootout gui/$(id -u)/com.kernsharma.radar-watch`.
+
+## 5. Actions secret (backup runner health pushes)
+
+Repository → Settings → Secrets → Actions → `NTFY_TOPIC` = the same topic.
+
+## Checking the gates
+
+    cd ~/.local/share/intern-radar/repo
+    PYTHONPATH=src python3 scripts/gate_check.py --phase 1 --expect "R3|vanshb03"
+    PYTHONPATH=src python3 scripts/regression_replay.py
diff --git a/launchd/com.kernsharma.radar-watch.plist b/launchd/com.kernsharma.radar-watch.plist
new file mode 100644
index 0000000..141ddf4
--- /dev/null
+++ b/launchd/com.kernsharma.radar-watch.plist
@@ -0,0 +1,25 @@
+<?xml version="1.0" encoding="UTF-8"?>
+<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
+<plist version="1.0">
+<dict>
+  <key>Label</key><string>com.kernsharma.radar-watch</string>
+  <key>ProgramArguments</key>
+  <array>
+    <string>/bin/bash</string>
+    <string>/Users/kernsharma/.local/share/intern-radar/repo/scripts/mac-watch.sh</string>
+  </array>
+  <key>StartCalendarInterval</key>
+  <array>
+    <dict><key>Minute</key><integer>7</integer></dict>
+    <dict><key>Minute</key><integer>37</integer></dict>
+  </array>
+  <key>RunAtLoad</key><false/>
+  <key>EnvironmentVariables</key>
+  <dict>
+    <key>PATH</key><string>/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
+    <key>HOME</key><string>/Users/kernsharma</string>
+  </dict>
+  <key>StandardOutPath</key><string>/Users/kernsharma/.local/state/intern-radar/launchd.log</string>
+  <key>StandardErrorPath</key><string>/Users/kernsharma/.local/state/intern-radar/launchd.err</string>
+</dict>
+</plist>
diff --git a/scripts/git-askpass.sh b/scripts/git-askpass.sh
new file mode 100755
index 0000000..b7b20e0
--- /dev/null
+++ b/scripts/git-askpass.sh
@@ -0,0 +1,7 @@
+#!/bin/bash
+# GIT_ASKPASS helper for the Mac runner: username x-access-token, password =
+# the fine-grained PAT (this repo only; contents + issues) from the Keychain.
+case "$1" in
+  Username*) echo "x-access-token" ;;
+  *) security find-generic-password -s radar-mac-pat -w ;;
+esac
diff --git a/scripts/mac-watch.sh b/scripts/mac-watch.sh
new file mode 100755
index 0000000..96113e9
--- /dev/null
+++ b/scripts/mac-watch.sh
@@ -0,0 +1,39 @@
+#!/bin/bash
+# Mac primary runner for intern-radar (launchd: com.kernsharma.radar-watch).
+# Runs the watcher in the dedicated clone; the program itself fetches,
+# commits, pushes and replays (RADAR_GIT=1). Secrets come from the Keychain.
+set -u
+REPO="$HOME/.local/share/intern-radar/repo"
+PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
+STATE="$HOME/.local/state/intern-radar"
+mkdir -p "$STATE"
+cd "$REPO" || exit 1
+
+LOCK="$STATE/lock"
+if ! mkdir "$LOCK" 2>/dev/null; then
+  pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
+  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
+    echo "$(date -u +%FT%TZ) already running (pid $pid)"; exit 0
+  fi
+  rm -rf "$LOCK" && mkdir "$LOCK" || exit 1
+fi
+echo $$ > "$LOCK/pid"
+trap 'rm -rf "$LOCK"' EXIT
+
+keychain() { security find-generic-password -s "$1" -w 2>/dev/null || true; }
+
+export PYTHONPATH=src RADAR_GIT=1 RADAR_RUNNER=mac RADAR_GITHUB_ISSUES=0
+export GITHUB_REPOSITORY="KernSharma/intern-radar"
+export GITHUB_TOKEN="$(keychain radar-mac-pat)"
+export DISCORD_WEBHOOK_URL="$(keychain radar-discord-webhook)"
+export NTFY_TOPIC="$(keychain ntfy-topic)"
+export GIT_ASKPASS="$REPO/scripts/git-askpass.sh" GIT_TERMINAL_PROMPT=0
+if [ -z "$GITHUB_TOKEN" ]; then
+  echo "$(date -u +%FT%TZ) missing Keychain item radar-mac-pat" >&2; exit 1
+fi
+
+echo "$(date -u +%FT%TZ) start"
+"$PY" -m intern_radar
+rc=$?
+echo "$(date -u +%FT%TZ) exit $rc"
+exit $rc
```

- [ ] **Step 2: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `204 passed`, `All checks passed!`.

- [ ] **Step 3: Commit.**

```bash
git add .github/workflows/watch.yml scripts/mac-watch.sh scripts/git-askpass.sh launchd/com.kernsharma.radar-watch.plist docs/mac-runner.md
git commit -m "ci: Actions becomes backup runner; add Mac runner, launchd plist, setup doc" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Gate check and regression replay scripts

**Files:** `scripts/gate_check.py`, `scripts/regression_replay.py`

- [ ] **Step 1: Implement.** Apply:

```diff
diff --git a/scripts/gate_check.py b/scripts/gate_check.py
new file mode 100644
index 0000000..66d3adf
--- /dev/null
+++ b/scripts/gate_check.py
@@ -0,0 +1,92 @@
+"""Phase gates as PASS/FAIL lines (wider-watcher spec, "Phased rollout").
+
+    PYTHONPATH=src python3 scripts/gate_check.py --phase 1 [--expect "R3|vanshb03"]
+
+Reads data/health.json, data/sightings.json and data/boards.json. With
+GITHUB_TOKEN + GITHUB_REPOSITORY set, also checks open health issues. Exits
+nonzero if any criterion fails.
+"""
+
+from __future__ import annotations
+
+import argparse
+import json
+import os
+import sys
+import urllib.request
+from collections import defaultdict
+from datetime import UTC, datetime, timedelta
+from pathlib import Path
+
+from intern_radar.health import head_starts
+from intern_radar.http import USER_AGENT
+
+
+def _load(path: Path) -> dict:
+    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
+
+
+def phase1(data: Path, now: datetime, expect: set[str]) -> list[tuple[bool, str]]:
+    health = _load(data / "health.json")
+    runs = [r for r in health.get("runs", [])
+            if datetime.fromisoformat(r["at"]) >= now - timedelta(days=7)]
+    out: list[tuple[bool, str]] = []
+    span = (datetime.fromisoformat(runs[0]["at"]) if runs else now)
+    out.append((bool(runs) and now - span >= timedelta(days=6, hours=12),
+                f"7 days of runs recorded ({len(runs)} runs since {span:%Y-%m-%d %H:%M})"))
+    slow = [r for r in runs if r.get("seconds", 0) >= 720]
+    slowest = max((r["seconds"] for r in runs), default=0)
+    out.append((not slow, f"every run under 12 min (slowest {slowest:.0f} s)"))
+    clean = sum(1 for r in runs if r.get("boards_skipped_budget", 0) == 0)
+    out.append((bool(runs) and clean / len(runs) >= 0.9,
+                f"skipped_budget = 0 on >= 90% of runs ({clean}/{len(runs)})"))
+    by_day: dict[str, list[datetime]] = defaultdict(list)
+    for r in runs:
+        if r.get("runner") == "mac":
+            by_day[r["at"][:10]].append(datetime.fromisoformat(r["at"]))
+    coverage_ok = bool(by_day)
+    details = []
+    for day, stamps in sorted(by_day.items()):
+        first, last = min(stamps), max(stamps)
+        expected = max(1, int((last - first).total_seconds() // 1800) + 1)
+        ratio = min(1.0, len(stamps) / expected)
+        coverage_ok &= ratio >= 0.9
+        details.append(f"{day[5:]} {len(stamps)}/{expected}")
+    out.append((coverage_ok,
+                "Mac covers >= 90% of :07/:37 slots each day (" + ", ".join(details) + ")"))
+    pairs = {f: v for f, v in head_starts(_load(data / "sightings.json")).items() if v[0] >= 10}
+    paired = ", ".join(f"{f}: n={n}, {h:+.1f} h" for f, (n, h) in sorted(pairs.items()))
+    out.append((bool(pairs),
+                f"a direct family has n >= 10 sightings paired with simplify ({paired})"))
+    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
+    if token and repo:
+        req = urllib.request.Request(
+            f"https://api.github.com/repos/{repo}/issues?state=open&labels=health&per_page=100",
+            headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"})
+        with urllib.request.urlopen(req, timeout=30) as resp:
+            issues = json.loads(resp.read())
+        bad = [i["title"] for i in issues
+               if "false-positive" not in {lbl["name"] for lbl in i.get("labels", [])}
+               and i["title"].removeprefix("health: ").replace(" ", "|", 1) not in expect]
+        out.append((not bad, f"no unexplained open health issues ({len(bad)}: {bad[:5]})"))
+    else:
+        alerts = [k for k in health.get("alerts", {}) if k not in expect]
+        out.append((not alerts, f"no unexpected active alerts in health.json ({alerts[:5]})"))
+    return out
+
+
+def main(argv: list[str] | None = None) -> int:
+    ap = argparse.ArgumentParser()
+    ap.add_argument("--phase", type=int, default=1, choices=[1])
+    ap.add_argument("--data", type=Path, default=Path("data"))
+    ap.add_argument("--expect", action="append", default=[],
+                    help="alert key that is expected, e.g. 'R3|vanshb03'")
+    args = ap.parse_args(argv)
+    results = phase1(args.data, datetime.now(tz=UTC), set(args.expect))
+    for ok, text in results:
+        print(f"{'PASS' if ok else 'FAIL'}  {text}")
+    return 0 if all(ok for ok, _ in results) else 1
+
+
+if __name__ == "__main__":
+    sys.exit(main())
diff --git a/scripts/regression_replay.py b/scripts/regression_replay.py
new file mode 100644
index 0000000..4ddafdb
--- /dev/null
+++ b/scripts/regression_replay.py
@@ -0,0 +1,58 @@
+"""Live check: the new pipeline finds everything the old watcher found.
+
+    PYTHONPATH=src python3 scripts/regression_replay.py
+
+Runs the legacy source set (config boards + simplify, sequential, as before
+this change) and the new poll set side by side, read-only, and reports any
+matched posting the legacy run found that the new run did not. Postings from
+sources that errored in either run are excluded. Exit 0 = superset holds.
+"""
+
+from __future__ import annotations
+
+import json
+import sys
+from datetime import UTC, datetime
+from pathlib import Path
+
+from intern_radar import main as m
+from intern_radar.boards import empty_registry
+from intern_radar.config import load_config
+from intern_radar.filters import apply_filters, passes_discovered_gate
+from intern_radar.poll import run_jobs
+
+
+def main() -> int:
+    config = load_config(Path("config.toml"))
+    now = datetime.now(tz=UTC)
+    legacy: set[str] = set()
+    legacy_failed: set[str] = set()
+    for name, fetch in m.build_fetch_jobs(config):
+        try:
+            legacy |= {p.url_key for p in apply_filters(fetch(), config.filters)}
+        except Exception as e:  # report and exclude
+            legacy_failed.add(name)
+            print(f"legacy error {name}: {e}", file=sys.stderr)
+    reg_path = Path("data/boards.json")
+    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() \
+        else empty_registry()
+    pins = {k: m.boards_mod.new_row(a, b, pinned=True, discovered_via=None, now=now.isoformat())
+            for k, (a, b) in m._config_pins(config).items() if k not in registry["boards"]}
+    results = run_jobs(m.build_poll_jobs(config, registry, pins, now))
+    new: set[str] = set()
+    new_failed = {r.job.name for r in results if not r.ok}
+    for r in results:
+        keep = apply_filters(r.postings, config.filters)
+        if r.job.board_key is not None and not r.job.pinned:
+            keep = [p for p in keep if passes_discovered_gate(p, config.filters)]
+        new |= {p.url_key for p in keep}
+    missing = sorted(legacy - new)
+    print(f"legacy matched {len(legacy)} | new matched {len(new)} | missing {len(missing)}"
+          f" | failed legacy {sorted(legacy_failed)} new {sorted(new_failed)}")
+    for key in missing[:20]:
+        print(f"  missing: {key}")
+    return 0 if not missing else 1
+
+
+if __name__ == "__main__":
+    sys.exit(main())
```

- [ ] **Step 2: Verify.** Run: `python3 -m pytest -q && python3 -m ruff check src tests scripts`
  Expected: `204 passed`, `All checks passed!`.

- [ ] **Step 3: Commit.**

```bash
git add scripts/gate_check.py scripts/regression_replay.py
git commit -m "chore: gate_check and regression_replay scripts" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Retire the inbox-consuming playbook step (kern-sharma-resume)

The watcher's inbox is now append-only (spec, "Inbox writers").

**Files:** `/Users/kernsharma/projects/kern-sharma-resume/auto-tailor.md`, delete `auto-tailor-runner.ps1`

- [ ] **Step 1:** In `/Users/kernsharma/projects/kern-sharma-resume` on `main`, apply:

```diff
diff --git a/auto-tailor-runner.ps1 b/auto-tailor-runner.ps1
deleted file mode 100644
index 3b68392..0000000
--- a/auto-tailor-runner.ps1
+++ /dev/null
@@ -1,58 +0,0 @@
-# Runs the auto-tailor agent headless. Registered in Windows Task Scheduler
-# as "intern-radar-auto-tailor" (every 2 hours). Cheap when idle: pulls both
-# repos, exits without invoking Claude if the inbox is empty.
-
-$ErrorActionPreference = "Continue"
-$resume = "C:\Users\kerns\Projects\kern-sharma-resume"
-$radar = "C:\Users\kerns\Projects\intern-radar"
-$claude = "C:\Users\kerns\.local\bin\claude.exe"
-$log = Join-Path $resume "auto-tailor.log"
-
-function Log($msg) {
-    Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
-}
-
-# Cap the log at ~500KB by keeping the tail.
-if ((Test-Path $log) -and ((Get-Item $log).Length -gt 500KB)) {
-    Get-Content $log -Tail 1000 | Set-Content $log -Encoding utf8
-}
-
-# A killed run (shutdown mid-flight, closed console) leaves a "starting agent"
-# line with no matching finish; say so, loudly, where the next reader looks.
-if (Test-Path $log) {
-    $lastStart = Select-String -Path $log -Pattern ' - starting agent$' | Select-Object -Last 1
-    $lastEnd = Select-String -Path $log -Pattern 'agent finished with exit code' | Select-Object -Last 1
-    if ($lastStart -and ((-not $lastEnd) -or ($lastEnd.LineNumber -lt $lastStart.LineNumber))) {
-        Log "WARN: previous run died mid-flight (no finish line after its start) - inbox was left intact, reprocessing"
-    }
-}
-
-Set-Location $radar
-git pull --rebase --quiet 2>&1 | Out-Null
-Set-Location $resume
-git pull --rebase --quiet 2>&1 | Out-Null
-
-$inbox = Join-Path $radar "data\inbox.json"
-$empty = $true
-if (Test-Path $inbox) {
-    try {
-        $entries = Get-Content $inbox -Raw | ConvertFrom-Json
-        if ($entries -and @($entries).Count -gt 0) { $empty = $false }
-    } catch { Log "WARN: inbox.json unreadable, invoking agent to sort it out"; $empty = $false }
-}
-if ($empty) {
-    Log "inbox empty - skipped (no tokens spent)"
-    exit 0
-}
-
-Log ("inbox has {0} entries - starting agent" -f @($entries).Count)
-# Pass a short pointer, not the file body: PowerShell 5.1 mangles a long
-# multi-line string when it hands it to a native exe, and the agent was
-# receiving the instructions truncated mid-sentence.
-$playbook = Join-Path $resume "auto-tailor.md"
-$prompt = "Read $playbook and follow it exactly. It is your complete set of instructions."
-& $claude -p $prompt `
-    --allowedTools "Bash(python:*)" "Bash(git:*)" "Bash(gh:*)" "Read" "Write" "Edit" "Glob" "Grep" `
-    2>&1 | Add-Content -Path $log
-Log ("agent finished with exit code {0}" -f $LASTEXITCODE)
-exit $LASTEXITCODE
diff --git a/auto-tailor.md b/auto-tailor.md
index a709e6f..92537db 100644
--- a/auto-tailor.md
+++ b/auto-tailor.md
@@ -1,4 +1,12 @@
-# Auto-tailor agent instructions (run headless by auto-tailor-runner.ps1)
+# Auto-tailor agent instructions (RETIRED 2026-09-30)
+
+> **Retired.** The Windows runner that executed this playbook is gone, and
+> intern-radar's `data/inbox.json` is now **append-only**: only the watcher
+> writes it (intern-radar wider-watcher spec, "Inbox writers"). Never remove
+> entries from it. Wherever this file says "remove from inbox", record the
+> decision in this repo instead (`triage.json` / `done.json`). The Mac
+> auto-tailor spec (docs/superpowers/specs/2026-09-24-mac-auto-tailor-triage-design.md,
+> phase 2) replaces this playbook.
 
 You are the auto-tailor agent for Kern Sharma's internship pipeline, running
 locally on his machine. Repos: `C:\Users\kerns\Projects\intern-radar`
@@ -150,10 +158,6 @@ Skip any entry whose URL already appears in an existing
    must print 1. If 2+, trim the least JD-relevant bullets and re-render.
 
 ## Commit + push (both repos)
-- ..\intern-radar: rewrite data/inbox.json with only unprocessed entries
-  (empty list fine). Commit `chore: consume inbox [skip ci]`, push; on
-  rejection `git pull --rebase` then push, retry up to 3 times (the watcher
-  cron pushes every 30 min).
 - kern-sharma-resume: `git add` the new applications/ folders + SKIPPED.md,
   commit `Auto-tailor: <company> <title>[, ...]`, pull --rebase, push.
```

- [ ] **Step 2: Verify.** `grep -c "consume inbox" auto-tailor.md` prints `0`; `ls auto-tailor-runner.ps1` fails; `python3 -m pytest -q` still passes (107 passed, 2 skipped).
- [ ] **Step 3: Commit (local).** `git add -A auto-tailor.md auto-tailor-runner.ps1 && git commit -m "Retire inbox-consuming playbook step; delete Windows runner" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

---

### Task 14: Whole-branch verification (read-only against live boards)

- [ ] **Step 1:** `python3 -m pytest -q && python3 -m ruff check src tests scripts && python3 -m mypy src`
  Expected: `204 passed`, clean, `Success: no issues found in 25 source files`.
- [ ] **Step 2: Live regression replay** (read-only GETs; ~3 min): `PYTHONPATH=src python3 scripts/regression_replay.py`
  Expected: final line `... | missing 0 | ...`, exit 0. Source failures listed are tolerated (both runs exclude them).
- [ ] **Step 3: Live run in a throwaway copy** (no git, no notifications):
  `rm -rf /tmp/radar-try && cp -R /Users/kernsharma/projects/intern-radar /tmp/radar-try && cd /tmp/radar-try && env -u GITHUB_TOKEN -u GITHUB_REPOSITORY -u DISCORD_WEBHOOK_URL -u NTFY_TOPIC RADAR_GITHUB_ISSUES=0 PYTHONPATH=src python3 -m intern_radar`
  Expected: a `fetched … | boards polled ~440` line, `inbox: queued N for triage (N quietly, first board poll)` with **all** N quiet, no `- [ ]` notification lines, run under 3 minutes; `data/boards.json` has ≥ 540 boards; `data/health.json` alerts only `R3|vanshb03`. Then `rm -rf /tmp/radar-try`.
- [ ] **Step 4: Code review.** Dispatch superpowers:code-reviewer on `git diff main...wider-watcher-p1` against the spec (rev 6) phase 1.

---

### Task 15: Owner-gated deployment (each step needs the owner's go)

These publish to a public repository or change a live system; the implementer does not run them without the owner.

1. **Merge + push intern-radar** (`git switch main && git merge --ff-only wider-watcher-p1 && git push`). From the next Actions run the new code is live as the backup runner (it will run, since no `health.json` exists yet) and performs the quiet migration run.
2. **Push kern-sharma-resume** (Task 13 commit plus the local spec commits).
3. **Mac runner setup** per `docs/mac-runner.md`: fine-grained PAT → Keychain `radar-mac-pat`; dedicated clone; manual test run; `launchctl bootstrap` the plist.
4. **Actions secret** `NTFY_TOPIC`.
5. **Phase-1 gate after 7 days:** `PYTHONPATH=src python3 scripts/gate_check.py --phase 1 --expect "R3|vanshb03"` in the Mac clone → all PASS; `regression_replay.py` → missing 0.
