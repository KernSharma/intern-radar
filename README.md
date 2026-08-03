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
Greenhouse boards API       ├─> normalize ─> filter (term/degree/title/location)
Lever postings API          │        │
Ashby job-board API ────────┘        v
                    diff vs data/seen.json ─> new? ─> GitHub Issue + Discord
                                     │                 (you review, you apply)
                                     └──── state committed back by the Action
```

No database, no server, no paid APIs. Python stdlib only.

## Setup

1. Fork/clone, edit `config.toml` (target terms, categories, boards to watch).
2. Push to GitHub. Actions runs `watch.yml` on a 30-minute cron.
3. First run: trigger the workflow manually (**Actions → watch → Run
   workflow**) with **bootstrap** checked. This marks everything currently
   live as seen so you don't get one giant issue with 200 postings.
4. Watch the repo (**Watch → All activity**) so new-posting issues hit your
   email. Optional: add a `DISCORD_WEBHOOK_URL` repo secret for Discord pings.

## Local usage

```
# fetch + filter + print, no state writes, no notifications
PYTHONPATH=src python -m intern_radar --dry-run

# first run: mark everything currently live as seen
PYTHONPATH=src python -m intern_radar --bootstrap
```

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
- **Direct ATS boards have no term metadata**, so they gate on title keywords;
  Simplify listings gate on term/category/degree fields.
- Seen-state entries prune after a year to bound file growth.
