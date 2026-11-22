import { beforeEach, describe, expect, it } from 'vitest';
import { fetchOwnMirrorRecords } from './api';
import { PKCE_SECRET_KEY, SESSION_SECRET_KEY } from './auth';
import {
  CACHE_TTL_MS,
  formatSavedCopyLabel,
  loadCache,
  MAX_MIRROR_RECORDS,
  saveMirrorRecords,
  wipeCache,
} from './cache';
import { createPreviewNativeBridge } from './native';
import {
  enqueueInboxItem,
  flushBoundQueue,
  getBoundQueueItems,
  loadQueue,
  removeQueuedItem,
  wipeQueue,
} from './queue';
import { writeNonSecretJson } from './storage';

describe('context cache and offline queue', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('always filters mirror_records by owner_id=eq.<uid> and keeps confidential stubs as stubs', async () => {
    let requestedUrl = '';
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        requestedUrl = req.url;
        return {
          status: 200,
          headers: {},
          body: JSON.stringify([
            {
              id: 'r1',
              owner_id: 'uid-admin',
              kind: 'content_item',
              local_id: 'draft-1',
              title: 'Public Cero Post',
              data: { status: 'draft', body: 'Hello Cero' },
              local_updated_at: '2026-11-22T10:00:00Z',
            },
            {
              id: 'r2',
              owner_id: 'uid-admin',
              kind: 'chat_session',
              local_id: 'sess-secret',
              title: 'Secret title that should stay stub',
              data: { confidential: true, messages: [{ role: 'user', text: 'leak' }] },
              local_updated_at: '2026-11-22T11:00:00Z',
            },
            {
              id: 'r3',
              owner_id: 'uid-other-member',
              kind: 'content_item',
              local_id: 'other-draft',
              title: 'Teammate Draft',
              data: { status: 'draft' },
              local_updated_at: '2026-11-22T12:00:00Z',
            },
          ]),
        };
      },
    });

    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-admin',
        refresh_token: 'ref-admin',
        expires_at: 9_999_999_999,
        user_id: 'uid-admin',
        email: 'admin@example.com',
      }),
    );

    await fetchOwnMirrorRecords('uid-admin', bridge);
    expect(requestedUrl).toContain('owner_id=eq.uid-admin');

    const cache = loadCache();
    expect(cache.mirrorRecords).toHaveLength(2);
    expect(cache.mirrorRecords.every((r) => r.owner_id === 'uid-admin')).toBe(true);
    const conf = cache.mirrorRecords.find((r) => r.local_id === 'sess-secret');
    expect(conf?.title).toBe('Confidential chat');
    expect(conf?.data.confidential).toBe(true);
    expect(conf?.data.messages).toBeUndefined();
  });

  it('caps mirror_records at 200 and drops entries older than 14 days', () => {
    const now = 1_800_000_000_000;
    const records = Array.from({ length: 240 }, (_, i) => ({
      id: `id-${i}`,
      owner_id: 'uid-1',
      kind: 'content_item',
      local_id: `loc-${i}`,
      title: `Post ${i}`,
      data: { idx: i },
      local_updated_at: `2026-11-${String((i % 28) + 1).padStart(2, '0')}T00:${String(i % 60).padStart(2, '0')}:00Z`,
    }));

    const saved = saveMirrorRecords('uid-1', records, now);
    expect(saved.mirrorRecords).toHaveLength(MAX_MIRROR_RECORDS);
    expect(formatSavedCopyLabel(now)).toContain('Saved copy from ');

    // Within 14 days -> retained
    expect(loadCache(now + CACHE_TTL_MS - 1000).mirrorRecords).toHaveLength(MAX_MIRROR_RECORDS);
    // Past 14 days -> dropped
    expect(loadCache(now + CACHE_TTL_MS + 1000).mirrorRecords).toHaveLength(0);

    wipeCache();
    expect(loadCache(now).mirrorRecords).toHaveLength(0);
  });

  it('persists offline queue, removes only acknowledged client_ids, and refuses secret keys in web storage', async () => {
    expect(() => writeNonSecretJson('my_access_token', { x: 1 })).toThrow(/secret/i);
    expect(() => writeNonSecretJson(PKCE_SECRET_KEY, { x: 1 })).toThrow(/secret/i);

    const a = enqueueInboxItem({
      kind: 'idea',
      title: 'Idea A',
      body: 'Body A',
      ownerUid: 'uid-1',
      laptopDeviceId: 'pdev-1',
      clientId: 'cid-a',
    });
    const b = enqueueInboxItem({
      kind: 'note',
      title: 'Note B',
      body: 'Body B',
      ownerUid: 'uid-1',
      laptopDeviceId: 'pdev-1',
      clientId: 'cid-b',
    });

    // Idempotent duplicate client_id
    enqueueInboxItem({
      kind: 'idea',
      title: 'Idea A',
      body: 'Body A',
      ownerUid: 'uid-1',
      laptopDeviceId: 'pdev-1',
      clientId: 'cid-a',
    });
    expect(getBoundQueueItems('pdev-1')).toHaveLength(2);

    // Survives reload (reading from localStorage)
    expect(loadQueue().map((i) => i.client_id)).toEqual([a.client_id, b.client_id]);

    // Failure leaves both in queue
    await expect(
      flushBoundQueue('pdev-1', async () => {
        throw new Error('Network unreachable');
      }),
    ).rejects.toThrow('Network unreachable');
    expect(loadQueue()).toHaveLength(2);

    // Partial ack removes ONLY cid-a; cid-b stays until acknowledged
    const res = await flushBoundQueue('pdev-1', async () => ({
      items: [{ client_id: 'cid-a', kind: 'idea', id: 'note-1', deduplicated: false }],
    }));
    expect(res.acknowledgedIds).toEqual(['cid-a']);
    expect(loadQueue().map((i) => i.client_id)).toEqual(['cid-b']);

    removeQueuedItem('cid-b');
    expect(loadQueue()).toHaveLength(0);

    enqueueInboxItem({
      kind: 'draft',
      title: 'Draft C',
      body: 'Body C',
      ownerUid: 'uid-1',
      laptopDeviceId: 'pdev-1',
    });
    wipeQueue();
    expect(loadQueue()).toHaveLength(0);
  });
});
