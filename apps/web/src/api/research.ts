import { api } from '../api';
import type { AgentSession, EngineStatusInfo, JobRecord } from '../types';

export interface ResearchItem {
  id: string;
  url: string;
  title: string;
  snippet: string;
  source: string;
  published_at?: string | null;
  topic_id?: string | null;
  found_at: string;
  score?: number | null;
}

export interface WatchTopic {
  id: string;
  query: string;
  sources: string[] | null;
  cadence_hours: number;
  days: number;
  enabled: boolean;
  last_run?: string | null;
  created_at: string;
}

export interface BrowserFrameEvent {
  session_id?: string | null;
  url?: string;
  screenshot_path: string;
}

export interface ResearcherAnswer {
  success: boolean;
  waiting_user?: boolean;
  session_id?: string;
  question?: string;
  options?: string[];
  response?: string;
  error?: string;
}

/** Ask the Researcher agent a question via /api/agent_chat. */
export function askResearcher(question: string): Promise<ResearcherAnswer> {
  return api.post<ResearcherAnswer>('/api/agent_chat', {
    agent_id: 'researcher',
    message: question,
  });
}

/** Start the market_scan background job. */
export function startMarketScan(): Promise<{ success: boolean; job: JobRecord; message?: string }> {
  return api.post<{ success: boolean; job: JobRecord; message?: string }>('/api/run', {
    job: 'market_scan',
  });
}

/** Start the watch_topics background job. */
export function startWatchTopicsScan(): Promise<{ success: boolean; job: JobRecord; message?: string }> {
  return api.post<{ success: boolean; job: JobRecord; message?: string }>('/api/run', {
    job: 'watch_topics',
  });
}

/** Fetch engine status (includes research_findings from market_scan). */
export function getEngineStatus(): Promise<EngineStatusInfo> {
  return api.get<EngineStatusInfo>('/api/status');
}

/** Fetch stored research items, optionally filtered by topic_id. */
export function listResearchItems(limit = 100, topicId?: string): Promise<ResearchItem[]> {
  const qs = topicId
    ? `?limit=${limit}&topic_id=${encodeURIComponent(topicId)}`
    : `?limit=${limit}`;
  return api.get<ResearchItem[]>(`/api/research_items${qs}`);
}

/** Fetch configured watch topics. */
export function listWatchTopics(): Promise<WatchTopic[]> {
  return api.get<WatchTopic[]>('/api/watch_topics');
}

/** Create a new watch topic. */
export function createWatchTopic(payload: {
  query: string;
  sources?: string[] | null;
  cadence_hours?: number;
  days?: number;
}): Promise<WatchTopic & { success: boolean }> {
  return api.post<WatchTopic & { success: boolean }>('/api/watch_topics', payload);
}

/** Toggle enabled state on a watch topic. */
export function toggleWatchTopic(topicId: string): Promise<WatchTopic & { success: boolean }> {
  return api.post<WatchTopic & { success: boolean }>(
    `/api/watch_topics/${encodeURIComponent(topicId)}/toggle`,
    {},
  );
}

/** Remove a watch topic by ID. */
export function deleteWatchTopic(topicId: string): Promise<{ success: boolean; id: string }> {
  return api.delete<{ success: boolean; id: string }>(
    `/api/watch_topics/${encodeURIComponent(topicId)}`,
  );
}

/** Fetch recent agent sessions (used to track live researcher sessions). */
export function listSessions(limit = 10): Promise<AgentSession[]> {
  return api.get<AgentSession[]>(`/api/sessions?limit=${limit}`);
}

/** Cancel an active researcher or browser session. */
export function cancelSession(sessionId: string): Promise<{ success: boolean; session: AgentSession }> {
  return api.post<{ success: boolean; session: AgentSession }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );
}

/** Resume a waiting researcher session with an answer. */
export function answerSession(
  sessionId: string,
  answer: string,
): Promise<{ success: boolean; session_id: string; status: string }> {
  return api.post<{ success: boolean; session_id: string; status: string }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/answer`,
    { answer },
  );
}
