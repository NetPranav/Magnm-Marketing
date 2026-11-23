import { wipeActions } from './actions';
import { ensureCacheOwner, loadCache, wipeCache } from './cache';
import { getNativeBridge, type NativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, netJson } from './net';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { wipeQueue } from './queue';
import { bumpStateGeneration, getStateGeneration, removeNonSecretKey } from './storage';
import type { AccountProfile, AuthSession } from './types';

export const AUTH_REDIRECT_URL = 'com.magnm.studio://auth/callback';
export const PKCE_TTL_MS = 10 * 60 * 1000; // 10 minutes
export const REFRESH_LEEWAY_S = 60;

export const PKCE_SECRET_KEY = 'magnm.auth.pkce';
export const SESSION_SECRET_KEY = 'magnm.auth.session';
export const NIM_KEY_SECRET_KEY = 'magnm.nim.personalKey';

const NOT_INVITED_MESSAGE =
  'This Google account is not invited to Magnm Studio. Ask your admin to invite it.';

let inFlightRefresh: Promise<string | null> | null = null;

interface PendingPkce {
  state: string;
  verifier: string;
  createdAt: number;
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function randomBase64Url(numBytes: number): string {
  const bytes = new Uint8Array(numBytes);
  globalThis.crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export async function computePkceChallenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data);
  return bytesToBase64Url(new Uint8Array(digest));
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length === 0 || b.length === 0) return false;
  const maxLen = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < maxLen; i += 1) {
    const ca = i < a.length ? a.charCodeAt(i) : 0;
    const cb = i < b.length ? b.charCodeAt(i) : 0;
    diff |= ca ^ cb;
  }
  return diff === 0;
}

function decodeJwtSub(token: string): string {
  try {
    const parts = token.split('.');
    if (parts.length < 2) return '';
    const padded = parts[1] + '='.repeat((4 - (parts[1].length % 4)) % 4);
    const json = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
    const claims = JSON.parse(json) as { sub?: unknown };
    return typeof claims.sub === 'string' ? claims.sub : '';
  } catch {
    return '';
  }
}

export async function getStoredSession(
  bridge: NativeBridge = getNativeBridge(),
): Promise<AuthSession | null> {
  const raw = await bridge.secureStore.get(SESSION_SECRET_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AuthSession>;
    if (!parsed || typeof parsed.access_token !== 'string' || !parsed.access_token) {
      return null;
    }
    return {
      access_token: parsed.access_token,
      refresh_token: String(parsed.refresh_token ?? ''),
      expires_at: Number(parsed.expires_at ?? 0),
      user_id: String(parsed.user_id ?? decodeJwtSub(parsed.access_token)),
      email: String(parsed.email ?? '').toLowerCase(),
    };
  } catch {
    return null;
  }
}

async function saveSession(session: AuthSession, bridge: NativeBridge): Promise<void> {
  await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify(session));
}

/** Wipe all account, pairing, key, cache, and queue state locally and bump generation. */
export async function wipeAllLocalState(bridge: NativeBridge = getNativeBridge()): Promise<void> {
  bumpStateGeneration();
  inFlightRefresh = null;
  await bridge.secureStore.remove(SESSION_SECRET_KEY);
  await bridge.secureStore.remove(PKCE_SECRET_KEY);
  await bridge.secureStore.remove(LAPTOP_TOKEN_SECRET_KEY);
  await bridge.secureStore.remove(NIM_KEY_SECRET_KEY);
  removeNonSecretKey(PAIRING_META_KEY);
  wipeCache();
  wipeQueue();
  wipeActions();
}

/** Begin Google PKCE sign-in, storing verifier + state in secureStore and opening system browser. */
export async function startGoogleSignIn(
  bridge: NativeBridge = getNativeBridge(),
  nowMs: number = Date.now(),
): Promise<string> {
  const cfg = getPublicSupabaseConfig();
  const verifier = randomBase64Url(48);
  const challenge = await computePkceChallenge(verifier);
  const state = randomBase64Url(24);

  const pending: PendingPkce = { state, verifier, createdAt: nowMs };
  await bridge.secureStore.set(PKCE_SECRET_KEY, JSON.stringify(pending));

  const redirectTo = `${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state)}`;
  const params = new URLSearchParams({
    provider: 'google',
    redirect_to: redirectTo,
    code_challenge: challenge,
    code_challenge_method: 's256',
  });
  const authorizeUrl = `${cfg.url}/auth/v1/authorize?${params.toString()}`;
  await bridge.openBrowser(authorizeUrl);
  return authorizeUrl;
}

export interface DeepLinkAuthResult {
  handled: boolean;
  reason?: 'ignored' | 'unsolicited' | 'rejected' | 'expired' | 'error' | 'signed_in';
  error?: string;
  session?: AuthSession;
  accountChanged?: boolean;
}

function hasForbiddenTokenParams(url: URL): boolean {
  const checkParams = (sp: URLSearchParams) =>
    sp.has('access_token') ||
    sp.has('refresh_token') ||
    sp.has('id_token') ||
    sp.has('provider_token');

  if (checkParams(url.searchParams)) return true;
  if (url.hash && url.hash.length > 1) {
    const hashParams = new URLSearchParams(url.hash.slice(1));
    if (checkParams(hashParams)) return true;
  }
  return false;
}

/** Handle com.magnm.studio://auth/callback deep links with strict PKCE checks. */
export async function handleAuthDeepLink(
  rawUrl: string,
  bridge: NativeBridge = getNativeBridge(),
  nowMs: number = Date.now(),
): Promise<DeepLinkAuthResult> {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return { handled: false, reason: 'ignored' };
  }

  if (
    parsed.protocol !== 'com.magnm.studio:' ||
    parsed.hostname.toLowerCase() !== 'auth' ||
    parsed.pathname !== '/callback'
  ) {
    return { handled: false, reason: 'ignored' };
  }

  // Never accept tokens directly from a deep link (implicit/fragment leak).
  if (hasForbiddenTokenParams(parsed)) {
    return {
      handled: false,
      reason: 'rejected',
      error: 'Direct tokens in callback link are refused; only authorization codes are accepted.',
    };
  }

  const rawPending = await bridge.secureStore.get(PKCE_SECRET_KEY);
  if (!rawPending) {
    // Unsolicited deep links are ignored silently.
    return { handled: false, reason: 'unsolicited' };
  }

  let pending: PendingPkce;
  try {
    pending = JSON.parse(rawPending) as PendingPkce;
  } catch {
    await bridge.secureStore.remove(PKCE_SECRET_KEY);
    return { handled: false, reason: 'unsolicited' };
  }

  const stateParam = parsed.searchParams.get('s') ?? '';
  if (!timingSafeEqual(stateParam, pending.state)) {
    return {
      handled: false,
      reason: 'rejected',
      error: 'Sign-in state mismatch. Unrecognized callback ignored.',
    };
  }

  // Single use: consume the PKCE challenge before exchanging or checking expiry.
  await bridge.secureStore.remove(PKCE_SECRET_KEY);

  if (nowMs - pending.createdAt > PKCE_TTL_MS) {
    return {
      handled: true,
      reason: 'expired',
      error: 'Sign-in session expired after 10 minutes. Please try again.',
    };
  }

  const oauthError = parsed.searchParams.get('error') ?? '';
  const oauthDesc = parsed.searchParams.get('error_description') ?? '';
  if (oauthError || oauthDesc) {
    const combined = `${oauthError} ${oauthDesc}`;
    const msg = combined.includes('Database error saving new user')
      ? NOT_INVITED_MESSAGE
      : 'Google sign-in could not be completed. Please try again.';
    return { handled: true, reason: 'error', error: msg };
  }

  const code = (parsed.searchParams.get('code') ?? '').trim();
  if (!code) {
    return {
      handled: true,
      reason: 'error',
      error: 'Missing authorization code from Google sign-in.',
    };
  }

  const cfg = getPublicSupabaseConfig();
  const prevSession = await getStoredSession(bridge);
  const prevCachedUid = loadCache(nowMs).ownerUid;
  const prevUid = prevSession?.user_id || prevCachedUid || '';
  const gen = getStateGeneration();

  let tokenData: {
    access_token?: string;
    refresh_token?: string;
    expires_at?: number;
    expires_in?: number;
    user?: { id?: string; email?: string };
  };
  try {
    tokenData = await netJson(
      {
        destination: 'supabase',
        method: 'POST',
        url: `${cfg.url}/auth/v1/token?grant_type=pkce`,
        requireAuth: false,
        body: {
          auth_code: code,
          code_verifier: pending.verifier,
        },
      },
      { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey },
      bridge,
    );
  } catch (err) {
    const rawMsg = err instanceof Error ? err.message : 'Sign-in failed.';
    const msg = rawMsg.includes('Database error saving new user') ? NOT_INVITED_MESSAGE : rawMsg;
    return { handled: true, reason: 'error', error: msg };
  } finally {
    await bridge.closeBrowser();
  }

  if (gen !== getStateGeneration()) {
    return { handled: true, reason: 'error', error: 'Sign-in was cancelled.' };
  }

  if (!tokenData?.access_token) {
    return {
      handled: true,
      reason: 'error',
      error: 'Supabase token response was incomplete.',
    };
  }

  const nowSec = Math.floor(nowMs / 1000);
  const exp =
    typeof tokenData.expires_at === 'number'
      ? Math.trunc(tokenData.expires_at)
      : nowSec + Number(tokenData.expires_in ?? 3600);
  const uid = String(tokenData.user?.id || decodeJwtSub(tokenData.access_token) || '');
  const email = String(tokenData.user?.email || '').toLowerCase();

  const nextSession: AuthSession = {
    access_token: tokenData.access_token,
    refresh_token: String(tokenData.refresh_token ?? ''),
    expires_at: exp,
    user_id: uid,
    email,
  };

  const accountChanged = Boolean(prevUid && uid && prevUid !== uid);
  if (accountChanged) {
    await wipeAllLocalState(bridge);
  }
  ensureCacheOwner(uid || null, nowMs);
  await saveSession(nextSession, bridge);

  return { handled: true, reason: 'signed_in', session: nextSession, accountChanged };
}

/** Return a valid Supabase access token, coalescing concurrent refreshes to one request at a time. */
export async function getValidAccessToken(
  bridge: NativeBridge = getNativeBridge(),
  nowMs: number = Date.now(),
): Promise<string | null> {
  const session = await getStoredSession(bridge);
  if (!session) return null;

  const nowSec = Math.floor(nowMs / 1000);
  if (session.expires_at - nowSec > REFRESH_LEEWAY_S) {
    return session.access_token;
  }

  if (!session.refresh_token) {
    await bridge.secureStore.remove(SESSION_SECRET_KEY);
    return null;
  }

  if (inFlightRefresh) {
    return inFlightRefresh;
  }

  const gen = getStateGeneration();
  const cfg = getPublicSupabaseConfig();
  let refreshPromise: Promise<string | null> | null = null;
  refreshPromise = (async (): Promise<string | null> => {
    try {
      const refreshed = await netJson<{
        access_token?: string;
        refresh_token?: string;
        expires_at?: number;
        expires_in?: number;
        user?: { id?: string; email?: string };
      }>(
        {
          destination: 'supabase',
          method: 'POST',
          url: `${cfg.url}/auth/v1/token?grant_type=refresh_token`,
          requireAuth: false,
          body: { refresh_token: session.refresh_token },
        },
        { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey },
        bridge,
      );
      if (gen !== getStateGeneration()) {
        return null;
      }
      if (!refreshed?.access_token) {
        await bridge.secureStore.remove(SESSION_SECRET_KEY);
        return null;
      }
      const exp =
        typeof refreshed.expires_at === 'number'
          ? Math.trunc(refreshed.expires_at)
          : nowSec + Number(refreshed.expires_in ?? 3600);
      const updated: AuthSession = {
        access_token: refreshed.access_token,
        refresh_token: String(refreshed.refresh_token || session.refresh_token),
        expires_at: exp,
        user_id: String(refreshed.user?.id || session.user_id || decodeJwtSub(refreshed.access_token)),
        email: String(refreshed.user?.email || session.email).toLowerCase(),
      };
      await saveSession(updated, bridge);
      return updated.access_token;
    } catch (err) {
      if (gen !== getStateGeneration()) {
        return null;
      }
      if (err instanceof HttpStatusError && (err.status === 400 || err.status === 401)) {
        await bridge.secureStore.remove(SESSION_SECRET_KEY);
        return null;
      }
      throw err;
    } finally {
      if (inFlightRefresh === refreshPromise) {
        inFlightRefresh = null;
      }
    }
  })();

  inFlightRefresh = refreshPromise;
  return refreshPromise;
}

/** Load the signed-in member's profile from Supabase profiles table. */
export async function fetchAccountProfile(
  bridge: NativeBridge = getNativeBridge(),
): Promise<AccountProfile | null> {
  const session = await getStoredSession(bridge);
  const token = await getValidAccessToken(bridge);
  if (!session || !token || !session.user_id) return null;

  const cfg = getPublicSupabaseConfig();
  const rows = await netJson<AccountProfile[]>(
    {
      destination: 'supabase',
      method: 'GET',
      url: `${cfg.url}/rest/v1/profiles?id=eq.${encodeURIComponent(session.user_id)}&select=id,email,display_name,avatar_url,role,active`,
    },
    {
      supabaseUrl: cfg.url,
      supabaseAnonKey: cfg.publishableKey,
      supabaseAccessToken: token,
    },
    bridge,
  );
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return rows[0];
}

/** Sign out on Supabase (best effort) and wipe all local tokens, pairing, keys, caches, and queue. */
export async function signOut(bridge: NativeBridge = getNativeBridge()): Promise<void> {
  const session = await getStoredSession(bridge);
  const cfg = getPublicSupabaseConfig();
  if (session?.access_token) {
    try {
      await netJson(
        {
          destination: 'supabase',
          method: 'POST',
          url: `${cfg.url}/auth/v1/logout`,
        },
        {
          supabaseUrl: cfg.url,
          supabaseAnonKey: cfg.publishableKey,
          supabaseAccessToken: session.access_token,
        },
        bridge,
      );
    } catch {
      // Best-effort remote logout; local wipe always completes.
    }
  }
  await wipeAllLocalState(bridge);
}

export async function getPersonalNimKey(
  bridge: NativeBridge = getNativeBridge(),
): Promise<string | null> {
  const val = await bridge.secureStore.get(NIM_KEY_SECRET_KEY);
  return val && val.trim() ? val.trim() : null;
}

export async function setPersonalNimKey(
  key: string | null,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const cleaned = (key ?? '').trim();
  if (!cleaned) {
    await bridge.secureStore.remove(NIM_KEY_SECRET_KEY);
    return;
  }
  await bridge.secureStore.set(NIM_KEY_SECRET_KEY, cleaned);
}
