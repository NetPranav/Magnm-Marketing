import { api } from '../api';
import type { AccountRole, StorageUsage } from './account';

export type AdminPresence = 'working' | 'online' | 'idle' | 'offline';

export interface AdminDevice {
  device_id: string;
  device_kind: 'desktop' | 'android' | string;
  status: string;
  running_agents: string[];
  app_version: string | null;
  last_seen_at: string;
}

export interface AdminMember {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  role: AccountRole;
  active: boolean;
  created_at: string;
  last_seen_at: string | null;
  device_last_seen_at: string | null;
  latest_activity_ts: string | null;
  last_active_at: string | null;
  app_version: string | null;
  presence: AdminPresence;
  status: AdminPresence;
  running_agents: string[];
  devices: AdminDevice[];
}

export interface AdminTeamResponse {
  members: AdminMember[];
  current_user_id: string;
}

export interface AdminActivityEvent {
  id: number | string;
  owner_id: string;
  member_name: string;
  member_email?: string | null;
  ts: string;
  kind: string;
  agent_id: string | null;
  summary: string | null;
  data: Record<string, unknown>;
}

export interface AdminActivityResponse {
  events: AdminActivityEvent[];
  next_before: string | null;
}

export interface AdminDailySeriesItem {
  date: string;
  total: number;
  by_member: Record<string, number>;
}

export interface AdminDailyActivityResponse {
  days: number;
  dates: string[];
  series: AdminDailySeriesItem[];
  members: Array<{ id: string; display_name: string }>;
}

export type MirrorRecordKind =
  | 'chat_session'
  | 'content_item'
  | 'research_note'
  | 'review_comment'
  | 'brief'
  | 'task'
  | 'posted_link'
  | 'metric'
  | 'team_message'
  | 'content_version'
  | 'chat_turn';

export interface MirrorRecordSummary {
  id: string;
  kind: MirrorRecordKind | string;
  local_id: string;
  parent_local_id: string | null;
  title: string | null;
  local_updated_at: string;
  synced_at: string;
  data?: Record<string, unknown>;
}

export interface AdminRecordsResponse {
  records: MirrorRecordSummary[];
  next_before: string | null;
}

export interface MirrorRecordDetail extends MirrorRecordSummary {
  owner_id?: string;
  data: Record<string, unknown>;
}

export interface AdminRecordDetailResponse {
  record: MirrorRecordDetail;
  children: MirrorRecordSummary[];
}

export interface AdminPreview {
  id: string;
  owner_id: string;
  local_source_id: string;
  source_kind: string;
  parent_local_id: string | null;
  kind: 'thumbnail' | 'image_preview' | 'video_preview' | string;
  storage_path: string;
  content_type: string;
  bytes: number;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  created_at: string;
  expires_at: string | null;
  signed_url: string;
}

export interface AdminPreviewsResponse {
  previews: AdminPreview[];
}

export interface AdminPitchProposal {
  share_id: string;
  local_proposal_id: string | null;
  kind: string;
  status: string;
  client_name: string;
  business_name: string;
  updated_at: string;
  created_at?: string;
  expires_at?: string | null;
}

export interface AdminQuoteSubmission {
  id: string;
  share_id: string;
  name: string;
  business: string;
  phone: string;
  notes: string;
  items: string[];
  total_shown: number | null;
  created_at: string;
  seen_at: string | null;
}

export interface AdminPitchResponse {
  proposals: AdminPitchProposal[];
  submissions: AdminQuoteSubmission[];
}

export interface AdminAuditEntry {
  id: number | string;
  actor_id: string | null;
  actor_name: string;
  action: string;
  target: string;
  target_label?: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface AdminAuditResponse {
  entries: AdminAuditEntry[];
  next_before: string | null;
}

/** Fetch all team members with presence, devices, running agents, and latest activity. */
export function getAdminTeam(): Promise<AdminTeamResponse> {
  return api.get<AdminTeamResponse>('/api/admin/team');
}

/** Fetch team activity timeline with optional filters and timestamp cursor. */
export function getAdminActivity(params: {
  member?: string;
  kind?: string;
  before?: string;
  limit?: number;
} = {}): Promise<AdminActivityResponse> {
  const qs = new URLSearchParams();
  if (params.member) qs.set('member', params.member);
  if (params.kind) qs.set('kind', params.kind);
  if (params.before) qs.set('before', params.before);
  if (params.limit) qs.set('limit', String(params.limit));
  const query = qs.toString();
  return api.get<AdminActivityResponse>(query ? `/api/admin/activity?${query}` : '/api/admin/activity');
}

/** Fetch daily activity counts per member for the last N days. */
export function getAdminDailyActivity(days = 14): Promise<AdminDailyActivityResponse> {
  return api.get<AdminDailyActivityResponse>(`/api/admin/activity/daily?days=${days}`);
}

/** Fetch a member's mirrored records list (without data blobs). */
export function getMemberRecords(
  memberId: string,
  params: { kind?: string; q?: string; before?: string; limit?: number } = {},
): Promise<AdminRecordsResponse> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set('kind', params.kind);
  if (params.q) qs.set('q', params.q);
  if (params.before) qs.set('before', params.before);
  if (params.limit) qs.set('limit', String(params.limit));
  const query = qs.toString();
  const base = `/api/admin/members/${encodeURIComponent(memberId)}/records`;
  return api.get<AdminRecordsResponse>(query ? `${base}?${query}` : base);
}

/** Fetch a single mirrored record with data and its children list rows. */
export function getMemberRecordDetail(
  memberId: string,
  kind: string,
  localId: string,
): Promise<AdminRecordDetailResponse> {
  return api.get<AdminRecordDetailResponse>(
    `/api/admin/members/${encodeURIComponent(memberId)}/records/${encodeURIComponent(kind)}/${encodeURIComponent(localId)}`,
  );
}

/** Fetch signed preview URLs for a member, optionally filtered by parent content local_id. */
export function getMemberPreviews(
  memberId: string,
  parent?: string,
  limit?: number,
): Promise<AdminPreviewsResponse> {
  const qs = new URLSearchParams();
  if (parent) qs.set('parent', parent);
  if (limit) qs.set('limit', String(limit));
  const query = qs.toString();
  const base = `/api/admin/members/${encodeURIComponent(memberId)}/previews`;
  return api.get<AdminPreviewsResponse>(query ? `${base}?${query}` : base);
}

/** Fetch read-only Pitch published links and quote requests for a member. */
export function getMemberPitch(memberId: string): Promise<AdminPitchResponse> {
  return api.get<AdminPitchResponse>(`/api/admin/members/${encodeURIComponent(memberId)}/pitch`);
}

/** Fetch team storage usage summary. */
export function getAdminStorage(): Promise<StorageUsage> {
  return api.get<StorageUsage>('/api/admin/storage');
}

/** Fetch newest-first admin audit log entries. */
export function getAdminAudit(params: { before?: string; limit?: number } = {}): Promise<AdminAuditResponse> {
  const qs = new URLSearchParams();
  if (params.before) qs.set('before', params.before);
  if (params.limit) qs.set('limit', String(params.limit));
  const query = qs.toString();
  return api.get<AdminAuditResponse>(query ? `/api/admin/audit?${query}` : '/api/admin/audit');
}

/** Change another team member's role ('admin' or 'member'). */
export function setMemberRole(memberId: string, role: AccountRole): Promise<{ member: AdminMember }> {
  return api.post<{ member: AdminMember }>(
    `/api/admin/members/${encodeURIComponent(memberId)}/role`,
    { role },
  );
}

/** Activate or deactivate another team member's account. */
export function setMemberActive(memberId: string, active: boolean): Promise<{ member: AdminMember }> {
  return api.post<{ member: AdminMember }>(
    `/api/admin/members/${encodeURIComponent(memberId)}/active`,
    { active },
  );
}
