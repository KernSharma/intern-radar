# Wider watcher: design

Status: rev 1 (2026-09-30), from a brainstorm with the repo owner. Pending
council review, then owner approval. Delivered in three phases, each with an
acceptance gate.

This repository is **public**. This spec holds no personal data. Secrets (the
ntfy topic) live only in GitHub Actions secrets and the owner's macOS Keychain.

## Problem

The watcher's inbox (2,012 entries on 2026-09-30) has these sources:

| Source | Share of inbox | Share of tier-1 matches downstream |
|---|---|---|
| Simplify aggregator | 78% | **92%** (731 of 796) |
| 104 directly polled boards | 22% | 8% |

The watcher therefore effectively depends on one aggregator:
- **Coverage:** anything Simplify never lists is missed.
- **Speed:** a posting appears only after Simplify lists it.
- **Resilience:** if Simplify goes stale or changes format, volume collapses.

## Goals (owner, 2026-09-30: "all of the above")

1. **Coverage.** Add independent sources: two more internship lists, 441
   auto-discovered company boards, three new ATS readers, and job search
   engines (LinkedIn, Indeed, Glassdoor, Google Jobs).
2. **Speed.** Poll companies' own boards directly, so a posting is seen before
   aggregators list it, and measure the head start.
3. **Resilience.** No single source's failure drops volume much. Every source's
   health is tracked and alerted on.

## Non-goals

- Logged-in sources: Handshake (owner, 2026-09-30: "not now"). The search
  engines are used logged-out only.
- Crawlers for custom career sites (83 hosts, about 4% of volume).
- Readers for small ATSes: Taleo, Paylocity, Rippling, Jobvite, etc.
- Paid data feeds.
- Any change to filters' intent or to triage.

## Decisions (owner)

| Decision | Choice |
|---|---|
| Goal (09-30) | Coverage + speed + resilience |
| Allowed sources (09-30) | Anything, including LinkedIn and Indeed. Implemented logged-out, to protect the owner's accounts |
| Approach (09-30) | A: layered watcher built in this repo (not a paid feed, not a crawler) |
| Handshake (09-30) | Not now |

## Architecture

```
CLOUD (GitHub Actions, cron 7,37 * * * *), the ONLY writer of inbox.json / seen.json
  L1 lists:       simplify (existing), vanshb03, speedyapply
  L2 boards:      data/boards.json registry → poll by tier → workday, greenhouse,
                  ashby, lever, smartrecruiters (existing) + oracle, icims, eightfold (new)
  L3 ingest:      data/search-feed.json (written by the Mac job)
  → existing filters → dedupe → inbox.json, seen.json
  → discovery (boards.json) → health (health.json) → alerts
MAC (launchd, every 2 h at :55)
  L3 search:      JobSpy over linkedin, indeed, glassdoor, google (logged out)
                  → link tracing → commits ONLY data/search-feed.json
```

**Single-writer rule.** Only the cloud job writes `inbox.json`, `seen.json`,
`boards.json` and `health.json`. The Mac job writes only
`search-feed.json`. The two jobs never modify the same file.

**Source interface.** Each source is `fetch(...) -> list[Posting]` in
`sources/<name>.py`, as today. One failing source never aborts the run. The
existing per-source `try`/log becomes a `SourceResult{name, ok, error,
postings, seconds}` that health consumes.

**`Posting` gains two fields:**
- `first_source` is the name of the source that first produced this key.
- `first_seen_at` is an ISO UTC timestamp set once, when the key is first seen.

Both are stored in `inbox.json` entries. For keys already present on
rollout, `first_source` is taken from the existing `source` field and
`first_seen_at` from the existing `added` date.

**Dedupe.** The primary key is the existing URL key (`normalize_url`). A
posting with a traced canonical URL (L3) uses that. Postings with different
keys but the same `role id` (sha1 of normalized company|title) are **not**
merged in the inbox, because triage already groups them. The health
"first-seen" statistic compares across sources by role id.

## L1: lists

| Source | Format | Endpoint | Notes |
|---|---|---|---|
| `simplify` | JSON (existing) | existing | unchanged |
| `vanshb03` | JSON, same schema as Simplify's `listings.json` | `https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json` | Reuses `parse_simplify`. Its `season` values are `Summer`/`Fall`/…; only `Summer` counts, and it maps to the filter term `Summer 2027`. Last updated 2026-08-23, so the staleness alert is expected |
| `speedyapply` | Markdown tables in `README.md` | `https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md` | Parse table rows whose header is `Company \| Position \| Location \| [Salary \|] Posting \| Age`. Company = the text inside `<strong>`. URL = the `href` in the Posting cell. Age `Nd` → posted = run date − N days (`Nh` = today). The README holds USA internships (INTL and new-grad are other files, never read). Term: none, so the untermed title rules apply |

## L2: board discovery and polling

**`boards.board_of(url) -> (ats, board) | None`**:
- `workday`: `<tenant>.<wdN>.myworkdayjobs.com/[<locale>/]<site>/...` → `f"{tenant}.{wdN}/{site}"`. The locale matches `^[a-z]{2}-[A-Z]{2}$` and is dropped.
- `greenhouse`: `(job-)?boards.greenhouse.io/<board>/...` → `<board>`. A URL containing `gh_jid=<id>` on another host → the board comes from `GET https://boards.greenhouse.io/embed/job_app?token=<id>` (no redirect-follow). It's the `for=` parameter of `Location`, and the result is cached in `boards.json` under `gh_custom:<host>`.
- `ashby`: `jobs.ashbyhq.com/<org>/...` → `<org>`.
- `lever`: `jobs.lever.co/<company>/...` → `<company>`.
- `smartrecruiters`: `jobs.smartrecruiters.com/<company>/...` → `<company>`.
- `oracle`: `<host>` ending in `oraclecloud.com` with path containing `/hcmUI/CandidateExperience/<lang>/sites/<site>/` → `f"{host}|{site}"`.
- `icims`: `<host>` ending in `.icims.com` → `<host>`.
- `eightfold`: `<host>` ending in `eightfold.ai`, or a host whose page embeds `eightfold` (not auto-detected, config only) → `<host>`.
- Anything else → `None`.

**`data/boards.json`** (committed): `{"version": 1, "boards": {"<ats>:<board>": {ats, board, pinned: bool, discovered_via: source|null, first_seen, last_ok, last_match, consecutive_errors, disabled: bool}}}`.
- Every board in `config.toml` is seeded with `pinned: true`.
- On rollout, every URL in the current `inbox.json` is run through `board_of`, which adds 441 boards (measured 2026-09-30):

| Site | Boards added |
|---|---|
| workday | 245 |
| greenhouse | 50 + 25 custom |
| icims | 35 |
| ashby | 34 |
| oracle | 28 |
| smartrecruiters | 10 |
| lever | 10 |
| eightfold | 4 |

- Each run adds boards from that run's postings, from every source including the search feed.

**Polling tiers** are a pure function `due(board, run_index) -> bool`, where
`run_index = floor(unix_time / 1800)`:

| Condition | Polled |
|---|---|
| `pinned`, or `last_match` within 60 days | every run |
| `last_match` within 60–180 days, or never matched but `first_seen` within 14 days | `run_index % 4 == hash(board) % 4` |
| otherwise | `run_index % 48 == hash(board) % 48` (daily) |
| `disabled` | never |

- `hash` = the first 8 hex digits of `sha1(key)`, taken as an int, so load spreads evenly across runs.
- A board is disabled after `consecutive_errors ≥ 10`, which raises a health alert. It's re-enabled automatically when any source produces a posting on that board.
- **Concurrency:** a `ThreadPoolExecutor(max_workers=8)` with at most 2 requests in flight per host (a per-host semaphore), using the existing `http` retry and backoff.
- **Budget:** a run must finish in under 12 minutes (the job timeout is 15). If the elapsed time passes 10 minutes, remaining non-pinned boards are skipped and recorded as `skipped_budget` in health.

**Workday** (existing reader, extended):
- `POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` with `{"appliedFacets": {}, "limit": 20, "offset": n, "searchText": "intern"}`. Pages continue while `offset < total`, capped at 5 pages. Workday returns zero rows for `limit` > 20.
- `postedOn` ("Posted Today", "Posted Yesterday", "Posted N Days Ago", "Posted 30+ Days Ago") → a posted date (30+ → run date − 30).

**New readers:**
- **`oracle`:**
  - `GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=<site>,facetsList=NONE,keyword=intern,limit=25,offset=<n>`, paging to 4 pages.
  - `requisitionList[]` → title `Title`, id `Id`, location `PrimaryLocation`, posted `PostedDate`.
  - The URL is `https://<host>/hcmUI/CandidateExperience/en/sites/<site>/job/<Id>`.
  - Oracle documents this endpoint as internal, but career sites call it anonymously. It is verified on 3 inbox tenants at the phase-2 gate; a tenant that returns 401 or 403 is disabled with a note.
- **`icims`:**
  - `GET https://<host>/jobs/search?ss=1&searchKeyword=intern&in_iframe=1&pr=<page>` with a browser User-Agent, paging to 3 pages.
  - Parse `a.iCIMS_Anchor` (href `/jobs/<id>/<slug>/job`) plus the title text, and the location from the row's `.iCIMS_JobHeaderData` or header field.
  - The URL is the job href without query parameters.
  - Verified on 5 inbox hosts at the phase-2 gate. A host whose HTML yields 0 anchors on a page that says "results" is flagged as `parse_error`.
- **`eightfold`:**
  - `GET https://<host>/api/apply/v2/jobs?domain=<domain>&query=intern&start=<n>&num=10`, paging to 5 pages; `domain` is configured per board.
  - On 401 or 403, the fallback is `GET https://<host>/api/pcsx/search?domain=<domain>&query=intern&start=<n>`.
  - Title `name`, location `locations[]`, URL `canonicalPositionUrl`.

All new readers produce untermed postings. The existing untermed filter rules
apply (the title must say intern or co-op; the 2026-term and non-technical
excludes). No reader performs any write, POST (except Workday's search, which
is read-only) or login.

## L3: search engines (Mac)

**Code.** `search/radar_search.py` in this repo, run as its own program. It
does not import the watcher, and uses `intern_radar.models.normalize_url` via
`PYTHONPATH`.
- Environment: `~/.local/share/radar-search/venv` with `python-jobspy==<pinned version at implementation>` (Python 3.10+). The pin is updated deliberately, never floating.
- Config is `search/search.toml`:
  - `sites = ["linkedin","indeed","glassdoor","google"]`
  - `location = "United States"`
  - `country_indeed = "USA"`
  - `results_per_query = 100`
  - `hours_old_first_run = 720`, `hours_old = 72`
  - `delay_seconds = [3, 8]`
  - `terms` = "software engineer intern", "software engineering internship summer 2027", "data science intern", "machine learning intern", "data engineer intern", "quantitative developer intern"
  - Google Jobs uses `google_search_term = "<term> jobs in United States since yesterday"`.
- **Run:** for each site × term, call `scrape_jobs(...)`. For LinkedIn, `linkedin_fetch_description=True`, needed to get `job_url_direct`.
  - Between calls: a random sleep in `delay_seconds`.
  - A site is **blocked for this run** after a 429, a response containing a CAPTCHA page, or 3 consecutive empty results for terms that yielded before. It's skipped for the rest of the run and recorded.
  - No logins, cookies or persistent sessions.
- **Filtering (before writing):** untermed title rules (the same `FilterConfig`) **plus** the description (when present) must contain `2027` or `summer` (case-insensitive). Postings without a description need a title containing `2027` or `summer`.
- **Link tracing:**
  1. If `job_url_direct` is present and http(s), use it.
  2. Otherwise follow redirects of `job_url` with `GET` (at most 5 hops, 10 s timeout, the same private-address refusal as `jd.py`'s fetcher) and take the final URL.
  3. If `board_of(final)` is not `None`, the posting's URL is the traced URL (its canonical key) and `traced: true`.
  4. Otherwise the URL stays the search engine's `job_url` and `traced: false`.
- **Output:** `data/search-feed.json` = `{"version":1, "generated_at": iso, "sites": {site: {ok, blocked, error, results, kept}}, "postings": [{url, company, title, locations, source: "<site>", posted_at, traced}]}`. Postings older than 7 days are dropped.
- **Commit:** under `mkdir ~/.local/state/radar-search/lock`:
  1. `git -C <repo> pull --rebase -q`
  2. write the file
  3. `git add data/search-feed.json && git commit -m "chore: search feed [skip ci]" && git push`
  4. On a push rejection, rebase and retry up to 3 times.
  - It never stages any other path.
- **launchd:** `com.kernsharma.radar-search`, `StartCalendarInterval` Minute 55 of every even hour, `RunAtLoad` false, logs in `~/.local/state/radar-search/`.

**Cloud ingest:** a source `search_feed` reads `data/search-feed.json` from
the checkout. It yields its postings (as `Posting`, with `source` = the
site), and health records `generated_at` age.

## L4: health

**`data/health.json`** (committed, public): `{"version":1, "runs": [last 48 {at, seconds, sources:{name:{ok, error, postings, matches, seconds}}, boards_polled, boards_skipped_budget}], "boards_summary": {ats: {total, disabled}}, "first_seen": {"window_days": 30, "pairs": {"<a>|<b>": {"n", "median_hours_a_before_b"}}}}`.
- `first_seen` compares each pair of sources over roles (role id) seen by both within the window.

**Alert rules**, evaluated at the end of each run:
1. A source with ≥1 match in the last 7 days gets 0 matches for 6 consecutive runs.
2. A source errors in 3 consecutive runs.
3. A list source's newest item timestamp is more than 7 days old (vansh uses `date_updated`, speedyapply the minimum Age).
4. `search-feed.json` `generated_at` is more than 6 h old.
5. The run took more than 12 minutes, or any `skipped_budget` > 0 for 3 consecutive runs.
6. A board was newly disabled.

**Alert delivery:**
- One GitHub issue per `(rule, subject)`, labelled `health`, opened once and commented on at most daily while the condition holds, and closed automatically when it clears. These issues are excluded from the one-time watcher-issue cleanup script's filter by title.
- Plus an ntfy push (Actions secret `NTFY_TOPIC`; absent means no push) with content-free text: `"intern-radar health: 2 open alerts"`, at most once per 6 h.

**Weekly summary:** every Monday's first run writes
`data/health-weekly.md` with:
- postings per source;
- unique finds (roles found only by that source) per source;
- the median head start of each direct-ATS source over `simplify`;
- the list of disabled boards.

## Testing

pytest, with fixtures in `tests/fixtures/`:
- **Parsers:** Oracle JSON; iCIMS HTML (2 layouts); Eightfold v2 JSON and pcsx JSON; speedyapply README excerpt (with and without the Salary column); vanshb03 JSON; Workday `postedOn` variants; a JobSpy output rows fixture (a list of dicts, no pandas in the watcher tests).
- **`board_of`:** a table of 25 URLs: locales, `gh_jid` custom (using a fake redirect server), each ATS, and unknowns → None.
- **Tiers:** `due()` across pinned, 30-day, 90-day, new and 400-day boards and the disabled state, at several run indices; the spread is uniform across 4 and 48 buckets.
- **Budget:** a fake clock past 10 minutes skips non-pinned boards and records them.
- **Discovery:** postings from each source add boards; an existing board is not duplicated; a disabled board re-enables on a new posting.
- **Link tracing:** a fake server with a 3-hop redirect to a Workday URL gives `traced: true` and the canonical key; a redirect to `10.0.0.1` is refused; 6 hops stops at 5.
- **Search filter:** the description rule, and the no-description title rule.
- **Health:** each alert rule fires and clears; issue dedupe (one issue per rule and subject); the ntfy rate limit; `first_seen` median computation.
- **Regression:** a recorded run of the current six sources through the new pipeline produces a superset of the old run's inbox keys.
- The existing suite passes. The **search program** is tested separately with `scrape_jobs` monkeypatched.

## Phased rollout

1. **Phase 1: lists, discovery, tiers, health (cloud).**
   - Covers: vanshb03, speedyapply, `boards.json` + seeding, `board_of`, tiered polling with concurrency and budget, `SourceResult`, `health.json`, alerts, the weekly summary, `first_source`/`first_seen_at`.
   - **Gate:**
     - 7 days of scheduled runs, all under 12 minutes and with `skipped_budget` 0 on ≥ 90% of runs.
     - The regression superset holds on live data.
     - `health-weekly.md` shows the direct-ATS head start.
     - The owner reviews the alert issues raised.
2. **Phase 2: new readers.** Oracle, iCIMS, Eightfold.
   - **Gate:** Oracle verified on 3 tenants and iCIMS on 5 hosts, each yielding ≥1 posting overall; each reader's error rate below 5% over 3 days; tenants that fail are disabled with notes.
3. **Phase 3: search engines (Mac).**
   - Covers: `search/radar_search.py`, the venv, launchd, link tracing, the cloud ingest source.
   - **Gate:**
     - 3 days of feeds, with `generated_at` never more than 6 h stale while the Mac is awake.
     - The report includes: kept postings per site, the traced rate, unique finds, and boards discovered through traced links.
     - Any site blocked on more than 50% of runs is removed from `sites` with a note.

## Open follow-ups (not in this change)

- Handshake (logged in), if the health stats show a coverage gap.
- Custom career-site crawlers.
- Readers for small ATSes when volume justifies them.
- The auto-submit fast lane reads `first_seen_at` for freshness.
