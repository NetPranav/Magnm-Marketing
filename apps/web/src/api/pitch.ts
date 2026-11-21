import { api } from '../api';

export type PitchProposalKind = 'proposal' | 'quote_form' | 'final' | 'default';
export type PitchProposalStatus = 'draft' | 'ready' | 'sent' | 'archived';
export type PitchSectionKey = 'p2' | 'p3' | 'p4' | 'p5';
export type PitchAboutSectionKey =
  | 'lead'
  | 'what_we_do'
  | 'portfolio'
  | 'this_plan'
  | 'architecture'
  | 'timeline'
  | 'payment';
export type PitchCategoryPlacement = PitchSectionKey | 'new_sheet';
export type PitchPriceSource = 'base' | 'admin' | 'proposal' | 'custom';
export type PitchServicePlanPeriod = 'one_time' | 'monthly' | 'quarterly' | 'yearly';

export interface PitchCustomCategory {
  id: string;
  name: string;
  placement: PitchCategoryPlacement;
  sheet_title?: string;
  sheet_lead?: string;
  item_ids?: string[];
}

export interface PitchIncludedFromHidden {
  item_id: string;
  name: string;
  sheet_shown_on: string;
  needed_for: string[];
  also_needed_on?: string[];
}

export interface PitchDroppedItemChange {
  item_id: string;
  name: string;
  sheet: string;
  dropped_from: string;
  reason: string;
  message: string;
}

export interface PitchPaymentPlanRow {
  percent: number;
  label: string;
  amount?: number;
}

export interface PitchTimelineStep {
  title: string;
  when: string;
  detail: string;
}

export interface PitchServicePlan {
  id: string;
  name: string;
  description: string;
  price: number;
  period: PitchServicePlanPeriod;
}

export interface PitchRecurringPlan {
  id: string;
  name: string;
  price: number;
  period: 'monthly' | 'quarterly' | 'yearly';
}

export interface PitchRecurringTotals {
  monthly: number;
  quarterly: number;
  yearly: number;
}

export interface PitchDefaultPlans {
  payment_plan: PitchPaymentPlanRow[];
  payment_note: string;
  timeline: PitchTimelineStep[];
  service_plans?: PitchServicePlan[];
}

export interface PitchVersionInfo {
  repo?: string;
  ref?: string;
  commit?: string;
  synced_at?: string;
}

export interface PitchCatalogItem {
  g: string;
  n: string;
  d: string;
  r: string[];
  p: number;
  base_price: number;
  price: number;
  overridden: boolean;
}

export interface PitchRoleBlock {
  label: string;
  points: string[];
}

export interface PitchArchitectureBlock {
  heading: string;
  sub: string;
  roles: PitchRoleBlock[];
}

export type PitchSections = Record<PitchSectionKey, string[]>;

export interface PitchPreset {
  slug: string;
  name: string;
  badge: string;
  clientName: string;
  businessName: string;
  title: string;
  lead: string;
  whatWeDo: string;
  portfolio: string;
  thisPlan: string;
  architectureBlock: PitchArchitectureBlock;
  initialSelected: string[];
  sections: PitchSections;
}

export interface PitchCatalog {
  items: Record<string, PitchCatalogItem>;
  groups: string[];
  standalone_fee: number;
  standalone_label: string;
  presets: Record<string, PitchPreset>;
  default_plans?: PitchDefaultPlans;
}

export interface PitchCustomItem {
  id: string;
  g: string;
  n: string;
  d: string;
  p: number;
  r: string[];
}

export interface PitchContact {
  name?: string;
  email?: string;
  phone?: string;
  notes?: string;
}

export interface PitchPricingLine {
  item: string;
  item_id: string;
  name: string;
  group: string;
  base: number;
  effective: number;
  delta: number;
  source: PitchPriceSource;
  price?: number;
}

export interface PitchPricingDiff {
  item: string;
  item_id: string;
  name: string;
  base: number;
  effective: number;
  base_price: number;
  effective_price: number;
  delta: number;
  source: PitchPriceSource;
  selected: boolean;
}

export interface PitchPricingSummary {
  lines: PitchPricingLine[];
  custom_items: PitchPricingLine[];
  one_time_plans?: PitchServicePlan[];
  recurring?: PitchRecurringPlan[];
  recurring_totals?: PitchRecurringTotals;
  payment_plan?: PitchPaymentPlanRow[];
  subtotal: number;
  standalone_fee: number;
  standalone_label: string;
  total: number;
  differs_from_base: PitchPricingDiff[];
}

export interface PitchRuleViolation {
  field: string;
  rule: string;
  message: string;
}

export interface PitchProductWarning {
  product: string;
  level?: string;
  message: string;
  matches?: string[];
}

export interface PitchProposal {
  id: string;
  slug: string;
  kind: PitchProposalKind;
  status: PitchProposalStatus;
  client_name: string;
  business_name: string;
  contact: PitchContact;
  preset: PitchPreset;
  price_overrides: Record<string, number>;
  custom_items: PitchCustomItem[];
  hidden_sheets?: PitchSectionKey[];
  hidden_about?: PitchAboutSectionKey[];
  custom_categories?: PitchCustomCategory[];
  included_from_hidden?: PitchIncludedFromHidden[];
  changes?: PitchDroppedItemChange[];
  payment_plan?: PitchPaymentPlanRow[];
  payment_note?: string;
  timeline?: PitchTimelineStep[];
  service_plans?: PitchServicePlan[];
  notes: string;
  created_by: string;
  share_id?: string;
  pitch_commit?: string;
  published_at?: string | null;
  link_status?: 'live' | 'withdrawn' | null;
  created_at: string;
  updated_at: string;
  pricing_summary: PitchPricingSummary;
  warnings?: PitchProductWarning[];
  pitch_version?: PitchVersionInfo;
  added_dependencies?: string[];
}

export interface PitchPdfRecord {
  filename: string;
  path: string;
  url: string;
  size: number;
  created_at: string;
}

export interface PitchOutboxEvent {
  id: string | number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
  synced?: boolean;
}

export interface PitchOutboxResponse {
  events: PitchOutboxEvent[];
  pending_count: number;
}

export interface CreateProposalInput {
  slug: string;
  kind?: PitchProposalKind;
  status?: PitchProposalStatus;
  client_name?: string;
  business_name?: string;
  contact?: PitchContact;
  from_preset?: string;
  duplicate_of?: string;
  preset?: Partial<PitchPreset>;
  price_overrides?: Record<string, number>;
  custom_items?: PitchCustomItem[];
  hidden_sheets?: PitchSectionKey[];
  hidden_about?: PitchAboutSectionKey[];
  custom_categories?: PitchCustomCategory[];
  payment_plan?: PitchPaymentPlanRow[];
  payment_note?: string;
  timeline?: PitchTimelineStep[];
  service_plans?: PitchServicePlan[];
  notes?: string;
}

export interface UpdateProposalInput {
  slug?: string;
  kind?: PitchProposalKind;
  status?: PitchProposalStatus;
  client_name?: string;
  business_name?: string;
  contact?: PitchContact;
  preset?: Partial<PitchPreset>;
  price_overrides?: Record<string, number>;
  custom_items?: PitchCustomItem[];
  hidden_sheets?: PitchSectionKey[];
  hidden_about?: PitchAboutSectionKey[];
  custom_categories?: PitchCustomCategory[];
  payment_plan?: PitchPaymentPlanRow[];
  payment_note?: string;
  timeline?: PitchTimelineStep[];
  service_plans?: PitchServicePlan[];
  notes?: string;
}

export interface PitchPreviewPayloadInput extends UpdateProposalInput {
  id?: string;
  proposal_id?: string;
  mode?: 'preview' | 'present' | 'form';
}

export interface PitchPreviewPayloadResponse {
  mode: string;
  kind: PitchProposalKind | null;
  locked: boolean;
  prices: Record<string, number>;
  custom_items: PitchCustomItem[];
  preset: PitchPreset | null;
  proposal_id?: string | null;
  contact: PitchContact & { business?: string };
  payment_plan: PitchPaymentPlanRow[];
  payment_note: string;
  timeline: PitchTimelineStep[];
  service_plans: PitchServicePlan[];
  hidden_sheets: PitchSectionKey[];
  hidden_about: PitchAboutSectionKey[];
  custom_categories: PitchCustomCategory[];
  included_from_hidden: PitchIncludedFromHidden[];
  violations?: PitchRuleViolation[];
  valid?: boolean;
  changes?: PitchDroppedItemChange[];
  added_dependencies?: string[];
  pricing_summary?: PitchPricingSummary;
  warnings?: PitchProductWarning[];
}

export interface FinalizeProposalInput {
  slug?: string;
  status?: PitchProposalStatus;
  client_name?: string;
  business_name?: string;
  contact?: PitchContact;
  notes?: string;
}

export interface PitchPublishResult {
  url: string;
  published_at: string;
}

export interface PitchWithdrawResult {
  ok: boolean;
  proposal_id: string;
  link_status: 'withdrawn';
}

export interface PitchQuoteRequestItem {
  id: string;
  name: string;
  price: number;
  group?: string;
}

export interface PitchQuoteRequestContact {
  name: string;
  business: string;
  phone: string;
  notes: string;
}

export interface PitchQuoteRequest {
  id: string;
  share_id: string;
  local_proposal_id: string | null;
  proposal_title: string;
  proposal_slug?: string | null;
  choices: {
    selected?: string[];
    custom_selected?: string[];
    total_shown?: number;
  };
  contact: PitchQuoteRequestContact;
  selected_items: PitchQuoteRequestItem[];
  computed_total: number;
  total_shown: number | null;
  total_mismatch: boolean;
  created_at: string;
  received_at: string;
  read_at: string | null;
}

export interface PitchQuoteRequestsResponse {
  requests: PitchQuoteRequest[];
  unread_count: number;
}

/** Format an INR amount with Indian digit grouping matching Pitch's money(). */
export function formatRupees(amount: number | null | undefined): string {
  const safe = typeof amount === 'number' && Number.isFinite(amount) ? Math.round(amount) : 0;
  return `₹${safe.toLocaleString('en-IN')}`;
}

/** Fetch vendored Magnm-Pitch version metadata. */
export function getPitchVersion(): Promise<PitchVersionInfo> {
  return api.get<PitchVersionInfo>('/api/pitch/version');
}

/** Fetch merged Pitch catalog (items with Pitch base + admin overrides, groups, presets). */
export function getPitchCatalog(): Promise<PitchCatalog> {
  return api.get<PitchCatalog>('/api/pitch/catalog');
}

/** List proposals with optional status and kind filters. */
export async function listProposals(filters?: {
  status?: string;
  kind?: string;
}): Promise<PitchProposal[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set('status', filters.status);
  if (filters?.kind) params.set('kind', filters.kind);
  const qs = params.toString();
  const res = await api.get<{ proposals: PitchProposal[] }>(
    `/api/pitch/proposals${qs ? `?${qs}` : ''}`,
  );
  return Array.isArray(res?.proposals) ? res.proposals : [];
}

/** Fetch the system default quotation (Pitch default options and layout). */
export function getDefaultProposal(): Promise<PitchProposal> {
  return api.get<PitchProposal>('/api/pitch/default');
}

/** Fetch a single proposal enriched with pricing_summary and product-rule warnings. */
export function getProposal(proposalId: string): Promise<PitchProposal> {
  return api.get<PitchProposal>(`/api/pitch/proposals/${encodeURIComponent(proposalId)}`);
}

/** Create a new blank proposal, quote form, final quotation, preset-based proposal, or duplicate. */
export function createProposal(payload: CreateProposalInput): Promise<PitchProposal> {
  return api.post<PitchProposal>('/api/pitch/proposals', payload);
}

/** Update an existing proposal after engine Pitch-rule validation. */
export function updateProposal(
  proposalId: string,
  payload: UpdateProposalInput,
): Promise<PitchProposal> {
  return api.post<PitchProposal>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}`,
    payload,
  );
}

/** Duplicate an existing proposal onto a new slug (optionally converting default to proposal or quote_form). */
export function duplicateProposal(
  proposalId: string,
  slug: string,
  kind?: PitchProposalKind,
): Promise<PitchProposal> {
  return api.post<PitchProposal>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/duplicate`,
    kind ? { slug, kind } : { slug },
  );
}

/** Create a new final quotation from an existing proposal or quote form, keeping the original untouched. */
export function finalizeProposal(
  proposalId: string,
  overrides: FinalizeProposalInput = {},
): Promise<PitchProposal> {
  return api.post<PitchProposal>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/finalize`,
    overrides,
  );
}

/** Archive (hard=false) or permanently delete (hard=true) a proposal. */
export function deleteProposal(
  proposalId: string,
  hard = false,
): Promise<{ deleted: boolean; hard: boolean; proposal_id: string; status: string }> {
  return api.post<{ deleted: boolean; hard: boolean; proposal_id: string; status: string }>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/delete`,
    { hard },
  );
}

/** List saved PDF exports for a proposal. */
export async function listProposalPdfs(
  proposalId: string,
): Promise<{ proposal_id: string; slug: string; pdfs: PitchPdfRecord[] }> {
  return api.get<{ proposal_id: string; slug: string; pdfs: PitchPdfRecord[] }>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/pdfs`,
  );
}

/** Set an admin base price override for a Pitch catalog item. */
export function setBasePrice(
  itemId: string,
  price: number,
): Promise<{ admin_only: boolean; item_id: string; item: PitchCatalogItem; old_price: number; new_price: number }> {
  return api.post('/api/pitch/base_prices', { item_id: itemId, price });
}

/** Reset an admin base price override back to Pitch's catalog price. */
export function resetBasePrice(
  itemId: string,
): Promise<{ admin_only: boolean; item_id: string; item: PitchCatalogItem; old_price: number; new_price: number; reset: boolean }> {
  return api.post('/api/pitch/base_prices/reset', { item_id: itemId });
}

/** Fetch queued sync outbox events and pending notification count. */
export function getPitchOutbox(pendingOnly = true): Promise<PitchOutboxResponse> {
  return api.get<PitchOutboxResponse>(
    `/api/pitch/outbox?pending=${pendingOnly ? '1' : '0'}`,
  );
}

/** Validate a draft proposal payload without saving and return derived preview fields. */
export function getPitchPreviewPayload(
  payload: PitchPreviewPayloadInput,
): Promise<PitchPreviewPayloadResponse> {
  return api.post<PitchPreviewPayloadResponse>('/api/pitch/preview_payload', payload);
}

/** Publish a proposal as a public client link on Supabase. */
export function publishProposal(proposalId: string): Promise<PitchPublishResult> {
  return api.post<PitchPublishResult>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/publish`,
    {},
  );
}

/** Withdraw a published proposal client link. */
export function withdrawProposalLink(proposalId: string): Promise<PitchWithdrawResult> {
  return api.post<PitchWithdrawResult>(
    `/api/pitch/proposals/${encodeURIComponent(proposalId)}/withdraw`,
    {},
  );
}

/** List received client quote requests (newest first) and unread count. */
export async function listQuoteRequests(): Promise<PitchQuoteRequestsResponse> {
  const res = await api.get<Partial<PitchQuoteRequestsResponse>>('/api/pitch/requests');
  return {
    requests: Array.isArray(res?.requests) ? res.requests : [],
    unread_count: typeof res?.unread_count === 'number' ? res.unread_count : 0,
  };
}

/** Mark a received quote request as read. */
export function markQuoteRequestRead(
  requestId: string,
): Promise<{ ok: boolean; request: PitchQuoteRequest; unread_count: number }> {
  return api.post<{ ok: boolean; request: PitchQuoteRequest; unread_count: number }>(
    `/api/pitch/requests/${encodeURIComponent(requestId)}/read`,
    {},
  );
}
