"""
aggregate_signals — Tide Telemetry's BC 2.0 compute-job → Cloud SQL probe.

What it does, end-to-end:

1. Pick a deterministic seed (the `SMOKE_RUN_ID` env var; generate one
   if missing). Synthesise ~1000 maritime "activity events" — each with
   a category and a numeric value — from that seed. Same run_id =>
   same input => same aggregates.
2. Reduce the events to one (category, event_count, sum_value) row per
   category.
3. Connect to the per-tenant Cloud SQL Postgres using the
   `DATABASE_URL` env var the platform injects. `CREATE TABLE IF NOT
   EXISTS tide_aggregates(...)`, then `INSERT` one row per category
   tagged with `run_id` and `created_at = NOW()`. Re-runs append; they
   do not overwrite.
4. `SELECT` the rows we just inserted back, log them, and emit a final
   `JOB_SUCCESS run_id=… rows_inserted=… total_events=…` sentinel line.
5. Exit 0 on success, 1 on any failure.

Standard env vars injected by the compute platform (see
`.agents/skills/aether/compute.md` § _Standard environment variables_):

  - ORG_ID
  - GATEWAY_URL
  - QUERY_SERVER_URL
  - GOOGLE_CLOUD_PROJECT
  - DATABASE_URL       ← what this job actually needs
  - BIGQUERY_DATASET, BIGQUERY_LOCATION (not used here)

Local testing:
    cd jobs/aggregate_signals
    pip install -r requirements.txt
    DATABASE_URL=postgres://... SMOKE_RUN_ID=$(uuidgen) python main.py
"""

from __future__ import annotations

import logging
import os
import random
import sys
import uuid
from typing import Iterable

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("aggregate_signals")

CATEGORIES = ("vessel", "event", "entity", "signal", "observation")
EVENT_COUNT_TARGET = 1000

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tide_aggregates (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    event_count   INTEGER     NOT NULL,
    sum_value     DOUBLE PRECISION NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tide_aggregates_run_id
    ON tide_aggregates (run_id);
CREATE INDEX IF NOT EXISTS idx_tide_aggregates_created_at
    ON tide_aggregates (created_at DESC);
"""


def resolve_run_id() -> str:
    raw = os.environ.get("SMOKE_RUN_ID", "").strip()
    if raw:
        log.info("Using SMOKE_RUN_ID from env: %s", raw)
        return raw
    new_id = str(uuid.uuid4())
    log.info("SMOKE_RUN_ID not set; generated %s", new_id)
    return new_id


def synthesise_events(run_id: str) -> list[tuple[str, float]]:
    """Deterministic synthesis: same run_id always produces the same events."""
    rng = random.Random(run_id)
    events: list[tuple[str, float]] = []
    for _ in range(EVENT_COUNT_TARGET):
        category = rng.choice(CATEGORIES)
        value = rng.uniform(0.0, 100.0)
        events.append((category, value))
    return events


def aggregate(events: Iterable[tuple[str, float]]) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    for category, value in events:
        count, total = out.get(category, (0, 0.0))
        out[category] = (count + 1, total + value)
    return out


def main() -> int:
    run_id = resolve_run_id()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error(
            "DATABASE_URL is not set. The compute platform should inject it "
            "for tenants with Cloud SQL enabled — check that the warm-up has "
            "finished and that the secret is wired through `compute.md` § "
            "Standard environment variables."
        )
        return 1

    log.info("Synthesising %d events for run_id=%s", EVENT_COUNT_TARGET, run_id)
    events = synthesise_events(run_id)
    total_events = len(events)

    aggregates = aggregate(events)
    log.info("Aggregated to %d categories", len(aggregates))

    rows_to_insert = [
        (run_id, category, count, total)
        for category, (count, total) in sorted(aggregates.items())
    ]

    try:
        with psycopg.connect(db_url, autocommit=False, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                log.info("Ensuring tide_aggregates table exists")
                cur.execute(CREATE_TABLE_SQL)

                log.info("Inserting %d rows", len(rows_to_insert))
                cur.executemany(
                    "INSERT INTO tide_aggregates (run_id, category, event_count, sum_value) "
                    "VALUES (%s, %s, %s, %s)",
                    rows_to_insert,
                )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT category, event_count, sum_value "
                    "FROM tide_aggregates WHERE run_id = %s ORDER BY category",
                    (run_id,),
                )
                read_back = cur.fetchall()
    except psycopg.Error as exc:
        log.exception("Cloud SQL operation failed: %s", exc)
        return 1

    if len(read_back) != len(aggregates):
        log.error(
            "Read-back row count drift: inserted=%d read=%d run_id=%s",
            len(aggregates),
            len(read_back),
            run_id,
        )
        return 1

    read_back_total = sum(int(row[1]) for row in read_back)
    if read_back_total != total_events:
        log.error(
            "Read-back event_count drift: synthesised=%d read=%d run_id=%s",
            total_events,
            read_back_total,
            run_id,
        )
        return 1

    log.info("Read-back rows for run_id=%s:", run_id)
    for category, event_count, sum_value in read_back:
        log.info("  %-12s count=%-5d sum=%.2f", category, event_count, float(sum_value))

    log.info(
        "JOB_SUCCESS run_id=%s rows_inserted=%d total_events=%d",
        run_id,
        len(read_back),
        total_events,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
