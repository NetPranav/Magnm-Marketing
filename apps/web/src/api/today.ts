import { api } from '../api';
import type {
  AgentSession,
  ContentItem,
  JobRecord,
  ProductFact,
  WorkflowRun,
} from '../types';

/** Fetch recent background jobs. */
export function listJobs(limit = 20): Promise<JobRecord[]> {
  return api.get<JobRecord[]>(`/api/jobs?limit=${limit}`);
}

/** Cancel a queued or running background job. */
export function cancelJob(id: string): Promise<{ success: boolean; job: JobRecord }> {
  return api.post<{ success: boolean; job: JobRecord }>('/api/jobs/cancel', { id });
}

/** Fetch recent agent sessions. */
export function listSessions(limit = 20, status?: string): Promise<AgentSession[]> {
  const query = status ? `?limit=${limit}&status=${encodeURIComponent(status)}` : `?limit=${limit}`;
  return api.get<AgentSession[]>(`/api/sessions${query}`);
}

/** Resume a waiting_user agent session with the user's answer. */
export function answerSession(
  sessionId: string,
  answer: string,
): Promise<{ success: boolean; session_id: string; status: string }> {
  return api.post<{ success: boolean; session_id: string; status: string }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/answer`,
    { answer },
  );
}

/** Cancel a running or waiting_user agent session. */
export function cancelSession(
  sessionId: string,
): Promise<{ success: boolean; session: AgentSession }> {
  return api.post<{ success: boolean; session: AgentSession }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );
}

/** Fetch recent workflow runs (including open gates). */
export function listWorkflows(limit = 20): Promise<WorkflowRun[]> {
  return api.get<WorkflowRun[]>(`/api/workflows?limit=${limit}`);
}

/** Answer a pending workflow approval gate. */
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

/** Cancel a workflow run. */
export function cancelWorkflow(runId: string): Promise<WorkflowRun> {
  return api.post<WorkflowRun>(`/api/workflows/${encodeURIComponent(runId)}/cancel`, {});
}

/** Fetch pending product facts awaiting approval. */
export function listPendingFacts(): Promise<ProductFact[]> {
  return api.get<ProductFact[]>('/api/facts?status=pending');
}

/** Fetch recent content items. */
export function listContent(limit = 50): Promise<ContentItem[]> {
  return api.get<ContentItem[]>(`/api/content?limit=${limit}`);
}
