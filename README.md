# intern-radar

Zero-dependency watcher for SWE internship postings. Polls the
[SimplifyJobs Summer 2027 list](https://github.com/SimplifyJobs/Summer2027-Internships)
plus Greenhouse / Lever / Ashby boards directly, filters for your eligibility,
and opens a GitHub Issue (which GitHub emails you) the moment something new
appears. Runs free on GitHub Actions every 30 minutes.

**Why direct ATS polling?** Aggregators batch their updates. Polling the
company's own board API catches a posting within ~30 minutes of it going live —
and for the big early-cycle programs, applying in the first day matters.

## How it works

```
SimplifyJobs listings.json ─┐
Greenhouse boards API       │
Lever postings API          ├─> normalize ─> filter (term/degree/title/location)
Ashby job-board API         │        │
Workday CXS API             │        v
SmartRecruiters API ────────┘  diff vs data/seen.json ─> new? ─> Issue + Discord
                                     │                     (you review, you apply)
                                     └──── state committed back by the Action
```

No database, no server, no paid APIs. Python stdlib only.

## Setup

1. Clone (or fork — note GitHub disables scheduled workflows on forks until
   you enable Actions manually), edit `config.toml` (target terms,
   categories, boards to watch).
2. Push to GitHub. Actions runs `watch.yml` on a 30-minute cron.
3. The very first run bootstraps automatically: when `data/seen.json` doesn't
   exist yet, everything currently live is marked seen with no notifications —
   no giant first issue with 200 postings. You can also force this any time
   via **Actions → watch → Run workflow** with **bootstrap** checked.
4. Watch the repo (**Watch → All activity**) so new-posting issues hit your
   email. When the first real issue lands, confirm the email actually arrived
   before trusting the pipeline for months. Optional: add a
   `DISCORD_WEBHOOK_URL` repo secret for Discord pings.

## Local usage

```
# fetch + filter + print, no state writes, no notifications
PYTHONPATH=src python -m intern_radar --dry-run

# first run: mark everything currently live as seen
PYTHONPATH=src python -m intern_radar --bootstrap
```

## Tracking applications

Once you apply, track the pipeline in the same repo:

```
PYTHONPATH=src python -m intern_radar track add <posting-url>       # marks applied
PYTHONPATH=src python -m intern_radar track add <posting-url> --status interested  # queue, not yet applied
PYTHONPATH=src python -m intern_radar track set <posting-url> applied
PYTHONPATH=src python -m intern_radar track set <posting-url> oa --note "HackerRank due 8/10"
PYTHONPATH=src python -m intern_radar track set <posting-url> interview
PYTHONPATH=src python -m intern_radar track list
```

Stages: `interested → applied → oa → interview → offer` (plus
`rejected`/`withdrawn`). `interested` is the pre-application queue —
postings worth applying to that you haven't submitted yet.
Company/title resolve automatically from `data/postings.json` (snapshotted by
every watcher run); `APPLICATIONS.md` is regenerated as a dashboard on every
change. Commit when convenient — it's your data.

See `PROGRAMS.md` for the curated freshman/sophomore program list the
watcher can't poll (custom career sites).

## Fetching a full job description

For tailoring a resume (or just reading the whole posting without the
board's JS app), `jd` pulls the complete description as plain text from
the same ATS APIs the watcher polls:

```
PYTHONPATH=src python -m intern_radar jd <posting-url>
PYTHONPATH=src python -m intern_radar jd <posting-url> --out jd.md
PYTHONPATH=src python -m intern_radar jd "https://stripe.com/jobs/search?gh_jid=123" --board stripe
```

Supported posting URLs: Greenhouse (`boards.greenhouse.io`/`job-boards.`
paths, plus custom-domain `?gh_jid=` links with `--board`), Lever, Ashby,
Workday (`*.myworkdayjobs.com`, locale prefixes fine), SmartRecruiters,
iCIMS (`*.icims.com/jobs/<id>/job`), and Oracle Recruiting Cloud
(`*.oraclecloud.com/hcmUI/CandidateExperience/.../job/<id>`).

Two notes on the last two. iCIMS publishes no per-job API, so `jd` reads the
schema.org `JobPosting` block embedded in the job page — and answers HTTP 410
for a filled or expired req, which `jd` reports as a closed posting rather
than a fetch failure. Oracle returns no company name anywhere in its
requisition payload, so the header shows the tenant code (`egug`, `ehzq`) for
employers whose subdomain isn't their name; the description itself is complete.

## Development

```
pip install pytest ruff mypy
pytest          # parsers are tested against real captured API payloads
ruff check .
mypy
```

## Design notes

- **Filters bias toward recall.** A false positive costs one line in an issue;
  a false negative is a missed job. Title matching is substring-based on
  purpose ("intern" catches "Internship").
- **Notify before persist.** Remote notification failures exit nonzero
  *without* marking postings seen, so the next run retries (at-least-once
  delivery; duplicates are possible, silence is not).
- **Dedup across sources** by normalized URL (e.g. Lever `/apply` links from
  aggregators fold into the bare posting URL). Near-duplicate URLs that differ
  more than that may occasionally notify twice.
- **Direct ATS boards have no term metadata**, so they gate on the source's
  employment-type label plus title keywords; Simplify listings gate on
  term/category/degree fields.
- **Discord is best-effort** when GitHub Issues are enabled: the issue is the
  durable notification, and a dead webhook must not re-notify forever.
- Seen-state entries prune after a year of not being observed live.
- Known limitation: a source that fails on a run is reported in that run's
  issue body (if one is created) and in the Actions log, but a permanently
  dead board with no new postings elsewhere won't email you on its own.
