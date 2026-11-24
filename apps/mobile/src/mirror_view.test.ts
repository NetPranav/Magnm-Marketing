import { beforeEach, describe, expect, it } from 'vitest';
import { fetchOwnMirrorRecords } from './api';
import { SESSION_SECRET_KEY } from './auth';
import { loadCache, saveLaptopSnapshot, saveMirrorRecords } from './cache';
import { draftsFromCache, mirrorIsNewer, remindersFromCache } from './mirrorView';
import { createPreviewNativeBridge } from './native';
import { dumpStorage } from './test-storage';

const UID = 'uid-alpha';

function rec(kind: string, local_id: string, title: string, data: Record<string, unknown>, parent: string | null = null, synced = '2026-11-23T10:00:00Z') {
  return {
    id: `m_${local_id}`,
    owner_id: UID,
    kind,
    local_id,
    parent_local_id: parent,
    title,
    data,
    local_updated_at: synced,
    synced_at: synced,
  };
}

describe('what the phone shows from the cloud mirror', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('builds comment threads, open reminders and confidential stubs without any text', () => {
    saveMirrorRecords(UID, [
      rec('content_item', 'cnt_1', 'Launch', { status: 'review', current_version: 1, posted_url: 'https://example.com/x' }),
      rec('review_comment', 'c1', 'x', { body: 'Root comment', author: 'reviewer', parent_id: null, created_at: '2026-11-23T09:00:00Z' }, 'cnt_1'),
      rec('review_comment', 'c2', 'x', { body: 'A reply', author: 'phone:cloud', parent_id: 'c1', created_at: '2026-11-23T09:30:00Z' }, 'cnt_1'),
      rec('review_comment', 'c3', 'Confidential item', { confidential: true, body: 'SECRET BODY' }, 'cnt_1'),
      rec('content_item', 'cnt_2', 'Confidential item', { confidential: true, status: 'draft' }),
      rec('task', 'rem_1', 'Open reminder', { kind: 'reminder', note: 'n', due_at: '2026-11-24T10:00:00Z', done_at: null }),
      rec('task', 'rem_2', 'Finished reminder', { kind: 'reminder', due_at: '2026-11-24T10:00:00Z', done_at: '2026-11-23T10:00:00Z' }),
      rec('task', 'rem_3', 'Confidential item', { kind: 'reminder', confidential: true, title: 'SECRET TITLE', note: 'SECRET NOTE', due_at: '2026-11-25T10:00:00Z' }),
      rec('task', 'job_1', 'A background job', { status: 'done' }),
    ]);
    const cache = loadCache();
    const drafts = draftsFromCache(cache);
    const launch = drafts.find((d) => d.id === 'cnt_1');
    expect(launch?.posted_url).toBe('https://example.com/x');
    expect(launch?.comments?.map((c) => c.body)).toEqual(['Root comment', '']);
    expect(launch?.comments?.[0].replies?.map((r) => r.body)).toEqual(['A reply']);
    expect(launch?.comments?.[1].confidential).toBe(true);
    const stub = drafts.find((d) => d.id === 'cnt_2');
    expect(stub).toMatchObject({ confidential: true, title: 'Confidential item', comments: [], versions: [] });

    const reminders = remindersFromCache(cache);
    expect(reminders.map((r) => r.id)).toEqual(['rem_1', 'rem_3']);
    expect(reminders[1]).toMatchObject({ confidential: true, title: 'Confidential reminder', note: '' });
    expect(dumpStorage()).not.toContain('SECRET');
  });

  it('uses the live copy from the laptop unless the cloud copy is newer', () => {
    saveMirrorRecords(UID, [
      rec('content_item', 'cnt_cloud', 'From the cloud', { status: 'draft' }, null, '2026-11-23T12:00:00Z'),
      rec('task', 'rem_cloud', 'Cloud reminder', { kind: 'reminder', due_at: '2026-11-24T10:00:00Z', done_at: null }, null, '2026-11-23T12:00:00Z'),
    ]);
    saveLaptopSnapshot('pdev_1', {
      drafts: [{ id: 'cnt_live', title: 'From the laptop' }],
      reminders: [{ id: 'rem_live', title: 'Live reminder' }],
    }, Date.parse('2026-11-23T11:00:00Z'));
    let cache = loadCache(Date.parse('2026-11-23T11:30:00Z'));
    expect(mirrorIsNewer(cache)).toBe(true);
    expect(draftsFromCache(cache).map((d) => d.id)).toEqual(['cnt_cloud']);
    expect(remindersFromCache(cache).map((r) => r.id)).toEqual(['rem_cloud']);

    saveLaptopSnapshot('pdev_1', {
      drafts: [{ id: 'cnt_live', title: 'From the laptop' }],
      reminders: [{ id: 'rem_live', title: 'Live reminder' }],
    }, Date.parse('2026-11-23T13:00:00Z'));
    cache = loadCache(Date.parse('2026-11-23T13:30:00Z'));
    expect(mirrorIsNewer(cache)).toBe(false);
    expect(draftsFromCache(cache).map((d) => d.id)).toEqual(['cnt_live']);
    expect(remindersFromCache(cache).map((r) => r.id)).toEqual(['rem_live']);
  });
});

describe('mirror requests', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('ask for each needed kind separately, always for the user\'s own rows, and keep one copy of each record', async () => {
    const urls: string[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        urls.push(req.url);
        return { status: 200, headers: {}, body: JSON.stringify([rec('content_item', 'cnt_1', 'Launch', { status: 'draft' })]) };
      },
    });
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
    );
    const records = await fetchOwnMirrorRecords(UID, bridge);
    expect(urls).toHaveLength(11);
    expect(urls.every((u) => u.includes(`owner_id=eq.${UID}`) && u.includes('deleted_at=is.null'))).toBe(true);
    for (const kind of ['content_item', 'content_version', 'review_comment', 'task', 'pitch_proposal', 'asset', 'schedule', 'editorial_plan', 'brief', 'board_item', 'automation']) {
      expect(urls.some((u) => u.includes(`kind=eq.${kind}`))).toBe(true);
    }
    expect(urls.some((u) => u.includes('done_at=is.null'))).toBe(true);
    expect(records).toHaveLength(1);
  });

  it('keeps what arrived when one of the requests fails, and fails only when all do', async () => {
    let calls = 0;
    const bridge = createPreviewNativeBridge({
      httpHandler: async () => {
        calls += 1;
        if (calls === 2) return { status: 500, headers: {}, body: '{}' };
        return { status: 200, headers: {}, body: JSON.stringify([rec('content_item', `cnt_${calls}`, 'Launch', { status: 'draft' })]) };
      },
    });
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
    );
    expect((await fetchOwnMirrorRecords(UID, bridge)).length).toBe(10);
    const down = createPreviewNativeBridge({ httpHandler: async () => ({ status: 500, headers: {}, body: '{}' }) });
    await down.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
    );
    await expect(fetchOwnMirrorRecords(UID, down)).rejects.toBeDefined();
  });

  const session = JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' });

  function seed() {
    saveMirrorRecords(UID, [
      rec('content_item', 'cnt_old', 'Saved draft', { status: 'draft' }),
      rec('content_version', 'ver_old', 'v1', { version: 1, body: 'Saved body' }, 'cnt_old'),
      rec('review_comment', 'cmt_old', 'x', { body: 'Saved comment', author: 'reviewer' }, 'cnt_old'),
      rec('task', 'rem_old', 'Saved reminder', { kind: 'reminder', due_at: '2026-11-24T10:00:00Z', done_at: null }),
    ]);
  }

  async function fetchWith(answer: (kind: string) => 'fail' | 'empty' | unknown[], uid = UID) {
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        const kind = ['content_item', 'content_version', 'review_comment', 'task'].find((k) => req.url.includes(`kind=eq.${k}`)) ?? '';
        const a = answer(kind);
        if (a === 'fail') return { status: 500, headers: {}, body: '{}' };
        return { status: 200, headers: {}, body: JSON.stringify(a === 'empty' ? [] : a) };
      },
    });
    await bridge.secureStore.set(SESSION_SECRET_KEY, session);
    await fetchOwnMirrorRecords(uid, bridge);
  }

  it('keeps the saved records of a kind whose request failed, and clears a kind whose request answered empty', async () => {
    seed();
    await fetchWith((kind) => {
      if (kind === 'content_item') return [rec('content_item', 'cnt_new', 'Fresh draft', { status: 'draft' })];
      if (kind === 'review_comment') return 'fail';
      if (kind === 'task') return 'empty'; // every reminder was finished: really empty
      return 'fail';
    });
    const ids = loadCache().mirrorRecords.map((r) => r.local_id).sort();
    expect(ids).toEqual(['cmt_old', 'cnt_new', 'ver_old']);
  });

  it('keeps everything saved when every request fails (the call itself fails)', async () => {
    seed();
    await expect(fetchWith(() => 'fail')).rejects.toBeDefined();
    expect(loadCache().mirrorRecords).toHaveLength(4);
  });

  it('never carries another account\'s saved records into this account', async () => {
    saveMirrorRecords('uid-beta', [
      { ...rec('review_comment', 'beta_cmt', 'x', { body: 'Beta secret' }, 'cnt_b'), owner_id: 'uid-beta' },
    ]);
    await fetchWith((kind) => (kind === 'content_item' ? [rec('content_item', 'cnt_new', 'Fresh', { status: 'draft' })] : 'fail'));
    const cache = loadCache();
    expect(cache.ownerUid).toBe(UID);
    expect(cache.mirrorRecords.map((r) => r.local_id)).toEqual(['cnt_new']);
    expect(dumpStorage()).not.toContain('Beta secret');
  });

  it('does not make old records look fresh by keeping them', async () => {
    seed();
    const before = loadCache().mirrorRecords.find((r) => r.local_id === 'cmt_old')!.cached_at;
    await new Promise((r) => setTimeout(r, 5));
    await fetchWith((kind) => (kind === 'review_comment' ? 'fail' : 'empty'));
    expect(loadCache().mirrorRecords.find((r) => r.local_id === 'cmt_old')!.cached_at).toBe(before);
  });
});
