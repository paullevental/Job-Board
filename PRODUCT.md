# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI (reusing the existing SQLAlchemy models) + React + Vite + TypeScript + Tailwind.
Chosen by the user. Database access stays behind SQLAlchemy with the URL in env
(`DATABASE_URL`, already honored by `job_poller/db.py`) so SQLite → Postgres is a
one-variable change if the app is ever hosted.

## Users

One user: the project's owner, a university computer-science student seeking a
software-development internship or co-op term. Canadian; Canada eligibility is a
first-class filter in the existing classifier, not an afterthought.

Usage situation: checking in several times a day between classes, often on a laptop
with other tabs open, under time pressure — internship postings close within days,
sometimes on application count rather than date. The user arrives wanting to know
one thing fast: *what is new since I last looked, and have I already applied to it?*

Not a multi-user product. No other audience is confirmed. Sharing with classmates
is an acknowledged possible future, explicitly out of scope for this build.

## Product Purpose

Watch company ATS job boards, detect new internship/co-op/new-grad postings within
minutes, and give the user one place to read a posting, decide, apply, and track
what stage each application has reached.

Success: the user applies to relevant postings in the first wave of applicants, and
never loses track of which roles they have already acted on.

## Positioning

Two things no job board does:

1. **Full posting history.** Postings are never deleted — a vanished posting gets a
   `closed_at` stamp and stays forever (`job_poller/models.py`). Job boards show what
   is open right now and discard when things opened and closed. Accumulating that
   history answers questions nothing else can: when a given company actually opens
   summer roles, how long its postings stay up, which companies posted last cycle and
   have not posted yet this one.
2. **Detection speed over breadth.** It reads the public JSON APIs that applicant
   tracking systems publish for their own hosted boards — no scraping, no browser
   automation, no API keys. That buys minutes-fresh detection on a defined company
   list rather than a stale index of everything.

Deliberately not a resume product. No resume parsing, scoring, tailoring, or storage.
The user ruled this out explicitly.

## Operating Context

- A poller runs on an interval (`watch --every N`, 5 minutes is the documented
  default) and writes to SQLite. The dashboard reads the same database.
- Company list is human-maintained YAML (`config/companies.yml`, 39 entries at time
  of writing), tagged, loaded into the `companies` table. Board tokens go stale when
  companies rename or migrate ATS; a `verify --prune` command exists for this.
- Supported ATS vendors: Greenhouse, Lever, Ashby. Workday, Taleo, SuccessFactors and
  bespoke career sites are out of scope and exclude some large Canadian employers,
  including several banks.
- Existing alert channels: console, JSONL, Discord webhook. Email is requested but
  not implemented.
- Applying always happens on the employer's own ATS page, never inside this product.

## Capabilities and Constraints

**Already built and working:**
- Concurrent polling with bounded concurrency, per-board retry, and failure isolation
- Rule-based classification: intern / new_grad / other, plus term season+year, Canada
  eligibility, remote, engineering flags — every verdict carries a `reasons` trace
- Change detection with two load-bearing correctness rules: a failed fetch never
  closes a posting, and a posting that disappears and returns is treated as new
- `PollRun` audit rows, so "no new postings today" is distinguishable from "the poller
  died three days ago"
- CLI: `load-companies`, `verify`, `poll`, `watch`, `list`, `stats`, `timing`

**Confirmed gaps this work must close:**
- No application tracking of any kind. `Posting` has no status, applied date, or notes.
- No email notification. Discord webhook is the only real channel.
- No UI. Everything is CLI-only.
- **No job description text anywhere in the database.** The adapters normalize to
  `RawPosting` (title, url, location, department, posted_at) and deliberately skip
  Greenhouse's `content=true` because it inflates payloads ~12x. A readable detail
  view therefore requires a new fetch path: descriptions are fetched on demand when
  the user opens a posting, then cached permanently.

**Binding constraints:**
- Be a good citizen of free public endpoints: capped concurrency, exponential backoff,
  no retry on 404, honest User-Agent, no payload inflation for data not needed. The
  on-demand description fetch must respect this — one request per posting, cached
  forever, never refetched on a poll.
- Single writer, SQLite, WAL mode. The dashboard reading while the watcher writes is
  an existing supported case.
- Cross-ATS deduplication is a blunt title+location match and will not catch a role
  listed under meaningfully different titles.

**Incomplete rename (must be fixed before the API imports the package):** the package
directory is `job_poller/`, but `pyproject.toml` still declares `name = "coopwatch"`
with entry point `coopwatch.cli:main`, and the README documents `python -m coopwatch`.
Those commands fail as written. Env vars are still `COOPWATCH_*`.

## Brand Commitments

Name: `job_poller`. Renamed by the user from `coopwatch`.

## Evidence on Hand

Real, in-repo:
- 39 tagged companies in `config/companies.yml` (board tokens unverified — the README
  says to expect failures and run `verify` first)
- Three ATS adapters with JSON fixtures in `tests/fixtures/`
- An offline end-to-end demo (`tests/demo_offline.py`) covering cold start, a board
  timing out, and recovery
- A test suite the README describes as 105 tests. Not yet executed in this
  environment — dependencies are not installed, so this count is unverified.

Not on hand, and not to be fabricated: no production database with real history yet,
no user accounts, no usage metrics, no company logos or brand assets, no screenshots.
The `timing` feature needs a full co-op cycle of data before it says anything.

## Product Principles

1. **Speed of first application is the one thing the user controls.** Every design
   decision serves getting from "posting appeared" to "I applied" in as few seconds as
   possible.
2. **History is the product.** Data is append-only. Nothing is ever deleted or
   overwritten in place — not postings, and not application status transitions.
3. **A missed posting costs an opportunity; a false positive costs three seconds.**
   The existing classifier is deliberately asymmetric this way, and the UI must stay
   consistent with it: show the ambiguous case, make dismissing it cheap.
4. **Never silently fail.** A dead poller must be visible in the UI, not inferred from
   an empty feed. `PollRun` exists for exactly this.
5. **Not a resume tool.** No resume features, ever. Strictly find → read → apply →
   track.

## Accessibility & Inclusion

No user-specific requirement established. Target WCAG 2.1 AA as a floor: full keyboard
operation of the triage flow (the primary interaction, and the one most worth making
keyboard-first), visible focus states, and AA contrast in both light and dark themes.
