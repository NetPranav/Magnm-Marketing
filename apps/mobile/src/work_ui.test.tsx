import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ActionInput } from './actions';
import { SESSION_SECRET_KEY } from './auth';
import type { ContextCacheState } from './cache';
import { WorkTab } from './components/WorkTab';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { saveCachedProductRules } from './cache';
import type { MirrorRecord, PairingConfig, PhoneAction, ProductRulesConfig } from './types';

const UID = '11111111-1111-4111-8111-111111111111';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};
const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, ...new Array(40).fill(0)]);
const ok = (body: unknown, status = 200): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });
const PAIRING: PairingConfig = { host: '100.64.0.7', port: 8787, deviceId: 'dev1', deviceName: 'Pixel', ownerUid: UID, pairedAt: 'x' };

function rec(kind: string, id: string, data: Record<string, unknown>, extra: Partial<MirrorRecord> = {}): MirrorRecord {
  return { id: `m-${kind}-${id}`, owner_id: UID, kind, local_id: id, title: String(data.name ?? id), data, local_updated_at: '2026-11-24T08:00:00+00:00', cached_at: Date.now(), ...extra };
}

const MIRROR = [
  rec('asset', 'asset_1', { name: 'logo.png', file_group: 'image', size_bytes: 1200, category: 'brand', fetchable: true, uploaded_at: '2026-11-24T08:00:00+00:00' }),
  rec('asset', 'asset_2', { confidential: true }),
  rec('schedule', 'market_scan', { name: 'Market scanner', description: 'Scans for talk', enabled: true, interval_hours: 6, cron: null, weekly: false }),
  rec('schedule', 'cmo_daily', { name: 'Daily CMO Cycle', enabled: true, interval_hours: null, cron: '46 8 * * *', weekly: false }),
  rec('editorial_plan', 'plan', { rows: [{ slot: 'Mon 24', platforms: 'X', topic: 'Cero launch', status: 'idea' }] }),
  rec('brief', 'wf_weekly_plan_1', { kind: 'weekly_plan', status: 'waiting_user', inputs: { week_start: '2026-11-30' }, proposed_slots: [{ date: '2026-11-30', platform: 'linkedin', topic: 'Cero tips', angle: '', product_id: 'cero', reason: '' }] }, { title: 'weekly_plan: 2026-11-30' }),
  rec('brief', 'wf_post_1', { kind: 'post', status: 'done', inputs: {} }, { title: 'post: Announce the release' }),
];

function cache(records: MirrorRecord[] = MIRROR): ContextCacheState {
  return { ownerUid: UID, mirrorRecords: records, productRules: null, laptop: null } as unknown as ContextCacheState;
}

function action(over: Partial<PhoneAction> = {}): PhoneAction {
  return { id: '99999999-9999-4999-8999-999999999999', kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: false }, label: null, created_at: '2026-11-24T08:00:00Z', owner_uid: UID, sent: false, state: 'waiting', result: null, ...over };
}

interface Opts {
  reachable?: boolean;
  actions?: PhoneAction[];
  signedIn?: boolean;
  http?: (req: NativeHttpRequest) => Promise<NativeHttpResponse>;
  uploads?: Array<{ url: string; data: Uint8Array }>;
  shared?: string[];
  records?: MirrorRecord[];
}

async function renderTab(opts: Opts = {}) {
  const queue = vi.fn(async (_input: ActionInput, _ok: string) => true);
  const changed = vi.fn();
  const posts: Array<{ url: string; body: Record<string, unknown> }> = [];
  const bridge = createPreviewNativeBridge({
    httpHandler:
      opts.http ??
      (async (req) => {
        if (req.url.includes('/rest/v1/phone_uploads')) {
          if (req.method === 'POST') {
            posts.push({ url: req.url, body: JSON.parse(req.body || '{}') });
            return { status: 201, headers: {}, body: '' };
          }
          return ok([]);
        }
        if (req.url.includes('/rest/v1/team_repos')) return ok([{ id: 'cero-web', name: 'Cero web', confidential: false, status: 'released' }]);
        if (req.url.includes('/rest/v1/shared_knowledge')) return ok([{ key: 'product_rules', data: RULES, updated_at: null }]);
        if (req.url.includes('/functions/v1/ai-proxy')) return ok({ choices: [{ message: { content: 'TITLE: Cero is out\n\nCero is out today. Try it.' } }] });
        if (req.url.includes('/phone/v1/library')) return ok({ assets: [{ id: 'asset_1', name: 'logo.png', file_group: 'image', size_bytes: 1200, category: 'brand', fetchable: true }] });
        return ok([]);
      }),
    onUploadCloudFile: (info) => {
      opts.uploads?.push({ url: info.url, data: info.data });
      return { status: 200, body: '{}' };
    },
    onShareLaptopFile: (o) => void opts.shared?.push(o.url),
  });
  await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }));
  await bridge.secureStore.set('magnm.laptop.deviceToken', 'tok');
  saveCachedProductRules(UID, RULES);
  render(
    <WorkTab
      ownerUid={opts.signedIn === false ? null : UID}
      signedIn={opts.signedIn !== false}
      laptopReachable={opts.reachable ?? false}
      pairing={opts.reachable ? PAIRING : null}
      cache={cache(opts.records)}
      phoneActions={opts.actions ?? []}
      bridge={bridge}
      savedCopyLabel="Saved copy from 2 hours ago"
      onQueueAction={queue}
      onActionsChanged={changed}
    />,
  );
  return { queue, changed, posts, bridge };
}

function pickSection(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }));
}

beforeEach(() => window.localStorage.clear());
afterEach(() => cleanup());

describe('Library', () => {
  it('greys out the laptop assets with "On the laptop" when the laptop is not reachable', async () => {
    await renderTab();
    expect(screen.getByRole('tab', { name: 'Library', selected: true })).toBeTruthy();
    const row = screen.getByText('logo.png').closest('[class*="mob-asset"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.getAttribute('aria-disabled')).toBe('true');
    expect(within(row).getByText(/On the laptop/)).toBeTruthy();
    expect((within(row).getByRole('button', { name: /Get on phone/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Confidential item')).toBeTruthy();
    expect(screen.getByText(/Saved copy from 2 hours ago/)).toBeTruthy();
  });

  it('lets the owner fetch an asset when the laptop is reachable', async () => {
    const shared: string[] = [];
    await renderTab({ reachable: true, shared });
    const button = (await screen.findByRole('button', { name: /Get on phone: logo.png/ })) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(shared).toEqual(['http://100.64.0.7:8787/phone/v1/files?asset_id=asset_1']));
  });

  it('uploads a picked file and shows it waiting for the laptop', async () => {
    const uploads: Array<{ url: string; data: Uint8Array }> = [];
    const { posts, changed } = await renderTab({ uploads });
    const input = screen.getByLabelText('Choose a file') as HTMLInputElement;
    const file = new File([PNG], 'Launch logo.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'brand' } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload to my Library' }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({ file_name: 'Launch logo.png', kind: 'image' });
    expect(uploads).toHaveLength(1);
    expect(await screen.findByText(/Uploaded\. Your laptop adds it to its Library/)).toBeTruthy();
  });

  it('refuses a file that is not an image, video or PDF and sends nothing', async () => {
    const { posts, changed } = await renderTab();
    const file = new File([new TextEncoder().encode('<script>alert(1)</script>')], 'evil.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Choose a file'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload to my Library' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toMatch(/Only images/);
    expect(posts).toEqual([]);
    expect(changed).not.toHaveBeenCalled();
  });

  it('needs a sign-in to upload', async () => {
    await renderTab({ signedIn: false });
    expect(screen.getByText(/Sign in/i)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Upload to my Library' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows how each upload action ended, with the laptops reason', async () => {
    await renderTab({ actions: [action({ kind: 'library_upload', target_id: UID, payload: { category: 'brand' }, label: 'old.png', state: 'rejected', result: 'Not applied: the PDF has active content, so it was not added.' })] });
    expect(screen.getByText(/old\.png/)).toBeTruthy();
    expect(screen.getByText(/active content/)).toBeTruthy();
  });
});

describe('Plan', () => {
  it('shows the weekly plan, briefs, the calendar and the schedules from the cloud copy', async () => {
    await renderTab();
    pickSection('Plan');
    expect(screen.getByText('Cero tips')).toBeTruthy();
    expect(screen.getByText(/weekly_plan: 2026-11-30/)).toBeTruthy();
    expect(screen.getByText('Cero launch')).toBeTruthy();
    expect(screen.getByText('Market scanner')).toBeTruthy();
    expect(screen.getByText(/Every day at 08:46 IST/)).toBeTruthy();
  });

  it('queues a schedule change for the laptop and shows it as waiting', async () => {
    const { queue } = await renderTab();
    pickSection('Plan');
    fireEvent.click(screen.getByRole('button', { name: 'Pause Market scanner' }));
    await waitFor(() => expect(queue).toHaveBeenCalledTimes(1));
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: false } });
    expect(String(queue.mock.calls[0][1])).toMatch(/laptop/i);
  });

  it('changes an interval and a time through the same queue, and shows what is already waiting', async () => {
    const { queue } = await renderTab({ actions: [action({ target_id: 'cmo_daily', payload: { time: '07:00' } })] });
    pickSection('Plan');
    expect(screen.getByText(/Waiting for the laptop: runs at 07:00/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Run Market scanner every'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change interval of Market scanner' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ target_id: 'market_scan', payload: { interval_hours: 12 } });
  });

  it('asks the laptop to plan the week without approving anything', async () => {
    const { queue } = await renderTab();
    pickSection('Plan');
    fireEvent.change(screen.getByLabelText('Posts per week'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue the weekly plan for the laptop' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'workflow_request', target_id: 'weekly_plan', payload: { posts_per_week: 3 } });
    expect(screen.getByText(/asks for your approval/i)).toBeTruthy();
  });

  it('has nothing to show without a cloud copy and says so', async () => {
    await renderTab({ records: [] });
    pickSection('Plan');
    expect(screen.getByText(/No saved plan yet/i)).toBeTruthy();
  });
});

describe('Create', () => {
  async function createTab(opts: Opts = {}) {
    const out = await renderTab(opts);
    pickSection('Create');
    return out;
  }

  it('writes a post on the phone and saves it as a draft queued for the laptop', async () => {
    const { queue } = await createTab();
    fireEvent.change(screen.getByLabelText('What is it about?'), { target: { value: 'Announce Cero' } });
    fireEvent.click(screen.getByRole('button', { name: 'Write it on this phone' }));
    const title = (await screen.findByLabelText('Title')) as HTMLInputElement;
    expect(title.value).toBe('Cero is out');
    expect((screen.getByLabelText('Text') as HTMLTextAreaElement).value).toContain('Cero is out today');
    expect(screen.getByText(/Written on this phone with Phone Lite/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save as a draft for the laptop' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'draft_create', target_id: 'new', payload: { platform: 'linkedin', title: 'Cero is out', mode: 'post' } });
  });

  it('refuses a brief that names a confidential product and sends nothing to the model', async () => {
    const aiCalls: string[] = [];
    const { queue } = await createTab({
      http: async (req) => {
        if (req.url.includes('/functions/v1/ai-proxy')) aiCalls.push(req.url);
        if (req.url.includes('/rest/v1/shared_knowledge')) return ok([{ key: 'product_rules', data: RULES, updated_at: null }]);
        return ok([]);
      },
    });
    fireEvent.change(screen.getByLabelText('What is it about?'), { target: { value: 'Tease ProjectNightfall' } });
    fireEvent.click(screen.getByRole('button', { name: 'Write it on this phone' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/confidential/i);
    expect(aiCalls).toEqual([]);
    expect(queue).not.toHaveBeenCalled();
  });

  it('says plainly what needs the laptop and queues it as a request', async () => {
    const { queue } = await createTab();
    fireEvent.change(screen.getByLabelText('What do you want to make?'), { target: { value: 'blog' } });
    expect(screen.getByText(/Needs your laptop/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'How the phone and laptop share work' } });
    await screen.findByRole('option', { name: 'Cero web' });
    fireEvent.change(screen.getByLabelText('Repository (optional)'), { target: { value: 'cero-web' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue for the laptop' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'workflow_request', target_id: 'blog', payload: { topic: 'How the phone and laptop share work', repo_id: 'cero-web' } });
    expect(String(queue.mock.calls[0][1])).toMatch(/starts when your laptop is back/i);
  });

  it('queues research as a request to the researcher', async () => {
    const { queue } = await createTab();
    fireEvent.change(screen.getByLabelText('What do you want to make?'), { target: { value: 'research' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'What do developers say about local-first tools?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue for the laptop' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'agent_task', target_id: 'researcher' });
  });

  it('lists what is waiting and what finished, with the laptops reason', async () => {
    await createTab({
      actions: [
        action({ kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 'Thread idea', body: 'b' }, label: 'Draft: Thread idea', state: 'applied', result: 'Draft saved on the laptop.' }),
        action({ id: '88888888-8888-4888-8888-888888888888', kind: 'workflow_request', target_id: 'blog', payload: { topic: 't' }, label: 'Blog post' }),
      ],
    });
    expect(screen.getByText(/Draft: Thread idea/)).toBeTruthy();
    expect(screen.getByText(/Draft saved on the laptop/)).toBeTruthy();
    const waiting = screen.getByText('Waiting and finished').closest('.mg-card') as HTMLElement;
    expect(within(waiting).getByText(/Blog post/)).toBeTruthy();
    expect(screen.getAllByText(/Waiting for laptop/).length).toBeGreaterThan(0);
  });

  it('does not offer to queue without a sign-in', async () => {
    await createTab({ signedIn: false });
    expect(screen.getByText(/Sign in/i)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Write it on this phone' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('act() hygiene', () => {
  it('renders without warnings about state updates after unmount', async () => {
    await renderTab();
    await act(async () => {
      cleanup();
    });
  });
});
