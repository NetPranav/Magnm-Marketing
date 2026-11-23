import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import baseline from '@magnm/config/product_rules.json';
import { ACTIONS_STORAGE_KEY, loadActions } from './actions';
import { App } from './App';
import { SESSION_SECRET_KEY } from './auth';
import { getCachedProductRules, saveCachedProductRules } from './cache';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { writeNonSecretJson } from './storage';

const SUPA = getPublicSupabaseConfig();
const UID = 'uid-alpha';
const RULES = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released' as const, show: 'everything' as const, confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret' as const, show: 'nothing' as const, confidential: true },
  ],
};

function mirror(kind: string, local_id: string, title: string, data: Record<string, unknown>, parent: string | null = null) {
  return {
    id: `m_${kind}_${local_id}`,
    owner_id: UID,
    kind,
    local_id,
    parent_local_id: parent,
    title,
    data,
    local_updated_at: '2026-11-23T10:00:00Z',
    synced_at: '2026-11-23T10:00:05Z',
  };
}

const MIRROR_ROWS = [
  mirror('content_item', 'cnt_pub', 'Cero launch post', { platform: 'linkedin', status: 'review', current_version: 1 }),
  mirror('content_version', 'ver_1', 'v1', { version: 1, body: 'Cero is out today.' }, 'cnt_pub'),
  mirror('content_item', 'cnt_conf', 'Confidential item', { confidential: true, platform: 'x', status: 'draft' }),
  mirror('review_comment', 'cmt_1', 'Check the hook', { version: 1, kind: 'general', body: 'Check the hook', author: 'reviewer', status: 'open', parent_id: null }, 'cnt_pub'),
  mirror('review_comment', 'cmt_2', 'Agreed', { version: 1, kind: 'general', body: 'Agreed, fix it', author: 'writer', status: 'open', parent_id: 'cmt_1' }, 'cnt_pub'),
  mirror('task', 'rem_pub', 'Post the Cero update', { kind: 'reminder', reminder_kind: 'custom', note: 'before noon', due_at: '2026-11-24T10:00:00Z', done_at: null }, 'cnt_pub'),
  mirror('task', 'rem_conf', 'Confidential item', { kind: 'reminder', confidential: true, reminder_kind: 'custom', due_at: '2026-11-24T10:00:00Z', done_at: null }, 'cnt_conf'),
];

interface Cloud {
  posts: Array<{ url: string; headers: Record<string, string>; body: Record<string, unknown> }>;
  mirrorUrls: string[];
  laptopUrls: string[];
  actionRows: Array<Record<string, unknown>>;
  internet: boolean;
  laptop: boolean;
  rulesRows: Array<Record<string, unknown>> | null;
  rulesCalls: number;
  rulesFail: boolean;
}

function makeCloud(): Cloud {
  return { posts: [], mirrorUrls: [], laptopUrls: [], actionRows: [], internet: true, laptop: false, rulesRows: null, rulesCalls: 0, rulesFail: false };
}

function json(body: unknown, status = 200): NativeHttpResponse {
  return { status, headers: {}, body: JSON.stringify(body) };
}

function handler(cloud: Cloud) {
  return async (req: NativeHttpRequest): Promise<NativeHttpResponse> => {
    if (req.url.startsWith('http://100.')) {
      cloud.laptopUrls.push(`${req.method} ${req.url}`);
      if (!cloud.laptop) throw new Error('Network request failed');
      if (req.url.endsWith('/phone/v1/status')) {
        return json({ ok: true, version: '0.1.0', running_agents: [], running_jobs: [], unread_notifications: 0 });
      }
      if (req.url.endsWith('/comments')) {
        return json({ comment: { id: 'cmt_live', body: 'live', author: 'phone', replies: [] } });
      }
      return json({}, 404);
    }
    if (!cloud.internet) throw new Error('Network request failed');
    if (req.url.includes('/rest/v1/profiles')) {
      return json([{ id: UID, email: 'alpha@example.com', display_name: 'Alpha', avatar_url: null, role: 'member', active: true }]);
    }
    if (req.url.includes('/rest/v1/shared_knowledge')) {
      cloud.rulesCalls += 1;
      if (cloud.rulesFail) return json({ message: 'down' }, 503);
      return json(cloud.rulesRows ?? [{ key: 'product_rules', data: RULES, updated_at: null }]);
    }
    if (req.url.includes('/rest/v1/mirror_records')) {
      cloud.mirrorUrls.push(req.url);
      return json(MIRROR_ROWS);
    }
    if (req.url.includes('/rest/v1/phone_actions')) {
      if (req.method === 'POST') {
        cloud.posts.push({ url: req.url, headers: req.headers ?? {}, body: JSON.parse(req.body || '{}') });
        return { status: 201, headers: {}, body: '' };
      }
      return json(cloud.actionRows);
    }
    return json({}, 404);
  };
}

async function start(cloud: Cloud, options: { signedIn?: boolean; paired?: boolean; rules?: boolean } = {}) {
  const { signedIn = true, paired = false, rules = true } = options;
  if (paired) {
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10', port: 8788, deviceId: 'pdev_1', deviceName: 'Pixel', ownerUid: UID, pairedAt: '2026-11-22T09:00:00Z',
    });
  }
  const bridge = createPreviewNativeBridge({ httpHandler: handler(cloud) });
  if (paired) await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-laptop');
  if (signedIn) {
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt-alpha', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: UID, email: 'alpha@example.com' }),
    );
    if (rules) saveCachedProductRules(UID, RULES);
  }
  render(<App bridge={bridge} />);
  return bridge;
}

async function openDraft(title = 'Cero launch post') {
  fireEvent.click(screen.getByRole('tab', { name: /Drafts/i }));
  fireEvent.click(await screen.findByText(title));
  await screen.findByRole('button', { name: 'Add comment' });
}

describe('phone actions without the laptop', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    cleanup();
  });

  it('shows reminders, drafts and comments from the cloud mirror on open, with no earlier laptop connection and no Sync click', async () => {
    const cloud = makeCloud();
    await start(cloud);
    expect(await screen.findByText('Post the Cero update')).toBeTruthy();
    expect(cloud.mirrorUrls.length).toBeGreaterThan(0);
    expect(cloud.mirrorUrls.every((u) => u.includes(`owner_id=eq.${UID}`))).toBe(true);
    // The confidential reminder is a stub with no Done button; only the public one can be finished.
    expect(screen.getAllByRole('button', { name: 'Done' })).toHaveLength(1);
    expect(screen.getByText('Confidential reminder')).toBeTruthy();

    await openDraft();
    expect(screen.getByText('Check the hook')).toBeTruthy();
    expect(screen.getByText('Agreed, fix it')).toBeTruthy();
  });

  it('refreshes the mirror by itself when the app comes back to the foreground', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await screen.findByText('Post the Cero update');
    const before = cloud.mirrorUrls.length;
    await act(async () => {
      bridge.emitResume();
    });
    await waitFor(() => expect(cloud.mirrorUrls.length).toBeGreaterThan(before));
  });

  it('queues a comment when the laptop is unreachable, shows it waiting at once, and shows Applied when the laptop did it', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud, { paired: true });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Tighten the opening.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));

    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    expect(cloud.posts[0].url).toBe(`${SUPA.url}/rest/v1/phone_actions?on_conflict=id`);
    expect(cloud.posts[0].body).toMatchObject({ kind: 'draft_comment', target_id: 'cnt_pub', payload: { body: 'Tighten the opening.', version: 1 } });
    expect(Object.keys(cloud.posts[0].body).sort()).toEqual(['id', 'kind', 'payload', 'target_id']);
    expect(await screen.findAllByText('Waiting for laptop')).not.toHaveLength(0);
    expect(screen.getByText('Tighten the opening.')).toBeTruthy();
    expect((screen.getByLabelText('Add comment') as HTMLTextAreaElement).value).toBe('');
    expect(cloud.laptopUrls.some((u) => u.includes('/comments'))).toBe(false);

    cloud.actionRows = [{ id: cloud.posts[0].body.id, owner_id: UID, status: 'applied', result: 'Comment added.', expires_at: '2999-01-01T00:00:00Z' }];
    await act(async () => {
      bridge.emitResume();
    });
    expect(await screen.findAllByText('Applied')).not.toHaveLength(0);
  });

  it('queues Mark as posted the same way, with the link', async () => {
    const cloud = makeCloud();
    await start(cloud, { paired: true });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Posted link (optional)'), { target: { value: 'https://example.com/p/1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mark as posted' }));
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    expect(cloud.posts[0].body).toMatchObject({ kind: 'draft_posted', target_id: 'cnt_pub', payload: { url: 'https://example.com/p/1' } });
    expect((await screen.findAllByText('Waiting for laptop')).length).toBeGreaterThan(0);
  });

  it('finishes a reminder through the queue and drops it once the laptop applied it', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await screen.findByText('Post the Cero update');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    expect(cloud.posts[0].body).toMatchObject({ kind: 'reminder_done', target_id: 'rem_pub', payload: {} });
    expect((await screen.findAllByText('Waiting for laptop')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();

    cloud.actionRows = [{ id: cloud.posts[0].body.id, owner_id: UID, status: 'applied', result: 'Reminder done.', expires_at: '2999-01-01T00:00:00Z' }];
    await act(async () => {
      bridge.emitResume();
    });
    await waitFor(() => expect(screen.queryByText('Post the Cero update')).toBeNull());
  });

  it('keeps the direct path when the laptop is reachable', async () => {
    const cloud = makeCloud();
    cloud.laptop = true;
    await start(cloud, { paired: true });
    await waitFor(() => expect(screen.getByText('Laptop connected')).toBeTruthy());
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Direct comment' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    await waitFor(() => expect(cloud.laptopUrls.some((u) => u.endsWith('/comments'))).toBe(true));
    expect(cloud.posts).toHaveLength(0);
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('with no internet keeps the action on the phone and sends it later by itself, once, with the same id', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud, { paired: true });
    await openDraft();
    cloud.internet = false;
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Written in a tunnel' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    expect((await screen.findAllByText('Waiting for laptop')).length).toBeGreaterThan(0);
    expect(loadActions(UID)).toMatchObject([{ sent: false, state: 'waiting' }]);
    const id = loadActions(UID)[0].id;

    cloud.internet = true;
    await act(async () => {
      bridge.emitResume();
    });
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    expect(cloud.posts[0].body.id).toBe(id);
    await act(async () => {
      bridge.emitResume();
    });
    expect(cloud.posts).toHaveLength(1);
    await waitFor(() => expect(loadActions(UID)[0].sent).toBe(true));
  });

  it('cannot queue without a sign-in, and says so', async () => {
    const cloud = makeCloud();
    await start(cloud, { signedIn: false, paired: true });
    fireEvent.click(screen.getByRole('tab', { name: /Drafts/i }));
    // No mirror without an account and no laptop: nothing to open, and nothing can be queued.
    expect(cloud.posts).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Add comment' })).toBeNull();
  });

  it('refuses text that names a confidential product, and sends nothing', async () => {
    const cloud = makeCloud();
    await start(cloud, { paired: true });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Mention ProjectNightfall here' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/confidential/i);
    expect(cloud.posts).toHaveLength(0);
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('gives a confidential draft no actions at all', async () => {
    const cloud = makeCloud();
    await start(cloud, { paired: true });
    fireEvent.click(screen.getByRole('tab', { name: /Drafts/i }));
    fireEvent.click(await screen.findByText('Confidential item'));
    await screen.findByText(/Confidential drafts stay stubs on mobile/);
    expect(screen.queryByRole('button', { name: 'Add comment' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark as posted' })).toBeNull();
  });

  it('never shows or sends another account\'s queued actions', async () => {
    const cloud = makeCloud();
    writeNonSecretJson(ACTIONS_STORAGE_KEY, [
      {
        id: '44444444-4444-4444-8444-444444444444', kind: 'draft_comment', target_id: 'cnt_pub',
        payload: { body: 'Secret note of the other account' }, label: 'Other draft', created_at: '2026-11-23T09:00:00Z',
        owner_uid: 'uid-beta', sent: false, state: 'waiting', result: null,
      },
    ]);
    await start(cloud);
    await screen.findByText('Post the Cero update');
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    expect(screen.queryByText(/Secret note of the other account/)).toBeNull();
    expect(screen.queryByText('Other draft')).toBeNull();
    expect(cloud.posts).toHaveLength(0);
  });

  it('lists every action in More with its state and the laptop\'s reason', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud, { paired: true });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'A note for later' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    cloud.actionRows = [
      { id: cloud.posts[0].body.id, owner_id: UID, status: 'rejected', result: 'Not applied: it was not found on this laptop.', expires_at: '2999-01-01T00:00:00Z' },
    ];
    await act(async () => {
      bridge.emitResume();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Back to drafts' }));
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    expect(await screen.findByText('Phone actions')).toBeTruthy();
    expect((await screen.findAllByText(/Not applied/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/not found on this laptop/)).toBeTruthy();
  });

  it('works when the team never published product rules: the bundled baseline guards the queue', async () => {
    const cloud = makeCloud();
    cloud.rulesRows = []; // zero rows in shared_knowledge, as with the real data
    await start(cloud, { paired: true, rules: false });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'A harmless note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    await waitFor(() => expect(cloud.posts).toHaveLength(1));

    const secret = (baseline.products as Array<{ name: string; confidential?: boolean }>).find((p) => p.confidential)!.name;
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: `About ${secret} again` } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/confidential/i);
    expect(cloud.posts).toHaveLength(1);
  });

  it('queues a posted link when no rules were ever published', async () => {
    const cloud = makeCloud();
    cloud.rulesRows = [];
    await start(cloud, { paired: true, rules: false });
    await openDraft();
    fireEvent.change(screen.getByLabelText('Posted link (optional)'), { target: { value: 'https://example.com/p/9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mark as posted' }));
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
  });

  it('refreshes the shared rules on open and on foreground even when a saved copy exists', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud, { paired: true });
    await waitFor(() => expect(cloud.rulesCalls).toBeGreaterThan(0));
    const afterOpen = cloud.rulesCalls;
    await act(async () => {
      bridge.emitResume();
    });
    await waitFor(() => expect(cloud.rulesCalls).toBeGreaterThan(afterOpen));
  });

  it('keeps the saved rules when the refresh fails, and shared rules never remove a baseline confidential product', async () => {
    const cloud = makeCloud();
    cloud.rulesFail = true;
    await start(cloud, { paired: true });
    await waitFor(() => expect(cloud.rulesCalls).toBeGreaterThan(0));
    expect(getCachedProductRules(UID)?.products.some((p) => p.id === 'secretproj')).toBe(true);
    await openDraft();
    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Mention ProjectNightfall here' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/confidential/i);
    expect(cloud.posts).toHaveLength(0);
  });
});
