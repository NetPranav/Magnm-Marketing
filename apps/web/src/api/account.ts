import { api } from '../api';

export type AccountRole = 'admin' | 'member';

export interface AccountUser {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  role: AccountRole;
  active: boolean;
}

export interface AccountState {
  configured: boolean;
  signed_in: boolean;
  deactivated?: boolean;
  user: AccountUser | null;
  last_error?: string | null;
}

export interface LoginStartResponse {
  authorize_url: string;
}

export interface InviteRecord {
  email: string;
  role: AccountRole;
  invited_by?: string | null;
  created_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface SyncStatus {
  enabled: boolean;
  signed_in: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  pending: number;
  synced_count: number;
  running: boolean;
}

export interface MediaPreviewStatus {
  ffmpeg: boolean;
  uploaded: number;
  waiting: number;
  failed: number;
  last_error: string | null;
}

export interface StorageUsage {
  bytes: number;
  files: number;
  by_kind: Record<string, { bytes: number; files: number }>;
  limit_bytes: number;
  percent: number;
  warning: boolean;
}

export interface AccountAiStatus {
  mode: 'own_keys' | 'team' | 'none';
  today: {
    requests: number;
    prompt_tokens: number;
    completion_tokens: number;
  };
  limit: {
    daily_requests: number;
    daily_tokens: number;
  };
}

/** Fetch current Supabase team account configuration and sign-in status. */
export function getAccount(): Promise<AccountState> {
  return api.get<AccountState>('/api/account');
}

/** Start a Google PKCE login flow and optionally open the system browser. */
export function startLogin(openBrowser = true): Promise<LoginStartResponse> {
  return api.post<LoginStartResponse>('/api/account/login/start', {
    open_browser: openBrowser,
  });
}

/** Cancel any pending Google PKCE login flow. */
export function cancelLogin(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/account/login/cancel', {});
}

/** Sign out of the current team account and clear the local session. */
export function logoutAccount(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/account/logout', {});
}

/** List team invites (admin only). */
export async function listInvites(): Promise<InviteRecord[]> {
  const res = await api.get<{ invites?: InviteRecord[] } | InviteRecord[]>('/api/account/invites');
  if (Array.isArray(res)) return res;
  return Array.isArray(res?.invites) ? res.invites : [];
}

/** Create an invite for a Google account email (admin only). */
export async function createInvite(email: string, role: AccountRole): Promise<InviteRecord> {
  const res = await api.post<{ invite?: InviteRecord } & Partial<InviteRecord>>(
    '/api/account/invites',
    { email, role },
  );
  return (res.invite ?? res) as InviteRecord;
}

/** Revoke an open invite by email (admin only). */
export async function revokeInvite(email: string): Promise<InviteRecord> {
  const res = await api.post<{ invite?: InviteRecord } & Partial<InviteRecord>>(
    '/api/account/invites/revoke',
    { email },
  );
  return (res.invite ?? res) as InviteRecord;
}

/** Fetch current workspace cloud sync status. */
export function getSyncStatus(): Promise<SyncStatus> {
  return api.get<SyncStatus>('/api/account/sync');
}

/** Trigger an immediate workspace cloud sync. */
export function triggerSync(): Promise<SyncStatus> {
  return api.post<SyncStatus>('/api/account/sync', {});
}

/** Fetch local media preview generation and upload queue status. */
export function getMediaStatus(): Promise<MediaPreviewStatus> {
  return api.get<MediaPreviewStatus>('/api/account/media');
}

/** Fetch team preview storage usage summary from Supabase. */
export function getStorageUsage(): Promise<StorageUsage> {
  return api.get<StorageUsage>('/api/account/storage');
}

/** Request a short-lived signed URL for a preview object in private storage. */
export function signMediaPreview(storagePath: string, expiresIn = 3600): Promise<{ url: string }> {
  return api.post<{ url: string }>('/api/account/media/sign', {
    storage_path: storagePath,
    expires_in: expiresIn,
  });
}

/** Fetch current AI key mode and today's team AI usage/limit for the signed-in user. */
export function getAccountAi(): Promise<AccountAiStatus> {
  return api.get<AccountAiStatus>('/api/account/ai');
}
