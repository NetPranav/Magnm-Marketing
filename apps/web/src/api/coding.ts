import { api } from '../api';
import type {
  AssetRecord,
  CodingAgentInfo,
  CodingRunLogResponse,
  CodingRunRecord,
  ContentItem,
} from '../types';

export interface CodingAgentsResponse {
  agents: CodingAgentInfo[];
  adapters?: CodingAgentInfo[];
  agent_engines: Record<string, string>;
  timeout_s?: number;
}

/** Detect installed coding CLIs, confinement capability, and detected ACP adapters. */
export function getCodingAgents(refresh = false): Promise<CodingAgentsResponse> {
  const qs = refresh ? '?refresh=1' : '';
  return api.get<CodingAgentsResponse>(`/api/coding/agents${qs}`);
}

/** Fetch per-agent engine overrides and detected coding CLIs. */
export function getAgentEngines(): Promise<{
  agent_engines: Record<string, string>;
  agents: CodingAgentInfo[];
  adapters?: CodingAgentInfo[];
}> {
  return api.get<{
    agent_engines: Record<string, string>;
    agents: CodingAgentInfo[];
    adapters?: CodingAgentInfo[];
  }>('/api/coding/agent_engines');
}

/** Save a per-agent engine selection ('ai' or an enabled confined coding CLI id). */
export function setAgentEngine(
  agentId: string,
  engine: string,
): Promise<{
  success: boolean;
  agent_id: string;
  engine: string;
  agent_engines: Record<string, string>;
}> {
  return api.post('/api/coding/agent_engines', { agent_id: agentId, engine });
}

/** List recent coding runs ordered newest-first. */
export function listCodingRuns(limit = 50): Promise<{ runs: CodingRunRecord[] }> {
  return api.get<{ runs: CodingRunRecord[] }>(`/api/coding/runs?limit=${limit}`);
}

/** Fetch a single coding run with summary, out_files, and log_tail. */
export function getCodingRun(runId: string): Promise<CodingRunRecord> {
  return api.get<CodingRunRecord>(`/api/coding/runs/${encodeURIComponent(runId)}`);
}

/** Start a user-submitted coding run from the form. */
export function startCodingRun(payload: {
  engine: string;
  prompt: string;
  repo_ids: string[];
  mode: 'read_only' | 'write_out';
  confirm_confidential?: boolean;
}): Promise<{ success: boolean; run: CodingRunRecord }> {
  return api.post<{ success: boolean; run: CodingRunRecord }>('/api/coding/runs', payload);
}

/** Approve and start an awaiting_approval coding run requested by an agent. */
export function approveCodingRun(
  runId: string,
  payload: {
    approval_hash?: string | null;
    confirm_confidential?: boolean;
  } = {},
): Promise<{ success: boolean; run: CodingRunRecord }> {
  return api.post<{ success: boolean; run: CodingRunRecord }>(
    `/api/coding/runs/${encodeURIComponent(runId)}/approve`,
    payload,
  );
}

/** Cancel an awaiting_approval, queued, or running coding run. */
export function cancelCodingRun(
  runId: string,
): Promise<{ success: boolean; run: CodingRunRecord }> {
  return api.post<{ success: boolean; run: CodingRunRecord }>(
    `/api/coding/runs/${encodeURIComponent(runId)}/cancel`,
    {},
  );
}

/** Fetch incremental log output starting from byte offset. */
export function getCodingRunLog(runId: string, fromOffset = 0): Promise<CodingRunLogResponse> {
  return api.get<CodingRunLogResponse>(
    `/api/coding/runs/${encodeURIComponent(runId)}/log?from=${Math.max(0, fromOffset)}`,
  );
}

/** Build safe download URL for an output file inside out/. */
export function getCodingOutFileUrl(runId: string, filename: string): string {
  const encodedPath = filename
    .split('/')
    .filter((p) => p.length > 0)
    .map((p) => encodeURIComponent(p))
    .join('/');
  return `/api/coding/runs/${encodeURIComponent(runId)}/out/${encodedPath}`;
}

/** Save a safe-extension output file from workspace/out/ to the Assets Library. */
export function saveCodingOutFile(
  runId: string,
  filename: string,
  category = 'generated',
  description = '',
): Promise<{ success: boolean; asset: AssetRecord }> {
  return api.post<{ success: boolean; asset: AssetRecord }>(
    `/api/coding/runs/${encodeURIComponent(runId)}/out/save`,
    { filename, category, description },
  );
}

/** Attach a safe-extension output file from workspace/out/ to an existing content item. */
export function attachCodingOutFile(
  runId: string,
  filename: string,
  contentId: string,
): Promise<{ success: boolean; content: ContentItem }> {
  return api.post<{ success: boolean; content: ContentItem }>(
    `/api/coding/runs/${encodeURIComponent(runId)}/out/attach`,
    { filename, content_id: contentId },
  );
}
