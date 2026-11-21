import { api } from '../api';
import type { AccountRole } from './account';

export type TeamKnowledgeKey =
  | 'brand_kit'
  | 'product_rules'
  | 'agent_instructions'
  | 'templates'
  | 'pitch_base_prices';

export interface TeamKnowledgeItem {
  key: TeamKnowledgeKey;
  label: string;
  managed_by_team: boolean;
  role: AccountRole | null;
  local_changed_since_publish: boolean;
  remote_updated_at: string | null;
  remote_updated_by: string | null;
  published_at?: string | null;
  applied_at: string | null;
  remote_newer: boolean;
  last_error: string | null;
}

export interface TeamKnowledgeStatus {
  signed_in: boolean;
  role: AccountRole | null;
  items: TeamKnowledgeItem[];
}

export interface TeamKnowledgeActionResponse {
  ok: boolean;
  item?: TeamKnowledgeItem;
  status?: TeamKnowledgeStatus;
}

/** Fetch shared team knowledge sync status for all keys. */
export function getTeamKnowledge(): Promise<TeamKnowledgeStatus> {
  return api.get<TeamKnowledgeStatus>('/api/team/knowledge');
}

/** Publish local files or base prices for key to the team (admins only). */
export function publishTeamKnowledge(key: TeamKnowledgeKey): Promise<TeamKnowledgeActionResponse> {
  return api.post<TeamKnowledgeActionResponse>(
    `/api/team/knowledge/${encodeURIComponent(key)}/publish`,
    {},
  );
}

/** Apply the team version of key locally after backing up the previous local version. */
export function applyTeamKnowledge(key: TeamKnowledgeKey): Promise<TeamKnowledgeActionResponse> {
  return api.post<TeamKnowledgeActionResponse>(
    `/api/team/knowledge/${encodeURIComponent(key)}/apply`,
    {},
  );
}
