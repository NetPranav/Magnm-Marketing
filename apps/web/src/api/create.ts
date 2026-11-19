import { api } from '../api';
import type {
  AssetRecord,
  CheckTextWarning,
  ContentItem,
  ProductRulesConfig,
  WorkflowRun,
} from '../types';

export interface CardRenderResult {
  success: boolean;
  post_id: string;
  rendered_files: string[];
  manifest?: Record<string, unknown>;
  error?: string;
}

export interface PrDraftResult {
  success: boolean;
  draft?: string;
  model_used?: string;
  provider?: string;
  fallback_used?: boolean;
  fallback_reason?: string | null;
  severity?: string;
  scenario?: string;
  error?: string;
  error_code?: string;
}

/** Start a multi-step workflow (post, blog, repurpose, weekly_plan, video_script). */
export function startWorkflow(
  kind: string,
  inputs: Record<string, unknown>,
): Promise<WorkflowRun> {
  return api.post<WorkflowRun>('/api/workflows/start', { kind, inputs });
}

/** Fetch recent workflow runs ordered newest-first. */
export function listWorkflows(limit = 30): Promise<WorkflowRun[]> {
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

/** Cancel a queued, running, or waiting workflow run. */
export function cancelWorkflow(runId: string): Promise<WorkflowRun> {
  return api.post<WorkflowRun>(`/api/workflows/${encodeURIComponent(runId)}/cancel`, {});
}

/** Render multi-ratio Palette v3 visual cards using templates/card.py. */
export function renderCard(payload: {
  post_id: string;
  field: string;
  kicker: string;
  big: string;
  serif: string;
  foot: string;
  asset_id?: string;
  sizes?: string[];
}): Promise<CardRenderResult> {
  return api.post<CardRenderResult>('/api/cards/render', payload);
}

/** Draft a grounded PR or crisis reply via @community. */
export function draftPrReply(payload: {
  scenario: string;
  severity: string;
  source_context: string;
}): Promise<PrDraftResult> {
  return api.post<PrDraftResult>('/api/pr/draft_reply', payload);
}

/** Check text against product rules and optional platform limits. */
export function checkText(
  text: string,
  platform?: string,
  contentType?: string,
): Promise<{ warnings: CheckTextWarning[] }> {
  return api.post<{ warnings: CheckTextWarning[] }>('/api/check_text', {
    text,
    platform: platform || undefined,
    type: contentType || undefined,
  });
}

/** Load configurable product rules for product selection. */
export function getProductRules(): Promise<ProductRulesConfig> {
  return api.get<ProductRulesConfig>('/api/product_rules');
}

/** List existing content items for repurpose source selection. */
export function listContent(limit = 50): Promise<ContentItem[]> {
  return api.get<ContentItem[]>(`/api/content?limit=${limit}`);
}

/** List uploaded assets for Card Studio or workflow attachment. */
export function listAssets(): Promise<AssetRecord[]> {
  return api.get<AssetRecord[]>('/api/assets');
}
