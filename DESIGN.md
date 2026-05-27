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

**Build:** complete and pushed to `main`. App is live at
`https://tide-telemetry.vercel.app/` — the home page renders, and
`GET /api/aggregates` returns the documented "not configured" empty
state (`{configured:false, tableExists:false, runs:[]}`) without
500ing.

**End-to-end:** blocked on two platform issues — `/deploy_job`
silently fails to create the Cloud Run Job, and `DATABASE_URL` has
not propagated to the Vercel function env. See _Test results_ below.

## Test results & feedback

The acceptance criteria #1–#4 are not yet met; only #4's read-path
machinery is verified (the read path works, but it has no data yet).
The blockers below are platform-side findings, captured per the
DESIGN's "feedback is the actual product" rubric.

### 1. Did the documented path actually work?

- **`scripts/validate-job-manifest.py`** — held up perfectly.
  Validated the manifest cleanly on the first try, produced normalized
  JSON. No drift.
- **`storage.md` "Handle missing tables in GET routes"** — held up.
  Catching SQLSTATE `42P01` and rendering an empty state worked as
  documented. The route also returns a sensible
  `{configured:false}` shape when `DATABASE_URL` itself is unset,
  which made the live "not yet wired" page state self-explanatory.
- **`deploy_job.md` Step 5 (POST `/api/projects/<org>/deploy`)** —
  drifted from reality. The doc says the endpoint triggers the
  `deploy-job.yml` GitHub Actions workflow. In practice it returns
  `{"ok":true,"method":"cloud-build","build_id":"..."}` and no GHA
  run is created (verified via `gh run list` — only `Tenant Init`
  ever ran). The Cloud Build link in `log_url` requires GCP console
  auth, which the coding agent does not have, so the build is
  effectively opaque.
- **`deploy_job.md` Step 7 (POST `/jobs/<name>/run`)** —
  the endpoint exists and returns a real GCP error, but with three
  deploy attempts spanning ~25 minutes, the Cloud Run Job
  `aggregate-signals` was never created. The Portal returned
  `200 ok` from `/deploy` each time; the build either silently
  failed or is queued indefinitely. No way to tell from outside
  GCP console.
- **`compute.md` Standard environment variables** — couldn't
  verify, because the job never ran. `DATABASE_URL` injection into
  the Cloud Run Job is therefore untested.

### 2. Where did the platform make you wait or guess?

- **Build status is opaque to the agent.** The Portal exposes
  `/deploy` (POST) and `/jobs/<name>/run`+`/runs` (POST/GET) but no
  `/builds/<id>` or `/deploys/<id>` endpoint. The `log_url` it
  returns is the public Cloud Build console URL which requires
  Google SSO. When the build fails, the only externally-visible
  signal is "the Cloud Run Job still doesn't exist" — the agent
  has to poll `/jobs/<name>/run` and infer from the
  `(or resource may not exist)` half of the GCP error. That's
  guesswork.
- **`DATABASE_URL` propagation to Vercel.** The user reported
  the project as "provisioned", but `/api/aggregates` on the live
  app still reports `configured:false` ~30 minutes later, meaning
  `DATABASE_URL` is not in the Vercel function env. Pushed an
  empty commit to force a redeploy in case the env was added
  post-build; still `configured:false` afterwards. There's no
  documented "is the DATABASE_URL wired up?" signal short of
  hitting the app and reading its empty state.
- **Cloud SQL warm-up signal.** `compute.md` says watch the
  Portal cockpit / `get_infra_status` for `cloud_sql: ready`. The
  Portal `/api/projects/<org>/infra-status` endpoint returns 404
  (no handler). `get_infra_status` is an MCP-side tool, and
  `lovelace-elemental` was in the `error` state during this
  session. So the documented "is it ready" signals were both
  unreachable.

### 3. What would have made this 10× easier?

- **A status endpoint on the Portal.** Even a barebones
  `GET /api/projects/<org>/deploys/<build_id>` returning
  `{status, log_excerpt, last_message}` would have closed the
  black-box. Today the agent has no way to know whether a deploy
  is queued, running, or has failed without GCP console auth.
- **Either fold the `deploy-job.yml` GHA workflow into the path,
  or update `deploy_job.md`.** The doc says GHA; the API does
  Cloud Build. The agent can `gh run view` GHA logs (`gh` is
  read-only but works for log inspection), so steering deploys
  through GHA — or simply documenting the Cloud-Build-only
  reality — would unblock self-diagnosis.
- **A `/api/projects/<org>/env` (or similar) endpoint** that
  reports which env vars are wired to the Vercel project would
  let the agent confirm `DATABASE_URL` propagation without
  needing to commit to the repo + curl the live app to deduce.
- **A worked Cloud-SQL-from-a-job example in `compute.md`.** The
  skill mentions `DATABASE_URL` is injected for tenants with
  Cloud SQL, but the example block (line ~127 in `example_job/main.py`)
  is a `psycopg.connect(os.environ["DATABASE_URL"])` snippet inside
  a comment, with no detail on Cloud SQL proxy / connector
  requirements, SSL mode, IAM auth vs password auth, etc. A
  10-line working `main.py` that opens the connection, runs
  `CREATE TABLE IF NOT EXISTS`, and exits — committed alongside
  the example_job — would have made this whole project a copy-paste.
- **Validator could warn about `task_timeout: "10m"` vs Cloud Run
  Jobs minimums.** Not blocking here, but the validator silently
  accepts durations that may exceed/fall short of platform
  minimums; surfacing the platform constraints inline would catch
  these earlier.

### Repro for the platform team

```
# After tenant provisioning (cloud_sql:true), with this repo on main:
curl -sf -X POST \
  https://broadchurch-portal-194773164895.us-central1.run.app/api/projects/org_lAYeeUtyfn6AyLVI/deploy \
  -H 'Content-Type: application/json' \
  -d '{"type":"job","name":"aggregate_signals"}'
# → 200 {"ok":true,"method":"cloud-build","build_id":"…"}

# 6+ minutes later:
curl -sf -X POST \
  https://broadchurch-portal-194773164895.us-central1.run.app/api/projects/org_lAYeeUtyfn6AyLVI/jobs/aggregate-signals/run \
  -H 'Content-Type: application/json' -d '{}'
# → 403 "Permission 'run.jobs.run' denied … (or resource may not exist)"
```

Build IDs observed during this session:
`cb8db50d-dcc3-4600-8a88-d99e47d243f7`,
`c806b533-e552-4a04-8034-9b62abf542d2`,
`5a2f24db-c600-4853-844a-5a92c978fe87`.
None of them produced a Cloud Run Job named `aggregate-signals` in
the tenant's GCP project.

## Modules

_None yet — the agent will populate this as features are built._
