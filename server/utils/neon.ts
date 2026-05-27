/**
 * Postgres client for the tenant Aether app.
 *
 * Reads `DATABASE_URL` from the environment and returns a shared `pg.Pool`.
 * `DATABASE_URL` is auto-injected by Vercel on deploy for tenants that have
 * a Postgres backend (Neon for BC 1.0; Cloud SQL via the BC 2.0 secret
 * pipeline). The wire protocol is the same for both, so the same `pg`
 * driver works against either.
 *
 * Usage from a Nitro server route:
 *
 *     import { getDb } from '~/server/utils/neon';
 *     const db = getDb();
 *     if (!db) return { ok: false, reason: 'no_database_url' };
 *     const { rows } = await db.query('SELECT 1');
 *
 * Returns `null` when `DATABASE_URL` is not set — callers should render an
 * empty / not-configured state rather than 500ing. Handle missing tables
 * the same way (catch `42P01`) so the first page load on a fresh deploy,
 * before any compute job has run `CREATE TABLE IF NOT EXISTS`, does not
 * fault.
 */

import type { Pool as PgPool } from 'pg';

let cachedPool: PgPool | null | undefined;

export function getDb(): PgPool | null {
    if (cachedPool !== undefined) return cachedPool;

    const url = process.env.DATABASE_URL;
    if (!url) {
        cachedPool = null;
        return null;
    }

    const { Pool } = require('pg') as typeof import('pg');
    cachedPool = new Pool({
        connectionString: url,
        max: 4,
        idleTimeoutMillis: 30_000,
        connectionTimeoutMillis: 10_000,
        ssl:
            url.includes('sslmode=disable') || url.startsWith('postgres://localhost')
                ? undefined
                : { rejectUnauthorized: false },
    });

    cachedPool.on('error', (err) => {
        console.error('[neon] idle pg client error:', err);
    });

    return cachedPool;
}

/**
 * Returns true if the given error is a "relation does not exist" error
 * (Postgres `undefined_table` SQLSTATE 42P01).
 */
export function isMissingTableError(err: unknown): boolean {
    return Boolean(err && typeof err === 'object' && (err as { code?: string }).code === '42P01');
}
