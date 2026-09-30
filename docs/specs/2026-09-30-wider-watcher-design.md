# Wider watcher: design

Status: rev 2 (2026-09-30).
- Rev 1 came from a brainstorm with the repo owner.
- Council round 1 (feasibility, tested against live endpoints; completeness + security) found 7 blockers and 17 should-fixes. All are resolved here, and the owner made two new decisions.
- Pending: council round 2, then owner approval.
- Delivered in three phases.

This repository is **public**. This spec contains no personal data.
Secrets live only in GitHub Actions secrets and the owner's macOS Keychain.
Data scraped from search engines never enters this repository.

## Problem

1. **One aggregator.** The inbox (2,012 entries on 2026-09-30) is 78% from Simplify. Simplify also supplies 92% of the tier-1 matches downstream (731 of 796), and the 104 directly polled boards add the rest. Coverage, speed and resilience all hinge on one aggregator.
2. **The cron is throttled.** GitHub runs the `7,37 * * * *` cron only **4–7 times a day**. The last 100 scheduled runs show 4 runs a day on Sep 28–30, at 00:22, 05:56, 12:27 and 18:06. The watcher therefore effectively polls every 4–6 hours.

## Goals (owner, 2026-09-30: "all of the above")

1. **Coverage:** more lists, auto-discovered company boards, three new ATS readers, and search engines.
2. **Speed:** real 30-minute polling while the owner's Mac is awake. Measure the direct-poll head start over Simplify.
3. **Resilience:** no single source or runner failure stops the pipeline; health tracking and alerts.

## Non-goals

- Logged-in sources: Handshake is "not now" (owner, 09-30). Search engines are used logged-out only.
- Custom career-site crawlers, including the 25 iCIMS custom domains.
- Readers for Taleo, Paylocity, Rippling and Jobvite.
- Paid data feeds.
- Changing filter intent.

## Decisions (owner)

| Decision | Choice |
|---|---|
| Goal (09-30) | Coverage + speed + resilience |
| Sources (09-30) | Anything, including LinkedIn and Indeed, implemented logged-out |
| Approach (09-30) | Layered watcher in this repo |
| Handshake (09-30) | Not now |
| Runner (09-30) | **Mac primary** (launchd, every 30 min while awake) **plus GitHub Actions as backup** |
| Search-engine data (09-30) | **Private repo `intern-radar-feed`**. The public repo only ever stores postings traced to the company's own ATS URL. Untraced search-engine postings stay private and reach triage directly |

## Runners and the write protocol

Two runners execute the **same** watcher program (`python -m intern_radar`) against this repository:

**Mac runner (primary):**
- A dedicated clone at `~/.local/share/intern-radar/repo`, never the owner's working copy.
- launchd `com.kernsharma.radar-watch`, `StartCalendarInterval` minutes 7 and 37 of every hour, `RunAtLoad` false. Python is `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, stdlib only. Logs go to `~/.local/state/intern-radar/`.
- Environment:
  - `GITHUB_TOKEN` comes from `gh auth token` at start. It's used for issues and for pushing through the existing gh credential helper.
  - `DISCORD_WEBHOOK_URL` and `NTFY_TOPIC` are read from Keychain items `radar-discord-webhook` and `ntfy-topic`. An absent item means that notifier is off.
  - `RADAR_FEED_PATH=~/.local/share/radar-search/feed/search-feed.json`.

**Actions runner (backup):**
- The existing `watch.yml`, unchanged in schedule.
- New step before running: if `data/health.json` at the checked-out HEAD has `last_run_at` within 50 minutes, the job exits 0 without running (the Mac is active).
- New env:
  - `NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}`
  - `FEED_TOKEN: ${{ secrets.FEED_TOKEN }}` (a fine-grained PAT, read-only, `intern-radar-feed` contents only)
  - `RADAR_FEED_URL=https://api.github.com/repos/<owner>/intern-radar-feed/contents/search-feed.json`
- Triggers remain `schedule` + `workflow_dispatch` only. **`pull_request_target` or any PR trigger must never be added.**

**Write protocol (both runners):**
1. `git pull --rebase` at start.
2. Run; write files.
3. Commit `data/`.
4. `git push`. On rejection: `git pull --rebase`.
   - If the rebase conflicts on `data/`, **discard this run's commit** (`git rebase --abort && git reset --hard origin/<branch>`) and exit 0.
   - Discarding is safe. `seen.json` is discarded together with the `inbox.json` additions, so the next run re-finds the same postings.
   - Up to 3 attempts.
5. `health.json` records `last_run_at` and `runner` (`mac`|`actions`).

**Inbox writers.** The watcher only **appends** to `inbox.json`. The
kern-sharma-resume consumer only **removes** processed entries.
`append_inbox` re-reads the file immediately before writing. That keeps the
existing two-writer contract.

## Architecture

```
WATCHER (Mac every 30 min, or Actions backup)
  L1 lists:    simplify (existing), vanshb03, speedyapply
  L2 boards:   data/boards.json → due() by last_polled → workday, greenhouse, ashby, lever,
               smartrecruiters (existing) + oracle, icims, eightfold (new)
  L3 ingest:   search feed (local file on Mac / GitHub API on Actions) → TRACED postings only
  → filters → dedupe (url key + canon.json) → inbox.json, seen.json
  → discovery → sightings.json → health.json → alerts
SEARCH JOB (Mac, every 2 h at :55) → private repo intern-radar-feed/search-feed.json
  JobSpy (linkedin, indeed, glassdoor, google; logged out) → trace links
UNTRACED search postings → never public; kern-sharma-resume triage reads them from the local feed clone
```

**Source interface:** `fetch(...) -> list[Posting]`, one module per
`sources/<name>.py`. Each fetch is wrapped in a
`SourceResult{family, name, ok, error, postings, seconds}`. `family` is one
of:
- the lists: `simplify`, `vanshb03`, `speedyapply`;
- the search feed: `search`;
- the ATS readers: `workday`, `greenhouse`, `ashby`, `lever`, `smartrecruiters`, `oracle`, `icims`, `eightfold`.

A failure never aborts the run.

**Fetch order** (it defines `first_source` within a run): simplify,
vanshb03, speedyapply, the boards (in `boards.json` key order), search.

## Dedupe

- **Storage key**: `normalize_url` is **unchanged**, so existing `seen.json`, inbox and tracker keys stay valid.
- **Canonical key** (dedupe only), `canon_key(url)`:
  - Take `normalize_url(url)`.
  - Workday: drop a path segment matching `^[a-z]{2}-[a-z]{2}$`. It is lowercase here because `normalize_url` lowercases paths.
  - iCIMS: rewrite `/jobs/<id>[/<slug>]/job[?…]` to `/jobs/<id>/job` with no query.
  - Others: unchanged.
- **`data/canon.json`** = `{canon_key: storage_key}`, maintained for every matched posting.
  - A new posting whose `canon_key` is already present counts as seen: `seen.json` is refreshed for the stored storage key, and nothing is appended.
  - Entries are pruned when their storage key is pruned from `seen.json` (365 days).

## L1: lists

| Source | Endpoint | Parsing |
|---|---|---|
| `simplify` | existing | unchanged |
| `vanshb03` | `https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json` | A new wrapper, `parse_vansh`, reuses the row mapping of `parse_simplify`, with `source="vanshb03"` and key `vanshb03:<id>`. **Term:** `season == "Summer"` maps to `terms=("Summer 2027",)` **only if** `date_posted` ≥ 2026-06-01 (epoch) **and** the title contains no 4-digit year other than 2027. Otherwise `terms=()`, and the untermed title rules apply. Only `active and is_visible` rows are used. The last update was 2026-08-23, so the staleness alert is expected |
| `speedyapply` | `https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md` | Parse table rows under a header that is exactly `\| Company \| Position \| Location \| Salary \| Posting \| Age \|` or the same without `Salary`. Company = the text of the first `<strong>`; title = Position; location = Location; URL = the `href` of the Posting cell's `<a>`; Age `^(\d+)d$` → posted = run date (UTC) − N days. Other Age formats → posted unknown. No term, so the untermed title rules apply |

## L2: board discovery and polling

**`boards.board_of(url) -> (ats, board) | None`**. Host matching is **exact,
or a dot-boundary suffix** (`host == s or host.endswith("." + s)`). Every
host label must match `^[a-z0-9-]{1,63}$`, otherwise the result is `None`.
The mappings:
- `workday`: host `<tenant>.<wdN>.myworkdayjobs.com`, with `tenant` matching `^[a-z0-9_-]+$` and `wdN` matching `^wd\d{1,2}$`. The first path segment that isn't a locale is `site` (`^[A-Za-z0-9_-]+$`). → `f"{tenant}.{wdN}/{site}"`.
- `greenhouse`: host `boards.greenhouse.io` or `job-boards.greenhouse.io` → the first path segment. On any other host with query `gh_jid=<digits>`, the board is resolved **only on the watcher** (never on the search job): `GET https://boards.greenhouse.io/embed/job_app?token=<id>` without following redirects, and the board is the `for=` parameter of `Location` (host `job-boards.greenhouse.io`, verified). The result is cached in `boards.json` as `gh_custom:<host> → <board>`.
- `ashby`: `jobs.ashbyhq.com` → the first segment.
- `lever`: `jobs.lever.co` → the first segment.
- `smartrecruiters`: `jobs.smartrecruiters.com` → the first segment.
- `oracle`: suffix `oraclecloud.com`, with path containing `/hcmUI/CandidateExperience/<lang>/sites/<site>/` (case-insensitive) → `f"{host}|{site}"`.
- `icims`: suffix `icims.com` → `host`. The 25 custom-domain iCIMS hosts are out of scope.
- `eightfold`: suffix `eightfold.ai` → `host`.
- Anything else → `None`.

**Config** (`config.toml`, parsed into new `SourcesConfig` fields):
- `vanshb03 = true`, `speedyapply = true`.
- `[sources.oracle] boards = ["<host>|<site>", …]`.
- `[sources.icims] hosts = […]`.
- `[sources.eightfold] boards = [{host = "…", domain = "…"}]`. This needs a new table-list parser, because `_str_tuple` can't read tables.

Config boards are the **pinned** set.

**`data/boards.json`** (committed): `{"version":1, "gh_custom":{host: board}, "boards": {"<ats>:<board>": {ats, board, pinned, discovered_via, first_seen, last_polled, last_ok, last_match, consecutive_errors, disabled, needs_config}}}`.
- Keys are casefolded, and dates are ISO UTC.
- On every run, every config board is upserted with `pinned: true`. A board removed from config becomes `pinned: false`; it is never deleted by that.
- **Seeding** runs every current `inbox.json` URL through `board_of` and adds 441 boards (measured 2026-09-30):

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

- Each run adds boards from its matched postings from every source, including traced search postings.
- A discovered `eightfold` board with no config entry gets `needs_config: true` and is never polled until the owner adds its `domain` to config.
- **Limits:**
  - A non-pinned board with no match for 400 days is deleted.
  - There are at most 2,000 non-pinned boards; beyond that, the oldest `last_match` is evicted first (then the oldest `first_seen`).

**Due rule**, based on wall-clock time. Runs happen every 30 minutes on the Mac and about 5 times a day on Actions. `due(board, now)` means `now − last_polled ≥ interval(board)` (a board never polled is due), where:

| Condition | interval |
|---|---|
| `pinned`, or `last_match` within 60 days, or `first_seen` within 14 days | 0 (every run) |
| `last_match` within 60–180 days | 6 h − jitter |
| otherwise | 24 h − jitter |
| `disabled` or `needs_config` | never |

- `jitter = bucket(key) % 60` minutes, where `bucket(key) = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)`.
- A board is disabled after 10 consecutive errors, which raises a health alert. It's re-enabled when any source produces a posting on that board.

**Concurrency and budget:**
- `ThreadPoolExecutor(max_workers=8)`, with a per-host semaphore of 2, using the existing `http` retries and backoff.
- At an elapsed time of 10 minutes, due non-pinned boards that haven't started are skipped and counted as `skipped_budget`.
- **Projected first run with the depth caps below:** about 1,400 s of requests serially, which is about 3–4 minutes at 8 workers. Measured timings: Workday about 1.0 s per page, Greenhouse 0.06–0.97 s, Oracle 0.5–1.0 s, iCIMS 0.15–0.5 s.

**Readers and depth caps:**
- **workday** (existing reader):
  - `POST …/wday/cxs/<tenant>/<site>/jobs` with `{"appliedFacets":{}, "limit":20, "offset":n, "searchText":"intern"}`. `limit` > 20 gives HTTP 400.
  - **Pinned boards keep the existing `MAX_RESULTS = 1000`.** The fuzzy search buries intern roles deep: 25 of 26 were past offset 200 on Palo Alto Networks.
  - **Discovered boards stop at 5 pages (100).** A discovered board whose `total` exceeds 100 gets `deep: true` in `boards.json`, and health lists it so the owner can pin it.
  - `postedOn`: "Posted Today" → today; "Posted Yesterday" → −1; "Posted N Days Ago" → −N; "Posted 30+ Days Ago" → −30.
- **oracle** (new; verified anonymously on eofe/CX_1001, ibqbjb/Honeywell and fa-eowa/CSXCareers):
  - `GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=<site>,facetsList=NONE,keyword=intern,limit=200,offset=<n>`.
  - Rows are in `items[0].requisitionList[]` with fields `Title`, `Id`, `PrimaryLocation`, `PostedDate` (`YYYY-MM-DD`). Paging uses `items[0].TotalJobsCount`, up to 5 pages (1,000).
  - The URL is `https://<host>/hcmUI/CandidateExperience/en/sites/<site>/job/<Id>`.
  - A 401 or 403 disables the board with the note `oracle_auth`.
- **icims** (new; verified on 3 hosts):
  - `GET https://<host>/jobs/search?ss=1&searchKeyword=intern&in_iframe=1&pr=<page>` (page 0-based) with a browser `User-Agent`, up to 3 pages, stopping when the text "Page N of M" shows N ≥ M.
  - Rows are `li.iCIMS_JobCardItem`, and the anchor is the `a` whose class list **exactly contains** `iCIMS_Anchor` (not `iCIMS_Anchor_Nav`). Its absolute `href` has the query dropped and is canonicalized. The title is the `h3` text.
  - Location is the `dd.iCIMS_JobHeaderData` whose preceding `dt` text contains "Location".
  - A page containing "results" but yielding 0 rows records `parse_error`.
- **eightfold** (new; the v2 endpoint returns 403 "Not authorized for PCSX" on all 4 tenants, so **pcsx is primary**):
  - `GET https://<host>/api/pcsx/search?domain=<domain>&query=intern&start=<n>`. There are 10 rows per page (fixed), paging by `data.count`, up to 20 pages.
  - Rows are `data.positions[]` with fields `name`, `locations[]`, `positionUrl` (relative) and `postedTs` (epoch seconds). The URL is `https://<host>` + `positionUrl`.
  - `domain` comes from config (verified values are `<company>.com`, e.g. `paypal.com`).
- The existing greenhouse, ashby, lever and smartrecruiters readers are unchanged.

All readers are read-only (the Workday search POST is read-only), and none of them log in. New readers produce untermed postings, and the existing untermed title rules apply.

## L3: search engines (Mac, private data)

**Repos and paths:**
- The private repo `<owner>/intern-radar-feed` holds only `search-feed.json` and a README. It's cloned at `~/.local/share/radar-search/feed`.
- The program is `search/radar_search.py` **in this public repo** (code is public, data isn't). It runs from a dedicated clone at `~/.local/share/radar-search/code`, pulled at the start of each run.

**Environment:**
- `~/.local/share/radar-search/venv`, **built from Python 3.12** (`/opt/homebrew/bin/python3.12`, installed with `brew install python@3.12` at the phase-3 go). JobSpy pins `numpy==1.26.3`, which has no 3.13 wheels.
- `python-jobspy==1.1.82`.
- It imports only these watcher modules, all stdlib-only: `intern_radar.models`, `config`, `filters` and `boards`. `board_of` never performs the `gh_jid` network lookup here.

**Config** `search/search.toml`:
- `sites = ["linkedin","indeed","glassdoor","google"]`
- `location = "United States"`, `country_indeed = "USA"`, `job_type = "internship"`
- `results_per_query = 100`
- `hours_old_first_run = 720`, `hours_old = 72`
- `delay_seconds = [3, 8]`
- `terms` = "software engineer intern", "software engineering internship summer 2027", "data science intern", "machine learning intern", "data engineer intern", "quantitative developer intern"
- Google Jobs uses `google_search_term = "<term> jobs in United States since yesterday"`.
- LinkedIn uses `linkedin_fetch_description=True`, which is the 1.1.82 parameter name; it is renamed only when the pin changes.

**Run:**
- Call `scrape_jobs` for each site × term, with a random sleep in `delay_seconds` between calls.
- A site is **blocked for this run** after an exception mentioning 429, a CAPTCHA page, or 3 consecutive empty results for terms that yielded before; it's then skipped. LinkedIn is expected to rate-limit around its 10th page.
- No logins, no cookies, no persistence.

**Filter:** the untermed title rules (the same `FilterConfig`), plus the description (when present) must contain `2027` or `summer` (case-insensitive). Postings without a description need `2027` or `summer` in the title.

**Link tracing (SSRF-safe):**
1. The candidate is `job_url_direct` if it's http(s), otherwise `job_url`.
2. Follow redirects **manually**, with automatic following off, up to 5 hops. Each hop must be `http` or `https` on port 80 or 443.
3. Resolve the hop's host with `socket.getaddrinfo`. Every resolved address must satisfy `ipaddress.ip_address(a).is_global`, which rejects IPv4-mapped private addresses, CGNAT and 169.254.0.0/16; otherwise refuse.
4. Connect to **the vetted IP**, with an explicit `Host` header and TLS SNI set to the hostname, so DNS rebinding between check and connect is impossible.
5. The timeout is 10 s, and at most 1 MB of body is read.
6. If `board_of(final)` is not `None`, the posting URL is the final URL and `traced: true`. Otherwise it keeps the search-engine `job_url` and `traced: false`.

**Output:** `search-feed.json` = `{"version":1, "generated_at", "sites": {site: {ok, blocked, error, results, kept, traced}}, "postings": [{url, company, title, locations, source, posted_at, traced}]}`.
- Postings older than 7 days are dropped.
- Strings are stored raw, for private use.

**Commit:** in the feed clone, under the lock `~/.local/state/radar-search/lock` (a `mkdir` lock holding a PID; a lock older than 3 h is stale and removed):
1. `git pull --rebase`.
2. Write the file.
3. `git add search-feed.json && git commit -m "feed" && git push`, retrying up to 3 times.

The push uses the owner's existing gh credential. Nothing else is staged.

**launchd:** `com.kernsharma.radar-search`, minute 55 of every even hour, `RunAtLoad` false, logs in `~/.local/state/radar-search/`.

**Watcher ingest** (`sources/search_feed.py`):
- It reads `RADAR_FEED_PATH` (Mac), or on Actions `RADAR_FEED_URL` with `FEED_TOKEN`, using the GitHub contents API with `Accept: application/vnd.github.raw`.
- **Validation:** file ≤ 2 MB; ≤ 3,000 postings; `url` https; `title` ≤ 200 characters; `company` ≤ 100; control characters stripped. A malformed feed fails only this source.
- **Only `traced: true` postings** become `Posting`s, and only their canonical URL, company, title, locations and posted date are used. `source = "search"`: the site name is never written publicly. Nothing else from the feed reaches public files.

**Untraced postings → triage (private).** kern-sharma-resume's `triage.py` adds one input: when `~/.local/share/radar-search/feed/search-feed.json` exists, its `traced: false` postings join the watcher inbox for grouping and triage, with `added` = `posted_at` or today. They are always treated as live, since they're outside `seen.json`. Auto-submit treats their URLs as `other:<host>` (manual). This is a one-function change in kern-sharma-resume; it has its own test there and ships in phase 3.

The feed's staleness is expected while the Mac sleeps. Health alerts on it only outside a quiet window.

## L4: health

**`data/sightings.json`** (committed): `{"version":1, "keys": {canon_key: {family: first_seen_at ISO-UTC}}}`.
- Written for every **matched** posting from every family, including `search` (traced only); pruned after 30 days.
- The head start and unique finds are computed from it, by canonical key, which is exact. Simplify often links the same ATS URL.

**`data/health.json`** (committed, public):
```json
{"version": 1, "last_run_at": "iso", "runner": "mac|actions",
 "runs": [{"at", "runner", "seconds", "families": {"<family>": {"ok", "errors", "postings", "matches", "seconds"}},
           "boards_polled", "boards_skipped_budget"}],
 "alerts": {"<rule>|<subject>": {"issue", "opened_at", "last_comment_at"}},
 "weekly_written_for": "YYYY-Www"}
```
- `runs` keeps the last 200 entries.
- Per-board state lives only in `boards.json`.

**Alert rules**, evaluated at the end of each run, with wall-clock windows:

| Rule | Condition |
|---|---|
| R1 | A family with ≥1 match in the prior 7 days has 0 matches across all runs in the last 24 h |
| R2 | A family errors on every run in the last 6 h, with at least 2 runs |
| R3 | A list's newest item is more than 7 days old. vanshb03 uses max `date_updated`; speedyapply uses its minimum Age |
| R4 | The search feed's `generated_at` is more than 6 h old, evaluated only between 09:00 and 23:00 America/New_York (the quiet window covers sleep) |
| R5 | A run took more than 12 minutes, or `boards_skipped_budget` > 0 on every run in the last 3 h |
| R6 | A board was disabled or newly marked `deep` (one issue per board) |

**Delivery:**
- **Issues** are titled `health: <R#> <subject>`. That title can never match the kern-sharma-resume cleanup filter `^[0-9]+ new internship posting\(s\) — `.
  - Each carries the label `health`, created if a GET for it returns 404. No assignee.
  - One issue per `(rule, subject)`. It gets at most one comment a day while the condition holds, and closes automatically when it clears.
  - At most 10 open health issues; beyond that, one summary issue `health: R0 alert overflow` is updated.
- **ntfy** uses `NTFY_TOPIC`; absent means off. The text is `"intern-radar health: <n> open alerts"`, sent at most once every 6 h.
- **Weekly summary:** the first run whose UTC ISO week differs from `weekly_written_for` writes `data/health-weekly.md`:
  - matched postings per family;
  - unique finds per family (canonical keys seen only by that family);
  - the median head start (hours) of each ATS family and of `search` over `simplify`, where n ≥ 5;
  - disabled, `deep` and `needs_config` boards.

**Untrusted text in issues:** `notify.format_lines` escapes `[]()<>!@` and backticks in company and title for **all** sources. Health issue bodies contain only family names, board keys and numbers.

## Testing (pytest; fixtures in `tests/fixtures/`, recorded 2026-09-30)

- **Parsers:**
  - Oracle JSON; iCIMS HTML from 3 hosts; Eightfold pcsx JSON; the speedyapply README excerpt (with and without Salary); vanshb03 JSON.
  - vanshb03 cases: a Summer row posted 2026-04 with no year gives no term; posted 2026-07 gives Summer 2027; a title containing 2026 gives no term.
  - Workday `postedOn` variants.
  - Search-feed JSON: valid, oversize, bad URL, and untraced postings, which are ignored.
- **`board_of`:** a table of 30 URLs, including `evil-oraclecloud.com` and `oraclecloud.com.evil.io`, both → None; bad labels; locales; the `gh_jid` lookup via a fake server.
- **`canon_key`:** Workday locale; the iCIMS slug and query variants → one key; plus the `canon.json` seen-refresh path.
- **`due()`:** each tier at several `now` values; jitter bounds; disabled and `needs_config`.
- **Budget:** a fake clock past 10 minutes skips and records.
- **Discovery:**
  - Seeding; dedupe; disable after 10 errors, then re-enable.
  - The 400-day delete and the 2,000 cap eviction.
  - An Eightfold board with no domain gets `needs_config`.
- **Write protocol:** a simulated rebase conflict discards the run; a later run re-finds the postings.
- **Actions skip:** health `last_run_at` within 50 minutes means exit 0.
- **Link tracing:** a 3-hop redirect to Workday gives `traced` and the canonical URL. Refused cases: `127.0.0.1`, `169.254.169.254`, `[::ffff:10.0.0.1]`, a rebinding resolver (first public, then private) that must still connect to the vetted IP, and a 6th hop. Plus the 1 MB cap.
- **Search program:** `scrape_jobs` monkeypatched; the block rule; the description filter; feed commit with a fake git.
- **Health:** each rule fires and clears (R4 respects the quiet window); issue dedupe; the cap of 10; the ntfy rate limit; the weekly ISO-week trigger; head-start medians from `sightings.json`; issue-body escaping.
- **Regression:** a recorded run of today's six sources through the new pipeline yields a superset of the old run's inbox keys.
- **kern-sharma-resume:** a `triage.py` test for untraced feed postings joining the analysis.
- The existing suites pass.

`scripts/gate_check.py [--phase N]` reads `health.json`, `sightings.json`
and `boards.json`, prints each gate criterion with PASS or FAIL, and exits
nonzero on any failure.

## Phased rollout

1. **Phase 1: Mac runner, lists, discovery, dedupe, health.**
   - Covers:
     - The Mac clone and launchd; the Actions skip logic and new env; the write protocol.
     - vanshb03 and speedyapply.
     - `boards.json` with seeding, `board_of` and `due()`; concurrency and budget; the Workday depth caps.
     - `canon.json`, `sightings.json`, `health.json`, the alert rules, issues, ntfy, the weekly summary.
     - The `format_lines` escaping.
   - **Gate** (`gate_check.py --phase 1`), over 7 days:
     - every run under 12 minutes;
     - `boards_skipped_budget` = 0 on ≥ 90% of runs;
     - ≥ 30 Mac runs a day on days the Mac was awake;
     - the regression superset holds on a live replay;
     - `sightings.json` has ≥ 1 ATS family with n ≥ 10 paired with `simplify`;
     - every open R1–R6 issue on a family that is actually working is labelled `false-positive` and the rule is tuned.
2. **Phase 2: new readers.** Oracle, iCIMS, Eightfold.
   - **Gate:** over 3 days, each reader's error rate is < 5% of its polls; Oracle yields ≥ 1 posting on ≥ 3 tenants, iCIMS on ≥ 5 hosts, and Eightfold on ≥ 2 configured tenants.
3. **Phase 3: search engines + the private feed.**
   - Covers: the `intern-radar-feed` repo, the `FEED_TOKEN` secret, Python 3.12 plus the venv, `radar_search.py`, launchd, the ingest source, and the kern-sharma-resume triage input.
   - **Gate:** over 3 days:
     - R4 is never open outside the quiet window;
     - the report lists kept postings per site, the traced rate, and the untraced count reaching triage;
     - `search` has ≥ 1 unique find in `sightings.json`;
     - any site blocked on > 50% of its runs is removed from `sites` with a note.

## Open follow-ups (not in this change)

- Handshake (logged in), if the stats show a coverage gap.
- iCIMS custom-domain hosts (25) and other custom career sites.
- Readers for the small ATSes.
- The auto-submit fast lane reads `first_seen_at` from `sightings.json`.
