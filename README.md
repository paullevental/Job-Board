# job_poller

Watches company job boards and alerts you within minutes when a new internship,
co-op, or new-grad role goes up.

Internship postings close fast — often within days, sometimes after a fixed
number of applications regardless of the date. Being in the first wave of
applicants is one of the few parts of the process you actually control.
job_poller polls several hundred company boards on an interval, detects new
postings, filters them to roles you can actually apply to, and pushes an alert.

It also keeps something no job board does: **the full history of when every
posting appeared and disappeared.** After a co-op cycle, that answers questions
nothing else can — when a given company actually opens summer roles, how long
its postings stay up, which companies posted last year and haven't yet this
year.

```
$ python -m job_poller poll --canada-only

2 new matches

╭─────────────── Wealthsimple · Internship / Co-op ───────────────╮
│ Software Development Co-op, Summer 2027                         │
│ Toronto, ON, Canada                                             │
│ Summer 2027                                                     │
│ https://job-boards.greenhouse.io/wealthsimple/jobs/7412905      │
╰─────────────────────────────────────────────────────────────────╯

37 boards ok · 2 failed · 1,284 postings seen · 2 new · 5 closed · 4.1s
```

## How it gets the data

Not by scraping LinkedIn or Indeed — those actively block you and the terms are
hostile. Instead it reads the **public JSON APIs that applicant tracking systems
publish for their own hosted job boards**. Greenhouse documents that job board
data is public and requires no authentication; Lever and Ashby publish
equivalent endpoints.

| ATS | Endpoint | Token source |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs` | `job-boards.greenhouse.io/SLUG` |
| Lever | `api.lever.co/v0/postings/{token}?mode=json` | `jobs.lever.co/SLUG` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}` | `jobs.ashbyhq.com/SLUG` |

No API keys, no browser automation, no proxies. Between them these cover a large
share of tech employers. Companies on Workday, Taleo or a bespoke system are out
of scope.

## Setup

```bash
pip install -r requirements.txt

python -m job_poller load-companies     # read config/companies.yml
python -m job_poller verify --prune     # check which tokens are live
python -m job_poller poll               # one pass
```

**Run `verify` first and expect failures.** The seed company list in
`config/companies.yml` contains unverified board tokens — they go stale when
companies rename, migrate ATS, or restructure their careers page. `verify` hits
every one and reports which are live, which return empty, and which are dead;
`--prune` deactivates the dead ones.

Adding a company takes ten seconds: open its careers page, click into any single
posting, and read the slug out of the URL.

### Running continuously

```bash
python -m job_poller watch --every 300 --canada-only
```

For real alerts, set a Discord webhook — the lowest-friction notification
channel that reaches your phone:

```bash
export JOB_POLLER_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
python -m job_poller watch --every 300 --canada-only
```

Or run it from cron / a systemd timer / a cheap VPS and let it alert you while
you sleep.

## Commands

| Command | What it does |
|---|---|
| `load-companies` | Load or refresh the board list from YAML |
| `verify [--prune]` | Check which board tokens actually work |
| `poll` | One polling pass; alerts on new matches |
| `watch --every N` | Poll on a loop, forever |
| `list [--open] [--canada-only] [--tag canada]` | Browse tracked postings |
| `stats` | Coverage, posting lifespan, poller health |
| `timing` | When each company actually posts |

Useful flags on `poll` and `watch`: `--canada-only`, `--engineering-only`,
`--discord URL`, `--jsonl path`, `--concurrency N`.

## Dashboard API

A read-only HTTP API over the same database is in place (the React dashboard that
consumes it is not built yet):

```bash
pip install -r requirements.txt
python -m uvicorn job_poller.api.asgi:app --reload    # http://127.0.0.1:8000/docs
```

`GET /api/feed` (triage feed, grouped by role, keyset-paginated) ·
`/api/postings[/{id}]` (browse and detail) · `/api/applications` (pipeline by stage) ·
`/api/companies` (board health) · `/api/insights/{stats,timing}` ·
`/api/system/health` (is the poller alive).

The poller stays a separate process. The API only reads what `watch` writes — SQLite
in WAL mode supports that concurrently, which is why WAL is enabled.

## Design notes

Three decisions carry most of the weight.

**Postings are never deleted.** When one vanishes from a board it gets a
`closed_at` stamp and stays in the table forever. This is the entire point of
the project — job boards show you what's open right now, and the history of
*when* things opened and closed is thrown away by everyone. Accumulating it is
the one thing here that gets more valuable the longer it runs.

**A failed fetch never closes a posting.** Postings are only marked closed when
their board was fetched *successfully* and the posting was absent. Without this
rule, one network blip would close every posting at a company and "reopen" them
on the next pass, firing a storm of false alerts. This is the single most
important correctness property in the codebase and it has dedicated tests.

**Classification is rule-based, not ML.** The patterns in `classify.py` are
inspectable and run in microseconds, and every verdict carries a `reasons` trace
so you can see exactly which rule fired. The asymmetry is deliberate: a missed
posting costs you an opportunity, a false positive costs you three seconds of
reading. So ambiguous cases (a bare "Remote" with no country) are kept, and the
seniority filter is aggressive.

## Architecture

```
config/companies.yml   board list, tagged
        │
        ▼
    poller.py  ──── async fan-out, bounded concurrency, per-board retry
        │              failure isolation: one bad board never stalls a run
        ▼
     ats/*.py  ──── Greenhouse │ Lever │ Ashby  →  one normalized RawPosting
        │
        ▼
   classify.py ──── intern / new-grad / other · term · Canada eligibility
        │
        ▼
    models.py  ──── SQLite. first_seen_at · last_seen_at · closed_at
        │              postings are append-only; history is the product
        ▼
    notify.py  ──── console · Discord webhook · JSONL
```

## Tests

```bash
python -m pytest -q                 # 211 tests
python tests/demo_offline.py        # full pipeline against fixtures
```

The demo runs the real adapters, classifier, diff logic and notifier across
three simulated polling cycles — cold start, a board timing out, then recovery
with one posting pulled and one added — and asserts that the failure cycle
closes nothing and the recovery cycle re-alerts nothing. It needs no network, so
it works as a CI check.

## Being a good citizen

These endpoints are free and public, which is a reason to be careful with them,
not a reason not to be. The poller caps concurrency, backs off exponentially on
errors, stops retrying on a 404, sends an honest `User-Agent`, and skips the
`content=true` flag on Greenhouse that would inflate payloads ~12x for data it
doesn't need. A 5-minute interval is plenty; don't drop it to 10 seconds because
you can.

## Known limitations

- Workday, Taleo, SuccessFactors and bespoke career sites aren't supported. That
  excludes some large Canadian employers, including several banks.
- The seed company list needs verification and ongoing maintenance.
- Cross-ATS deduplication is a blunt title+location match; it won't catch a role
  listed under meaningfully different titles.
- `timing` needs a full co-op cycle of data before it says anything interesting.

## Next steps

- A small web UI over the same database, so it's shareable rather than
  personal-CLI-only
- Per-user saved searches and subscriptions
- Postings-per-week charts by company, which is the shareable finding that gets
  the project users
- SmartRecruiters and Workable adapters (both have public endpoints too)
