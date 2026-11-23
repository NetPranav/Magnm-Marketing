import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { AdminTab } from './components/AdminTab';
import { SESSION_SECRET_KEY } from './auth';
import { createPreviewNativeBridge, type NativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';
import type { AccountProfile } from './types';

const SUPA = getPublicSupabaseConfig();
const ADMIN = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
const MEMBER = '33333333-3333-4333-8333-333333333333';
const TABS = ['Team', 'Activity', 'Workspace', 'Pitch', 'Storage', 'AI keys', 'Audit log'];
const now = Date.now();
const iso = (m: number) => new Date(now - m * 60_000).toISOString();

const PROFILES = [
  { id: ADMIN, email: 'admin@example.com', display_name: 'Ada Admin', avatar_url: null, role: 'admin', active: true, created_at: iso(9000), last_seen_at: iso(1) },
  { id: OTHER, email: 'two@example.com', display_name: 'Omar Other', avatar_url: null, role: 'admin', active: true, created_at: iso(8000), last_seen_at: null },
  { id: MEMBER, email: 'member@example.com', display_name: 'Mia Member', avatar_url: null, role: 'member', active: true, created_at: iso(7000), last_seen_at: null },
];

interface Cloud {
  seen: Array<{ method: string; url: string; body: unknown }>;
  denyAll: boolean;
}

function json(body: unknown, status = 200): NativeHttpResponse {
  return { status, headers: {}, body: JSON.stringify(body) };
}

function handler(cloud: Cloud) {
  return async (req: NativeHttpRequest): Promise<NativeHttpResponse> => {
    cloud.seen.push({ method: req.method, url: req.url, body: req.body ? JSON.parse(req.body) : null });
    if (cloud.denyAll) return json({ message: 'permission denied' }, 403);
    const u = req.url;
    if (u.includes('/rest/v1/profiles') && req.method === 'PATCH') return json([{ id: MEMBER }]);
    if (u.includes('/rest/v1/profiles')) return json(PROFILES);
    if (u.includes('/rest/v1/invites') && req.method === 'POST') return json([{ email: 'new@example.com', role: 'member' }], 201);
    if (u.includes('/rest/v1/invites')) return json([{ email: 'pending@example.com', role: 'member', created_at: iso(5), accepted_at: null, revoked_at: null }]);
    if (u.includes('/rest/v1/device_status')) return json([{ owner_id: MEMBER, device_id: 'd1', device_kind: 'android', status: 'online', running_agents: ['writer'], app_version: '0.1', last_seen_at: iso(1) }]);
    if (u.includes('/rest/v1/activity_events')) return json([{ id: 5, owner_id: MEMBER, ts: iso(3), kind: 'review', agent_id: 'writer', summary: 'Reviewed the launch post' }]);
    if (u.includes('/rest/v1/mirror_records')) return json([{ id: 'r1', kind: 'content_item', local_id: 'cnt_1', parent_local_id: null, title: 'Launch post', local_updated_at: iso(2), synced_at: iso(2), data: { body: 'hello' } }]);
    if (u.includes('/rest/v1/media_previews')) return json([{ id: 'p1', owner_id: MEMBER, kind: 'thumbnail', storage_path: `${MEMBER}/a.webp`, content_type: 'image/webp', bytes: 2048, created_at: iso(1), expires_at: null }]);
    if (u.includes('/rest/v1/published_proposals')) return json([{ share_id: 'AAAAAAAAAAAAAAAAAAAAAA', local_proposal_id: 'prop_1', kind: 'proposal', status: 'live', payload: { contact: { name: 'Meera', business: 'Shah Clinics' } }, updated_at: iso(1) }]);
    if (u.includes('/rest/v1/quote_submissions')) return json([{ id: 'q1', share_id: 'AAAAAAAAAAAAAAAAAAAAAA', choices: { selected: ['w_front'], total_shown: 6000 }, contact: { name: 'Client One', phone: '+91 3' }, created_at: iso(2), seen_at: null }]);
    if (u.includes('rpc/storage_usage')) return json({ bytes: 5_000_000, files: 3, by_kind: { thumbnail: { bytes: 5_000_000, files: 3 } } });
    if (u.includes('rpc/admin_ai_key_labels')) return json([{ label: 'key-1 (...abcd)', last_status: 200, last_ok_at: iso(1), cooldown_until: null, last_error: null }]);
    if (u.includes('/rest/v1/ai_allowed_models')) return json([{ model: 'vendor/model-one', enabled: true, max_tokens_cap: 4096 }]);
    if (u.includes('/rest/v1/ai_limits') && req.method !== 'GET') return json([{ owner_id: null }]);
    if (u.includes('/rest/v1/ai_limits')) return json([{ id: 'a', owner_id: null, daily_requests: 200, daily_tokens: 400000 }]);
    if (u.includes('/rest/v1/ai_usage_daily')) return json([]);
    if (u.includes('/rest/v1/audit_log')) return json([{ id: 3, actor_id: ADMIN, action: 'profile.role_changed', target: MEMBER, created_at: iso(1) }]);
    return json([]);
  };
}

async function setup(cloud: Cloud, uid = ADMIN): Promise<NativeBridge> {
  const bridge = createPreviewNativeBridge({ httpHandler: handler(cloud) });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: 'jwt-admin', refresh_token: 'r', expires_at: 9_999_999_999, user_id: uid, email: 'admin@example.com' }),
  );
  return bridge;
}

const adminProfile: AccountProfile = { id: ADMIN, email: 'admin@example.com', display_name: 'Ada Admin', avatar_url: null, role: 'admin', active: true };
const memberProfile: AccountProfile = { ...adminProfile, id: MEMBER, role: 'member' };

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(cleanup);

describe('Admin on the phone', () => {
  it('offers all seven tabs to an admin, each opening without the laptop', async () => {
    const cloud: Cloud = { seen: [], denyAll: false };
    const bridge = await setup(cloud);
    render(<AdminTab profile={adminProfile} bridge={bridge} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(TABS);
    // Team is open first and lists the team with presence.
    await screen.findByText('Mia Member');
    expect(screen.getByText(/online/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
    await screen.findByText(/Reviewed the launch post/);

    fireEvent.click(screen.getByRole('tab', { name: 'Workspace' }));
    fireEvent.change(await screen.findByLabelText('Member'), { target: { value: MEMBER } });
    await screen.findByText('Launch post');
    expect(screen.getByText(/Previews/)).toBeTruthy();
    expect(screen.getByText(/thumbnail/)).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Pitch' }));
    fireEvent.change(await screen.findByLabelText('Member'), { target: { value: MEMBER } });
    await screen.findByText(/Shah Clinics/);
    await screen.findByText(/Client One/);

    fireEvent.click(screen.getByRole('tab', { name: 'Storage' }));
    await screen.findByText(/3 files/);

    fireEvent.click(screen.getByRole('tab', { name: 'AI keys' }));
    await screen.findByText(/key-1 \(\.\.\.abcd\)/);
    expect(screen.getByText(/laptop/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Audit log' }));
    await screen.findByText(/profile\.role_changed/);

    // No laptop address was ever called, only the cloud's /rest/v1/ paths.
    expect(cloud.seen.every((r) => r.url.startsWith(`${SUPA.url}/rest/v1/`))).toBe(true);
  });

  it('shows a member nothing and asks the cloud nothing', async () => {
    const cloud: Cloud = { seen: [], denyAll: false };
    const bridge = await setup(cloud, MEMBER);
    render(<AdminTab profile={memberProfile} bridge={bridge} />);
    expect(screen.queryAllByRole('tab')).toEqual([]);
    expect(screen.getByText(/admins only/i)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 30));
    expect(cloud.seen).toEqual([]);
    cleanup();
    render(<AdminTab profile={null} bridge={bridge} />);
    expect(screen.queryAllByRole('tab')).toEqual([]);
    render(<AdminTab profile={{ ...adminProfile, active: false }} bridge={bridge} />);
    expect(screen.queryAllByRole('tab')).toEqual([]);
    expect(cloud.seen).toEqual([]);
  });

  it('when the database refuses (a demoted admin on an open screen) the screen says so and shows no data', async () => {
    const cloud: Cloud = { seen: [], denyAll: true };
    const bridge = await setup(cloud);
    render(<AdminTab profile={adminProfile} bridge={bridge} />);
    await screen.findByText(/admins only/i);
    expect(screen.queryByText('Mia Member')).toBeNull();
  });

  it('changes a role or deactivates another member, never your own, and invites by email', async () => {
    const cloud: Cloud = { seen: [], denyAll: false };
    const bridge = await setup(cloud);
    render(<AdminTab profile={adminProfile} bridge={bridge} />);
    const mia = (await screen.findByText('Mia Member')).closest('[data-testid="admin-member"]') as HTMLElement;
    const ada = screen.getByText('Ada Admin').closest('[data-testid="admin-member"]') as HTMLElement;
    expect(within(ada).queryByRole('button', { name: /make member|make admin|deactivate/i })).toBeNull();
    expect(within(ada).getByText(/you/i)).toBeTruthy();

    fireEvent.click(within(mia).getByRole('button', { name: /make admin/i }));
    await waitFor(() => expect(cloud.seen.some((r) => r.method === 'PATCH' && (r.body as { role?: string })?.role === 'admin')).toBe(true));
    const patch = cloud.seen.find((r) => r.method === 'PATCH')!;
    expect(patch.url).toBe(`${SUPA.url}/rest/v1/profiles?id=eq.${MEMBER}`);

    fireEvent.change(screen.getByLabelText('Invite email'), { target: { value: 'New@Example.com' } });
    fireEvent.click(screen.getByRole('button', { name: /send invite/i }));
    await waitFor(() => expect(cloud.seen.some((r) => r.method === 'POST' && r.url.endsWith('/invites'))).toBe(true));
    expect(cloud.seen.find((r) => r.method === 'POST' && r.url.endsWith('/invites'))!.body).toEqual({ email: 'new@example.com', role: 'member' });
    fireEvent.change(screen.getByLabelText('Invite email'), { target: { value: 'not an email' } });
    fireEvent.click(screen.getByRole('button', { name: /send invite/i }));
    await screen.findByText(/valid email/i);
  });

  it('sets a daily AI limit with checks, and says sharing keys is a laptop job', async () => {
    const cloud: Cloud = { seen: [], denyAll: false };
    const bridge = await setup(cloud);
    render(<AdminTab profile={adminProfile} bridge={bridge} />);
    fireEvent.click(screen.getByRole('tab', { name: 'AI keys' }));
    await screen.findByText(/key-1/);
    expect(screen.queryByRole('button', { name: /share/i })).toBeNull();
    const requests = await screen.findByLabelText('Team requests per day');
    fireEvent.change(requests, { target: { value: '12.5' } });
    fireEvent.click(screen.getByRole('button', { name: /save team limit/i }));
    await screen.findByText(/whole number/i);
    expect(cloud.seen.some((r) => r.url.includes('/ai_limits') && r.method !== 'GET')).toBe(false);
    fireEvent.change(requests, { target: { value: '300' } });
    fireEvent.click(screen.getByRole('button', { name: /save team limit/i }));
    await waitFor(() => expect(cloud.seen.some((r) => r.url.includes('/ai_limits') && r.method === 'PATCH')).toBe(true));
  });
});

describe('Admin inside the app', () => {
  async function appFor(role: 'admin' | 'member') {
    const cloud: Cloud = { seen: [], denyAll: false };
    const base = handler(cloud);
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.includes('/rest/v1/profiles') && req.url.includes('id=eq.') && req.method === 'GET' && req.url.includes('select=id,email,display_name,avatar_url,role,active')) {
          cloud.seen.push({ method: req.method, url: req.url, body: null });
          return json([{ id: ADMIN, email: 'admin@example.com', display_name: 'Ada', avatar_url: null, role, active: true }]);
        }
        return base(req);
      },
    });
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt-admin', refresh_token: 'r', expires_at: 9_999_999_999, user_id: ADMIN, email: 'admin@example.com' }),
    );
    render(<App bridge={bridge} />);
    return cloud;
  }

  it('an admin gets an Admin tab next to the others', async () => {
    await appFor('admin');
    const nav = await screen.findByRole('navigation', { name: 'Mobile sections' });
    await waitFor(() => expect(within(nav).getByText('Admin')).toBeTruthy());
    fireEvent.click(within(nav).getByText('Admin'));
    const sections = await screen.findByRole('tablist', { name: 'Admin sections' });
    expect(within(sections).getAllByRole('tab').map((t) => t.textContent)).toEqual(TABS);
  });

  it('a member never sees an Admin tab and the admin data is never asked for', async () => {
    const cloud = await appFor('member');
    const nav = await screen.findByRole('navigation', { name: 'Mobile sections' });
    await waitFor(() => expect(cloud.seen.some((r) => r.url.includes('/rest/v1/profiles'))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(within(nav).queryByText('Admin')).toBeNull();
    expect(cloud.seen.some((r) => /device_status|audit_log|invites|ai_limits|ai_usage_daily/.test(r.url))).toBe(false);
  });
});

describe('phone width', () => {
  const css = readFileSync(path.resolve(__dirname, 'mobile.css'), 'utf-8');

  function rule(selector: string): string {
    const at = css.indexOf(`${selector} {`);
    expect(at, `${selector} rule exists`).toBeGreaterThan(-1);
    return css.slice(at, css.indexOf('}', at));
  }

  it('lets pills, tabs and button rows wrap instead of clipping', () => {
    expect(rule('.mob-actions')).toMatch(/flex-wrap:\s*wrap/);
    expect(rule('.mob-subtabs')).toMatch(/flex-wrap:\s*wrap/);
    expect(rule('.mob-subtab')).toMatch(/min-width:\s*0/);
    expect(rule('.mob-pitch-item-actions')).toMatch(/flex-wrap:\s*wrap/);
    expect(rule('.mob-admin-member-actions')).toMatch(/flex-wrap:\s*wrap/);
    expect(rule('.mob-admin-row')).toMatch(/flex-wrap:\s*wrap/);
  });

  it('never lets long text or controls push the page wider than the screen', () => {
    expect(rule('.mob-pitch-editor')).toMatch(/min-width:\s*0/);
    expect(rule('.mob-pitch-editor')).toMatch(/max-width:\s*100%/);
    expect(rule('.mob-admin')).toMatch(/min-width:\s*0/);
    expect(rule('.mob-admin')).toMatch(/max-width:\s*100%/);
    expect(rule('.mob-wrap-text')).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
