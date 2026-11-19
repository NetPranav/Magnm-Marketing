import { api } from '../api';
import type { AgentSession, PendingQuestion } from '../types';

export interface RosterModelInfo {
  role_id?: string;
  model?: string;
  has_key?: boolean;
  key_source?: string;
}

export interface RosterAgent {
  id: string;
  name: string;
  code: string;
  role: string;
  description?: string;
  status: string;
  message_count?: number;
  model_info?: RosterModelInfo;
  tools?: string[];
}

export interface TeamMessage {
  id: string;
  sender: string;
  sender_name?: string;
  sender_code?: string;
  text: string;
  mentions?: string[];
  attached_assets?: string[];
  session_id?: string;
  content_ids?: string[];
  waiting_user?: boolean;
  model_used?: string | null;
  provider?: string | null;
  fallback_used?: boolean;
  timestamp: string;
}

export interface DirectChatTurn {
  sender: string;
  name?: string;
  text: string;
  attached_assets?: string[];
  waiting_user?: boolean;
  session_id?: string;
  content_ids?: string[];
  model_used?: string | null;
  provider?: string | null;
  fallback_used?: boolean;
  capability_redirect?: boolean;
  redirect_to?: string;
  timestamp: string;
}

export interface AssetRecord {
  id: string;
  name: string;
  mime_type?: string;
  file_group?: string;
  size?: string;
  category?: string;
  description?: string;
}

export interface AgentMemoryItem {
  id: string;
  agent_id: string;
  text: string;
  source: string;
  content_id?: string | null;
  pinned: boolean;
  created_at: string;
  last_used_at?: string | null;
}

export interface AgentChatResponse {
  success: boolean;
  waiting_user?: boolean;
  session_id?: string;
  question?: string;
  options?: string[];
  response?: string;
  error?: string;
  error_code?: string;
  history?: DirectChatTurn[];
}

/** Load the 7-agent roster with live status and model info. */
export function getRoster(): Promise<RosterAgent[]> {
  return api.get<RosterAgent[]>('/api/roster');
}

/** Load shared team room messages. */
export function getMessages(): Promise<TeamMessage[]> {
  return api.get<TeamMessage[]>('/api/messages');
}

/** Post a message to the shared team room. */
export function postMessage(
  text: string,
  attachedAssets: string[] = [],
): Promise<{ success: boolean; replies: TeamMessage[] }> {
  return api.post<{ success: boolean; replies: TeamMessage[] }>('/api/messages', {
    text,
    sender: 'CEO',
    attached_assets: attachedAssets,
  });
}

/** Load 1-on-1 chat history for an agent. */
export function getChatHistory(agentId: string): Promise<DirectChatTurn[]> {
  return api.get<DirectChatTurn[]>(`/api/chat_history/${encodeURIComponent(agentId)}`);
}

/** Send a 1-on-1 message to an agent. */
export function postAgentChat(
  agentId: string,
  message: string,
  attachedAssets: string[] = [],
): Promise<AgentChatResponse> {
  return api.post<AgentChatResponse>('/api/agent_chat', {
    agent_id: agentId,
    message,
    attached_assets: attachedAssets,
  });
}

/** Load registered assets available to attach in chat. */
export function listAssets(): Promise<AssetRecord[]> {
  return api.get<AssetRecord[]>('/api/assets');
}

/** List waiting or running agent sessions. */
export function listSessions(limit = 20, status?: string): Promise<AgentSession[]> {
  const qs = status ? `?limit=${limit}&status=${encodeURIComponent(status)}` : `?limit=${limit}`;
  return api.get<AgentSession[]>(`/api/sessions${qs}`);
}

/** Resume a waiting_user agent session with an answer. */
export function answerSession(
  sessionId: string,
  answer: string,
): Promise<{ success: boolean; session_id: string; status: string }> {
  return api.post<{ success: boolean; session_id: string; status: string }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/answer`,
    { answer },
  );
}

/** Cancel an active or waiting agent session. */
export function cancelSession(sessionId: string): Promise<{ success: boolean; session: AgentSession }> {
  return api.post<{ success: boolean; session: AgentSession }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );
}

/** Load memory items for a given agent. */
export function listMemory(agentId: string): Promise<AgentMemoryItem[]> {
  return api.get<AgentMemoryItem[]>(`/api/memory?agent_id=${encodeURIComponent(agentId)}`);
}

/** Add a manual memory item for an agent. */
export function createMemory(
  agentId: string,
  text: string,
  pinned = false,
): Promise<AgentMemoryItem & { success: boolean }> {
  return api.post<AgentMemoryItem & { success: boolean }>('/api/memory', {
    agent_id: agentId,
    text,
    pinned,
  });
}

/** Update text or pinned state on a memory item. */
export function updateMemory(
  memoryId: string,
  updates: { text?: string; pinned?: boolean },
): Promise<AgentMemoryItem & { success: boolean }> {
  return api.post<AgentMemoryItem & { success: boolean }>(
    `/api/memory/${encodeURIComponent(memoryId)}`,
    updates,
  );
}

/** Delete an agent memory item. */
export function deleteMemory(memoryId: string): Promise<{ success: boolean; id: string }> {
  return api.delete<{ success: boolean; id: string }>(`/api/memory/${encodeURIComponent(memoryId)}`);
}

/** Record user feedback on agent output tied to a content_id. */
export function postFeedback(
  contentId: string,
  text: string,
  agentId?: string,
): Promise<{ success: boolean; memory: AgentMemoryItem }> {
  return api.post<{ success: boolean; memory: AgentMemoryItem }>('/api/feedback', {
    content_id: contentId,
    text,
    agent_id: agentId,
  });
}

export type { PendingQuestion };
