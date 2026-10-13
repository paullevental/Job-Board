# job_poller Dashboard — Design

**Date:** 2026-10-13
**Status:** approved design, ready for implementation plan

A web dashboard over the existing `job_poller` backend: browse and read postings
the poller finds, triage them fast, and track each application through to an
offer. Plus email alerts, which the backend has never had.

---

## 1. Job and audience

One user — the project owner, a CS student hunting an internship or co-op. They
open this several times a day between classes, under genuine time pressure:
internship postings close within days, sometimes on application count rather
than a date. They arrive wanting one answer immediately: **what is new since I
last looked, and have I already dealt with it?**

**Visitor mode: Operate.** This is a tool for completing a task, not a page that
persuades. Scanability, keyboard speed, and honest state beat expression — but
the expression lives in precise detail, because the user explicitly asked for
something that looks appealing rather than utilitarian.

## 2. Outcome and proof

**Primary task:** move a new posting from "appeared on a company board" to
"applied, and recorded as applied" in as few seconds as possible.

**Success:** the user applies in the first wave of applicants, and never loses
track of what they have already acted on.

**Product truth no neighboring tool can copy:** the database holds the full
open/close history of every posting ever seen (`first_seen_at`, `closed_at`,
never deleted). Job boards show what is open now and discard the rest. That
history is the only thing here that gets more valuable the longer it runs, and
the UI should make it visible rather than hide it behind a CLI.

## 3. Locked decisions

| Decision | Choice | Consequence |
|---|---|---|
| Scope | Single-user, local-first | No user table, no multi-tenancy in queries |
| Hosting | Local now, portable later | All DB access via SQLAlchemy; `DATABASE_URL` already honored |
| Stack | FastAPI + React + Vite + TypeScript + Tailwind | Two dev processes, one production process |
| Descriptions | Fetched on demand, cached permanently | Polling cost unchanged |
| Primary surface | Triage feed of what's new | Pipeline and browse are secondary routes |
| Visual direction | "The Wire" — single-column timeline | Time is the primary axis |
| Email | One SMTP sender for alerts + magic-link auth | Auth gated behind `AUTH_REQUIRED`, default off |

## 4. Anti-goals

- **No resume features.** Not parsing, scoring, tailoring, or storing one. Ruled
  out explicitly by the user.
- **No scraping.** Public ATS JSON only. No LinkedIn, no Indeed, no browser
  automation. Unchanged from the existing backend.
- **No applying inside the product.** Apply always opens the employer's ATS page.
- **No multi-user anything.** No signup, no per-user rows, no sharing.
- **No infinite-content engagement loop.** The feed is a finite set that drains.
  See §13.
- **No new polling cost.** The description fetch must not add per-poll requests.

## 5. What must not break

Three existing properties are load-bearing and the dashboard must not weaken any:

1. **A failed fetch never closes a posting** (`poller.py`, `apply_results`). The
   API must never write to `postings` in a way that bypasses this.
2. **Postings are append-only.** `closed_at` is a stamp, not a delete. The new
   tables follow the same rule — status changes append an event row.
3. **A dead poller must be visible, not inferred.** `PollRun` exists for this.
   The UI surfaces it in the header; an empty feed must never be ambiguous
   between "nothing new" and "the poller died three days ago".

## 6. Step 0: finish the rename

The rename from `coopwatch` to `job_poller` is incomplete, and the API cannot
import a package whose metadata is wrong. Before any new code:

- `pyproject.toml`: `name = "coopwatch"` → `job_poller`; script entry
  `coopwatch.cli:main` → `job_poller.cli:main`
- `job_poller/db.py`: `COOPWATCH_DB` → `JOB_POLLER_DB` (keep reading the old name
  as a fallback so an existing local database is not orphaned)
- `notify.py`: `COOPWATCH_DISCORD_WEBHOOK` → `JOB_POLLER_DISCORD_WEBHOOK`, same
  fallback
- Logger names `coopwatch.*` → `job_poller.*`
- `README.md`: every `python -m coopwatch` → `python -m job_poller`
- `USER_AGENT` in `poller.py` still says `coopwatch/0.1`
- `.env.example` keys

**Completed 2026-10-13.** The rename turned out to be worse than metadata: every
test file imported `coopwatch.*`, so the suite could not be collected at all —
3 collection errors, 0 tests run. After the rename all **105 tests pass**,
confirming the README's claim for the first time.

Two further defects surfaced and were fixed in the same pass:

- **The package was never installable under either name.** Setuptools'
  flat-layout discovery saw `config/` as a second top-level package and refused
  to build, so `pip install -e .` failed and the `job_poller` console script did
  not exist. Fixed with an explicit `[tool.setuptools.packages.find]` include.
- Pre-rename env vars (`COOPWATCH_DB`, `COOPWATCH_DISCORD_WEBHOOK`) are read as
  fallbacks, so an existing shell profile keeps working.

## 7. Architecture

```
config/companies.yml
      │
      ▼
  poller process ───writes───┐
  python -m job_poller watch │
                             ▼
                      SQLite (WAL)  ◄──reads/writes──  FastAPI  ◄──HTTP──  React SPA
                             ▲                      job_poller/api/          web/
                             └──────────────────────────┘
                                   manual "poll now" only
```

**The poller stays a separate process.** FastAPI does not own or supervise it.
Detection is already correct, tested, and failure-isolated; coupling its uptime
to a web server's would buy nothing and risk two writers racing. WAL mode
already supports a concurrent reader — `db.py` documents this as the reason it
is enabled.

**The one shared write is "poll now".** A button in the UI calls
`POST /api/poll`, which runs `poll_once` in a FastAPI background task. This makes
the API a second writer, so two guards are required:

- **`busy_timeout` pragma.** `db.py` currently sets `journal_mode`,
  `synchronous` and `foreign_keys` but **not** `busy_timeout`. Without it a
  concurrent write raises `database is locked` immediately instead of waiting.
  Add `PRAGMA busy_timeout=5000`.
- **Single-flight lock.** An `app_meta` row `poll_lock` holding a timestamp.
  Acquire if absent or older than 10 minutes (stale-lock recovery after a crash).
  A second concurrent request gets `409 Conflict`, not a queued poll.

**A query layer both surfaces call.** `cli.py` currently holds its list and stats
query logic inline in `cmd_list` and `cmd_stats`. Left alone, the API would
duplicate all of it and the two would drift. Extract:

- `job_poller/queries.py` — all read queries: feed, browse with filters, detail,
  stats, timing, company health. Returns ORM objects or plain dataclasses, never
  HTTP types.
- `job_poller/tracking.py` — all application-status writes: create, transition,
  note. Owns the event-append invariant so no caller can forget it.

Both the CLI and the API import these. Neither module imports FastAPI. This is
the layer that makes the backend testable without HTTP, and it is where the
interesting logic lives.

### Repository layout

```
job_poller/
  job_poller/          existing package
    api/               NEW — FastAPI
      app.py           app factory, static mount, exception handlers
      deps.py          session dependency, auth dependency
      schemas.py       Pydantic request/response models
      routes/
        feed.py  postings.py  applications.py  companies.py
        insights.py  system.py  auth.py
    queries.py         NEW — all read queries
    tracking.py        NEW — application status writes
    details.py         NEW — on-demand description fetch + sanitize
    email.py           NEW — SMTP sender (alerts + magic links)
    ats/
      *.py             existing adapters gain fetch_detail()
  web/                 NEW — React + Vite + TS + Tailwind
    src/
      routes/          Feed, Pipeline, Browse, Companies, Insights
      components/      PostingRow, AgeMeter, MonogramChip, FreshBand, …
      lib/             api client, keyboard map, cursor handling
      styles/tokens.css
```

**Production is one process.** `vite build` emits to `web/dist`; FastAPI serves
it with `StaticFiles` and an SPA fallback. One command, one URL, no CORS. In
development, Vite on 5173 proxies `/api` to 127.0.0.1:8000.

## 8. Data model

Four new tables. **No changes to existing tables**, which means
`Base.metadata.create_all` creates them on next startup and no migration tool is
needed. Alembic gets added the first time an existing column has to change, not
before.

### `applications`

One row per posting the user has acted on.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `posting_id` | FK → postings, **unique** | one application per posting |
| `status` | str, indexed | see enum below |
| `applied_at` | datetime, nullable | set on first transition into `applied` |
| `next_action_at` | datetime, nullable | explicit reminder (interview date) |
| `notes` | text | freeform, markdown rendered |
| `created_at` / `updated_at` | datetime | |

**Status enum:** `saved`, `applied`, `assessment`, `interviewing`, `offer`,
`rejected`, `dismissed`. `assessment` is included deliberately — online
assessments are near-universal in internship pipelines and collapsing them into
`interviewing` loses the stage the user most needs reminding about.

**Why a separate table rather than columns on `Posting`:**

1. `apply_results` **overwrites `Posting` rows in place** when a fingerprint
   changes. User data must not live in a row the poller rewrites.
2. A posting that disappears and returns is treated as new — `closed_at` is
   cleared, `first_seen_at` is reset, `notified_at` is nulled. None of that
   should touch the user's record of having applied.
3. Roughly 40 tracked rows against 1,284 postings seen. A sparse join table is
   the honest shape; nullable columns on every posting row are not.

### `application_events`

Append-only transition log: `id`, `application_id` FK, `from_status` (nullable at
creation), `to_status`, `note`, `occurred_at`.

Never updated, never deleted. Gives the detail view a real timeline — *applied
Oct 3 → assessment Oct 9 → interviewing Oct 14* — for free, and keeps the
project's history-is-the-product thesis true of user data as well as postings.

### `posting_details`

Cached description: `posting_id` PK/FK, `content_html` (sanitized),
`content_text` (for search), `fetched_at`, `fetch_error` nullable.

Separate from `postings` so a multi-kilobyte HTML blob never loads during feed
queries.

### `app_meta`

Single key/value table: `key` PK, `value` text, `updated_at`. Holds
`last_feed_seen_at` and `poll_lock`. Deliberately not a settings system.

## 9. API surface

All responses are Pydantic schemas. ORM objects are never returned directly.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/feed?cursor=` | Triage feed, keyset-paginated |
| POST | `/api/feed/seen` | Advance `last_feed_seen_at` — "caught up" |
| GET | `/api/postings?…&cursor=` | Browse with filters |
| GET | `/api/postings/{id}` | Detail; lazily fetches description on cache miss |
| GET | `/api/applications?status=` | Pipeline |
| POST | `/api/applications` | Create or transition `{posting_id, status, note?}` |
| PATCH | `/api/applications/{id}` | Notes, `next_action_at` |
| GET | `/api/applications/{id}/events` | Timeline |
| GET | `/api/companies` | List + board health |
| GET | `/api/insights/stats` | Coverage, lifespan |
| GET | `/api/insights/timing` | When each company posts |
| GET | `/api/system/health` | Last `PollRun`, failing boards, staleness |
| POST | `/api/poll` | Manual poll, single-flight, 409 if running |

**Browse filters:** `category`, `canada_only`, `engineering_only`, `remote`,
`season`, `year`, `company_id`, `tag`, `open_only`, `q` (title + description
text), `status` (tracked state, including `untracked`).

**Pagination is keyset, not offset.** Cursor is an opaque base64 of
`first_seen_at|id`; the query uses a row-value comparison
`WHERE (first_seen_at, id) < (:ts, :id) ORDER BY first_seen_at DESC, id DESC`
(SQLite supports row values from 3.15). Offset pagination visibly breaks on an
infinite-scroll feed: new rows arriving at the top shift every subsequent page
and the user sees duplicates.

### Feed semantics

The feed returns postings that are **open**, **not `other` category**, and
**untracked** — no `applications` row at all. Any action, including `saved`,
creates a row and therefore removes the posting from the feed. That is the drain,
and it is the whole interaction.

Saved is not a way of leaving something in the feed; it is a deliberate "yes,
later" that moves the posting into the pipeline's first column. A save that left
the item in place would mean the feed never empties, and "caught up" would become
unreachable — which would break the primary task.

### Role grouping

Measured: **28% of feed rows are the same title repeated across locations**, and
Palantir alone is 39% of the feed. Ungrouped, day one reads "Palantir, Palantir,
Palantir" and the user's eye stops working.

So the feed groups by `(company_id, title)` into one row carrying a location
count — *Software Engineer, Internship · Palantir · 6 locations* — expandable to
the individual postings. Grouping is a **presentation concern only**: each
underlying `Posting` keeps its own identity and its own `applications` row,
because the user applies to one specific location, not to a group. Triaging the
group acts on every posting inside it; expanding lets the user pick one.

This also means a feed page's row count and posting count differ, so the cursor
pages over **postings** and the UI accumulates groups client-side.

**`last_feed_seen_at` advances only on an explicit action**, never on page load.
If merely opening the dashboard advanced it, the "new since your last visit"
count would vanish before the user had read it. The fresh band shows the count of
postings newer than the marker, and a "Caught up" control advances it.

## 10. Description fetch

Each adapter gains `fetch_detail(client, token, external_id) -> DetailResult`.
Greenhouse supports `content=true` on its single-job endpoint; Lever and Ashby
expose description fields on their posting payloads. **Exact field names and
response shapes are to be confirmed against live responses during
implementation, not assumed from memory** — and each needs a captured fixture
added to `tests/fixtures/`.

Flow on `GET /api/postings/{id}`:

1. `posting_details` hit → return it.
2. Miss → fetch synchronously with a 6-second timeout. One HTTP GET while the
   user watches a skeleton; a background job plus client polling adds machinery
   for no perceived gain.
3. Success → sanitize, store, return.
4. Failure → store `fetch_error`, return `description: {state: "unavailable"}`.
   The UI shows the metadata it already has plus an "Open on <ATS>" link. A
   missing description must never block applying.

Cached forever. Never refetched by a poll. Polling cost is unchanged, which is
the constraint that made this approach the right one.

**Sanitization is mandatory and server-side.** Descriptions are employer-authored
HTML rendered in the user's browser — the one genuine security decision in this
build. Sanitize with `nh3` on a strict tag allowlist (headings, paragraphs,
lists, `strong`, `em`, `a`, `br`), strip every attribute except `href`, force
`rel="noopener noreferrer"` and `target="_blank"` on links, and store the
sanitized form so nothing unsanitized is ever persisted. Tests feed known XSS
payloads — `<script>`, `onerror=`, `javascript:` href, `<iframe>` — and assert
they do not survive.

## 11. Email and auth

**One sender, two consumers.** `job_poller/email.py` wraps `smtplib` with
credentials from env (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`ALERT_TO`). A Gmail app password works and needs no domain.

**Alerts:** `EmailNotifier` implements the existing `Notifier` protocol and drops
into `dispatch()` beside `ConsoleNotifier` and `DiscordNotifier` with no changes
to the poller. Default mode is a **digest** — one message per poll pass listing
all new matches, not one message per posting, because a 39-company poll can
surface a dozen at once and twelve emails is a reason to mute the sender.
Plain-text and HTML parts; subject carries the count and the top company.
Failures are logged and never raised, matching `DiscordNotifier`'s existing
contract that a dead sink must not take down the poller.

**Auth, gated off by default.** When `AUTH_REQUIRED` is unset or false — the
local case — a dependency returns an always-authorized session and no login UI
renders. When true: the user enters their email, receives a signed single-use
magic link (itsdangerous token, 15-minute expiry, consumed on first use), and
gets an HTTP-only session cookie. The allowed address is a single env var, so
there is still no user table. Rate-limited to 5 link requests per 15 minutes per
address.

This is built and tested now, dormant locally, and activated by one variable the
day the app is deployed. Retrofitting auth into a working app is the expensive
version of this.

## 12. Frontend routes

| Route | Purpose |
|---|---|
| `/` | **Feed** — The Wire. Triage what's new. The default and the point. |
| `/pipeline` | Applications by stage — Saved, Applied, Assessment, Interviewing, Offer, Rejected — with follow-up nudges |
| `/browse` | Everything open, full filters, search |
| `/companies` | Board health — which tokens are live, failing, stale |
| `/insights` | Stats and timing — the history payoff |

`⌘K` opens a command palette from anywhere: jump to a company, apply a filter,
mark caught-up, trigger a poll.

## 13. Visual direction: "The Wire"

**Structural thesis:** a single generous column on a vertical time rail. Time is
the primary axis because history is the product. Date dividers carry counts —
`TODAY · 4`, `YESTERDAY · 11` — so the day's volume reads before any individual
posting does.

**Focal moment:** the inline unfurl. Clicking a posting expands it **in place**
rather than opening a modal or navigating away. The row grows, the description
unfolds beneath it, and the user never loses their position in the scroll.
Modals break triage rhythm; this preserves it.

**On "make me want to scroll more."** A dashboard optimizing for scroll volume
is one failing at its job — the stated task is triage, which succeeds when the
list empties. So this design serves *momentum and visible progress* through a
finite set, not endlessness:

- A **fresh band** at the top of the feed whose fill visibly drains as items are
  triaged. Finishing is a state you can see arriving.
- **Date dividers** chunk the scroll into comprehensible units instead of a
  uniform wall.
- **Triaged rows animate out** — collapse and fade — so acting feels like
  something happened.
- Infinite scroll loads older postings, but the fresh set is always bounded and
  always ends in "caught up".

**Signature detail: the age meter.** Days-open is the most actionable number in
this product, because the entire thesis is first-wave advantage. Every posting
carries a small segmented decay indicator — full and bright at hours old,
draining and desaturating toward a week — rather than a `4 days ago` string the
user has to parse and translate into urgency. It appears in the feed, in browse,
and in the detail view, and it is the one element that makes this product look
like nothing else.

**Company identity:** deterministic monogram chips. Two letters from the company
name, background hue derived by hashing the name so a given company is always
the same color. **No logo assets exist and none will be fabricated**; this gives
visual recognition honestly.

**Typography:**
- Posting titles: **Instrument Serif** — editorial, distinctive, and the single
  biggest reason this reads as designed rather than generated. Titles only.
- UI text: **Inter**
- Metadata, counts, dates, age: **Geist Mono** — numerals tabular so columns of
  dates and day-counts align while scrolling

All self-hosted via `@fontsource`. No external font requests.

**Color:** dark-first, because the user checks this at night and between classes.
Near-black ground with a slight cool cast, never pure `#000`. Elevation via
subtle borders rather than shadows, which read as mud on dark surfaces. One
accent for *fresh/new*, a distinct one for *applied*, muted amber for
*assessment/interviewing*, desaturated red for *rejected*. Light mode defined
from the same tokens, not bolted on: palette lives in `tokens.css` as custom
properties, dark as the bare `:root`, light under both an explicit
`[data-theme]` and `prefers-color-scheme`.

**Motion:** purposeful only. Unfurl height transition, triaged-row collapse,
fresh-band fill. Everything behind `prefers-reduced-motion: reduce`. No
decorative parallax, no scroll-jacking.

**Keyboard map** — the triage flow is keyboard-first because it is the primary
interaction performed dozens of times a day:

| Key | Action |
|---|---|
| `j` / `k` | next / previous posting |
| `Enter` or `o` | unfurl detail in place |
| `a` | mark applied (and open the ATS page) |
| `s` | save for later — moves it to the pipeline's Saved column |
| `x` | dismiss |
| `u` | undo last action |
| `/` | focus search |
| `⌘K` | command palette |
| `?` | shortcut overlay |

`u` matters: a keyboard-fast triage flow without undo punishes exactly the speed
it encourages. Actions are optimistic client-side with a 5-second undo window
before the mutation is considered settled.

## 14. States and ranges

**Measured on 2026-10-13 against live boards**, not estimated — the earlier
estimates taken from the README's example output were wrong by roughly 3x:

| Quantity | Actual |
|---|---|
| Boards configured | 39 |
| Boards actually live | **20** (19 return 404) |
| Postings seen in one poll | **4,321** |
| Open matches (intern + new_grad) | **204** — 144 intern, 60 new grad |
| Canada-eligible matches | **13** |
| Feed rows that are duplicate titles | **57 of 204 (28%)** |
| Largest single company share | **Palantir, 79 of 204 (39%)** |

Two consequences the design must absorb, both covered below. Every view is
designed for these numbers, not for a comfortably mocked middle.

| State | Treatment |
|---|---|
| **First run, empty DB** | Not an empty list — an onboarding path: load companies, verify tokens, run first poll. The empty state is where a new user is either activated or lost. |
| **Caught up** | A real, designed, satisfying state. Not a blank area. This is the success condition of the primary task and should read as an achievement. |
| **Poller stale or dead** | Header status derived from the last `PollRun`: green if `finished_at` is within 2× the expected interval, amber if stale, red if no run in an hour or if >50% of boards failed. An empty feed next to a red indicator is unambiguous. |
| **Board failures** | `/companies` surfaces `consecutive_failures` and `last_error`, with the existing `verify --prune` as the suggested fix. |
| **Description unavailable** | Metadata plus "Open on Greenhouse". Never blocks applying. |
| **Description loading** | Skeleton matched to the real text block, not a spinner. |
| **Large backlog** | **This is day one, not an edge case** — a cold start puts 204 matches in the feed at once. First run offers "mark everything already open as seen", which reframes the feed as *new from here forward* and makes the drain reachable. Without it the primary task is unachievable on first use. |
| **Closed posting still tracked** | Pipeline shows it struck through with "posting closed" — the application is still real and still worth following up. |
| **Offline / API down** | Explicit banner. Cached data stays visible and read-only. |

## 15. Error handling

- **API:** typed exception handlers returning a consistent error envelope
  (`{error: {code, message}}`). No raw tracebacks to the client. 404 for unknown
  posting, 409 for a poll already running or a duplicate application, 422 from
  Pydantic for bad filters.
- **Database:** single writer plus `busy_timeout`; the single-flight poll lock
  with stale recovery. A failed background poll records the failure in `PollRun`
  rather than vanishing.
- **ATS fetches:** already failure-isolated per board. The new detail fetch gets
  the same treatment — caught, recorded in `fetch_error`, never raised to the
  request.
- **Frontend:** route-level error boundaries; optimistic mutations roll back and
  surface a toast on failure; the undo window is client-side so it survives a
  failed request.

## 16. Testing

**Python (pytest, extending the existing suite):**
- `queries.py` — feed filtering, keyset pagination correctness across a boundary,
  filter combinations
- `tracking.py` — every legal status transition, the event-append invariant,
  idempotency of re-applying the same status, `applied_at` set exactly once
- API via `TestClient` against an in-memory SQLite fixture — every endpoint,
  including 404/409/422 paths
- `details.py` — adapter detail parsing against new captured fixtures; **XSS
  payload tests on the sanitizer**
- `email.py` — digest rendering, and that a send failure is swallowed not raised
- Auth — token expiry, single-use consumption, rate limit, and that
  `AUTH_REQUIRED=false` bypasses cleanly
- **Regression:** the existing suite stays green, particularly the
  no-phantom-closures tests, which remain the most important property in the
  codebase

**Frontend (Vitest + React Testing Library):** triage reducer and optimistic
update/rollback, keyboard map including undo, cursor pagination accumulation,
age-meter bucket math, empty and error states.

**End-to-end:** extend `tests/demo_offline.py` to drive a poll → feed → apply →
pipeline round trip against fixtures, so the whole path is verifiable with no
network. One Playwright smoke test for the keyboard triage flow.

## 17. Implementation phases

0. Finish the rename; install deps; establish a green test baseline
1. New tables, `queries.py`, `tracking.py`, `busy_timeout` pragma
2. FastAPI skeleton, schemas, read endpoints
3. `fetch_detail` adapters, sanitization, `posting_details` cache
4. Frontend scaffold, design tokens, The Wire feed (read-only)
5. Triage actions: keyboard, optimistic updates, undo
6. Pipeline, browse, companies, insights routes
7. Poller health surface and manual poll with single-flight
8. SMTP sender and the email digest notifier
9. Magic-link auth behind `AUTH_REQUIRED`
10. Static build integration, one-command startup, README rewrite

Phases 1–5 produce a genuinely usable product. Everything after is additive.

## 18. Open decisions and risks

- **ATS detail response shapes are unconfirmed.** Field names for Lever and Ashby
  descriptions must be verified against live responses in phase 3. If one vendor
  has no usable description field, that ATS degrades to the metadata-plus-link
  state — the design already handles it.
- **The Canadian half of the company list is dead, and this is now the
  highest-value fix in the project — above any UI work.** Verified 2026-10-13:
  19 of 39 boards 404, and they are overwhelmingly the Canadian employers —
  Wealthsimple, Clio, Cohere, Hootsuite, Jane, Later, Thinkific, Trulioo,
  Vidyard, Visier, Benevity, Ada, Coveo, 1Password, AppDirect. 15 of 16 Lever
  tokens fail. The result is **13 Canada-eligible matches** for a user whose
  whole filter is Canada eligibility. Wealthsimple appears in the README's own
  example output as a working Greenhouse board yet 404s now, which suggests
  wrong slugs rather than departed companies. Re-sourcing these tokens is manual
  per-company work (open careers page, click a posting, read the slug) and is
  tracked as its own task, not folded into a UI phase.
- **`timing` says nothing useful yet.** It needs a full co-op cycle of history.
  The insights route must be honest about having insufficient data rather than
  rendering a confident empty chart.
- **No git repository.** This directory is not under version control, so this
  spec cannot be committed. Recommend `git init` before phase 0 — a project of
  this size without history is a real risk, and it is also the artifact an
  employer looks at.
- **Not decided:** whether the pipeline view needs calendar integration for
  interview dates. Deferred; `next_action_at` exists so the data is there if
  wanted later.
