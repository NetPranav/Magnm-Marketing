import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { PKCE_SECRET_KEY, SESSION_SECRET_KEY } from './auth';
import phoneContract from './fixtures/phone_contract.json';
import { ensureSession } from './test-session';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { loadQueue } from './queue';
import { mapClientPointToViewport } from './socialLiveGeometry';
import { writeNonSecretJson } from './storage';
import { dumpStorage } from './test-storage';
import type { SocialRunRecord } from './types';

function makePhoneBaseHandler(overrides: {
  onRequest?: (
    req: NativeHttpRequest,
  ) => Promise<NativeHttpResponse | null> | NativeHttpResponse | null;
}) {
  return async (req: NativeHttpRequest): Promise<NativeHttpResponse> => {
    const custom = await overrides.onRequest?.(req);
    if (custom) return custom;
    if (req.url.endsWith('/phone/v1/status')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
    }
    if (req.url.includes('/phone/v1/drafts?')) {
      return { status: 200, headers: {}, body: JSON.stringify({ drafts: [] }) };
    }
    if (req.url.includes('/phone/v1/reminders')) {
      return { status: 200, headers: {}, body: JSON.stringify({ reminders: [] }) };
    }
    if (req.url.includes('/phone/v1/notifications')) {
      return { status: 200, headers: {}, body: JSON.stringify({ notifications: [], unread_count: 0 }) };
    }
    if (req.url.endsWith('/phone/v1/summary')) {
      return { status: 200, headers: {}, body: JSON.stringify({ summary: null }) };
    }
    if (req.url.endsWith('/phone/v1/results/pending')) {
      return { status: 200, headers: {}, body: JSON.stringify({ proposals: [] }) };
    }
    if (req.url.endsWith('/phone/v1/agents')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.agents) };
    }
    if (req.url.endsWith('/phone/v1/pitch')) {
      return { status: 200, headers: {}, body: JSON.stringify({ proposals: [] }) };
    }
    if (req.url.endsWith('/phone/v1/social/status')) {
      return { status: 200, headers: {}, body: JSON.stringify(phoneContract.social_status) };
    }
    if (req.url.includes('/phone/v1/social/runs')) {
      return { status: 200, headers: {}, body: JSON.stringify({ runs: [] }) };
    }
    return { status: 404, headers: {}, body: '{}' };
  };
}

describe('T8.2 Mobile Social Research, Live View, Storage & Wipe', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('maps phone taps to 1280x800 across several screen sizes and rejects letterbox bars', () => {
    // 1280x800 exact
    expect(mapClientPointToViewport(320, 200, { left: 0, top: 0, width: 1280, height: 800 })).toEqual({
      x: 320,
      y: 200,
    });
    // 640x400 half-size
    expect(mapClientPointToViewport(320, 200, { left: 0, top: 0, width: 640, height: 400 })).toEqual({
      x: 640,
      y: 400,
    });
    // Tall mobile portrait 360x600: rendered height = 360 * 800 / 1280 = 225, top offset = (600 - 225) / 2 = 187.5
    expect(mapClientPointToViewport(180, 100, { left: 0, top: 0, width: 360, height: 600 })).toBeNull();
    expect(mapClientPointToViewport(180, 500, { left: 0, top: 0, width: 360, height: 600 })).toBeNull();
    expect(mapClientPointToViewport(180, 300, { left: 0, top: 0, width: 360, height: 600 })).toEqual({
      x: 640,
      y: 400,
    });
    // Wide landscape 800x400: rendered width = 400 * 1280 / 800 = 640, left offset = 80
    expect(mapClientPointToViewport(40, 200, { left: 0, top: 0, width: 800, height: 400 })).toBeNull();
    expect(mapClientPointToViewport(400, 200, { left: 0, top: 0, width: 800, height: 400 })).toEqual({
      x: 640,
      y: 400,
    });
  });

  it('queues social_request offline while laptop is away, flushes on reconnect, and still requires Start tap', async () => {
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_social_q',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    let laptopOnline = false;
    const inboxPayloads: unknown[] = [];
    const createdRun: SocialRunRecord = {
      id: 'srun_from_queue',
      platform: 'x',
      recipe: 'search',
      argument: 'Cero offline research',
      status: 'awaiting_start',
      approval_hash: 'hash_from_queue',
      pages_used: 0,
    };
    let runsOnLaptop: SocialRunRecord[] = [];
    let startCalls = 0;

    const bridge = createPreviewNativeBridge({
      httpHandler: makePhoneBaseHandler({
        onRequest: async (req) => {
          if (!laptopOnline) {
            throw new Error('Network unreachable');
          }
          if (req.url.endsWith('/phone/v1/inbox') && req.method === 'POST') {
            const body = JSON.parse(req.body || '{}');
            inboxPayloads.push(body);
            runsOnLaptop = [createdRun];
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({
                items: [
                  {
                    client_id: body.items[0].client_id,
                    kind: 'social_request',
                    id: createdRun.id,
                    deduplicated: false,
                    warnings: [],
                  },
                ],
              }),
            };
          }
          if (req.url.includes('/phone/v1/social/runs?')) {
            return { status: 200, headers: {}, body: JSON.stringify({ runs: runsOnLaptop }) };
          }
          if (req.url.endsWith(`/phone/v1/social/runs/${createdRun.id}/start`)) {
            startCalls += 1;
            runsOnLaptop = [{ ...createdRun, status: 'running' }];
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({ success: true, run: runsOnLaptop[0] }),
            };
          }
          return null;
        },
      }),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');

    await ensureSession(bridge);
    render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByText('Laptop unreachable')).toBeTruthy());

    // Queue a social request while offline
    fireEvent.change(screen.getByLabelText('Argument'), {
      target: { value: 'Cero offline research' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Queue social request' }));

    await waitFor(() => expect(loadQueue().length).toBe(1));
    expect(loadQueue()[0].kind).toBe('social_request');
    expect(startCalls).toBe(0);

    // Laptop comes back & app resumes -> inbox flushes, run appears as awaiting_start needing Start tap
    laptopOnline = true;
    act(() => {
      bridge.emitResume();
    });

    await waitFor(() => expect(inboxPayloads.length).toBe(1));
    await waitFor(() => expect(screen.getByText('Cero offline research')).toBeTruthy());
    expect(startCalls).toBe(0);
    const startBtn = screen.getByRole('button', { name: 'Start' });
    fireEvent.click(startBtn);
    await waitFor(() => expect(startCalls).toBe(1));
  });

  it('renders XSS results as plain text in Today & Chat, never writes secrets/frames/results to storage, and wipes on background/unpair/signout/account-change', async () => {
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_social_1',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const secretFrameB64 = btoa('TOP_SECRET_FRAME_BYTES_445566');
    const secretTyped = 'MY_SECRET_OTP_778899';
    const xssResultText = '<script>alert("xss")</script><img src=x onerror="alert(2)">UNTRUSTED_RESULT_PAYLOAD';
    const summaryText = 'AI_SUMMARY_TEXT_332211';

    let waitingRun: SocialRunRecord = {
      id: 'srun_mob_1',
      platform: 'x',
      recipe: 'search',
      argument: 'Cero terminal',
      status: 'waiting_user',
      challenge_kind: 'verification_code',
      result: {
        text: xssResultText,
        ai_summary: summaryText,
        untrusted: true,
      },
    };
    const sentLiveInputs: unknown[] = [];
    let framePollCount = 0;

    const bridge = createPreviewNativeBridge({
      httpHandler: makePhoneBaseHandler({
        onRequest: async (req) => {
          if (req.url.endsWith('/phone/v1/social/status')) {
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({
                ...phoneContract.social_status,
                active_run: waitingRun.status === 'waiting_user' ? waitingRun : null,
              }),
            };
          }
          if (req.url.includes('/phone/v1/social/runs?')) {
            return { status: 200, headers: {}, body: JSON.stringify({ runs: [waitingRun] }) };
          }
          if (req.url.endsWith('/phone/v1/social/live/frame')) {
            framePollCount += 1;
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({
                frame_base64: secretFrameB64,
                content_type: 'image/jpeg',
                width: 1280,
                height: 800,
                run_id: waitingRun.id,
                platform: 'x',
              }),
            };
          }
          if (req.url.endsWith('/phone/v1/social/live/input')) {
            sentLiveInputs.push(JSON.parse(req.body || '{}'));
            return { status: 200, headers: {}, body: JSON.stringify({ ok: true }) };
          }
          return null;
        },
      }),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'acc',
        refresh_token: 'ref',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        user_id: 'uid-1',
        email: 'u@example.com',
      }),
    );

    await ensureSession(bridge);
    const { container } = render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByText(xssResultText)).toBeTruthy());

    // XSS rendered strictly as plain text, labelled Untrusted
    expect(screen.getByText('Untrusted')).toBeTruthy();
    expect(screen.getByText(summaryText)).toBeTruthy();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();

    // Also visible in Chat tab
    fireEvent.click(screen.getByRole('tab', { name: /Chat/i }));
    await waitFor(() => expect(screen.getByText(xssResultText)).toBeTruthy());

    // Open full-screen live view via Solve now
    fireEvent.click(screen.getAllByRole('button', { name: 'Solve now' })[0]);
    const liveDialog = await screen.findByRole('dialog');
    await waitFor(() => expect(framePollCount).toBeGreaterThanOrEqual(1));

    // Masked text box clears right after send
    const maskedBox = within(liveDialog).getByLabelText('Masked live text input') as HTMLInputElement;
    expect(maskedBox.type).toBe('password');
    fireEvent.change(maskedBox, { target: { value: secretTyped } });
    fireEvent.click(within(liveDialog).getByRole('button', { name: 'Send' }));
    expect(maskedBox.value).toBe('');
    await waitFor(() =>
      expect(sentLiveInputs).toContainEqual({ action: 'text', text: secretTyped }),
    );

    // Drag to scroll and tap on image
    const liveImg = liveDialog.querySelector('img') as HTMLImageElement;
    liveImg.getBoundingClientRect = () => ({
      left: 0,
      top: 0,
      width: 640,
      height: 400,
      right: 640,
      bottom: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    fireEvent(
      liveImg,
      new MouseEvent('pointerdown', { bubbles: true, cancelable: true, clientX: 100, clientY: 100 }),
    );
    fireEvent(
      liveImg,
      new MouseEvent('pointerup', { bubbles: true, cancelable: true, clientX: 100, clientY: 200 }),
    );
    await waitFor(() =>
      expect(sentLiveInputs).toContainEqual({ action: 'scroll', delta_x: 0, delta_y: -200 }),
    );

    fireEvent(
      liveImg,
      new MouseEvent('pointerdown', { bubbles: true, cancelable: true, clientX: 320, clientY: 200 }),
    );
    fireEvent(
      liveImg,
      new MouseEvent('pointerup', { bubbles: true, cancelable: true, clientX: 320, clientY: 200 }),
    );
    await waitFor(() =>
      expect(sentLiveInputs).toContainEqual({ action: 'click', x: 640, y: 400, button: 'left' }),
    );

    // Storage MUST NOT contain frames, typed text, or social results
    const dumped = dumpStorage();
    expect(dumped).not.toContain(secretFrameB64);
    expect(dumped).not.toContain(secretTyped);
    expect(dumped).not.toContain('UNTRUSTED_RESULT_PAYLOAD');
    expect(dumped).not.toContain(summaryText);

    // Back button closes full-screen live view
    act(() => {
      bridge.emitBackButton();
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // App background wipes social results, closes live view, and stops polling
    const pollsBeforeBg = framePollCount;
    act(() => {
      bridge.emitBackground();
    });
    await waitFor(() => expect(screen.queryByText(xssResultText)).toBeNull());
    await new Promise((r) => setTimeout(r, 600));
    expect(framePollCount).toBe(pollsBeforeBg);

    // Resume reloads from laptop, and solving on laptop clears phone alert/live view
    act(() => {
      bridge.emitResume();
    });
    await waitFor(() => expect(screen.getByText(xssResultText)).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: 'Solve now' })[0]);
    await screen.findByRole('dialog');

    // Simulate laptop solving the check -> next foreground poll clears alert & closes live view
    waitingRun = { ...waitingRun, status: 'running', challenge_kind: null };
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), { timeout: 5000 });
    expect(screen.queryByRole('region', { name: 'Social security check alert' })).toBeNull();

    // Sign out wipes social runs from memory
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    const confirmSignOut = await screen.findByRole('dialog');
    fireEvent.click(confirmSignOut.querySelector('button.mg-btn--danger') as HTMLButtonElement);
    // Signing in is required, so signing out shows the welcome screen and none of the social results.
    expect(await screen.findByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(screen.queryByText(xssResultText)).toBeNull();
    expect(dumpStorage()).not.toContain('UNTRUSTED_RESULT_PAYLOAD');
  });

  it('wipes social runs and closes live view on account change via deep link', async () => {
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_acct_chg',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const bridge = createPreviewNativeBridge({
      httpHandler: makePhoneBaseHandler({
        onRequest: async (req) => {
          if (req.url.includes('/auth/v1/token')) {
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({
                access_token: 'acc2',
                refresh_token: 'ref2',
                expires_in: 3600,
                user: { id: 'uid-2', email: 'u2@example.com' },
              }),
            };
          }
          if (req.url.includes('/rest/v1/profiles')) {
            return {
              status: 200,
              headers: {},
              body: JSON.stringify([
                { id: 'uid-2', email: 'u2@example.com', display_name: 'User Two', role: 'member', active: true },
              ]),
            };
          }
          if (req.url.includes('/rest/v1/shared_knowledge')) {
            return { status: 200, headers: {}, body: JSON.stringify([]) };
          }
          if (req.url.includes('/phone/v1/social/runs?')) {
            return {
              status: 200,
              headers: {},
              body: JSON.stringify({
                runs: [
                  {
                    id: 'srun_acct_1',
                    platform: 'x',
                    recipe: 'search',
                    argument: 'Cero account1',
                    status: 'done',
                    result: { text: 'ACCOUNT1_SOCIAL_RESULT', untrusted: true },
                  },
                ],
              }),
            };
          }
          return null;
        },
      }),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'acc1',
        refresh_token: 'ref1',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );

    await ensureSession(bridge);
    render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByText('ACCOUNT1_SOCIAL_RESULT')).toBeTruthy());

    await bridge.secureStore.set(
      PKCE_SECRET_KEY,
      JSON.stringify({ state: 'st123', verifier: 'verif', createdAt: Date.now() }),
    );
    act(() => {
      bridge.emitDeepLink('com.magnm.studio://auth/callback?s=st123&code=authcode2');
    });

    await waitFor(() => expect(screen.queryByText('ACCOUNT1_SOCIAL_RESULT')).toBeNull());
  });

  it('discards stale social responses after unpair and wipes on sign-out and account change', async () => {
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_stale_social',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    let resolveLateRuns!: (resp: NativeHttpResponse) => void;
    const bridge = createPreviewNativeBridge({
      httpHandler: makePhoneBaseHandler({
        onRequest: async (req) => {
          if (req.url.includes('/phone/v1/social/runs?')) {
            return new Promise<NativeHttpResponse>((resolve) => {
              resolveLateRuns = resolve;
            });
          }
          return null;
        },
      }),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');

    await ensureSession(bridge);
    render(<App bridge={bridge} />);
    await waitFor(() => expect(typeof resolveLateRuns).toBe('function'));

    // Unpair BEFORE social/runs resolves
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Unpair laptop' }));
    await waitFor(() => expect(screen.getByText('Unpaired')).toBeTruthy());

    // Now resolve the stale in-flight social/runs response
    await act(async () => {
      resolveLateRuns({
        status: 200,
        headers: {},
        body: JSON.stringify({
          runs: [
            {
              id: 'srun_stale_leak',
              platform: 'x',
              recipe: 'search',
              argument: 'STALE_LEAK_ARGUMENT',
              status: 'done',
              result: { text: 'STALE_LEAK_RESULT', untrusted: true },
            },
          ],
        }),
      });
    });

    // Switch back to Today -> stale response must NOT appear
    fireEvent.click(screen.getByRole('tab', { name: /Today/i }));
    expect(screen.queryByText('STALE_LEAK_ARGUMENT')).toBeNull();
    expect(screen.queryByText('STALE_LEAK_RESULT')).toBeNull();
  });
});
