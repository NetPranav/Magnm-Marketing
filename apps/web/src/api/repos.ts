import { api } from '../api';
import type {
  ProductFact,
  ProductRule,
  ProductRulesConfig,
  RepoRecord,
  ShippedItem,
} from '../types';

/** Load connected repositories with last sync and commit metadata. */
export function listRepos(): Promise<RepoRecord[]> {
  return api.get<RepoRecord[]>('/api/repos');
}

/** Connect a local folder or GitHub repository. */
export function addRepo(payload: {
  kind: 'local' | 'github';
  name: string;
  local_path?: string;
  github?: string;
  product_id?: string;
  branch?: string;
  status?: string;
  confidential?: boolean;
  marketing_angle?: string;
}): Promise<{ success: boolean; repo: RepoRecord }> {
  return api.post<{ success: boolean; repo: RepoRecord }>('/api/repos', payload);
}

/** Sync a connected repository immediately. */
export function syncRepo(repoId: string): Promise<{ success: boolean; repo: RepoRecord }> {
  return api.post<{ success: boolean; repo: RepoRecord }>(
    `/api/repos/${encodeURIComponent(repoId)}/sync`,
    {},
  );
}

/** Disconnect and remove a repository by ID. */
export function removeRepo(repoId: string): Promise<{ success: boolean; id: string }> {
  return api.delete<{ success: boolean; id: string }>(`/api/repos/${encodeURIComponent(repoId)}`);
}

/** Fetch shipped releases, PRs, tags, and commits across repos. */
export function listShipped(days = 7, repoId?: string): Promise<ShippedItem[]> {
  const qs = repoId
    ? `?days=${days}&repo_id=${encodeURIComponent(repoId)}`
    : `?days=${days}`;
  return api.get<ShippedItem[]>(`/api/shipped${qs}`);
}

/** List product facts filtered by approval status. */
export function listFacts(status = 'pending'): Promise<ProductFact[]> {
  return api.get<ProductFact[]>(`/api/facts?status=${encodeURIComponent(status)}`);
}

/** Approve or reject a pending product fact, optionally editing its text. */
export function decideFact(
  factId: string,
  status: 'approved' | 'rejected',
  text?: string,
): Promise<ProductFact & { success: boolean }> {
  return api.post<ProductFact & { success: boolean }>(
    `/api/facts/${encodeURIComponent(factId)}/decide`,
    { status, text },
  );
}

/** Add a manually verified product fact (auto-approved). */
export function createFact(payload: {
  product_id: string;
  text: string;
  source_url?: string | null;
}): Promise<ProductFact & { success: boolean }> {
  return api.post<ProductFact & { success: boolean }>('/api/facts', {
    ...payload,
    source_kind: 'manual',
  });
}

/** Load configurable product visibility and confidentiality rules. */
export function getProductRules(): Promise<ProductRulesConfig> {
  return api.get<ProductRulesConfig>('/api/product_rules');
}

/** Save updated visibility, blocked words, and confidentiality rules for a product. */
export function saveProductRule(payload: {
  product_id: string;
  status: string;
  show: string;
  blocked_terms: string[];
  confidential: boolean;
  notes?: string;
}): Promise<{ success: boolean; product: ProductRule; rules: ProductRulesConfig }> {
  return api.post<{ success: boolean; product: ProductRule; rules: ProductRulesConfig }>(
    '/api/product_rules',
    payload,
  );
}
