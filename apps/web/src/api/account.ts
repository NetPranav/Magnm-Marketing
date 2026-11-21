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
