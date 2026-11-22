import { beforeEach, describe, expect, it } from 'vitest';
import {
  AUTH_REDIRECT_URL,
  getStoredSession,
  handleAuthDeepLink,
  PKCE_SECRET_KEY,
  PKCE_TTL_MS,
  SESSION_SECRET_KEY,
  signOut,
  startGoogleSignIn,
} from './auth';
import { CACHE_STORAGE_KEY, saveMirrorRecords } from './cache';
import { createPreviewNativeBridge } from './native';
import { getPublicSupabaseConfig } from './net';
import { enqueueInboxItem, loadQueue } from './queue';

const SUPA = getPublicSupabaseConfig();

describe('Google PKCE auth (src/auth.ts)', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('stores verifier in secureStore (never localStorage), checks state, exchanges code once', async () => {
    let tokenCalls = 0;
    const openedUrls: string[] = [];
    const bridge = createPreviewNativeBridge({
      onOpenBrowser: (u) => openedUrls.push(u),
      httpHandler: async (req) => {
        tokenCalls += 1;
        expect(req.url).toBe(`${SUPA.url}/auth/v1/token?grant_type=pkce`);
        const body = JSON.parse(req.body || '{}') as { auth_code: string; code_verifier: string };
        expect(body.auth_code).toBe('code-abc');
        expect(body.code_verifier.length).toBeGreaterThan(20);
        return {
          status: 200,
          headers: {},
          body: JSON.stringify({
            access_token: 'supa-access-1',
            refresh_token: 'supa-refresh-1',
            expires_in: 3600,
            user: { id: 'uid-alpha', email: 'Alpha@Example.com' },
          }),
        };
      },
    });

    const authUrl = await startGoogleSignIn(bridge, 1_000_000);
    expect(openedUrls).toEqual([authUrl]);
    const parsedAuth = new URL(authUrl);
    expect(parsedAuth.pathname).toBe('/auth/v1/authorize');
    expect(parsedAuth.searchParams.get('provider')).toBe('google');
    expect(parsedAuth.searchParams.get('code_challenge_method')).toBe('s256');
    const redirectTo = new URL(parsedAuth.searchParams.get('redirect_to') || '');
    expect(`${redirectTo.protocol}//${redirectTo.host}${redirectTo.pathname}`).toBe(AUTH_REDIRECT_URL);
    const state = redirectTo.searchParams.get('s') || '';
    expect(state.length).toBeGreaterThan(10);

    // Nothing secret in localStorage
    expect(window.localStorage.length).toBe(0);
    expect(await bridge.secureStore.get(PKCE_SECRET_KEY)).not.toBeNull();

    const callbackUrl = `${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state)}&code=code-abc`;
    const res = await handleAuthDeepLink(callbackUrl, bridge, 1_005_000);
    expect(res.handled).toBe(true);
    expect(res.reason).toBe('signed_in');
    expect(res.session?.user_id).toBe('uid-alpha');
    expect(res.session?.email).toBe('alpha@example.com');
    expect(tokenCalls).toBe(1);

    // Secrets live only in secureStore, never web storage
    const dumpedStorage = JSON.stringify(window.localStorage);
    expect(dumpedStorage).not.toContain('supa-access-1');
    expect(dumpedStorage).not.toContain('supa-refresh-1');
    expect(window.localStorage.getItem(SESSION_SECRET_KEY)).toBeNull();
    expect(window.localStorage.getItem(PKCE_SECRET_KEY)).toBeNull();
    expect(await bridge.secureStore.get(SESSION_SECRET_KEY)).not.toBeNull();
    // Single-use: PKCE entry was consumed
    expect(await bridge.secureStore.get(PKCE_SECRET_KEY)).toBeNull();

    // Replaying the same callback is ignored as unsolicited
    const replay = await handleAuthDeepLink(callbackUrl, bridge, 1_006_000);
    expect(replay.handled).toBe(false);
    expect(replay.reason).toBe('unsolicited');
    expect(tokenCalls).toBe(1);
  });

  it('rejects negative PKCE cases: unsolicited, wrong state, expired, direct tokens in link', async () => {
    let networkCalled = false;
    const bridge = createPreviewNativeBridge({
      httpHandler: async () => {
        networkCalled = true;
        return { status: 200, headers: {}, body: '{}' };
      },
    });

    // Unsolicited deep link before startGoogleSignIn
    const unsol = await handleAuthDeepLink(`${AUTH_REDIRECT_URL}?s=abc&code=123`, bridge);
    expect(unsol.handled).toBe(false);
    expect(unsol.reason).toBe('unsolicited');

    // Direct tokens in fragment or query refused
    const authUrl = await startGoogleSignIn(bridge, 2_000_000);
    const state = new URL(new URL(authUrl).searchParams.get('redirect_to') || '').searchParams.get('s')!;

    const fragToken = await handleAuthDeepLink(
      `${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state)}#access_token=evil&refresh_token=evil`,
      bridge,
      2_001_000,
    );
    expect(fragToken.reason).toBe('rejected');
    expect(await getStoredSession(bridge)).toBeNull();

    const queryToken = await handleAuthDeepLink(
      `${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state)}&access_token=evil`,
      bridge,
      2_001_000,
    );
    expect(queryToken.reason).toBe('rejected');

    // Wrong state does not sign in
    const wrongState = await handleAuthDeepLink(
      `${AUTH_REDIRECT_URL}?s=wrong-state&code=code-1`,
      bridge,
      2_002_000,
    );
    expect(wrongState.reason).toBe('rejected');

    // Expired (> 10 minutes) fails and consumes PKCE
    const expired = await handleAuthDeepLink(
      `${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state)}&code=code-1`,
      bridge,
      2_000_000 + PKCE_TTL_MS + 1,
    );
    expect(expired.reason).toBe('expired');
    expect(networkCalled).toBe(false);
    expect(await bridge.secureStore.get(PKCE_SECRET_KEY)).toBeNull();
  });

  it('wipes tokens and cache on sign out, and wipes cache/foreign queue on account change', async () => {
    let nextUid = 'uid-alpha';
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.includes('/logout')) {
          return { status: 204, headers: {}, body: '' };
        }
        return {
          status: 200,
          headers: {},
          body: JSON.stringify({
            access_token: `tok-${nextUid}`,
            refresh_token: `ref-${nextUid}`,
            expires_in: 3600,
            user: { id: nextUid, email: `${nextUid}@example.com` },
          }),
        };
      },
    });

    const url1 = await startGoogleSignIn(bridge, 10_000);
    const state1 = new URL(new URL(url1).searchParams.get('redirect_to') || '').searchParams.get('s')!;
    await handleAuthDeepLink(`${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state1)}&code=c1`, bridge, 11_000);

    saveMirrorRecords('uid-alpha', [
      {
        id: 'm1',
        owner_id: 'uid-alpha',
        kind: 'content_item',
        local_id: 'c1',
        title: 'Alpha Post',
        data: { status: 'draft' },
        local_updated_at: '2026-11-22T00:00:00Z',
      },
    ]);
    enqueueInboxItem({
      kind: 'idea',
      title: 'Alpha idea',
      body: 'Body',
      ownerUid: 'uid-alpha',
      laptopDeviceId: 'pdev-1',
    });

    expect(window.localStorage.getItem(CACHE_STORAGE_KEY)).not.toBeNull();
    await signOut(bridge);
    expect(await getStoredSession(bridge)).toBeNull();
    expect(window.localStorage.getItem(CACHE_STORAGE_KEY)).toBeNull();

    // Sign in as a different account -> foreign queue items wiped
    nextUid = 'uid-beta';
    const url2 = await startGoogleSignIn(bridge, 20_000);
    const state2 = new URL(new URL(url2).searchParams.get('redirect_to') || '').searchParams.get('s')!;
    await handleAuthDeepLink(`${AUTH_REDIRECT_URL}?s=${encodeURIComponent(state2)}&code=c2`, bridge, 21_000);
    expect(loadQueue()).toHaveLength(0);
  });
});
