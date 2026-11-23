import { beforeEach, describe, expect, it } from 'vitest';
import {
  AdminError,
  changeMemberActive,
  changeMemberRole,
  derivePresence,
  fetchActivity,
  fetchAdminTeam,
  fetchAiOverview,
  fetchAudit,
  fetchMemberPitch,
  fetchMemberPreviews,
  fetchMemberRecords,
  fetchRecordDetail,
  fetchStorage,
  inviteMember,
  resetAiLimit,
  revokeInvite,
  saveAiLimits,
} from './adminApi';
import { SESSION_SECRET_KEY } from './auth';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';

const SUPA = getPublicSupabaseConfig();
const ADMIN = '11111111-1111-4111-8111-111111111111';
const OTHER_ADMIN = '22222222-2222-4222-8222-222222222222';
const MEMBER = '33333333-3333-4333-8333-333333333333';

interface Seen {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

type Answer = (req: NativeHttpRequest, seen: Seen) => NativeHttpResponse | undefined;

async function bridgeWith(answer: Answer, seen: Seen[] = []) {
  const bridge = createPreviewNativeBridge({
    httpHandler: async (req) => {
      const entry: Seen = { method: req.method, url: req.url, headers: req.headers ?? {}, body: req.body ? JSON.parse(req.body) : null };
      seen.push(entry);
      return answer(req, entry) ?? { status: 200, headers: {}, body: '[]' };
    },
  });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: 'jwt-admin', refresh_token: 'r', expires_at: 9_999_999_999, user_id: ADMIN, email: 'admin@example.com' }),
  );
  return { bridge, seen };
}

const ok = (body: unknown, status = 200): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });
const NOW = Date.parse('2026-11-23T12:00:00Z');
const iso = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000).toISOString();

const PROFILES = [
  { id: ADMIN, email: 'admin@example.com', display_name: 'Admin', avatar_url: null, role: 'admin', active: true, created_at: iso(9000), last_seen_at: iso(1) },
  { id: OTHER_ADMIN, email: 'two@example.com', display_name: null, avatar_url: null, role: 'admin', active: true, created_at: iso(8000), last_seen_at: null },
  { id: MEMBER, email: 'member@example.com', display_name: 'Member', avatar_url: null, role: 'member', active: true, created_at: iso(7000), last_seen_at: null },
];

beforeEach(() => {
  window.localStorage.clear();
});

describe('who is online', () => {
  it('derives presence from device heartbeats like the web admin', () => {
    const dev = (status: string, ago: number) => ({ status, last_seen_at: iso(ago) });
    expect(derivePresence([dev('working', 1)], true, NOW)).toBe('working');
    expect(derivePresence([dev('online', 2)], true, NOW)).toBe('online');
    expect(derivePresence([dev('online', 10)], true, NOW)).toBe('idle');
    expect(derivePresence([dev('online', 120)], true, NOW)).toBe('offline');
    expect(derivePresence([dev('offline', 1)], true, NOW)).toBe('offline');
    expect(derivePresence([dev('working', 1)], false, NOW)).toBe('offline');
    expect(derivePresence([dev('idle', 1), dev('working', 1)], true, NOW)).toBe('working');
    expect(derivePresence([{ status: 'online', last_seen_at: 'nonsense' }, null as never], true, NOW)).toBe('offline');
  });
});

describe('Team', () => {
  it('lists members with presence, invites and devices using only reads', async () => {
    const { bridge, seen } = await bridgeWith((req) => {
      if (req.url.includes('/rest/v1/profiles')) return ok(PROFILES);
      if (req.url.includes('/rest/v1/device_status')) {
        return ok([{ owner_id: MEMBER, device_id: 'd1', device_kind: 'android', status: 'online', running_agents: ['writer'], app_version: '0.1', last_seen_at: iso(1) }]);
      }
      if (req.url.includes('/rest/v1/activity_events')) return ok([{ owner_id: MEMBER, ts: iso(5) }]);
      if (req.url.includes('/rest/v1/invites')) return ok([{ email: 'new@example.com', role: 'member', created_at: iso(10), accepted_at: null, revoked_at: null }]);
      return undefined;
    });
    const team = await fetchAdminTeam(ADMIN, bridge, NOW);
    expect(team.members.map((m) => m.id)).toEqual([ADMIN, OTHER_ADMIN, MEMBER]);
    const member = team.members.find((m) => m.id === MEMBER)!;
    expect(member.presence).toBe('online');
    expect(member.running_agents).toEqual(['writer']);
    expect(member.devices[0].device_kind).toBe('android');
    expect(team.invites).toHaveLength(1);
    expect(team.members.find((m) => m.id === OTHER_ADMIN)?.name).toBe('two@example.com');
    expect(seen.every((s) => s.method === 'GET' && s.url.startsWith(`${SUPA.url}/rest/v1/`))).toBe(true);
    expect(seen.every((s) => s.headers['Authorization'] === 'Bearer jwt-admin')).toBe(true);
  });

  it('keeps junk rows out and shows "Admins only" when the database says no', async () => {
    const junk = await bridgeWith((req) => (req.url.includes('/profiles') ? ok([...PROFILES, 'x', null, { id: 'not-a-uuid', role: 'admin' }, { id: MEMBER, role: 'owner' }]) : ok([])));
    const team = await fetchAdminTeam(ADMIN, junk.bridge, NOW);
    expect(team.members.map((m) => m.id)).toEqual([ADMIN, OTHER_ADMIN, MEMBER]);
    const denied = await bridgeWith(() => ok({ message: 'permission denied' }, 403));
    await expect(fetchAdminTeam(ADMIN, denied.bridge, NOW)).rejects.toThrow(/admins only/i);
    // A member's RLS answers an empty list for invites: that is not a crash and shows no invites.
    const empty = await bridgeWith((req) => (req.url.includes('/profiles') ? ok([PROFILES[2]]) : ok([])));
    expect((await fetchAdminTeam(MEMBER, empty.bridge, NOW)).invites).toEqual([]);
  });

  it('invites by email with a role, checking both first', async () => {
    const { bridge, seen } = await bridgeWith(() => ok([{ email: 'new@example.com', role: 'member' }], 201));
    await inviteMember('  New@Example.com ', 'member', bridge);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: 'POST', url: `${SUPA.url}/rest/v1/invites`, body: { email: 'new@example.com', role: 'member' } });
    expect(seen[0].headers['Prefer']).toBe('return=representation');
    for (const bad of ['', 'nobody', 'a@b', 'a b@c.d', 'x@y.z\nBcc: evil@e.com', `${'a'.repeat(300)}@example.com`]) {
      await expect(inviteMember(bad, 'member', bridge), bad).rejects.toThrow(AdminError);
    }
    await expect(inviteMember('ok@example.com', 'owner' as never, bridge)).rejects.toThrow(AdminError);
    expect(seen).toHaveLength(1);
    const dup = await bridgeWith(() => ok({ message: 'duplicate key value violates unique constraint' }, 409));
    await expect(inviteMember('new@example.com', 'member', dup.bridge)).rejects.toThrow(/already exists/i);
    const denied = await bridgeWith(() => ok({ message: 'new row violates row-level security policy' }, 403));
    await expect(inviteMember('new@example.com', 'admin', denied.bridge)).rejects.toThrow(/admins only/i);
  });

  it('revokes an open invite', async () => {
    const { bridge, seen } = await bridgeWith(() => ok([{ email: 'new@example.com' }]));
    await revokeInvite('New@Example.com', bridge);
    expect(seen[0].method).toBe('PATCH');
    expect(seen[0].url).toBe(`${SUPA.url}/rest/v1/invites?email=eq.new%40example.com&revoked_at=is.null`);
    expect(Object.keys(seen[0].body as object)).toEqual(['revoked_at']);
    await expect(revokeInvite('bad', bridge)).rejects.toThrow(AdminError);
  });

  it('changes a role or active state of another member only, and never removes the last admin', async () => {
    const { bridge, seen } = await bridgeWith(() => ok([{ id: MEMBER, role: 'admin' }]));
    await changeMemberRole(MEMBER, 'admin', ADMIN, PROFILES.map((p) => ({ id: p.id, role: p.role as 'admin' | 'member', active: p.active })), bridge);
    expect(seen[0]).toMatchObject({ method: 'PATCH', url: `${SUPA.url}/rest/v1/profiles?id=eq.${MEMBER}`, body: { role: 'admin' } });
    await changeMemberActive(MEMBER, false, ADMIN, [], bridge);
    expect(seen[1]).toMatchObject({ body: { active: false } });
    const before = seen.length;

    const onlyAdmin = [
      { id: ADMIN, role: 'admin' as const, active: true },
      { id: OTHER_ADMIN, role: 'admin' as const, active: false },
      { id: MEMBER, role: 'member' as const, active: true },
    ];
    await expect(changeMemberRole(ADMIN, 'member', ADMIN, onlyAdmin, bridge)).rejects.toThrow(/own/i);
    // Demoting or deactivating the one active admin from "another" admin session is refused before any request.
    await expect(changeMemberRole(OTHER_ADMIN, 'member', ADMIN, [{ id: OTHER_ADMIN, role: 'admin', active: true }, { id: ADMIN, role: 'admin', active: false }], bridge)).rejects.toThrow(/last admin/i);
    await expect(changeMemberActive(OTHER_ADMIN, false, ADMIN, [{ id: OTHER_ADMIN, role: 'admin', active: true }, { id: ADMIN, role: 'admin', active: false }], bridge)).rejects.toThrow(/last admin/i);
    await expect(changeMemberRole('nope', 'member', ADMIN, onlyAdmin, bridge)).rejects.toThrow(AdminError);
    await expect(changeMemberRole(MEMBER, 'root' as never, ADMIN, onlyAdmin, bridge)).rejects.toThrow(AdminError);
    await expect(changeMemberActive(MEMBER, 'no' as never, ADMIN, onlyAdmin, bridge)).rejects.toThrow(AdminError);
    expect(seen).toHaveLength(before);

    // The database has the last word and says it plainly.
    const guarded = await bridgeWith(() => ok({ message: 'Cannot demote or deactivate the last active admin' }, 400));
    await expect(changeMemberRole(OTHER_ADMIN, 'member', ADMIN, [], guarded.bridge)).rejects.toThrow(/last admin/i);
    const none = await bridgeWith(() => ok([]));
    await expect(changeMemberActive(MEMBER, true, ADMIN, [], none.bridge)).rejects.toThrow(/not found|admins only/i);
  });
});

describe('Activity, Workspace, Pitch, Storage, Audit', () => {
  it('reads activity newest first with names, and refuses unsafe filters', async () => {
    const { bridge, seen } = await bridgeWith((req) => {
      if (req.url.includes('/rest/v1/profiles')) return ok(PROFILES);
      if (req.url.includes('/rest/v1/activity_events')) return ok([{ id: 9, owner_id: MEMBER, ts: iso(3), kind: 'review', agent_id: 'writer', summary: 'Reviewed', data: { a: 1 } }, 'junk']);
      return undefined;
    });
    const res = await fetchActivity({ member: MEMBER, kind: 'review', before: iso(1), limit: 500 }, bridge);
    expect(res.events).toHaveLength(1);
    expect(res.events[0]).toMatchObject({ member_name: 'Member', kind: 'review', summary: 'Reviewed' });
    const url = seen.find((s) => s.url.includes('/activity_events'))!.url;
    expect(url).toContain(`owner_id=eq.${MEMBER}`);
    expect(url).toContain('kind=eq.review');
    expect(url).toContain('limit=200');
    expect(url).toContain('order=ts.desc,id.desc');
    for (const bad of [{ member: 'x' }, { kind: '../x' }, { kind: 'a&b=c' }, { before: 'now(),(x)' }, { before: '2026-11-23T00:00:00Z,id=eq.1' }]) {
      await expect(fetchActivity(bad, bridge), JSON.stringify(bad)).rejects.toThrow(AdminError);
    }
  });

  it('lists a member\'s records without their data, escapes the search and opens one record', async () => {
    const { bridge, seen } = await bridgeWith((req) => {
      if (req.url.includes('parent_local_id=eq.')) return ok([{ id: 'c1', kind: 'content_version', local_id: 'v1', title: 'v1', local_updated_at: iso(1), synced_at: iso(1) }]);
      if (req.url.includes('local_id=eq.')) return ok([{ id: 'r1', kind: 'content_item', local_id: 'cnt_1', title: 'Post', data: { body: 'hello' }, local_updated_at: iso(1), synced_at: iso(1) }]);
      return ok([{ id: 'r1', kind: 'content_item', local_id: 'cnt_1', parent_local_id: null, title: 'Post', local_updated_at: iso(1), synced_at: iso(1), data: { leak: true } }]);
    });
    const list = await fetchMemberRecords(MEMBER, { kind: 'content_item', q: '100%_(x),y*' }, bridge);
    expect(list.records[0].title).toBe('Post');
    expect(JSON.stringify(list.records)).not.toContain('leak');
    const listUrl = seen[0].url;
    expect(listUrl).toContain(`owner_id=eq.${MEMBER}`);
    expect(listUrl).toContain('deleted_at=is.null');
    expect(listUrl).not.toContain('data');
    expect(listUrl).toContain('title=ilike.*100%5C%25%5C_%5C(x%5C)%5C%2Cy%5C**');
    const detail = await fetchRecordDetail(MEMBER, 'content_item', 'cnt_1', bridge);
    expect(detail.record.data).toEqual({ body: 'hello' });
    expect(detail.children).toHaveLength(1);
    await expect(fetchMemberRecords('x', {}, bridge)).rejects.toThrow(AdminError);
    await expect(fetchRecordDetail(MEMBER, 'content_item', '../x', bridge)).rejects.toThrow(AdminError);
    await expect(fetchRecordDetail(MEMBER, 'a b', 'x', bridge)).rejects.toThrow(AdminError);
  });

  it('lists previews as metadata only and never asks storage for the image', async () => {
    const { bridge, seen } = await bridgeWith(() =>
      ok([
        { id: 'p1', owner_id: MEMBER, kind: 'thumbnail', storage_path: `${MEMBER}/aaa.webp`, content_type: 'image/webp', bytes: 1200, created_at: iso(1), expires_at: null, parent_local_id: 'cnt_1' },
        { id: 'p2', owner_id: MEMBER, kind: 'thumbnail', storage_path: `${OTHER_ADMIN}/bbb.webp`, content_type: 'image/webp', bytes: 1 },
        { id: 'p3', owner_id: MEMBER, kind: 'thumbnail', storage_path: `${MEMBER}/../x.webp`, content_type: 'image/webp', bytes: 1 },
      ]),
    );
    const res = await fetchMemberPreviews(MEMBER, bridge);
    expect(res.previews.map((p) => p.id)).toEqual(['p1']);
    expect(seen).toHaveLength(1);
    expect(seen.some((s) => s.url.includes('/storage/'))).toBe(false);
    expect(seen[0].url).not.toContain('signed');
  });

  it('shows a member\'s links (short share ids) and quote requests', async () => {
    const { bridge } = await bridgeWith((req) => {
      if (req.url.includes('/published_proposals')) {
        return ok([{ share_id: 'AAAAAAAAAAAAAAAAAAAAAA', local_proposal_id: 'prop_1', kind: 'proposal', status: 'live', payload: { contact: { name: 'Meera', business: 'Shah' }, custom_items: [{ id: 'custom_x', n: 'Custom thing' }] }, updated_at: iso(1), created_at: iso(5) }]);
      }
      return ok([{ id: 'q1', share_id: 'AAAAAAAAAAAAAAAAAAAAAA', choices: { selected: ['w_front', 'custom_x'], total_shown: 6000 }, contact: { name: 'Client', phone: '+91 3', notes: 'hi' }, created_at: iso(2), seen_at: null }]);
    });
    const res = await fetchMemberPitch(MEMBER, { w_front: 'Responsive Web Application' }, bridge);
    expect(res.proposals[0]).toMatchObject({ share_id: 'AAAAAA', status: 'live', client_name: 'Meera', business_name: 'Shah' });
    expect(res.submissions[0].items).toEqual(['Responsive Web Application', 'Custom thing']);
    expect(res.submissions[0].share_id).toBe('AAAAAA');
    await expect(fetchMemberPitch('x', {}, bridge)).rejects.toThrow(AdminError);
  });

  it('reads storage use and the audit log', async () => {
    const { bridge, seen } = await bridgeWith((req) => {
      if (req.url.includes('rpc/storage_usage')) return ok({ bytes: 900_000_000, files: 12, by_kind: { thumbnail: { bytes: 5, files: 2 }, phone_file: { bytes: 7, files: 1 } } });
      if (req.url.includes('/profiles')) return ok(PROFILES);
      return ok([{ id: 3, actor_id: ADMIN, action: 'profile.role_changed', target: MEMBER, data: { role: 'admin' }, created_at: iso(1) }, 7]);
    });
    const usage = await fetchStorage(bridge);
    expect(usage).toMatchObject({ bytes: 900_000_000, files: 12, warning: true });
    expect(usage.percent).toBeCloseTo(83.8, 1);
    expect(usage.by_kind.map((k) => k.kind).sort()).toEqual(['phone_file', 'thumbnail']);
    const audit = await fetchAudit({ before: iso(0) }, bridge);
    expect(audit.entries).toHaveLength(1);
    expect(audit.entries[0]).toMatchObject({ actor_name: 'Admin', target_label: 'Member', action: 'profile.role_changed' });
    expect(seen.some((s) => s.method !== 'GET' && !s.url.includes('rpc/storage_usage'))).toBe(false);
    await expect(fetchAudit({ before: 'x,y' }, bridge)).rejects.toThrow(AdminError);
  });
});

describe('AI keys', () => {
  const answer: Answer = (req) => {
    if (req.url.includes('rpc/admin_ai_key_labels')) return ok([{ label: 'key-1 (...abcd)', last_status: 200, last_ok_at: iso(1), cooldown_until: null, last_error: null }]);
    if (req.url.includes('/ai_allowed_models')) return ok([{ model: 'm/one', enabled: true, max_tokens_cap: 4096 }]);
    if (req.url.includes('/ai_limits')) return ok([{ id: 'a', owner_id: null, daily_requests: 200, daily_tokens: 400000 }, { id: 'b', owner_id: MEMBER, daily_requests: 50, daily_tokens: 1000, updated_at: iso(1) }]);
    if (req.url.includes('/ai_usage_daily')) {
      return ok([{ owner_id: MEMBER, day: new Date(NOW).toISOString().slice(0, 10), requests: 3, prompt_tokens: 100, completion_tokens: 50, rejected: 1 }, { owner_id: MEMBER, day: '2026-11-20', requests: 2, prompt_tokens: 10, completion_tokens: 5, rejected: 0 }]);
    }
    if (req.url.includes('/profiles')) return ok(PROFILES);
    return undefined;
  };

  it('shows key labels and health, usage and limits, and never a key', async () => {
    const { bridge, seen } = await bridgeWith(answer);
    const ai = await fetchAiOverview(bridge, NOW);
    expect(ai.keys).toEqual([{ label: 'key-1 (...abcd)', last_status: 200, last_ok_at: iso(1), cooldown_until: null, last_error: null }]);
    expect(ai.default_limit).toEqual({ daily_requests: 200, daily_tokens: 400000 });
    const member = ai.usage.find((u) => u.owner_id === MEMBER)!;
    expect(member.today).toMatchObject({ requests: 3, tokens: 150, rejected: 1 });
    expect(member.seven_days).toMatchObject({ requests: 5, tokens: 165 });
    expect(member.limit).toEqual({ daily_requests: 50, daily_tokens: 1000, is_override: true });
    expect(ai.models[0].model).toBe('m/one');
    expect(JSON.stringify(ai)).not.toMatch(/nvapi-/);
    // The RPC that lists labels is the only POST, and it sends nothing.
    const posts = seen.filter((s) => s.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain('rpc/admin_ai_key_labels');
    expect(seen.some((s) => s.url.includes('admin_set_ai_keys'))).toBe(false);
  });

  it('sets, resets and checks daily limits', async () => {
    const { bridge, seen } = await bridgeWith((req, s) => (s.method === 'PATCH' && req.url.includes('owner_id=is.null') ? ok([{ owner_id: null }]) : ok([{ owner_id: MEMBER }])));
    await saveAiLimits(null, 300, 500000, bridge);
    expect(seen[0]).toMatchObject({ method: 'PATCH', url: `${SUPA.url}/rest/v1/ai_limits?owner_id=is.null`, body: { daily_requests: 300, daily_tokens: 500000 } });
    await saveAiLimits(MEMBER, 10, 2000, bridge);
    expect(seen[1]).toMatchObject({ method: 'POST', url: `${SUPA.url}/rest/v1/ai_limits?on_conflict=owner_id`, body: { owner_id: MEMBER, daily_requests: 10, daily_tokens: 2000 } });
    await resetAiLimit(MEMBER, bridge);
    expect(seen[2]).toMatchObject({ method: 'DELETE', url: `${SUPA.url}/rest/v1/ai_limits?owner_id=eq.${MEMBER}` });
    const before = seen.length;
    for (const [req, tok] of [[0, 1], [100001, 1], [1, 0], [1, 100000001], [1.5, 1], [NaN, 1], [-1, 1]] as Array<[number, number]>) {
      await expect(saveAiLimits(MEMBER, req, tok, bridge), `${req}/${tok}`).rejects.toThrow(AdminError);
    }
    await expect(saveAiLimits('x', 1, 1, bridge)).rejects.toThrow(AdminError);
    await expect(resetAiLimit('x', bridge)).rejects.toThrow(AdminError);
    expect(seen).toHaveLength(before);
  });
});
