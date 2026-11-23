import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';
import { SESSION_SECRET_KEY, signOut } from './auth';
import phoneContract from './fixtures/phone_contract.json';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { writeNonSecretJson } from './storage';
import { saveCachedProductRules } from './cache';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse, type ShareCloudFileOptions } from './native';
import { getPublicSupabaseConfig } from './net';

const SUPA = getPublicSupabaseConfig();
const UID = '11111111-2222-4333-8444-555555555501';
const OTHER = '11111111-2222-4333-8444-555555555502';
const H = 'a'.repeat(64);
const H2 = 'b'.repeat(64);
const H3 = 'c'.repeat(64);
const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ1cmwiOiJ4In0.c2lnbmF0dXJl';
const RULES = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [{ id: 'cero', name: 'Cero', status: 'released' as const, show: 'everything' as const, confidential: false }],
};

const days = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();

function row(over: Record<string, unknown> = {}) {
  return {
    id: 'row-1', owner_id: UID, content_id: 'cnt_pub', file_path: 'outputs/cards/hero.png', file_name: 'hero.png', kind: 'image',
    content_type: 'image/png', bytes: 1_500_000, storage_path: `${UID}/${H}.png`, expires_at: days(10), ...over,
  };
}

interface Cloud {
  copies: Array<Record<string, unknown>>;
  signed: string[];
  shares: ShareCloudFileOptions[];
  signAnswer: (path: string) => NativeHttpResponse;
  urls: string[];
}

function makeCloud(): Cloud {
  const cloud: Cloud = {
    copies: [], signed: [], shares: [], urls: [],
    signAnswer: (path) => ({ status: 200, headers: {}, body: JSON.stringify({ signedURL: `/object/sign/phone-files/${path}?token=${TOKEN}` }) }),
  };
  return cloud;
}

function handler(cloud: Cloud) {
  return async (req: NativeHttpRequest): Promise<NativeHttpResponse> => {
    cloud.urls.push(req.url);
    if (req.url.startsWith('http://100.')) throw new Error('Network request failed');
    if (req.url.includes('/rest/v1/profiles')) {
      return { status: 200, headers: {}, body: JSON.stringify([{ id: UID, email: 'a@example.com', display_name: 'Alpha', avatar_url: null, role: 'member', active: true }]) };
    }
    if (req.url.includes('/rest/v1/shared_knowledge')) {
      return { status: 200, headers: {}, body: JSON.stringify([{ key: 'product_rules', data: RULES, updated_at: null }]) };
    }
    if (req.url.includes('/rest/v1/mirror_records')) {
      const rows = req.url.includes('kind=eq.content_item')
        ? [
            { id: 'm1', owner_id: UID, kind: 'content_item', local_id: 'cnt_pub', title: 'Cero launch post', data: { platform: 'linkedin', status: 'ready', current_version: 1 }, local_updated_at: '2026-11-23T10:00:00Z' },
            { id: 'm2', owner_id: UID, kind: 'content_item', local_id: 'cnt_conf', title: 'Confidential item', data: { confidential: true, status: 'ready' }, local_updated_at: '2026-11-23T10:00:00Z' },
          ]
        : req.url.includes('kind=eq.content_version')
          ? [{ id: 'm3', owner_id: UID, kind: 'content_version', local_id: 'ver_1', parent_local_id: 'cnt_pub', title: 'v1', data: { version: 1, body: 'Cero is out today.' }, local_updated_at: '2026-11-23T10:00:00Z' }]
          : [];
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    }
    if (req.url.includes('/rest/v1/phone_files')) return { status: 200, headers: {}, body: JSON.stringify(cloud.copies) };
    if (req.url.includes('/rest/v1/phone_actions')) return { status: 200, headers: {}, body: '[]' };
    if (req.url.includes('/storage/v1/object/sign/phone-files/') && req.method === 'POST') {
      const path = req.url.split('/storage/v1/object/sign/phone-files/')[1];
      cloud.signed.push(path);
      return cloud.signAnswer(path);
    }
    return { status: 404, headers: {}, body: '{}' };
  };
}

async function start(cloud: Cloud) {
  const bridge = createPreviewNativeBridge({
    httpHandler: handler(cloud),
    onShareCloudFile: async (o) => void cloud.shares.push(o),
  });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: 'jwt-alpha', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
  );
  saveCachedProductRules(UID, RULES);
  render(<App bridge={bridge} initialTab="drafts" />);
  return bridge;
}

async function openDraft(title = 'Cero launch post') {
  fireEvent.click(await screen.findByText(title));
  await screen.findByRole('button', { name: 'Add comment' });
}

describe('Get on phone', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => cleanup());

  it('shows each cloud copy with its size and expiry, and downloads it through a signed link when the laptop is away', async () => {
    const cloud = makeCloud();
    cloud.copies = [row(), row({ id: 'row-2', file_path: 'outputs/cards/demo.mp4', file_name: 'demo.mp4', kind: 'video', content_type: 'video/mp4', bytes: 48 * 1024 * 1024, storage_path: `${UID}/${H2}.mp4`, expires_at: days(3) })];
    await start(cloud);
    await openDraft();
    expect(await screen.findByText('hero.png')).toBeTruthy();
    expect(screen.getByText(/1\.4 MB/)).toBeTruthy();
    expect(screen.getByText(/Copy kept for 10 more days/)).toBeTruthy();
    expect(screen.getByText(/48\.0 MB/)).toBeTruthy();
    expect(screen.getByText(/Copy kept for 3 more days/)).toBeTruthy();
    const buttons = screen.getAllByRole('button', { name: 'Get on phone' });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(cloud.shares).toHaveLength(1));
    expect(cloud.signed).toEqual([`${UID}/${H}.png`]);
    expect(cloud.shares[0].url).toBe(`${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${H}.png?token=${TOKEN}`);
    expect(cloud.urls.some((u) => u.startsWith('http://100.'))).toBe(false);
  });

  it('disables an expired copy and says so', async () => {
    const cloud = makeCloud();
    cloud.copies = [row({ expires_at: new Date(Date.now() - 1000).toISOString() })];
    await start(cloud);
    await openDraft();
    expect(await screen.findByText(/Copy expired/)).toBeTruthy();
    const button = screen.getByRole('button', { name: 'Get on phone' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(cloud.signed).toEqual([]);
    expect(cloud.shares).toEqual([]);
  });

  it('shows only copies of this draft, this account and well-formed rows, and none on a confidential draft', async () => {
    const cloud = makeCloud();
    cloud.copies = [
      row(),
      row({ id: 'other-draft', content_id: 'cnt_other', file_name: 'other-draft.png', storage_path: `${UID}/${H2}.png` }),
      row({ id: 'other-owner', owner_id: OTHER, file_name: 'other-owner.png', storage_path: `${OTHER}/${H3}.png` }),
      row({ id: 'forged', file_name: 'forged.png', storage_path: `${OTHER}/${H3}.png` }),
      row({ id: 'conf', content_id: 'cnt_conf', file_name: 'conf.png', storage_path: `${UID}/${H3}.png` }),
    ];
    await start(cloud);
    await openDraft();
    expect(await screen.findByText('hero.png')).toBeTruthy();
    for (const hidden of ['other-draft.png', 'other-owner.png', 'forged.png', 'conf.png']) {
      expect(screen.queryByText(hidden)).toBeNull();
    }
    expect(screen.getAllByRole('button', { name: 'Get on phone' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Back to drafts' }));
    fireEvent.click(await screen.findByText('Confidential item'));
    expect(await screen.findByText(/stay stubs on mobile/i)).toBeTruthy();
    expect(screen.queryByText('conf.png')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Get on phone' })).toBeNull();
  });

  it('shows an error and shares nothing when the cloud gives a link to another file', async () => {
    const cloud = makeCloud();
    cloud.copies = [row()];
    cloud.signAnswer = () => ({ status: 200, headers: {}, body: JSON.stringify({ signedURL: `/object/sign/phone-files/${UID}/${H3}.png?token=${TOKEN}` }) });
    await start(cloud);
    await openDraft();
    fireEvent.click(await screen.findByRole('button', { name: 'Get on phone' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/different file|unusable|not available/i));
    expect(cloud.shares).toEqual([]);
  });

  it('shows a friendly reason when the copy is gone from the cloud', async () => {
    const cloud = makeCloud();
    cloud.copies = [row()];
    cloud.signAnswer = () => ({ status: 404, headers: {}, body: JSON.stringify({ message: 'Object not found' }) });
    await start(cloud);
    await openDraft();
    fireEvent.click(await screen.findByRole('button', { name: 'Get on phone' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/no longer in the cloud/i));
    expect(cloud.shares).toEqual([]);
  });

  it('forgets every copy when the account signs out', async () => {
    const cloud = makeCloud();
    cloud.copies = [row()];
    const bridge = await start(cloud);
    await openDraft();
    expect(await screen.findByText('hero.png')).toBeTruthy();
    await act(async () => {
      await signOut(bridge);
    });
    cleanup();
    cloud.copies = [];
    render(<App bridge={bridge} initialTab="drafts" />);
    expect(screen.queryByText('hero.png')).toBeNull();
  });

  describe('with the laptop reachable', () => {
    async function startWithLaptop(cloud: Cloud, laptopShareFails: boolean) {
      const laptopShares: unknown[] = [];
      const draft = { ...phoneContract.draft_detail.item, files: [{ kind: 'card', path: 'outputs/cards/hero.png' }] };
      writeNonSecretJson(PAIRING_META_KEY, {
        host: '100.64.5.10', port: 8788, deviceId: 'pdev_1', deviceName: 'Pixel', ownerUid: UID, pairedAt: '2026-11-22T09:00:00Z',
      });
      const bridge = createPreviewNativeBridge({
        httpHandler: async (req) => {
          if (req.url.startsWith('http://100.')) {
            if (req.url.endsWith('/phone/v1/status')) return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
            if (req.url.includes('/phone/v1/drafts?')) {
              return { status: 200, headers: {}, body: JSON.stringify({ drafts: [{ ...draft }] }) };
            }
            if (req.url.endsWith('/phone/v1/drafts/cnt_pub')) return { status: 200, headers: {}, body: JSON.stringify({ item: draft }) };
            if (req.url.includes('/phone/v1/reminders')) return { status: 200, headers: {}, body: JSON.stringify({ reminders: [] }) };
            if (req.url.includes('/phone/v1/notifications')) return { status: 200, headers: {}, body: JSON.stringify({ notifications: [], unread_count: 0 }) };
            if (req.url.endsWith('/phone/v1/agents')) return { status: 200, headers: {}, body: JSON.stringify({ agents: [] }) };
            if (req.url.endsWith('/phone/v1/pitch')) return { status: 200, headers: {}, body: JSON.stringify({ proposals: [], prices_as_of: null }) };
            return { status: 404, headers: {}, body: '{}' };
          }
          return handler(cloud)(req);
        },
        onShareLaptopFile: async (o) => {
          laptopShares.push(o);
          if (laptopShareFails) throw new Error('Network request failed');
        },
        onShareCloudFile: async (o) => void cloud.shares.push(o),
      });
      await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-laptop');
      await bridge.secureStore.set(
        SESSION_SECRET_KEY,
        JSON.stringify({ access_token: 'jwt-alpha', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
      );
      saveCachedProductRules(UID, RULES);
      render(<App bridge={bridge} initialTab="drafts" />);
      return laptopShares;
    }

    it('uses the direct laptop path and never touches the cloud copy', async () => {
      const cloud = makeCloud();
      cloud.copies = [row()];
      const laptopShares = await startWithLaptop(cloud, false);
      fireEvent.click(await screen.findByText('Public Cero Launch'));
      expect(await screen.findByText(/Copy kept for 10 more days/)).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Get on phone' }));
      await waitFor(() => expect(laptopShares).toHaveLength(1));
      expect(cloud.signed).toEqual([]);
      expect(cloud.shares).toEqual([]);
    });

    it('falls back to the cloud copy when the laptop turns out to be away', async () => {
      const cloud = makeCloud();
      cloud.copies = [row({ content_id: 'cnt_pub' })];
      const laptopShares = await startWithLaptop(cloud, true);
      fireEvent.click(await screen.findByText('Public Cero Launch'));
      await screen.findByText(/Copy kept for 10 more days/);
      fireEvent.click(screen.getByRole('button', { name: 'Get on phone' }));
      await waitFor(() => expect(cloud.shares).toHaveLength(1));
      expect(laptopShares).toHaveLength(1);
      expect(cloud.signed).toEqual([`${UID}/${H}.png`]);
    });
  });
});
