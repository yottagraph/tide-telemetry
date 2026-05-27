/**
 * GET /api/aggregates
 *
 * Returns the most recent `tide_aggregates` rows the `aggregate_signals`
 * compute job has written, grouped by `run_id` and ordered newest first.
 *
 * Response shape:
 *
 *     {
 *       configured: boolean;   // true when DATABASE_URL is set
 *       tableExists: boolean;  // false until the job has run once
 *       runs: Array<{
 *         runId: string;
 *         createdAt: string;            // ISO timestamp
 *         totalEventCount: number;
 *         totalSumValue: number;
 *         rows: Array<{
 *           category: string;
 *           eventCount: number;
 *           sumValue: number;
 *         }>;
 *       }>;
 *     }
 *
 * On a fresh deploy — before the job has ever run — the `tide_aggregates`
 * table does not exist. We catch SQLSTATE 42P01 (`undefined_table`) and
 * return an empty `runs` array with `tableExists: false`, per the
 * `storage.md` guidance "Handle missing tables in GET routes".
 */

import { getDb, isMissingTableError } from '../utils/neon';

interface CategoryRow {
    category: string;
    eventCount: number;
    sumValue: number;
}

interface RunGroup {
    runId: string;
    createdAt: string;
    totalEventCount: number;
    totalSumValue: number;
    rows: CategoryRow[];
}

const RUNS_LIMIT = 10;

export default defineEventHandler(async () => {
    const db = getDb();
    if (!db) {
        return {
            configured: false,
            tableExists: false,
            runs: [] as RunGroup[],
        };
    }

    try {
        const { rows } = await db.query<{
            run_id: string;
            created_at: Date;
            category: string;
            event_count: string;
            sum_value: string;
        }>(
            `WITH recent AS (
               SELECT run_id, MAX(created_at) AS created_at
               FROM tide_aggregates
               GROUP BY run_id
               ORDER BY created_at DESC
               LIMIT $1
             )
             SELECT a.run_id,
                    r.created_at,
                    a.category,
                    a.event_count,
                    a.sum_value
             FROM tide_aggregates a
             JOIN recent r ON r.run_id = a.run_id
             ORDER BY r.created_at DESC, a.category ASC`,
            [RUNS_LIMIT]
        );

        const grouped = new Map<string, RunGroup>();
        for (const r of rows) {
            const eventCount = Number(r.event_count) || 0;
            const sumValue = Number(r.sum_value) || 0;
            let group = grouped.get(r.run_id);
            if (!group) {
                group = {
                    runId: r.run_id,
                    createdAt: r.created_at.toISOString(),
                    totalEventCount: 0,
                    totalSumValue: 0,
                    rows: [],
                };
                grouped.set(r.run_id, group);
            }
            group.rows.push({
                category: r.category,
                eventCount,
                sumValue,
            });
            group.totalEventCount += eventCount;
            group.totalSumValue += sumValue;
        }

        return {
            configured: true,
            tableExists: true,
            runs: Array.from(grouped.values()),
        };
    } catch (err) {
        if (isMissingTableError(err)) {
            return {
                configured: true,
                tableExists: false,
                runs: [] as RunGroup[],
            };
        }
        console.error('[aggregates] query failed:', err);
        throw createError({
            statusCode: 500,
            statusMessage: 'aggregates query failed',
        });
    }
});
