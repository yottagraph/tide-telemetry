# Tide Telemetry

## Vision

# Tide Telemetry — Cloud SQL Compute-Job E2E

A small Aether app that proves the BC 2.0 **compute-job → Cloud SQL**
round-trip works end-to-end from a fresh tenant. One compute job
generates synthetic maritime activity events, aggregates them by
category, and writes the per-category summary rows into the tenant
Cloud SQL Postgres. One page in the app reads those rows back and
shows them.

This project exists to test the compute infrastructure from a coding
agent's perspective. The success criterion is **"the job deploys via
`/deploy_job`, runs end-to-end, and the rows it wrote show up both in
Cloud SQL and on the page."** Resist over-engineering — there's no
business problem hiding here.

## Deliverables

### 1. Compute job (`jobs/aggregate_signals/`)

A Python compute job that:

1. **Synthesises ~1000 events** deterministically from a per-run
   `SMOKE_RUN_ID` (UUID; generate if not supplied). Each event has
   a `category` (pick from a small fixed set like
   `vessel,event,entity,signal,observation`) and a numeric `value`
   in `[0, 100)`. Same `run_id` → same input → same aggregates;
   re-runs append, they do not overwrite.
2. **Aggregates** to `(category, event_count, sum_value)` per category.
3. **Writes one row per category** to a `tide_aggregates` table in
   the per-tenant Cloud SQL Postgres, tagged with `run_id` and a
   `created_at` timestamp. Use `CREATE TABLE IF NOT EXISTS` — there
   is no migrations framework.
4. **Reads the rows back** in the same execution and logs them, plus
   a final `JOB_SUCCESS run_id=… rows_inserted=… total_events=…`
   sentinel line so log scrapers (and humans) can confirm the run
   landed.
5. Exits 0 on success, 1 on any failure (missing GRANTs, schema
   mismatch, total-events drift, anything).

Manifest (`jobs/aggregate_signals/job.yaml`): pick the runner / cpu /
memory / task_timeout that suits a ~1000-row synthesis. Read the
`compute` Aether skill before you decide — it explains the Cloud Run
Jobs vs K8s Jobs trade-off, the standard env that's auto-injected,
and the validator you should run locally before pushing
(`python3 scripts/validate-job-manifest.py jobs/aggregate_signals/job.yaml`).

The schedule field is **off** for this project — the test path is
"Run now from the Portal", not "wait for cron". Adding `schedule:` is
a nice-to-have but not required.

### 2. Reader page (`pages/index.vue`)

A single page that shows the most recent runs and their per-category
aggregates. Minimal Vuetify is fine:

- One **"Latest run" card** at the top showing `run_id`,
  `created_at`, total `event_count`, and total `sum_value`.
- One **data table** below it with the per-category rows of the
  latest run (`category`, `event_count`, `sum_value`).
- A history strip showing the last ~10 runs so you can tell at a
  glance that re-running the job adds new rows rather than
  clobbering old ones.

Group by `run_id` and order by `created_at DESC`. If there are zero
rows yet (job hasn't run), show a friendly "No runs yet — trigger
the `aggregate_signals` job from the Portal's Jobs tab" state
instead of a blank table.

### 3. Server route (`server/api/aggregates.get.ts`)

A Nitro GET route that returns the JSON the page binds to. Reads
from Cloud SQL via the standard `getDb()` helper in
`server/utils/neon.ts` (the same helper Aether already uses for
`DATABASE_URL`-driven Postgres; BC 2.0 Cloud SQL is wire-compatible
with it). Return shape is your call; keep it boring.

Handle the "table doesn't exist yet" case per
`skills/aether/storage.md` § _Handle missing tables in GET routes_ —
the first page load on a fresh deploy will hit it before the job
has ever run, and you should return an empty state, not a 500.

## Acceptance criteria

Do all four of these and report back; that's the end of the project:

1. `/deploy_job aggregate_signals` succeeds — the GitHub Actions
   workflow goes green and the Portal's Jobs tab shows the new job.
2. An ad-hoc run (`POST <GATEWAY_URL>/api/projects/<ORG_ID>/jobs/aggregate-signals/run`,
   or the Portal's "Run now" button) completes with status
   `Succeeded`.
3. Querying the tenant Cloud SQL returns the expected rows for that
   run — one per category, `event_count` summing to the synthesised
   total. Use whatever tool the platform makes available; the
   `broadchurch-platform` MCP server's read helpers, an in-cluster
   psql via Connect Gateway, or the deployed app's
   `/api/aggregates` endpoint all qualify.
4. The deployed app's home page renders those rows. Push to `main`,
   wait for the Vercel deploy, hit the URL.

## Tech notes

- **This is a compute-infra test, not a product.** Don't add auth,
  branding, analytics, charts, dark mode, or any feature not listed
  above. Resist scope creep — the goal is to prove the substrate
  works, not to ship a real app.
- **Read the `compute` skill first.** `.agents/skills/aether/compute.md`
  is the canonical guide. It covers `job.yaml` shape, the
  `runner: cloud_run` vs `runner: k8s_job` decision, the standard env
  vars compute jobs receive, secret-ref syntax for any extra env
  values you might need, the validator, and where every component
  (image, Cloud Run Job, Cloud Scheduler entry, Portal registration)
  lives. Pair it with `compute.md`'s _Cloud SQL vs BigQuery — where
  to write_ section to confirm Cloud SQL is the right target here
  (it is — this is exactly the "job updates a few rows the UI then
  reads" pattern).
- **`/deploy_job` is the supported path.** Don't hand-roll a
  `kubectl apply`, don't `gcloud run jobs deploy` directly, don't
  build a Dockerfile by hand (the deploy workflow auto-generates one
  if missing). If `/deploy_job` doesn't work, **stop and report** —
  that's a platform finding worth surfacing rather than working
  around.
- **DATABASE_URL — two flavors, same destination.** The Vercel app
  reads `DATABASE_URL` from injected env, same as any Aether app
  using `getDb()`. The compute job side may need additional wiring
  to reach the same Cloud SQL instance — read `compute.md`'s
  _Standard environment variables_ section, and if anything is
  missing or surprising, log a clear error and report it. Either
  outcome ("it just worked" or "DATABASE_URL wasn't there for the
  job — had to do X") is a useful test result.
- **Cloud SQL warm-up takes 5-15 min.** The tenant is provisioned
  with `cloud_sql: true`, so the async worker is queued. Before you
  can write to it, the warm-up has to finish — watch the Portal
  cockpit / `get_infra_status` until the Cloud SQL row is `ready`
  and `DATABASE_URL` has landed in Vercel env. Don't try to deploy
  the job until that's true; you'll just churn deploy cycles.
- **Verification path matters.** Don't trust the Portal job-run
  status alone — read the Cloud SQL rows back. The point of the
  test is to prove the data plane works end-to-end, not just that
  the container exited 0.
- **You do NOT need to validate the UI behavior by running your own
  UI and gathering screenshots.** A one-shot `curl` against the
  deployed `/api/aggregates` endpoint (after pushing to `main` and
  waiting for the Vercel deploy) is enough to prove the read path
  works.
- **Auth can use the default dev bypass** (`NUXT_PUBLIC_USER_NAME`).
  No real users will see this app.

## Feedback we want back

When you're done — or stuck — report on:

1. **Did the documented path actually work?** Which steps in the
   `compute` skill, `/deploy_job` command, and `storage` skill held
   up; which ones drifted from reality; which ones were missing
   something you had to figure out yourself.
2. **Where did the platform make you wait or guess?** Cloud SQL
   warm-up, env-var propagation, Vercel deploy lag, anything where
   the right "is it ready?" signal wasn't obvious.
3. **What would have made this 10x easier?** A scaffold command,
   a more explicit standard env, a worked Cloud-SQL-from-a-job
   example in the skill, a different validator output — anything
   concrete.

That feedback is the actual product of this project; the running
job is just the evidence the substrate works.

## Status

Project just created. Run `/build_my_app` in Cursor to start building.

## Modules

### `jobs/aggregate_signals/`

Python Cloud Run Job that proves the BC 2.0 compute-job → Cloud SQL
round-trip. Synthesises 1000 deterministic events from `SMOKE_RUN_ID`
(generated if unset), aggregates by category, writes one row per
category into `tide_aggregates` via `DATABASE_URL`, reads them back in
the same execution, and emits the
`JOB_SUCCESS run_id=… rows_inserted=… total_events=…` sentinel line.
Validated by `scripts/validate-job-manifest.py`. Deploy with
`/deploy_job aggregate_signals`; trigger with the Portal's "Run now"
button.

### `server/utils/neon.ts`

Shared `pg.Pool` factory (`getDb()`) that reads `DATABASE_URL` and
returns `null` when it's not set. Wire-compatible with both BC 1.0
Neon and BC 2.0 Cloud SQL. Exports `isMissingTableError` for the
`42P01 undefined_table` empty-state path.

### `server/api/aggregates.get.ts`

Nitro GET route. Selects the latest ~10 `run_id` groups from
`tide_aggregates`, joins their per-category rows, and returns the
data shaped for the home page. Handles the
"DATABASE_URL not set" and "table doesn't exist yet" cases without
500ing — both surface as friendly empty states upstream.

### `pages/index.vue`

Single-page reader. Shows the latest run as a card + per-category
table, with a history strip below for the prior ~9 runs so re-running
the job is visibly additive. Surfaces three empty / not-ready states
("DATABASE_URL not set", "table doesn't exist yet", "no runs yet")
before the first job execution.
