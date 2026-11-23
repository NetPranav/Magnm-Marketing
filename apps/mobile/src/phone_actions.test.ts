import { beforeEach, describe, expect, it } from 'vitest';
import {
  ACTIONS_STORAGE_KEY,
  PhoneActionError,
  enqueueAction,
  loadActions,
  removeAction,
  validateActionInput,
} from './actions';
import { SESSION_SECRET_KEY, signOut } from './auth';
import fixture from './fixtures/phone_action_bodies.json';
import { refreshActionStatuses, sendWaitingActions } from './cloudActions';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';
import { bumpStateGeneration, writeNonSecretJson } from './storage';
import { dumpStorage } from './test-storage';
import type { PhoneAction, ProductRulesConfig } from './types';

const SUPA = getPublicSupabaseConfig();
const UID = 'uid-alpha';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};

type Http = (req: NativeHttpRequest) => Promise<NativeHttpResponse>;

async function bridgeWith(httpHandler: Http, uid = UID) {
  const bridge = createPreviewNativeBridge({ httpHandler });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({
      access_token: `jwt-${uid}`,
      refresh_token: 'ref',
      expires_at: 9_999_999_999,
      user_id: uid,
      email: `${uid}@example.com`,
    }),
  );
  return bridge;
}

function comment(body = 'Tighten the opening.', extra: Partial<Parameters<typeof enqueueAction>[0]> = {}) {
  return enqueueAction(
    { kind: 'draft_comment', target_id: 'cnt_1', payload: { body, version: 2 }, label: 'Launch post', ...extra },
    UID,
    RULES,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('queueing a phone action', () => {
  it('queues each enabled kind as waiting, bound to the account, with a random uuid', () => {
    const a = comment();
    const b = enqueueAction({ kind: 'draft_posted', target_id: 'cnt_1', payload: { url: 'https://example.com/p/1' } }, UID, RULES);
    const c = enqueueAction({ kind: 'reminder_done', target_id: 'rem_1', payload: {} }, UID, RULES);
    for (const action of [a, b, c]) {
      expect(action.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      expect(action.owner_uid).toBe(UID);
      expect(action.state).toBe('waiting');
      expect(action.sent).toBe(false);
    }
    expect(new Set([a.id, b.id, c.id]).size).toBe(3);
    expect(loadActions(UID).map((x) => x.kind)).toEqual(['draft_comment', 'draft_posted', 'reminder_done']);
  });

  it('refuses unknown kinds and malformed details', () => {
    const bad: Array<Parameters<typeof validateActionInput>[0]> = [
      { kind: 'run_agent' as never, target_id: 'writer', payload: { body: 'x' } },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: {} },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: { body: '   ' } },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'x'.repeat(4001) } },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'ok', version: 0 } },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'ok', version: 1.5 } },
      { kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'ok', extra: 1 } as never },
      { kind: 'draft_comment', target_id: '', payload: { body: 'ok' } },
      { kind: 'draft_comment', target_id: 'x'.repeat(201), payload: { body: 'ok' } },
      { kind: 'draft_comment', target_id: 'cnt\n1', payload: { body: 'ok' } },
      { kind: 'draft_posted', target_id: 'cnt_1', payload: { url: 'javascript:alert(1)' } },
      { kind: 'draft_posted', target_id: 'cnt_1', payload: { url: 'ftp://example.com/x' } },
      { kind: 'draft_posted', target_id: 'cnt_1', payload: { url: 'https://exa mple.com' } },
      { kind: 'draft_posted', target_id: 'cnt_1', payload: { url: `https://example.com/${'x'.repeat(2100)}` } },
      { kind: 'reminder_done', target_id: 'rem_1', payload: { body: 'x' } },
    ];
    for (const input of bad) {
      expect(() => validateActionInput(input), JSON.stringify(input).slice(0, 60)).toThrow(PhoneActionError);
      expect(() => enqueueAction(input, UID, RULES)).toThrow(PhoneActionError);
    }
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('never queues text that names a confidential product, or any text when the rules are unknown', () => {
    expect(() => comment('Mention ProjectNightfall here')).toThrow(/confidential/i);
    expect(() =>
      enqueueAction({ kind: 'draft_posted', target_id: 'cnt_1', payload: { url: 'https://example.com/projectnightfall' } }, UID, RULES),
    ).toThrow(/confidential/i);
    expect(() => enqueueAction({ kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'Fine text' } }, UID, null)).toThrow(
      /rules/i,
    );
    expect(() => comment('Fine text', { targetConfidential: true })).toThrow(/confidential/i);
    // No text, no check needed.
    expect(enqueueAction({ kind: 'reminder_done', target_id: 'rem_1', payload: {} }, UID, null).state).toBe('waiting');
    expect(dumpStorage()).not.toContain('ProjectNightfall');
  });

  it('needs a signed-in account and never mixes accounts', () => {
    expect(() => enqueueAction({ kind: 'reminder_done', target_id: 'rem_1', payload: {} }, '', RULES)).toThrow(PhoneActionError);
    comment();
    expect(loadActions('uid-beta')).toHaveLength(0);
    expect(loadActions(null)).toHaveLength(0);
    expect(loadActions('')).toHaveLength(0);
    // Another account queues: the first account's items are gone, not adopted.
    enqueueAction({ kind: 'reminder_done', target_id: 'rem_9', payload: {} }, 'uid-beta', RULES);
    expect(loadActions(UID)).toHaveLength(0);
    expect(loadActions('uid-beta')).toHaveLength(1);
  });

  it('discards stored items that fail the checks instead of trusting them', () => {
    const good = comment();
    const stored = JSON.parse(window.localStorage.getItem(ACTIONS_STORAGE_KEY) || '[]') as PhoneAction[];
    writeNonSecretJson(ACTIONS_STORAGE_KEY, [
      ...stored,
      { ...good, id: 'not-a-uuid' },
      { ...good, id: '11111111-1111-4111-8111-111111111111', payload: { body: 'x'.repeat(5000) } },
      { ...good, id: '22222222-2222-4222-8222-222222222222', kind: 'run_agent' },
      { ...good, id: '33333333-3333-4333-8333-333333333333', owner_uid: 'uid-beta' },
      'junk',
      null,
    ]);
    expect(loadActions(UID).map((x) => x.id)).toEqual([good.id]);
  });

  it('removes one item and keeps no secret in web storage', () => {
    const a = comment();
    const b = comment('Second note');
    removeAction(a.id, UID);
    expect(loadActions(UID).map((x) => x.id)).toEqual([b.id]);
    expect(dumpStorage()).not.toMatch(/jwt|access_token|refresh/i);
  });

  it('is wiped by sign-out', async () => {
    comment();
    const bridge = await bridgeWith(async () => ({ status: 204, headers: {}, body: '' }));
    await signOut(bridge);
    expect(window.localStorage.getItem(ACTIONS_STORAGE_KEY)).toBeNull();
    expect(loadActions(UID)).toHaveLength(0);
  });
});

describe('sending to the cloud', () => {
  it('posts only what the phone decides, idempotently by id, and marks it sent', async () => {
    const seen: Array<{ url: string; method: string; headers: Record<string, string>; body: unknown }> = [];
    const bridge = await bridgeWith(async (req) => {
      seen.push({ url: req.url, method: req.method, headers: req.headers ?? {}, body: JSON.parse(req.body || 'null') });
      return { status: 201, headers: {}, body: '' };
    });
    const a = comment();
    await sendWaitingActions(UID, bridge);
    expect(seen).toHaveLength(1);
    expect(seen[0].method).toBe('POST');
    expect(seen[0].url).toBe(`${SUPA.url}/rest/v1/phone_actions?on_conflict=id`);
    expect(seen[0].headers['Prefer']).toBe('resolution=ignore-duplicates,return=minimal');
    expect(seen[0].headers['Authorization']).toBe(`Bearer jwt-${UID}`);
    expect(seen[0].body).toEqual({ id: a.id, kind: 'draft_comment', target_id: 'cnt_1', payload: { body: 'Tighten the opening.', version: 2 } });
    expect(loadActions(UID)[0].sent).toBe(true);
    await sendWaitingActions(UID, bridge);
    expect(seen).toHaveLength(1);
  });

  it('keeps an action locally when there is no internet and sends it later with the same id', async () => {
    let online = false;
    const ids: string[] = [];
    const bridge = await bridgeWith(async (req) => {
      if (!online) throw new Error('Network request failed');
      ids.push((JSON.parse(req.body || '{}') as { id: string }).id);
      return { status: 201, headers: {}, body: '' };
    });
    const a = comment();
    await expect(sendWaitingActions(UID, bridge)).resolves.toBeDefined();
    expect(loadActions(UID)).toHaveLength(1);
    expect(loadActions(UID)[0]).toMatchObject({ sent: false, state: 'waiting' });
    online = true;
    await sendWaitingActions(UID, bridge);
    await sendWaitingActions(UID, bridge);
    expect(ids).toEqual([a.id]);
  });

  it('shows the reason when the cloud refuses an action, and keeps it waiting on server trouble or sign-in trouble', async () => {
    const answers: Array<{ status: number; message: string }> = [
      { status: 400, message: 'Too many waiting phone actions (limit 200)' },
      { status: 403, message: 'new row violates row-level security policy' },
      { status: 503, message: 'down' },
      { status: 401, message: 'JWT expired' },
    ];
    let i = 0;
    const bridge = await bridgeWith(async () => {
      const a = answers[i++];
      return { status: a.status, headers: {}, body: JSON.stringify({ message: a.message }) };
    });
    const first = comment('one');
    const second = comment('two');
    const third = comment('three');
    const fourth = comment('four');
    await sendWaitingActions(UID, bridge);
    const byId = Object.fromEntries(loadActions(UID).map((x) => [x.id, x]));
    expect(byId[first.id]).toMatchObject({ state: 'rejected', sent: false });
    expect(byId[first.id].result).toMatch(/Too many waiting/);
    expect(byId[second.id]).toMatchObject({ state: 'rejected' });
    expect(byId[third.id]).toMatchObject({ state: 'waiting', sent: false });
    expect(byId[fourth.id]).toMatchObject({ state: 'waiting', sent: false });
  });

  it('never sends another account\'s items or anything while another account is signed in', async () => {
    const urls: string[] = [];
    const bridge = await bridgeWith(async (req) => {
      urls.push(req.url);
      return { status: 201, headers: {}, body: '' };
    }, 'uid-beta');
    comment();
    await sendWaitingActions(UID, bridge); // signed in as beta, items belong to alpha
    await sendWaitingActions('uid-beta', bridge);
    expect(urls).toEqual([]);
    expect(loadActions(UID)).toHaveLength(1);
  });

  it('re-checks stored items before sending: a tampered item is dropped, never sent', async () => {
    const urls: string[] = [];
    const bridge = await bridgeWith(async (req) => {
      urls.push(req.url);
      return { status: 201, headers: {}, body: '' };
    });
    const a = comment();
    const stored = JSON.parse(window.localStorage.getItem(ACTIONS_STORAGE_KEY) || '[]') as PhoneAction[];
    stored[0] = { ...stored[0], payload: { body: 'x'.repeat(5000) } };
    writeNonSecretJson(ACTIONS_STORAGE_KEY, stored);
    await sendWaitingActions(UID, bridge);
    expect(urls).toEqual([]);
    expect(loadActions(UID).find((x) => x.id === a.id)).toBeUndefined();
  });

  it('does nothing when signed out', async () => {
    const urls: string[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        urls.push(req.url);
        return { status: 201, headers: {}, body: '' };
      },
    });
    comment();
    await sendWaitingActions(UID, bridge);
    expect(urls).toEqual([]);
  });
});

describe('showing what happened', () => {
  async function sentAction() {
    let rows: Array<Record<string, unknown>> = [];
    const bridge = await bridgeWith(async (req) => {
      if (req.method === 'POST') return { status: 201, headers: {}, body: '' };
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    });
    const a = comment();
    await sendWaitingActions(UID, bridge);
    return { bridge, a, setRows: (r: Array<Record<string, unknown>>) => (rows = r) };
  }

  it('asks only for the signed-in user\'s own rows and maps the laptop\'s answer', async () => {
    const urls: string[] = [];
    let rows: Array<Record<string, unknown>> = [];
    const bridge = await bridgeWith(async (req) => {
      if (req.method === 'POST') return { status: 201, headers: {}, body: '' };
      urls.push(req.url);
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    });
    const a = comment();
    const b = comment('second');
    await sendWaitingActions(UID, bridge);

    rows = [
      { id: a.id, owner_id: UID, status: 'applying', result: null, expires_at: '2999-01-01T00:00:00Z' },
      { id: b.id, owner_id: UID, status: 'queued', result: null, expires_at: '2999-01-01T00:00:00Z' },
    ];
    await refreshActionStatuses(UID, bridge);
    expect(urls[0]).toContain(`owner_id=eq.${UID}`);
    expect(loadActions(UID).map((x) => x.state)).toEqual(['waiting', 'waiting']);

    rows = [
      { id: a.id, owner_id: UID, status: 'applied', result: 'Comment added.', expires_at: '2999-01-01T00:00:00Z' },
      { id: b.id, owner_id: UID, status: 'rejected', result: 'Not applied: it was not found on this laptop.', expires_at: '2999-01-01T00:00:00Z' },
    ];
    await refreshActionStatuses(UID, bridge);
    const now = Object.fromEntries(loadActions(UID).map((x) => [x.id, x]));
    expect(now[a.id]).toMatchObject({ state: 'applied', result: 'Comment added.' });
    expect(now[b.id]).toMatchObject({ state: 'rejected', result: 'Not applied: it was not found on this laptop.' });
  });

  it('shows an action that sat past its expiry as not applied', async () => {
    const { bridge, a, setRows } = await sentAction();
    setRows([{ id: a.id, owner_id: UID, status: 'queued', result: null, expires_at: '2000-01-01T00:00:00Z' }]);
    await refreshActionStatuses(UID, bridge);
    expect(loadActions(UID)[0]).toMatchObject({ state: 'rejected' });
    expect(loadActions(UID)[0].result).toMatch(/expired/i);
  });

  it('ignores rows of other owners and rows it did not send, and never lets the cloud rewrite what was queued', async () => {
    const { bridge, a, setRows } = await sentAction();
    setRows([
      { id: a.id, owner_id: 'uid-beta', status: 'applied', result: 'forged' },
      { id: '99999999-9999-4999-8999-999999999999', owner_id: UID, status: 'applied', result: 'unknown to this phone' },
      { id: a.id, owner_id: UID, status: 'applied', result: 'x'.repeat(5000), kind: 'run_agent', payload: { body: 'rewritten' } },
    ]);
    await refreshActionStatuses(UID, bridge);
    const items = loadActions(UID);
    expect(items).toHaveLength(1);
    expect(items[0].payload).toEqual({ body: 'Tighten the opening.', version: 2 });
    expect(items[0].kind).toBe('draft_comment');
    expect((items[0].result ?? '').length).toBeLessThanOrEqual(300);
  });

  it('does not take another owner\'s row for its own, even with the same id (an admin can read every row)', async () => {
    const { bridge, a, setRows } = await sentAction();
    setRows([{ id: a.id, owner_id: 'uid-beta', status: 'applied', result: 'forged by the cloud' }]);
    await refreshActionStatuses(UID, bridge);
    expect(loadActions(UID)[0]).toMatchObject({ id: a.id, state: 'waiting', result: null });
  });

  it('tolerates a hostile answer and does nothing offline', async () => {
    const { bridge, a } = await sentAction();
    const hostile = await bridgeWith(async () => ({ status: 200, headers: {}, body: '{"not":"a list"}' }));
    await refreshActionStatuses(UID, hostile);
    const offline = await bridgeWith(async () => {
      throw new Error('Network request failed');
    });
    await refreshActionStatuses(UID, offline);
    expect(loadActions(UID)[0]).toMatchObject({ id: a.id, state: 'waiting' });
    expect(bridge).toBeDefined();
  });

  it('writes nothing after the account was wiped while the request was running', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let rows: Array<Record<string, unknown>> = [];
    const bridge = await bridgeWith(async (req) => {
      if (req.method === 'POST') return { status: 201, headers: {}, body: '' };
      await gate;
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    });
    const a = comment();
    await sendWaitingActions(UID, bridge);
    rows = [{ id: a.id, owner_id: UID, status: 'applied', result: 'Comment added.', expires_at: '2999-01-01T00:00:00Z' }];
    const pending = refreshActionStatuses(UID, bridge);
    window.localStorage.removeItem(ACTIONS_STORAGE_KEY);
    bumpStateGeneration();
    release();
    await pending;
    expect(window.localStorage.getItem(ACTIONS_STORAGE_KEY)).toBeNull();
  });
});

describe('contract with the laptop', () => {
  it('sends exactly the bodies in the shared fixture, which the laptop tests apply', async () => {
    const seen: Array<Record<string, unknown>> = [];
    const bridge = await bridgeWith(async (req) => {
      seen.push(JSON.parse(req.body || '{}'));
      return { status: 201, headers: {}, body: '' };
    });
    for (const item of fixture.actions) {
      const b = item.body as { id: string; kind: PhoneAction['kind']; target_id: string; payload: PhoneAction['payload'] };
      enqueueAction({ kind: b.kind, target_id: b.target_id, payload: b.payload }, UID, RULES, b.id);
    }
    await sendWaitingActions(UID, bridge);
    expect(seen).toEqual(fixture.actions.map((a) => a.body));
  });
});
