import { api } from '../api';
import type { LearningRecord } from '../types';

/** Fetch learnings optionally filtered by status. */
export async function listLearnings(
  status?: 'pending' | 'approved' | 'rejected' | 'retired' | 'all',
): Promise<LearningRecord[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  const res = await api.get<{ learnings?: LearningRecord[] }>(`/api/learnings${qs}`);
  return Array.isArray(res?.learnings) ? res.learnings : [];
}

/** Approve a pending learning (with optional edited text). */
export async function approveLearning(id: string, text?: string): Promise<LearningRecord> {
  const body = text !== undefined ? { text } : {};
  const res = await api.post<{ learning: LearningRecord }>(
    `/api/learnings/${encodeURIComponent(id)}/approve`,
    body,
  );
  return res.learning;
}

/** Reject a pending learning. */
export async function rejectLearning(id: string): Promise<LearningRecord> {
  const res = await api.post<{ learning: LearningRecord }>(
    `/api/learnings/${encodeURIComponent(id)}/reject`,
    {},
  );
  return res.learning;
}

/** Retire an approved learning so it is no longer injected into agent prompts. */
export async function retireLearning(id: string): Promise<LearningRecord> {
  const res = await api.post<{ learning: LearningRecord }>(
    `/api/learnings/${encodeURIComponent(id)}/retire`,
    {},
  );
  return res.learning;
}

/** Add a manual learning (approved immediately). */
export async function addLearning(payload: {
  text: string;
  applies_to: string[];
}): Promise<LearningRecord> {
  const res = await api.post<{ learning: LearningRecord }>('/api/learnings', payload);
  return res.learning;
}
