import { api } from '../api';

export type BoardColumnId = 'queued' | 'running' | 'waiting' | 'paused' | 'done' | 'failed';
export type BoardActionId = 'approve' | 'pause' | 'resume' | 'cancel' | 'retry';

export interface BoardItem {
  id: string;
  kind: 'agent_task' | 'workflow' | 'approval' | 'job' | 'reminder';
  title: string;
  column: BoardColumnId;
  status: string;
  agents: string[];
  repo: string;
  repo_name: string;
  scope: '' | 'personal' | 'team';
  updated_at: string;
  age_days: number;
  confidential: boolean;
  route: string;
  automatic: boolean;
  detail: {
    steps?: Array<{ name: string; status: string }>;
    error?: string;
    message?: string;
    options?: string[];
    paused_by?: string | null;
    content_ids?: string[];
  };
  actions: Array<{ id: BoardActionId; label: string }>;
}

export interface BoardData {
  columns: Array<{ id: BoardColumnId; label: string }>;
  items: BoardItem[];
  filters: { agents: string[]; repos: Array<{ id: string; name: string }>; kinds: string[] };
  paused: boolean;
  generated_at: string;
}

export interface BoardFilter {
  agent?: string;
  repo?: string;
  kind?: string;
}

/** The whole board, optionally filtered by agent, repo and kind. */
export function getBoard(filter: BoardFilter = {}): Promise<BoardData> {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(filter)) {
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  return api.get<BoardData>(`/api/board${query ? `?${query}` : ''}`);
}

/** Do one action an item offers (pause, resume, cancel, retry). "approve" only says where to go. */
export function actOnBoard(id: string, action: BoardActionId): Promise<{ route?: string }> {
  return api.post<{ route?: string }>('/api/board/act', { id, action });
}
