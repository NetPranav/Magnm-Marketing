import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const { capListenerCbs, mockGetLaunchUrl } = vi.hoisted(() => ({
  capListenerCbs: {} as Record<string, (ev: { url: string }) => void>,
  mockGetLaunchUrl: vi.fn(async () => ({ url: 'com.magnm.studio://auth/callback?s=1&code=abc' })),
}));

vi.mock('@capacitor/app', () => ({
  App: {
    getLaunchUrl: mockGetLaunchUrl,
    addListener: vi.fn(async (eventName: string, cb: (ev: { url: string }) => void) => {
      capListenerCbs[eventName] = cb;
      return { remove: async () => {} };
    }),
    minimizeApp: vi.fn(async () => {}),
  },
}));
import { App } from './App';
import {
  AGENT_MESSAGE_TIMEOUT_MS,
  PHONE_LITE_TIMEOUT_MS,
  SCREENSHOT_EXTRACT_TIMEOUT_MS,
  sendPhoneLiteMessage,
} from './api';
import {
  AUTH_REDIRECT_URL,
  getPersonalNimKey,
  getStoredSession,
  getValidAccessToken,
  handleAuthDeepLink,
  NIM_KEY_SECRET_KEY,
  PKCE_SECRET_KEY,
  SESSION_SECRET_KEY,
  startGoogleSignIn,
} from './auth';
import {
  CACHE_STORAGE_KEY,
  ensureCacheOwner,
  getCachedProductRules,
  loadCache,
  saveCachedProductRules,
  saveLaptopSnapshot,
  saveMirrorRecords,
} from './cache';
import { resolveDraftBody } from './components/DraftsTab';
import phoneContract from './fixtures/phone_contract.json';
import { ensureSession } from './test-session';
import { createCapacitorNativeBridge, createPreviewNativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, NIM_BASE_URL } from './net';
import { getLaptopToken, getPairingConfig, LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY, pollPairRequest } from './pairing';
import { enqueueInboxItem, flushBoundQueue, loadQueue, QUEUE_STORAGE_KEY } from './queue';
import { bumpStateGeneration, writeNonSecretJson } from './storage';
import { failStorageWrites } from './test-storage';

const SUPA = getPublicSupabaseConfig();

function makeDefaultHandler(overrides?: (req: { url: string; method: string; body?: string; timeoutMs?: number }) => Promise<{ status: number; headers: Record<string, string>; body: string } | null>) {
  return async (req: { url: string; method: string; body?: string; timeoutMs?: number }) => {
    if (overrides) {
      const custom = await overrides(req);
      if (custom) return custom;
    }
    if (req.url.endsWith('/phone/v1/status')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
    }
    if (req.url.includes('/phone/v1/drafts?')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.drafts_list) };
    }
    if (req.url.endsWith('/phone/v1/drafts/cnt_pub')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.draft_detail) };
    }
    if (req.url.includes('/phone/v1/reminders')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.reminders) };
    }
    if (req.url.includes('/phone/v1/notifications')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.notifications) };
    }
    if (req.url.endsWith('/phone/v1/summary')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.summary) };
    }
    if (req.url.endsWith('/phone/v1/results/pending')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.results_pending) };
    }
    if (req.url.endsWith('/phone/v1/agents')) {
      return {
        status: 200,
        headers: {},
        body: JSON.stringify({
          agents: [
            { id: 'writer', name: 'Writer' },
            { id: 'cmo', name: 'CMO' },
            { id: 'designer', name: 'Designer' },
          ],
        }),
      };
    }
    return { status: 404, headers: {}, body: '{}' };
  };
}

describe('T6.2b mobile review fixes', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('picks current version by number (not array position) in DraftsTab and mirror records', async () => {
    // DraftsTab: versions ordered descending [v2, v1] with current_version: 2 -> picks v2, NOT last element v1
    expect(
      resolveDraftBody({
        id: 'd1',
        current_version: 2,
        versions: [
          { version: 2, body: 'Version two body' },
          { version: 1, body: 'Version one body' },
        ],
      }),
    ).toBe('Version two body');

    // Also when current_version is 1 while v2 exists
    expect(
      resolveDraftBody({
        id: 'd2',
        current_version: 1,
        versions: [
          { version: 1, body: 'Version one body' },
          { version: 2, body: 'Version two body' },
        ],
      }),
    ).toBe('Version one body');

    // Mirror records: content_version [v1, v2] with current_version: 2 -> picks v2, NOT first array element v1
    saveMirrorRecords('uid-1', [
      {
        id: 'cv1',
        owner_id: 'uid-1',
        kind: 'content_version',
        local_id: 'ver_1',
        parent_local_id: 'cnt_mirror',
        title: 'v1',
        data: { version: 1, body: 'Mirror v1 old' },
        local_updated_at: '2026-11-22T12:00:00Z',
      },
      {
        id: 'cv2',
        owner_id: 'uid-1',
        kind: 'content_version',
        local_id: 'ver_2',
        parent_local_id: 'cnt_mirror',
        title: 'v2',
        data: { version: 2, body: 'Mirror v2 current' },
        local_updated_at: '2026-11-22T11:00:00Z',
      },
      {
        id: 'ci1',
        owner_id: 'uid-1',
        kind: 'content_item',
        local_id: 'cnt_mirror',
        title: 'Mirrored Post',
        data: { platform: 'linkedin', status: 'review', current_version: 2 },
        local_updated_at: '2026-11-22T10:00:00Z',
      },
    ]);

    const bridge = createPreviewNativeBridge();
    await ensureSession(bridge);
    render(<App bridge={bridge} initialTab="drafts" />);
    fireEvent.click(screen.getByText('Mirrored Post'));
    await waitFor(() => expect(screen.getByText('Mirror v2 current')).toBeTruthy());
  });

  it('supports laptop features without sign-in, never adopts unknown-owner data, and performs full wipes with confirmation', async () => {
    // Unknown-owner cache & queue entries are discarded, never adopted
    writeNonSecretJson(CACHE_STORAGE_KEY, {
      ownerUid: null,
      mirrorRecords: [
        {
          id: 'orphan-1',
          owner_id: 'uid-other',
          kind: 'content_item',
          local_id: 'c-orphan',
          title: 'Orphan Secret',
          data: {},
          local_updated_at: '2026-11-22T10:00:00Z',
          cached_at: Date.now(),
        },
      ],
      laptop: {
        status: null,
        drafts: [{ id: 'd-orphan', title: 'Orphan laptop draft' }],
        draftDetails: {},
        reminders: [],
        notifications: [],
        unreadCount: 0,
        summary: null,
        pendingResults: [],
        cachedAt: Date.now(),
      },
      productRules: {
        rules: { version: 1, show_levels: { everything: 'All' }, products: [] },
        updatedAt: null,
        cachedAt: Date.now(),
      },
    });
    writeNonSecretJson(QUEUE_STORAGE_KEY, [
      {
        client_id: 'orphan-q',
        kind: 'idea',
        title: 'Orphan queued',
        body: 'Body',
        created_at: '2026-11-22T10:00:00Z',
        owner_uid: '',
        laptop_device_id: '',
      },
    ]);

    const afterOwner = ensureCacheOwner('uid-new');
    expect(afterOwner.mirrorRecords).toHaveLength(0);
    expect(afterOwner.laptop).toBeNull();
    expect(getCachedProductRules('uid-new')).toBeNull();
    expect(loadQueue()).toHaveLength(0);

    // Pair WITHOUT sign-in -> laptop works, queues & flushes without sign-in
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_nosignin',
      deviceName: 'Pixel 9',
      ownerUid: '',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const inboxBatches: Array<Array<{ client_id: string; title: string }>> = [];
    const bridge = createPreviewNativeBridge({
      initialSharedItems: [
        { id: 'sh-keep', text: 'Shared item before signout', receivedAt: '2026-11-22T10:00:00Z' },
      ],
      httpHandler: makeDefaultHandler(async (req) => {
        if (req.url.endsWith('/phone/v1/inbox')) {
          const parsed = JSON.parse(req.body || '{}') as { items: Array<{ client_id: string; title: string }> };
          inboxBatches.push(parsed.items);
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              items: parsed.items.map((it) => ({
                client_id: it.client_id,
                kind: 'idea',
                id: `id_${it.client_id}`,
                deduplicated: false,
                warnings: [],
              })),
            }),
          };
        }
        return null;
      }),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-nosignin');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-1',
        refresh_token: 'ref-1',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );
    await bridge.secureStore.set(NIM_KEY_SECRET_KEY, 'nvapi-secret-key');
    await bridge.secureStore.set(PKCE_SECRET_KEY, '{"state":"s","verifier":"v","createdAt":1}');

    await ensureSession(bridge);
    render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByText('Laptop connected')).toBeTruthy());

    // Dismiss share modal temporarily? No, save it as idea -> first saved to queue with random id, then flushed with same id
    fireEvent.click(screen.getByRole('button', { name: 'Idea' }));
    await waitFor(() => expect(inboxBatches).toHaveLength(1));
    expect(inboxBatches[0][0].client_id).toMatch(/^mob_/);

    // Queue an item bound to deviceId for unpair & signout confirm checks
    enqueueInboxItem({
      kind: 'note',
      title: 'Unsent note 1',
      body: 'Keep me',
      laptopDeviceId: 'pdev_nosignin',
    });

    // Unpair with unsent item -> confirms first stating count
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Unpair laptop' }));
    expect(screen.getByRole('dialog').textContent).toContain('1 unsent queued item');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(getPairingConfig()).not.toBeNull();

    // Sign out confirms first stating how many unsent items will be lost, then performs full wipe
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByRole('dialog').textContent).toContain('1 unsent queued item');
    const confirmBtns = screen.getAllByRole('button', { name: 'Sign out' });
    fireEvent.click(confirmBtns[confirmBtns.length - 1]);

    await waitFor(async () => expect(await getStoredSession(bridge)).toBeNull());
    expect(await getLaptopToken(bridge)).toBeNull();
    expect(await getPersonalNimKey(bridge)).toBeNull();
    expect(await bridge.secureStore.get(PKCE_SECRET_KEY)).toBeNull();
    expect(getPairingConfig()).toBeNull();
    expect(loadCache().mirrorRecords).toHaveLength(0);
    expect(loadCache().laptop).toBeNull();
    expect(loadQueue()).toHaveLength(0);
  });

  it('wipes all data on account change and prevents cancelled pairing poll or stale async writes', async () => {
    // 1. Account change wipes pairing, device token, personal key, cache, queue BEFORE saving new session
    let nextUid = 'uid-alpha';
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.includes('/auth/v1/token?grant_type=pkce')) {
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
        }
        return { status: 404, headers: {}, body: '{}' };
      },
    });

    const url1 = await startGoogleSignIn(bridge, 10_000);
    const s1 = new URL(new URL(url1).searchParams.get('redirect_to') || '').searchParams.get('s')!;
    await handleAuthDeepLink(`${AUTH_REDIRECT_URL}?s=${encodeURIComponent(s1)}&code=c1`, bridge, 11_000);

    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'old-device-token');
    await bridge.secureStore.set(NIM_KEY_SECRET_KEY, 'nvapi-old-key');
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev-old',
      deviceName: 'Old Phone',
      ownerUid: 'uid-alpha',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    saveLaptopSnapshot('pdev-old', { drafts: [{ id: 'old-d', title: 'Old Draft' }] });
    enqueueInboxItem({ kind: 'idea', title: 'Old idea', body: 'B', ownerUid: 'uid-alpha', laptopDeviceId: 'pdev-old' });

    nextUid = 'uid-beta';
    const url2 = await startGoogleSignIn(bridge, 20_000);
    const s2 = new URL(new URL(url2).searchParams.get('redirect_to') || '').searchParams.get('s')!;
    const res2 = await handleAuthDeepLink(`${AUTH_REDIRECT_URL}?s=${encodeURIComponent(s2)}&code=c2`, bridge, 21_000);
    expect(res2.accountChanged).toBe(true);
    expect(await getLaptopToken(bridge)).toBeNull();
    expect(await getPersonalNimKey(bridge)).toBeNull();
    expect(getPairingConfig()).toBeNull();
    expect(loadCache().laptop).toBeNull();
    expect(loadQueue()).toHaveLength(0);

    // 2. Cancelled pairing poll never stores the device token
    let resolvePoll!: (resp: { status: number; headers: Record<string, string>; body: string }) => void;
    const pollBridge = createPreviewNativeBridge({
      httpHandler: () =>
        new Promise((resolve) => {
          resolvePoll = resolve;
        }),
    });
    let cancelled = false;
    const pollPromise = pollPairRequest(
      { v: 1, host: '100.64.5.10', port: 8788, code: 'c' },
      'req_1',
      'sec_1',
      '',
      pollBridge,
      () => cancelled,
    );
    cancelled = true;
    resolvePoll({
      status: 200,
      headers: {},
      body: JSON.stringify({
        status: 'approved',
        device_id: 'pdev_late',
        device_name: 'Pixel',
        device_token: 'secret-token-must-not-store',
      }),
    });
    const pollResult = await pollPromise;
    expect(pollResult.status).toBe('cancelled');
    expect(await getLaptopToken(pollBridge)).toBeNull();
    expect(getPairingConfig()).toBeNull();

    // 3. Stale async refresh started before generation bump does not write cache
    let resolveStatus!: (resp: { status: number; headers: Record<string, string>; body: string }) => void;
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_race',
      deviceName: 'Pixel',
      ownerUid: '',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    const raceBridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.endsWith('/phone/v1/status')) {
          return new Promise((resolve) => {
            resolveStatus = resolve;
          });
        }
        return makeDefaultHandler()(req);
      },
    });
    await raceBridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-race');
    await ensureSession(raceBridge);
    render(<App bridge={raceBridge} />);
    await waitFor(() => expect(resolveStatus).toBeTypeOf('function'));
    bumpStateGeneration();
    resolveStatus({ status: 200, headers: {}, body: JSON.stringify(phoneContract.status) });
    await new Promise((r) => setTimeout(r, 20));
    expect(loadCache().laptop).toBeNull();
  });

  it('enforces Phone Lite privacy: never sends laptop/failed/refused turns, sends displayed agent, restricts to writer/cmo', async () => {
    const cloudPayloads: Array<{ model: string; messages: Array<{ role: string; content: string }> }> = [];
    let laptopAlive = true;

    saveCachedProductRules('uid-1', {
      version: 1,
      show_levels: { everything: 'All', teasers_only: 'Teasers' },
      products: [
        { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
        { id: 'projectnightfall', name: 'ProjectNightfall', status: 'in_development', show: 'teasers_only', confidential: true },
      ],
    });
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_lite',
      deviceName: 'Pixel',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.includes('/rest/v1/shared_knowledge')) {
          return { status: 503, headers: {}, body: '{}' };
        }
        if (req.url.includes('/functions/v1/ai-proxy')) {
          cloudPayloads.push(JSON.parse(req.body || '{}'));
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({ choices: [{ message: { content: 'Cloud reply for Cero' } }] }),
          };
        }
        if (!laptopAlive) {
          throw new Error('Laptop unreachable');
        }
        if (req.url.endsWith('/phone/v1/agents/designer/message')) {
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              success: true,
              confidential: false,
              waiting_user: false,
              session_id: 's-lap',
              agent: { id: 'designer', name: 'Designer' },
              response: 'Laptop designer secret reply',
            }),
          };
        }
        return makeDefaultHandler()(req);
      },
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-1');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-1',
        refresh_token: 'ref-1',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );

    // sendPhoneLiteMessage talks to configured agents only
    await expect(
      sendPhoneLiteMessage({ agentId: 'nobody', text: 'Write about Cero', bridge }),
    ).rejects.toThrow(/configured agent/i);

    await ensureSession(bridge);
    render(<App bridge={bridge} initialTab="chat" />);
    await waitFor(() => expect(screen.getByText('Laptop connected')).toBeTruthy());

    // Select Designer while laptop is online and send a laptop turn
    fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'designer' } });
    fireEvent.change(screen.getByLabelText('Message or voice brief'), {
      target: { value: 'Laptop only prompt' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('Laptop designer secret reply')).toBeTruthy());

    // Laptop goes offline -> action network error switches UI to Phone Lite, where every agent (Designer too) stays available
    laptopAlive = false;
    fireEvent.change(screen.getByLabelText('Message or voice brief'), {
      target: { value: 'Tell me about ProjectNightfall' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('Phone Lite (offline)')).toBeTruthy());
    expect((screen.getByLabelText('Agent') as HTMLSelectElement).value).toBe('designer');

    // Now send a refused message in Phone Lite (keeps typed text, does not block next turn)
    fireEvent.change(screen.getByLabelText('Message or voice brief'), {
      target: { value: 'Tell me about ProjectNightfall' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('ProjectNightfall'));
    expect((screen.getByLabelText('Message or voice brief') as HTMLTextAreaElement).value).toBe(
      'Tell me about ProjectNightfall',
    );

    // Next valid Phone Lite turn succeeds AND cloud history contains NO laptop turns and NO refused turns
    fireEvent.change(screen.getByLabelText('Message or voice brief'), {
      target: { value: 'Write a post about Cero' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('Cloud reply for Cero')).toBeTruthy());
    expect(cloudPayloads).toHaveLength(1);
    const dumped = JSON.stringify(cloudPayloads[0]);
    expect(dumped).not.toContain('Laptop designer secret reply');
    expect(dumped).not.toContain('Laptop only prompt');
    expect(dumped).not.toContain('ProjectNightfall');
    expect((screen.getByLabelText('Message or voice brief') as HTMLTextAreaElement).value).toBe('');
  });

  it('handles queue serialization, 400 split recovery, >50 drain, storage write errors, and invalid metrics', async () => {
    // 1. Storage write failure keeps typed composer text and shows error
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_q',
      deviceName: 'Pixel',
      ownerUid: '',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    const setItemSpy = failStorageWrites();
    const failBridge = createPreviewNativeBridge({
      httpHandler: async () => {
        throw new Error('Offline');
      },
    });
    await ensureSession(failBridge);
    render(<App bridge={failBridge} initialTab="drafts" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'New item' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'New item' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Important Draft' } });
    fireEvent.change(screen.getByLabelText('Text'), { target: { value: 'Do not lose me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue offline' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('QuotaExceededError'));
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Important Draft');
    expect((screen.getByLabelText('Text') as HTMLTextAreaElement).value).toBe('Do not lose me');
    setItemSpy.mockRestore();
    cleanup();
    window.localStorage.clear();

    // 2. Drain > 50, one flush at a time, and 400 batch splits item-by-item keeping bad one
    for (let i = 0; i < 62; i += 1) {
      enqueueInboxItem({
        kind: 'idea',
        title: i === 5 ? 'BAD_ITEM' : `Idea ${i}`,
        body: `Body ${i}`,
        laptopDeviceId: 'pdev_drain',
        clientId: `cid_${i}`,
      });
    }

    let inFlight = 0;
    let maxConcurrent = 0;
    const batchSizes: number[] = [];
    const sendBatch = async (items: Array<{ client_id: string; title: string }>) => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      batchSizes.push(items.length);
      await new Promise((r) => setTimeout(r, 5));
      inFlight -= 1;
      if (items.some((it) => it.title === 'BAD_ITEM')) {
        throw new HttpStatusError('Bad item title', 400, '{}');
      }
      return {
        items: items.map((it) => ({ client_id: it.client_id, kind: 'idea', id: `ok_${it.client_id}` })),
      };
    };

    const [r1, r2] = await Promise.all([
      flushBoundQueue('pdev_drain', sendBatch),
      flushBoundQueue('pdev_drain', sendBatch),
    ]);
    expect(maxConcurrent).toBe(1);
    expect(r1.acknowledgedIds.length + r2.acknowledgedIds.length).toBe(61);
    expect(batchSizes[0]).toBe(50);
    const remaining = loadQueue();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].client_id).toBe('cid_5');
    expect(remaining[0].last_error).toContain('Bad item title');

    // 3. Invalid metric input is an error, never silently dropped
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_m',
      deviceName: 'Pixel',
      ownerUid: '',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    let confirmCalled = false;
    const metricBridge = createPreviewNativeBridge({
      httpHandler: makeDefaultHandler(async (req) => {
        if (req.url.includes('/confirm')) {
          confirmCalled = true;
        }
        return null;
      }),
    });
    await metricBridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-m');
    await ensureSession(metricBridge);
    render(<App bridge={metricBridge} initialTab="today" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review & save' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Review & save' }));
    fireEvent.change(screen.getByLabelText('views'), { target: { value: 'not-a-number' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Invalid value for "views"'));
    expect(confirmCalled).toBe(false);
  });

  it('handles launchUrl once, resume refresh, no duplicate start refresh, secondary failure tolerance, timeouts, token refresh lock, and Android back', async () => {
    // 1. Timeouts on agent message (180s), screenshot read (120s), ai-proxy (120s), NIM (120s)
    const timeouts: Record<string, number | undefined> = {};
    saveCachedProductRules('uid-1', {
      version: 1,
      show_levels: { everything: 'All' },
      products: [{ id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false }],
    });
    const tBridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        timeouts[req.url] = req.timeoutMs;
        if (req.url.includes('/rest/v1/shared_knowledge')) {
          return { status: 503, headers: {}, body: '{}' };
        }
        return {
          status: 200,
          headers: {},
          body: JSON.stringify({ choices: [{ message: { content: 'ok' } }] }),
        };
      },
    });
    await tBridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-1',
        refresh_token: 'ref-1',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );
    await sendPhoneLiteMessage({ agentId: 'writer', text: 'Cero post', bridge: tBridge });
    expect(timeouts[`${SUPA.url}/functions/v1/ai-proxy`]).toBe(PHONE_LITE_TIMEOUT_MS);
    expect(PHONE_LITE_TIMEOUT_MS).toBe(120_000);

    await tBridge.secureStore.set(NIM_KEY_SECRET_KEY, 'nvapi-k');
    await sendPhoneLiteMessage({ agentId: 'writer', text: 'Cero post', bridge: tBridge });
    expect(timeouts[`${NIM_BASE_URL}/chat/completions`]).toBe(120_000);
    expect(AGENT_MESSAGE_TIMEOUT_MS).toBe(180_000);
    expect(SCREENSHOT_EXTRACT_TIMEOUT_MS).toBe(120_000);

    // 2. One token refresh at a time
    let refreshCalls = 0;
    const refreshBridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.includes('/auth/v1/token?grant_type=refresh_token')) {
          refreshCalls += 1;
          await new Promise((r) => setTimeout(r, 15));
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              access_token: 'fresh-jwt',
              refresh_token: 'fresh-ref',
              expires_in: 3600,
              user: { id: 'uid-1', email: 'u1@example.com' },
            }),
          };
        }
        return { status: 404, headers: {}, body: '{}' };
      },
    });
    await refreshBridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'old-jwt',
        refresh_token: 'old-ref',
        expires_at: 100,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );
    const [tok1, tok2] = await Promise.all([
      getValidAccessToken(refreshBridge, 1_000_000),
      getValidAccessToken(refreshBridge, 1_000_000),
    ]);
    expect(tok1).toBe('fresh-jwt');
    expect(tok2).toBe('fresh-jwt');
    expect(refreshCalls).toBe(1);

    // 3. Startup single refresh, secondary request failure keeps laptop online, resume refreshes laptop + shared items, launchUrl once, Android back order
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_life',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    let statusCalls = 0;
    let pkceExchanges = 0;
    const appBridge = createPreviewNativeBridge({
      initialLaunchUrl: `${AUTH_REDIRECT_URL}?s=st_launch&code=code_launch`,
      httpHandler: makeDefaultHandler(async (req) => {
        if (req.url.endsWith('/phone/v1/status')) {
          statusCalls += 1;
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
        }
        if (req.url.endsWith('/phone/v1/summary')) {
          // Secondary request fails with 500 -> must NOT mark laptop offline
          return { status: 500, headers: {}, body: JSON.stringify({ error: 'Summary error' }) };
        }
        if (req.url.includes('/auth/v1/token?grant_type=pkce')) {
          pkceExchanges += 1;
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              access_token: 'launch-jwt',
              refresh_token: 'launch-ref',
              expires_in: 3600,
              user: { id: 'uid-1', email: 'u1@example.com' },
            }),
          };
        }
        if (req.url.includes('/rest/v1/')) {
          return { status: 200, headers: {}, body: '[]' };
        }
        return null;
      }),
    });
    await appBridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-life');
    await appBridge.secureStore.set(
      PKCE_SECRET_KEY,
      JSON.stringify({ state: 'st_launch', verifier: 'verifier-abc', createdAt: Date.now() }),
    );
    await appBridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-1',
        refresh_token: 'ref-1',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );

    await ensureSession(appBridge);
    render(<App bridge={appBridge} />);
    await waitFor(() => expect(screen.getByText('Laptop connected')).toBeTruthy());
    // Wait briefly to confirm no duplicate startup refresh
    await new Promise((r) => setTimeout(r, 30));
    expect(statusCalls).toBe(1);
    // Re-emitting same launch URL is ignored (handled once per URL)
    appBridge.emitDeepLink(`${AUTH_REDIRECT_URL}?s=st_launch&code=code_launch`);
    await new Promise((r) => setTimeout(r, 20));
    expect(pkceExchanges).toBe(1);

    // Resume refreshes laptop AND shared items
    appBridge.pushSharedItem({ id: 'sh-resume', text: 'Shared on resume', receivedAt: '2026-11-22T12:00:00Z' });
    appBridge.emitResume();
    await waitFor(() => expect(statusCalls).toBe(2));
    await waitFor(() => expect(screen.getByText('Shared into Magnm')).toBeTruthy());

    // Android back: 1) closes open ShareInModal dialog, 2) closes open draft detail, 3) goes to Today, 4) minimizes
    appBridge.emitBackButton();
    await waitFor(() => expect(screen.queryByText('Shared into Magnm')).toBeNull());

    fireEvent.click(screen.getByRole('tab', { name: /Drafts/i }));
    await waitFor(() => expect(screen.getByText('Public Cero Launch')).toBeTruthy());
    fireEvent.click(screen.getByText('Public Cero Launch'));
    await waitFor(() => expect(screen.getByText(/Cero v2 release is live/)).toBeTruthy());

    appBridge.emitBackButton();
    await waitFor(() => expect(screen.queryByText(/Cero v2 release is live/)).toBeNull());
    expect(screen.getByRole('tab', { name: /Drafts/i }).getAttribute('aria-selected')).toBe('true');

    appBridge.emitBackButton();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Today/i }).getAttribute('aria-selected')).toBe('true'),
    );
    expect(appBridge.getMinimizeCount()).toBe(0);

    appBridge.emitBackButton();
    expect(appBridge.getMinimizeCount()).toBe(1);
  });

  it('Capacitor native bridge delivers getLaunchUrl once and deduplicates appUrlOpen', async () => {
    const capBridge = createCapacitorNativeBridge();
    const received: string[] = [];
    const unsub = capBridge.onDeepLink((url) => received.push(url));
    await new Promise((r) => setTimeout(r, 10));
    capListenerCbs['appUrlOpen']?.({ url: 'com.magnm.studio://auth/callback?s=1&code=abc' });
    expect(received).toEqual(['com.magnm.studio://auth/callback?s=1&code=abc']);
    expect(mockGetLaunchUrl).toHaveBeenCalledTimes(1);
    unsub();
  });
});
