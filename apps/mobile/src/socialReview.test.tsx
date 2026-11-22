import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { App } from './App';
import contract from './fixtures/phone_contract.json';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { writeNonSecretJson } from './storage';

const ok = (body: unknown): NativeHttpResponse => ({ status: 200, headers: {}, body: JSON.stringify(body) });
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const waiting = { id: 'r1', platform: 'x', recipe: 'search', argument: 'Cero topic', status: 'waiting_user', challenge_kind: 'captcha', pages_used: 1 };
const statusWith = (active: unknown) => ok({ ...contract.social_status, active_run: active });

async function open(custom: (req: NativeHttpRequest) => Promise<NativeHttpResponse | null>, tab: 'today' | 'chat' = 'today') {
  writeNonSecretJson(PAIRING_META_KEY, {
    host: '100.64.5.10',
    port: 8788,
    deviceId: 'pdev_social_review',
    deviceName: 'Phone',
    ownerUid: '',
    pairedAt: '2026-11-22T09:00:00Z',
  });
  const bridge = createPreviewNativeBridge({
    httpHandler: async (req) => {
      const res = await custom(req);
      if (res) return res;
      if (req.url.endsWith('/v1/status')) return ok(contract.status);
      if (req.url.includes('/drafts?')) return ok({ drafts: [] });
      if (req.url.includes('/reminders')) return ok({ reminders: [] });
      if (req.url.includes('/notifications')) return ok({ notifications: [], unread_count: 0 });
      if (req.url.endsWith('/summary')) return ok({ summary: null });
      if (req.url.endsWith('/results/pending')) return ok({ proposals: [] });
      if (req.url.endsWith('/agents')) return ok(contract.agents);
      if (req.url.endsWith('/pitch')) return ok({ proposals: [] });
      if (req.url.endsWith('/social/status')) return statusWith(null);
      if (req.url.includes('/social/runs')) return ok({ runs: [] });
      if (req.url.endsWith('/live/frame')) return ok({ frame_base64: '/9j/2Q==', content_type: 'image/jpeg' });
      return null as unknown as NativeHttpResponse;
    },
  });
  await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok');
  render(<App bridge={bridge} initialTab={tab} />);
}

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

it('keeps what is being typed while status polls continue', async () => {
  await open(async (req) => {
    if (req.url.endsWith('/social/status')) return statusWith({ ...waiting });
    if (req.url.includes('/social/runs')) return ok({ runs: [{ ...waiting }] });
    return null;
  });
  fireEvent.click((await screen.findAllByRole('button', { name: 'Solve now' }))[0]);
  fireEvent.change(await screen.findByLabelText('Masked live text input'), { target: { value: '123456' } });
  await sleep(3200);
  expect((screen.getByLabelText('Masked live text input') as HTMLInputElement).value).toBe('123456');
}, 9000);

it('an idle phone learns about a check started on the laptop', async () => {
  let challenged = false;
  await open(async (req) => {
    if (req.url.endsWith('/social/status')) return statusWith(challenged ? { ...waiting } : null);
    if (req.url.includes('/social/runs')) return ok({ runs: challenged ? [{ ...waiting }] : [] });
    return null;
  });
  await screen.findByText('Laptop connected');
  await sleep(100);
  challenged = true;
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Solve now' }).length).toBeGreaterThan(0), { timeout: 12500 });
}, 16000);

it('a run requested in chat shows its card straight away', async () => {
  let asked = false;
  await open(async (req) => {
    if (req.url.endsWith('/message')) {
      asked = true;
      return ok({ success: true, response: 'Press Start when ready.', agent: { name: 'Writer' } });
    }
    if (req.url.includes('/social/runs')) return ok({ runs: asked ? [{ ...waiting, status: 'awaiting_start', approval_hash: 'h' }] : [] });
    return null;
  }, 'chat');
  await screen.findByText('Laptop connected');
  fireEvent.change(screen.getByLabelText('Message or voice brief'), { target: { value: 'check X' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(screen.getAllByText(/Cero topic/).length).toBeGreaterThan(0), { timeout: 2000 });
});

it('results that arrive after unpairing are dropped', async () => {
  let cleared = false;
  let release: (res: NativeHttpResponse) => void = () => {};
  await open(async (req) => {
    if (req.url.endsWith('/challenge/continue')) {
      cleared = true;
      return ok({ ok: true, cleared: true, run: { ...waiting, status: 'running' } });
    }
    if (req.url.endsWith('/social/status')) return statusWith(cleared ? null : { ...waiting });
    if (req.url.includes('/social/runs')) {
      if (!cleared) return ok({ runs: [{ ...waiting }] });
      return new Promise<NativeHttpResponse>((resolve) => {
        release = resolve;
      });
    }
    return null;
  });
  fireEvent.click((await screen.findAllByRole('button', { name: 'Solve now' }))[0]);
  fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
  await waitFor(() => expect(screen.queryByLabelText('Masked live text input')).toBeNull());
  fireEvent.click(screen.getByRole('tab', { name: /More/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Unpair laptop' }));
  await screen.findByLabelText(/Pairing code/);
  release(ok({ runs: [{ ...waiting, status: 'done', argument: 'LATE RESULT' }] }));
  await sleep(50);
  fireEvent.click(screen.getByRole('tab', { name: /Today/ }));
  expect(screen.queryAllByText(/LATE RESULT/)).toHaveLength(0);
});
