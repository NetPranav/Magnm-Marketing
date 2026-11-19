import { api } from '../api';
import type { ContentItem, EngineStatusInfo, WorkflowRun } from '../types';

/** Fetch recent workflow runs (weekly_plan and active briefs). */
export function listWorkflows(limit = 30): Promise<WorkflowRun[]> {
  return api.get<WorkflowRun[]>(`/api/workflows?limit=${limit}`);
}

/** Start the weekly_plan workflow ("Plan my week"). */
export function startWeeklyPlan(inputs: {
  platforms: string[];
  posts_per_week: number;
  week_start?: string;
}): Promise<WorkflowRun> {
  return api.post<WorkflowRun>('/api/workflows/start', {
    kind: 'weekly_plan',
    inputs,
  });
}

/** Answer a pending workflow gate (Keep / Change / Drop). */
export function answerGate(
  gateId: string,
  answer: string,
  feedback?: string | null,
): Promise<{ gate: unknown; run: WorkflowRun }> {
  return api.post<{ gate: unknown; run: WorkflowRun }>(
    `/api/gates/${encodeURIComponent(gateId)}/answer`,
    { answer, feedback: feedback || null },
  );
}

/** Cancel a running or waiting workflow. */
export function cancelWorkflow(runId: string): Promise<WorkflowRun> {
  return api.post<WorkflowRun>(`/api/workflows/${encodeURIComponent(runId)}/cancel`, {});
}

/** List stored content items (used for editorial calendar and stored weekly plans). */
export function listContent(limit = 100, contentType?: string): Promise<ContentItem[]> {
  const qs = contentType
    ? `?limit=${limit}&type=${encodeURIComponent(contentType)}`
    : `?limit=${limit}`;
  return api.get<ContentItem[]>(`/api/content${qs}`);
}

/** Fetch full content item details including body. */
export function getContentItem(contentId: string): Promise<ContentItem> {
  return api.get<ContentItem>(`/api/content/${encodeURIComponent(contentId)}`);
}

/** Fetch engine status (schedules + editorial_plan from plan/plan.md). */
export function getStatus(): Promise<EngineStatusInfo> {
  return api.get<EngineStatusInfo>('/api/status');
}

/** Toggle a schedule enabled/paused state. */
export function toggleSchedule(id: string): Promise<{ success: boolean }> {
  return api.post<{ success: boolean }>('/api/schedule/toggle', { id });
}

/** Update a schedule's interval_hours and enabled state. */
export function updateSchedule(payload: {
  id: string;
  interval_hours?: number | null;
  enabled?: boolean;
}): Promise<{ success: boolean }> {
  return api.post<{ success: boolean }>('/api/schedule/update', payload);
}
