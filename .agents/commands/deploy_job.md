# Deploy Compute Job

Deploy a Cloud Run Job from the `jobs/` directory via the Broadchurch Portal.

## Overview

A "compute job" is a containerized Python (or any-language) entrypoint
that runs on Google Cloud Run Jobs (or on the tenant's per-tenant GKE
cluster as a K8s Job when `runner: k8s_job` is set in `job.yaml`). Use
it for:

- **Cron jobs** (set `schedule:` in `job.yaml` — Cloud Scheduler is wired up automatically)
- **Event-triggered batch work** (HTTP-triggered from your Vercel app or an Agent Engine tool)
- **Heavy compute** (entity enrichment, scoring, ETL, exports, aggregations)
- **Workflow steps** (called from a Cloud Workflow definition under `workflows/`)

This command triggers a Cloud Build → Cloud Run Job deploy through
the Portal. No local GCP credentials needed.

The job must live in `jobs/<name>/` with at minimum:

```
jobs/<name>/
├── main.py             # Entrypoint (or any executable; see Dockerfile)
├── requirements.txt    # Python deps (only required if no custom Dockerfile)
├── job.yaml            # Manifest: resources, schedule, env
└── Dockerfile          # Optional — auto-generated if missing
```

**Prerequisite:** The project must have a valid `broadchurch.yaml`
(created during provisioning).

---

## Step 1: Read Configuration

Read `broadchurch.yaml` from the project root.

```bash
cat broadchurch.yaml
```

**If the file does not exist:**

> This project hasn't been provisioned yet. Create it in the Broadchurch Portal first.

Stop here.

Extract these values:

- `tenant.org_id` (tenant org ID)
- `gateway.url` (Portal Gateway URL)

---

## Step 2: Discover Jobs

List the directories under `jobs/`:

```bash
ls -d jobs/*/
```

**If no directories exist:**

> No jobs found. Create one by making a directory under `jobs/` with the structure above.
> See `docs/COMPUTE_JOBS.md` for guidance, or copy `jobs/example_job/` as a starting point.

Stop here.

**Skip `example_job`** — this is a template placeholder and should
never be deployed. Filter it out before proceeding.

**If multiple jobs remain:** Deploy all of them. If called interactively
(not from `/build_my_app`), ask the user which one to deploy.

**If only one job remains:** Proceed with it — no confirmation needed.

**Important:** Job directory names should use underscores; the deploy
workflow translates them to Cloud Run-friendly hyphens automatically.

---

## Step 3: Validate Job Structure

For the selected job directory, verify the required files exist:

```bash
ls jobs/<name>/main.py jobs/<name>/job.yaml
```

If `Dockerfile` exists, the deploy uses it as-is. If not, the deploy
auto-generates a Python 3.12 Dockerfile that runs `python main.py`.

If using the auto-Dockerfile, `requirements.txt` must also exist:

```bash
ls jobs/<name>/requirements.txt 2>/dev/null
```

Validate the manifest is well-formed:

```bash
yq -e '.name // ""' jobs/<name>/job.yaml >/dev/null
```

The platform-side validator (`scripts/validate-job-manifest.py`, run by
the deploy workflow) is the canonical enforcer — it rejects unknown
fields, bad runner values, malformed durations, secret-ref typos, and
cross-field violations (e.g. `runner: cloud_run` with
`provisioning_model: spot`) with line-level error messages. You can
preview locally:

```bash
python3 scripts/validate-job-manifest.py jobs/<name>/job.yaml
```

If it exits 0, the deploy workflow will accept the manifest. See
`docs/COMPUTE_JOBS.md` (in the broadchurch docs) for the full schema
reference, including the `runner: k8s_job` escalation path (per-tenant
GKE cluster — heavy compute / GPU / multi-day runs), the
`${secret://name/version}` env-deref syntax, the `notify:` block
(Slack on failure/success + artifact links), and the `post_steps:`
inline cleanup hooks.

---

## Step 4: Ensure Code is Pushed

The deployment workflow runs on the code in the GitHub repo, not the
local working directory:

```bash
git status
```

**If there are uncommitted changes in `jobs/<name>/`:**

> Your job code has local changes that aren't pushed yet. The
> deployment will use the version on GitHub. Would you like me to
> commit and push first?

If yes, commit and push. If no, warn them and continue.

---

## Step 5: Trigger Deployment

Call the Portal API to trigger the deploy workflow:

```bash
curl -sf -X POST "<GATEWAY_URL>/api/projects/<ORG_ID>/deploy" \
  -H "Content-Type: application/json" \
  -d '{"type": "job", "name": "<JOB_NAME>"}'
```

**If this fails with 404:** The job directory may not exist on GitHub
yet. Push your code first.

**If this succeeds:** The Portal has triggered the `deploy-job.yml`
GitHub Actions workflow.

---

## Step 6: Monitor Progress

> Deployment triggered! The compute job is being deployed via GitHub Actions.
>
> - **Job:** <name>
> - **Workflow:** deploy-job.yml
>
> This typically takes 2-5 minutes (container build + Cloud Run Job create/update).
> You can monitor progress:
>
> - In the Broadchurch Portal under your project's "Jobs" tab
> - On GitHub: `https://github.com/<REPO>/actions`
>
> Once complete:
>
> - The job is callable via the Portal "Run now" button
> - If `schedule:` is set in `job.yaml`, Cloud Scheduler will trigger it automatically
> - Run history is visible in the Portal's "Jobs" tab

---

## Step 7: (Optional) Trigger a Test Run

After deployment, trigger an ad-hoc run to verify the job works:

```bash
curl -sf -X POST "<GATEWAY_URL>/api/projects/<ORG_ID>/jobs/<JOB_NAME>/run" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Then poll for results:

```bash
curl -sf "<GATEWAY_URL>/api/projects/<ORG_ID>/jobs/<JOB_NAME>/runs" | jq '.runs[0]'
```

Each run has a `status` field. Terminal statuses are: `Succeeded`,
`Failed`, `Cancelled`.

---

## Troubleshooting

### Build fails

Check the GitHub Actions logs for the `Deploy Compute Job` workflow.
Common issues:

- **"requirements.txt" errors**: list every Python dep your `main.py` imports.
- **Custom Dockerfile**: ensure the `CMD` actually runs your entrypoint.
- **Memory/CPU mismatch**: Cloud Run Jobs require 1 vCPU per 4 GiB memory minimum (see Google Cloud docs).

### Job times out

Increase `task_timeout` in `job.yaml`. Cloud Run Jobs supports up to 24
hours per task. For longer-running work, split into shards
(`task_count: N` + `parallelism: N`) or escalate to K8s Jobs via
`runner: k8s_job` in `job.yaml` (see `docs/COMPUTE_JOBS.md`
"Escalation: K8s Jobs" for when the escalation is the right call).

### Schedule doesn't fire

Cloud Scheduler entries are named `job-<name>`. Check in the Cloud
Console (Cloud Scheduler → us-central1) and verify:

- The cron expression is valid
- The OAuth service account email matches the tenant SA
- The target URL points at the Cloud Run Job's `:run` endpoint

### Need to update an existing job

Just run `/deploy_job` again. It will rebuild the container, update
the Cloud Run Job in place, and reconcile the Cloud Scheduler entry
to match the current `job.yaml`.

### Want to delete a job

Delete via the Portal "Jobs" tab, or:

```bash
curl -sf -X DELETE "<GATEWAY_URL>/api/projects/<ORG_ID>/jobs/<JOB_NAME>"
```

This removes the Cloud Run Job, its Cloud Scheduler entry, and the
Portal's job registration.

---

## K8s Jobs path (`runner: k8s_job`)

When `job.yaml` sets `runner: k8s_job`, the deploy workflow takes a
different dispatch branch. Everything up through container build is
the same; what changes after build:

- **Cluster coordinates** are resolved from the Portal at deploy time
  (`GET /api/projects/<org_id>/connect-gateway`). No new fields in
  `broadchurch.yaml` — the Portal is the single source of truth.
- **Secrets** in `${secret://name/version}` env entries are fetched
  by the workflow (via the github-deploy SA) and materialized into a
  K8s Secret named `job-<name>-secrets`, keyed by env-var name. The
  rendered Pod spec references it through `secretKeyRef`. Optional
  refs (`?` suffix) are tolerated when the secret is missing.
- **Manifest** is rendered by `scripts/render-k8s-job.py`:
    - `schedule: ""` → a one-shot K8s `Job` (auto-runs on creation).
    - `schedule: "0 2 * * *"` → a K8s `CronJob` (the controller
      fires runs per the cron).
- **Submission** uses `kubectl` over Connect Gateway. The workflow
  exits as soon as the apiserver accepts the resource and the Pod
  surfaces no immediate failure; live execution monitoring belongs
  to the Portal cockpit (Linear ENG-553) and runs out-of-band.
- **`provisioning_model: spot`** is rejected at deploy time today;
  the per-tenant clusters don't have a spot nodepool yet. Tracked
  as [ENG-563](https://linear.app/lovelace-tech/issue/ENG-563); set
  `provisioning_model: standard` in the meantime.

Design doc:
[`docs/BC_2_TENANT_JOBS_DISPATCHER.md`](https://github.com/Lovelace-AI/broadchurch/blob/main/docs/BC_2_TENANT_JOBS_DISPATCHER.md)
in the broadchurch repo. `docs/COMPUTE_JOBS.md` (broadchurch repo)
covers the schema reference; this `deploy_job.md` only covers the
deploy mechanics.
