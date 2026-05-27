# Compute Jobs

A compute job is a **container that runs and exits** in the tenant's
GCP project. It is the right primitive whenever the work doesn't fit
the request/response shape of a Vercel function or the conversational
shape of an Agent Engine call. Use it for:

- **Cron** (nightly aggregations, daily exports, periodic refreshes)
- **Event-triggered batch** (HTTP from the Aether app or from an Agent
  Engine tool — kick off 30-minute work and let it run async)
- **Sharded fan-out** (process 100k entities across N parallel tasks)
- **Workflow steps** (multi-step DAGs orchestrated by Cloud Workflows)

| Capability       | How to check                                                              | Standard env injected                                                                    | Deploy command |
| ---------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | -------------- |
| **Compute Jobs** | Always on for BC 2.0 tenants. `gcp.project_id` set in `broadchurch.yaml`. | `ORG_ID`, `GATEWAY_URL`, `GOOGLE_CLOUD_PROJECT`, `DATABASE_URL`, `BIGQUERY_DATASET`, ... | `/deploy_job`  |

The Aether app never holds GCP credentials directly — the deploy
workflow uses the GitHub Deploy SA to build + register the job, and
the job runs as the tenant's **runtime SA** inside `bc-{slug}` (the
per-tenant GCP project). Standard env vars give it everything it
needs to reach the same storage backends the app uses.

For the canonical schema reference (every field on `job.yaml`,
secret-ref syntax, notify Block Kit overrides, post-step hooks), see
[`docs/COMPUTE_JOBS.md`](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/COMPUTE_JOBS.md)
in the broadchurch repo. This skill is the agent-first guide; the
broadchurch doc is the contract.

## Critical: never do these

The agent reflexively reaches for patterns that fit Vercel functions
or shared containers but break compute jobs. **Stop**, re-read this
file, and use the patterns below instead:

- **DO NOT put a job container into the Aether app.** Jobs ship as
  their own image — they have a `main.py` (or any executable) and a
  `requirements.txt` separate from the app's `package.json`. Mixing
  them bloats the Vercel build and breaks Cloud Run Jobs deploy.
- **DO NOT expect a job to listen on `$PORT`.** Cloud Run _Services_
  listen on a port; Cloud Run _Jobs_ are headless. The container
  starts, runs `main.py`, exits. If you try to bind a port, the job
  exits 0 immediately because `main.py` returned.
- **DO NOT keep state on the filesystem between runs.** Each
  execution is a fresh container — `/tmp` doesn't survive. Store
  progress / cursors / output in Cloud SQL, Firestore, BigQuery, or
  GCS — never on local disk.
- **DO NOT call a job synchronously from a Vercel route.** Vercel
  has a 60s execution ceiling; jobs can run 24h+. POST to
  `/api/projects/<orgId>/jobs/<name>/run` to _trigger_ (returns
  immediately with an execution ID) and poll
  `/api/projects/<orgId>/jobs/<name>/runs` for status.
- **DO NOT hand-roll a Pub/Sub → Cloud Run Job bridge.** The Portal
  trigger surface (`POST .../jobs/<name>/run`) is the supported entry
  point. Pub/Sub natively triggers Cloud Run _Services_, not Jobs —
  if you genuinely need event-driven triggering, ask the platform
  to add it rather than running a sidecar Service.
- **DO NOT add a `Dockerfile` "just to be safe".** The deploy
  workflow auto-generates a Python 3.12 Dockerfile that runs
  `python main.py` if one isn't present. Only write your own if
  you genuinely need a non-Python runtime or unusual deps.

## Quick start: a nightly cron job

```
jobs/nightly_refresh/
├── main.py
├── requirements.txt
└── job.yaml
```

`main.py`:

```python
"""Nightly aggregation — runs as the tenant runtime SA."""

import os
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]

with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
    cur.execute("""
        INSERT INTO daily_summary (date, total_count)
        SELECT CURRENT_DATE, COUNT(*) FROM events
            WHERE created_at::date = CURRENT_DATE
        ON CONFLICT (date) DO UPDATE
            SET total_count = excluded.total_count
    """)
    conn.commit()

print("aggregation complete")
```

`requirements.txt`:

```
psycopg[binary]>=3.1
```

`job.yaml`:

```yaml
name: nightly-refresh
cpu: '1'
memory: '1Gi'
task_timeout: '10m'
schedule: '0 2 * * *' # 2 AM daily, UTC
schedule_timezone: 'UTC'
```

Then commit, push, and from Cursor / Claude Code:

```
/deploy_job nightly_refresh
```

Cloud Scheduler fires the job nightly. Run history and ad-hoc
"Run now" live in the Portal's "Compute Jobs" tab (see
[ENG-638 W2](https://linear.app/lovelace-tech/issue/ENG-641) for
the BC 2.0 cockpit wiring).

## Quick start: trigger a job from your Aether app

The app can kick off any deployed job via the Portal gateway. Use
this for "run my 30-minute enrichment in the background while the
user keeps clicking around":

```typescript
// server/api/refresh.post.ts
export default defineEventHandler(async () => {
    const gateway = useRuntimeConfig().public.gatewayUrl;
    const orgId = useRuntimeConfig().public.tenantOrgId;

    const res = await $fetch<{ executionId: string }>(
        `${gateway}/api/projects/${orgId}/jobs/nightly-refresh/run`,
        { method: 'POST', body: {} }
    );

    return { kicked_off: res.executionId };
});
```

The Portal authenticates the request against the calling tenant,
mints a token for the tenant runtime SA, and calls Cloud Run Jobs
Execution API on your behalf. **Returns immediately** — poll
`/jobs/<name>/runs` for terminal status (`Succeeded` / `Failed` /
`Cancelled`).

> **Trigger latency note**: jobs take ~5-20s of cold-start before
> the container is actually running. Don't show a spinner; show
> "queued" and reload run-history every few seconds.

## Quick start: a sharded fan-out

Set `parallelism` and `task_count` to the same value for
embarrassingly parallel work. Each task receives
`CLOUD_RUN_TASK_INDEX` (0-based) and `CLOUD_RUN_TASK_COUNT`:

```yaml
# job.yaml
name: enrich-entities
cpu: '2'
memory: '4Gi'
task_count: 8
parallelism: 8
task_timeout: '30m'
```

```python
import os

shard = int(os.environ["CLOUD_RUN_TASK_INDEX"])
total = int(os.environ["CLOUD_RUN_TASK_COUNT"])

for entity in get_all_entities()[shard::total]:
    process(entity)
```

Cloud Run runs all 8 tasks in parallel against the same container
image. Each one slices the input by `[shard::total]`. The execution
succeeds when all tasks exit 0.

## Quick start: a multi-step workflow

For pipelines with retry semantics, error branches, or "after all
shards complete, then aggregate" patterns, escalate from a single
job to a Cloud Workflow that calls multiple jobs:

```
jobs/
├── enrich_entities/      # sharded job
├── score_entities/       # aggregator job
└── write_results/        # bulk insert job

workflows/
└── refresh_pipeline/
    ├── workflow.yaml     # Cloud Workflows DSL
    └── manifest.yaml     # platform-side schedule/timezone/input
```

Deploy each job with `/deploy_job` and the workflow itself with
`/deploy_workflow`. The workflow DSL lives at
[cloud.google.com/workflows/docs/reference/syntax](https://cloud.google.com/workflows/docs/reference/syntax);
the platform-side `manifest.yaml` fields are documented in
[`docs/COMPUTE_JOBS.md` § Workflow manifest reference](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/COMPUTE_JOBS.md#workflow-manifest-reference).

You almost certainly don't need a workflow if a single sharded job
suffices — the workflow engine is the right call only when steps
need retry-on-failure / continue-on-error / fan-out-then-aggregate
semantics that a single job can't express.

## Job manifest (`job.yaml`) at a glance

| Field                | Default      | Notes                                                                                                                                     |
| -------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `name`               | dir name     | Lowercase, hyphenated, ≤ 49 chars.                                                                                                        |
| `runner`             | `cloud_run`  | `cloud_run` (default) or `k8s_job` — see Escalation below.                                                                                |
| `cpu`                | `"1"`        | Cloud Run: `"1"`/`"2"`/`"4"`/`"8"`. K8s Jobs: up to nodepool ceiling.                                                                     |
| `memory`             | `"1Gi"`      | Cloud Run: up to `"32Gi"`. K8s Jobs: up to nodepool ceiling.                                                                              |
| `max_retries`        | `1`          | Per-task retry count, 0-10.                                                                                                               |
| `task_timeout`       | `"1h"`       | `"300s"`, `"30m"`, `"12h"`. Cloud Run hard-caps at `"24h"`.                                                                               |
| `parallelism`        | `1`          | Tasks running concurrently.                                                                                                               |
| `task_count`         | `1`          | Total task count (for sharding).                                                                                                          |
| `provisioning_model` | `"standard"` | `"standard"` / `"spot"`. K8s-Jobs-only. **Rejected at deploy today** — pending [ENG-563](https://linear.app/lovelace-tech/issue/ENG-563). |
| `schedule`           | (none)       | 5-field cron. Cloud Run only for now; K8s CronJob lands per dispatcher roadmap.                                                           |
| `schedule_timezone`  | `"UTC"`      | IANA timezone (`"America/New_York"`).                                                                                                     |
| `env`                | `{}`         | Extra env vars (see secret-ref syntax below).                                                                                             |
| `notify`             | (none)       | Slack/email notification rendering server-side.                                                                                           |
| `post_steps`         | `[]`         | Inline shell scripts that run after the main task.                                                                                        |

Run the validator locally before pushing to catch malformed
manifests at edit-time:

```bash
python3 scripts/validate-job-manifest.py jobs/<name>/job.yaml
```

The same validator runs in the deploy workflow and is the canonical
schema enforcer — it rejects unknown fields, bad runner values,
malformed durations, secret-ref typos, and cross-field violations
(e.g. `runner: cloud_run` + `provisioning_model: spot`) with
line-level error messages.

### Env values

```yaml
env:
    SHARD_LIMIT: '5000' # literal
    DB_PASS: '${secret://nightly-db-pass/latest}' # required Secret Manager ref
    OPT_API_KEY: '${secret://maybe-missing/1?}' # optional; empty string if missing
```

`name` is a Secret Manager secret in the tenant's GCP project.
`version` is either a numeric version (`"1"`, `"42"`) or `"latest"`.
The trailing `?` makes the ref optional — required refs fail the
run on missing secrets.

Don't restate the standard env (`DATABASE_URL`, `BIGQUERY_DATASET`,
`ORG_ID`, etc.) in `env:` — those are auto-injected. See below.

### Notifications

```yaml
notify:
    on_failure:
        slack: '#bc-alerts'
    on_success:
        slack: '#bc-jobs'
        email: oncall@example.com
    artifacts:
        - path: /tmp/report.html # task-local file, auto-uploaded
          slack_link: 'Report'
        - gcs: gs://my-bucket/result.csv # already-uploaded GCS object
          slack_link: 'CSV'
    signed_url_ttl: '24h'
```

The notify renderer + signed-URL minter ([ENG-552](https://linear.app/lovelace-tech/issue/ENG-552))
is in-flight. Until it lands, the schema parses but no Slack
message is sent — set up notifications when the issue closes.

## Standard environment variables

Every job task automatically receives:

| Env var                | Value                                                     | When set                |
| ---------------------- | --------------------------------------------------------- | ----------------------- |
| `ORG_ID`               | Auth0 org ID for this tenant                              | Always                  |
| `GATEWAY_URL`          | Broadchurch Portal base URL                               | Always                  |
| `GOOGLE_CLOUD_PROJECT` | `bc-{slug}`                                               | Always (BC 2.0)         |
| `QUERY_SERVER_URL`     | Yottagraph Elemental API URL                              | Always                  |
| `DATABASE_URL`         | Cloud SQL Postgres URL (transactional)                    | Cloud SQL enabled       |
| `BIGQUERY_DATASET`     | `bc-{slug}.bc_{slug}_analytics`                           | BigQuery enabled        |
| `BIGQUERY_LOCATION`    | BQ location (`US`, `us-central1`, ...)                    | BigQuery enabled        |
| `CLOUD_RUN_TASK_INDEX` | 0-based shard index                                       | Cloud Run, sharded jobs |
| `CLOUD_RUN_TASK_COUNT` | Total shards                                              | Cloud Run, sharded jobs |
| `EXEC_ID`              | Per-execution UUID (use for log lines, artifact prefixes) | Always                  |

Anything in `job.yaml`'s `env:` block is added on top. Optional
capability env vars (`DATABASE_URL`, `BIGQUERY_DATASET`) **may not
be set** — code SHOULD treat them as `os.environ.get(...)` and
either skip the missing-capability branch or fail fast with a clear
error message naming the missing capability.

## Cloud SQL vs BigQuery — where to write

A common source of confusion. The short answer:

| Dimension          | Cloud SQL (`DATABASE_URL`)                          | BigQuery (`BIGQUERY_DATASET`)                              |
| ------------------ | --------------------------------------------------- | ---------------------------------------------------------- |
| Workload           | Transactional. RMW, joins, FK, UI-driven mutations. | Analytical. Append-only, time-series, columnar.            |
| Typical row size   | KB                                                  | MB                                                         |
| Typical row count  | thousands–millions                                  | millions–billions                                          |
| Query latency      | ms                                                  | seconds                                                    |
| Idle cost          | constant (always-on instance)                       | zero (on-demand pricing)                                   |
| Schema flexibility | strict; migrations are real work                    | append-friendly                                            |
| App reads          | Yes — `DATABASE_URL` is what the Aether app sees    | No — app reads via Portal API ([bigquery.md](bigquery.md)) |

**Rule of thumb for compute jobs:**

- Job _reads state and updates a few rows_ → **Cloud SQL**
- Job _appends a result set the UI doesn't mutate_ → **BigQuery**
- Job _generates a snapshot for a dashboard_ → **BigQuery**
- Job _fans out work and records what it did_ → **BigQuery** for
  the audit trail; Cloud SQL only if the UI needs to mutate the
  records afterwards

If the job needs both — transactional state AND an analytics
snapshot — write to Cloud SQL first, then have a follow-up
sync step copy the snapshot to BigQuery. Don't dual-write inside
the same task.

Don't have one of these enabled? See [`storage.md`](storage.md)
for Cloud SQL provisioning and [`bigquery.md`](bigquery.md) for
the BigQuery analytical surface.

## Triggering jobs from your code

### From the Aether app (HTTP)

```typescript
const gateway = useRuntimeConfig().public.gatewayUrl;
const orgId = useRuntimeConfig().public.tenantOrgId;

await $fetch(`${gateway}/api/projects/${orgId}/jobs/<job-name>/run`, {
    method: 'POST',
    body: {
        /* optional env_overrides */
    },
});
```

Returns immediately. The Portal handles auth + token-minting. Poll
`/jobs/<name>/runs` for status. Per-execution `env_overrides`
(re-run with a different `SHARD_LIMIT`, say) lands when
[ENG-553](https://linear.app/lovelace-tech/issue/ENG-553) ships.

### From an Agent Engine tool

Same endpoint, called from inside a tool function. The agent's
delegated SA is granted `roles/run.invoker` on the job
automatically by the deploy workflow. See
[`agents.md`](agents.md) for the tool-defining pattern and
[`agents-data.md`](agents-data.md) for how the agent reaches the
Portal URL.

### From a schedule

Set `schedule:` and `schedule_timezone:` in `job.yaml`. The deploy
workflow creates a matching Cloud Scheduler entry (or K8s CronJob
for `runner: k8s_job` once that path lands). Re-deploy to change
the schedule — there's no "edit schedule" UI yet.

### From a workflow

The workflow DSL calls `googleapis.run.v1.namespaces.jobs.run`
with the job name. See the workflow quick-start above.

## Escalation: K8s Jobs

When you outgrow Cloud Run Jobs' 24h / 8 vCPU / 32GB ceiling — GPU
training, multi-day simulations, MPI workloads — escalate by
setting `runner: k8s_job` in `job.yaml`. Same `/deploy_job` flow,
same standard env, same Portal "Compute Jobs" surface; the
container runs as a Kubernetes Job on the tenant's per-tenant GKE
cluster instead of as a Cloud Run Job:

```yaml
name: heavy-enrichment
runner: k8s_job
cpu: '16'
memory: '64Gi'
parallelism: 4
task_count: 4
task_timeout: '12h'
env:
    DB_PASS: '${secret://db-pass/latest}'
```

The two paths share the same identity chain (Workload Identity
binds the tenant runtime SA to the K8s ServiceAccount the pod
runs as), the same Artifact Registry image, and the same dispatch
surface; `runner` is the only field that changes.

**When to escalate** (per the test): if Cloud Run's 24h / 8 vCPU
/ 32 GiB / 100-task ceiling fits, **stay on `cloud_run`** — it has
lower cold-start, simpler quotas, and the same observability
surface. If any one ceiling doesn't fit, switch the whole job
(mixing Cloud Run and K8s within a single job isn't supported).

**Why K8s and not GCP Batch?** [Pivoted 2026-05-23](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/BC_2_TENANT_COMPUTE_JOBS.md)
for cloud-agnostic posture. K8s primitives port to EKS/AKS if a
customer demands non-GCP deployment; GCP Batch would require a
full re-implementation. The user-facing `job.yaml` shape is the
same either way.

**`provisioning_model: spot`** is rejected at deploy today — the
per-tenant clusters don't have a spot nodepool yet
([ENG-563](https://linear.app/lovelace-tech/issue/ENG-563)). Set
`provisioning_model: standard` or omit it entirely.

Full dispatcher design:
[`docs/BC_2_TENANT_JOBS_DISPATCHER.md`](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/BC_2_TENANT_JOBS_DISPATCHER.md)
in the broadchurch repo.

## Common pitfalls

- **Container too lean for its `cpu`/`memory`.** Cloud Run rejects
  jobs that request more CPU than memory by a 1:4 ratio (a `"2"`
  vCPU job needs ≥ `"8Gi"` memory). The validator catches the
  obvious cases; the deploy workflow's Cloud Run API call surfaces
  the runtime ones.
- **Forgetting `task_count` matches `parallelism`.** For
  embarrassingly parallel work, set both to the same value. Set
  `task_count` higher than `parallelism` only when you want
  queued waves (`task_count: 10`, `parallelism: 2` runs 10 tasks
  but only 2 at a time).
- **Hardcoded paths.** `/tmp` is the only writable filesystem
  location, and it doesn't survive across executions. Write
  artifacts to GCS, not to a local path you'll never read again.
- **Slack URLs in `env:`.** Don't paste a webhook URL directly —
  put it in Secret Manager and reference it as
  `${secret://slack-webhook/latest}`. The `env:` block is visible
  in the Portal UI and to anyone with read access to the GCP
  project's Cloud Run Job spec.
- **Skipping the validator.** The platform-side validator catches
  cross-field violations (`runner: cloud_run` +
  `provisioning_model: spot`, `task_count: 5` + `parallelism: 10`,
  etc.) at deploy time. Running it locally first turns a 5-minute
  GitHub-Actions feedback loop into a 5-second one.
- **Treating a job like an agent.** Agents are conversational and
  long-lived (Vertex AI Agent Engine). Jobs are batch and exit. If
  you find yourself adding a chat loop or a tool-calling
  abstraction inside `main.py`, you're probably better off with
  an agent in `agents/` — see [`agents.md`](agents.md).

## Where things live

- **Job source**: `jobs/<name>/main.py`, `requirements.txt`,
  `job.yaml` (this repo).
- **Workflow source**: `workflows/<name>/workflow.yaml`,
  `manifest.yaml` (this repo).
- **Container image** (Cloud Run Jobs): `gcr.io/bc-{slug}/job-<name>`.
- **Cloud Run Job**: `<name>` (lowercase, hyphenated) in `bc-{slug}`.
- **K8s Job**: in the `tenant-jobs` namespace of the tenant GKE
  cluster, labelled with `bc-job-name=<name>`.
- **Cloud Scheduler entry**: `job-<name>` or `workflow-<name>` in
  `bc-{slug}` (us-central1).
- **Portal registration**: `tenants/<orgId>.jobs.<name>` (Firestore
  in the platform project, **not** the tenant Firestore).
- **Validator**: `scripts/validate-job-manifest.py` (this repo).
- **Starter**: `jobs/example_job/` — copy-and-customize.

## See also

- **Schema reference** (broadchurch repo):
  [`docs/COMPUTE_JOBS.md`](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/COMPUTE_JOBS.md)
  — every `job.yaml` field, secret-ref syntax, notify Block Kit
  override format, workflow manifest fields.
- **K8s Jobs dispatcher design** (broadchurch repo):
  [`docs/BC_2_TENANT_JOBS_DISPATCHER.md`](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/BC_2_TENANT_JOBS_DISPATCHER.md)
- **Transactional storage**: [`storage.md`](storage.md) — Cloud SQL
  (`DATABASE_URL`), Firestore (`getFirestoreDb`), Neon Postgres.
- **Analytical storage**: [`bigquery.md`](bigquery.md) — append-only
  surface, `runQuery()` / `runMutation()`, wire-format gotchas.
- **Agents**: [`agents.md`](agents.md) and
  [`agents-data.md`](agents-data.md) — when to use an agent vs a job.
- **MCP servers**: [`mcp-servers.md`](mcp-servers.md) — when to
  expose tools instead of running batch work.
- **Deployment in general**: [`deployment.md`](deployment.md) — how
  agents, MCP servers, and the Aether app all reach production.
- **Cloud Run Jobs docs**:
  [cloud.google.com/run/docs/create-jobs](https://cloud.google.com/run/docs/create-jobs)
- **Cloud Workflows DSL**:
  [cloud.google.com/workflows/docs/reference/syntax](https://cloud.google.com/workflows/docs/reference/syntax)
