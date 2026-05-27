<template>
    <div class="tide-page">
        <div class="tide-content">
            <header class="tide-header">
                <h1 class="tide-title">Tide Telemetry</h1>
                <p class="tide-subtitle">
                    Cloud SQL round-trip evidence from the
                    <code>aggregate_signals</code> compute job.
                </p>
            </header>

            <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
                Failed to load aggregates: {{ error }}
            </v-alert>

            <div v-else-if="pending && !data" class="tide-loading">
                <v-progress-circular indeterminate color="primary" size="32" />
                <span>Loading aggregates&hellip;</span>
            </div>

            <template v-else-if="data">
                <v-alert
                    v-if="!data.configured"
                    type="warning"
                    variant="tonal"
                    density="comfortable"
                    class="mb-6"
                >
                    <strong>DATABASE_URL is not set.</strong> Cloud SQL hasn't been wired into this
                    Vercel deployment yet. Wait for the tenant's Cloud SQL warm-up to finish (5-15
                    min), then redeploy so the env var lands.
                </v-alert>

                <v-alert
                    v-else-if="!data.tableExists || data.runs.length === 0"
                    type="info"
                    variant="tonal"
                    density="comfortable"
                    class="mb-6"
                >
                    <strong>No runs yet.</strong> Trigger the <code>aggregate_signals</code> job
                    from the Portal's Jobs tab, then refresh.
                </v-alert>

                <template v-else>
                    <v-card class="mb-6 latest-card">
                        <v-card-title class="latest-title">Latest run</v-card-title>
                        <v-card-text>
                            <div class="latest-grid">
                                <div class="latest-field">
                                    <div class="field-label">Run ID</div>
                                    <code class="field-value run-id">{{ latestRun.runId }}</code>
                                </div>
                                <div class="latest-field">
                                    <div class="field-label">Created at</div>
                                    <div class="field-value">
                                        {{ formatTs(latestRun.createdAt) }}
                                    </div>
                                </div>
                                <div class="latest-field">
                                    <div class="field-label">Total events</div>
                                    <div class="field-value">
                                        {{ latestRun.totalEventCount.toLocaleString() }}
                                    </div>
                                </div>
                                <div class="latest-field">
                                    <div class="field-label">Total sum_value</div>
                                    <div class="field-value">
                                        {{ latestRun.totalSumValue.toFixed(2) }}
                                    </div>
                                </div>
                            </div>
                        </v-card-text>

                        <v-table density="comfortable" class="latest-table">
                            <thead>
                                <tr>
                                    <th>Category</th>
                                    <th class="text-right">Event count</th>
                                    <th class="text-right">Sum value</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="row in latestRun.rows" :key="row.category">
                                    <td>{{ row.category }}</td>
                                    <td class="text-right">
                                        {{ row.eventCount.toLocaleString() }}
                                    </td>
                                    <td class="text-right">{{ row.sumValue.toFixed(2) }}</td>
                                </tr>
                            </tbody>
                        </v-table>
                    </v-card>

                    <section v-if="historyRuns.length > 0" class="history">
                        <h2 class="section-title">Recent runs</h2>
                        <div class="history-list">
                            <div v-for="run in historyRuns" :key="run.runId" class="history-item">
                                <div class="history-meta">
                                    <code class="run-id">{{ run.runId }}</code>
                                    <span class="history-ts">{{ formatTs(run.createdAt) }}</span>
                                </div>
                                <div class="history-stats">
                                    <span>{{ run.totalEventCount.toLocaleString() }} events</span>
                                    <span>{{ run.rows.length }} categories</span>
                                    <span>sum {{ run.totalSumValue.toFixed(1) }}</span>
                                </div>
                            </div>
                        </div>
                    </section>
                </template>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
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
    interface AggregatesResponse {
        configured: boolean;
        tableExists: boolean;
        runs: RunGroup[];
    }

    const { data, pending, error } = await useFetch<AggregatesResponse>('/api/aggregates', {
        default: () => ({ configured: false, tableExists: false, runs: [] }),
    });

    const latestRun = computed<RunGroup | null>(() => data.value?.runs[0] ?? null);
    const historyRuns = computed<RunGroup[]>(() => data.value?.runs.slice(1) ?? []);

    function formatTs(iso: string): string {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString();
    }
</script>

<style scoped>
    .tide-page {
        height: 100%;
        overflow-y: auto;
        display: flex;
        justify-content: center;
        padding: 48px 24px;
    }

    .tide-content {
        max-width: 960px;
        width: 100%;
    }

    .tide-header {
        margin-bottom: 32px;
    }

    .tide-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 2rem;
        letter-spacing: 0.02em;
        margin-bottom: 8px;
    }

    .tide-subtitle {
        color: var(--lv-silver);
        font-size: 1rem;
    }

    .tide-subtitle code,
    .step-desc code {
        font-size: 0.9em;
        padding: 1px 5px;
    }

    .tide-loading {
        display: flex;
        gap: 12px;
        align-items: center;
        color: var(--lv-silver);
    }

    .latest-card {
        padding-top: 4px;
    }

    .latest-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.1rem;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: var(--lv-silver);
    }

    .latest-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-bottom: 12px;
    }

    .field-label {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--lv-silver);
        margin-bottom: 4px;
    }

    .field-value {
        font-size: 1rem;
    }

    .run-id {
        font-family: var(--font-mono);
        font-size: 0.85rem;
        word-break: break-all;
    }

    .latest-table {
        background: transparent;
    }

    .section-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1rem;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: var(--lv-silver);
        margin-bottom: 16px;
    }

    .history-list {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .history-item {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        flex-wrap: wrap;
        padding: 10px 14px;
        background: var(--lv-surface);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px;
    }

    .history-meta {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
    }

    .history-ts {
        font-size: 0.85rem;
        color: var(--lv-silver);
    }

    .history-stats {
        display: flex;
        gap: 16px;
        font-size: 0.85rem;
        color: var(--lv-silver);
        font-family: var(--font-mono);
    }
</style>
