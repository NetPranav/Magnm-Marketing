import { api } from '../api';
import type { LearningRecord, WeeklySummaryRecord } from '../types';

/** Fetch stored weekly summaries ordered newest week first. */
export async function listSummaries(limit = 52): Promise<WeeklySummaryRecord[]> {
  const res = await api.get<{ summaries?: WeeklySummaryRecord[] }>(
    `/api/summaries?limit=${encodeURIComponent(String(limit))}`,
  );
  return Array.isArray(res?.summaries) ? res.summaries : [];
}

/** Fetch a single weekly summary by ID. */
export async function getSummary(id: string): Promise<WeeklySummaryRecord> {
  const res = await api.get<{ summary: WeeklySummaryRecord }>(
    `/api/summaries/${encodeURIComponent(id)}`,
  );
  return res.summary;
}

/** Generate (or regenerate) a weekly summary (defaults to last IST week). */
export function generateSummary(
  weekStart?: string | null,
): Promise<{ summary: WeeklySummaryRecord; learnings: LearningRecord[] }> {
  return api.post<{ summary: WeeklySummaryRecord; learnings: LearningRecord[] }>(
    '/api/summaries/generate',
    weekStart ? { week_start: weekStart } : {},
  );
}
