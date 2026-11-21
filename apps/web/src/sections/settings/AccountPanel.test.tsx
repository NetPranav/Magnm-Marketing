import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { AccountRole, InviteRecord } from '../../api/account';
import { AccountPanel } from './AccountPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('AccountPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('renders not configured state', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({ configured: false, signed_in: false, user: null }),
    );

    render(<AccountPanel />);

    await waitFor(() => {
      expect(screen.getByText(/Team sign-in is not set up/i)).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Sign in with Google' })).toBeNull();
  });

  it('shows last_error when signed out, starts login, polls until signed in, and stops polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    let accountCalls = 0;
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        accountCalls += 1;
        if (accountCalls === 1) {
          return jsonResponse({
            configured: true,
            signed_in: false,
            user: null,
            last_error: 'This Google account is not invited to Magnm Studio. Ask your admin to invite it.',
          });
        }
        if (accountCalls === 2) {
          return jsonResponse({ configured: true, signed_in: false, user: null, last_error: null });
        }
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'u-1',
            email: 'member@example.com',
            display_name: 'Maya',
            avatar_url: 'https://example.com/maya.png',
            role: 'member',
            active: true,
          },
          last_error: null,
        });
      }
      if (url === '/api/account/login/start' && init?.method === 'POST') {
        return jsonResponse({
          authorize_url: 'https://example.supabase.co/auth/v1/authorize?provider=google',
        });
      }
      return jsonResponse({});
    });

    render(<AccountPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain(
        'This Google account is not invited to Magnm Studio.',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Sign in with Google' }));

    await waitFor(() => {
      expect(screen.getByText('Waiting for Google sign-in in your browser...')).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/account/login/start',
      expect.objectContaining({ method: 'POST' }),
    );
    const fallbackLink = screen.getByRole('link', { name: 'Open the sign-in page again' });
    expect(fallbackLink.getAttribute('href')).toBe(
      'https://example.supabase.co/auth/v1/authorize?provider=google',
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();

    // First poll: still signed out
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText('Waiting for Google sign-in in your browser...')).toBeTruthy();

    // Second poll: signed in -> stops polling and renders member profile (no invites)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await waitFor(() => {
      expect(screen.getByText('Maya')).toBeTruthy();
    });
    expect(screen.getByText('member@example.com')).toBeTruthy();
    expect(screen.getByText('Member')).toBeTruthy();
    expect(screen.queryByText('Invite people')).toBeNull();

    const callsAfterSignIn = accountCalls;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(accountCalls).toBe(callsAfterSignIn);
    expect(fetchMock).not.toHaveBeenCalledWith('/api/account/invites', expect.anything());
  });

  it('renders deactivated state and allows sign out', async () => {
    let signedIn = true;
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: signedIn,
          deactivated: signedIn,
          user: null,
        });
      }
      if (url === '/api/account/logout' && init?.method === 'POST') {
        signedIn = false;
        return jsonResponse({ ok: true });
      }
      return jsonResponse({});
    });

    render(<AccountPanel />);

    await waitFor(() => {
      expect(screen.getByText('Your account has been deactivated. Ask your admin.')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/account/logout',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(screen.getByRole('button', { name: 'Sign in with Google' })).toBeTruthy();
    });
  });

  it('lets admins validate email, create, list, and revoke invites with confirmation', async () => {
    let invites: InviteRecord[] = [
      {
        email: 'open@example.com',
        role: 'member',
        created_at: '2026-11-21T00:00:00Z',
        accepted_at: null,
        revoked_at: null,
      },
      {
        email: 'joined@example.com',
        role: 'admin',
        created_at: '2026-11-20T00:00:00Z',
        accepted_at: '2026-11-20T01:00:00Z',
        revoked_at: null,
      },
      {
        email: 'revoked@example.com',
        role: 'member',
        created_at: '2026-11-19T00:00:00Z',
        accepted_at: null,
        revoked_at: '2026-11-19T02:00:00Z',
      },
    ];

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'admin-1',
            email: 'admin@example.com',
            display_name: 'Founder',
            avatar_url: null,
            role: 'admin',
            active: true,
          },
        });
      }
      if (url === '/api/account/invites' && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ invites });
      }
      if (url === '/api/account/invites' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { email: string; role: AccountRole };
        const created: InviteRecord = {
          email: body.email,
          role: body.role,
          created_at: '2026-11-21T01:00:00Z',
          accepted_at: null,
          revoked_at: null,
        };
        invites = [created, ...invites];
        return jsonResponse({ invite: created });
      }
      if (url === '/api/account/invites/revoke' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { email: string };
        invites = invites.map((inv) =>
          inv.email === body.email ? { ...inv, revoked_at: '2026-11-21T02:00:00Z' } : inv,
        );
        return jsonResponse({ invite: invites.find((i) => i.email === body.email) });
      }
      return jsonResponse({});
    });

    render(<AccountPanel />);

    await waitFor(() => {
      expect(screen.getByText('Invite people')).toBeTruthy();
      expect(screen.getByText('open@example.com')).toBeTruthy();
    });

    expect(screen.getAllByText('Admin').length).toBeGreaterThanOrEqual(1);
    const inviteList = screen.getByLabelText('Team invites');
    expect(within(inviteList).getByText('Joined')).toBeTruthy();
    expect(within(inviteList).getByText('Revoked')).toBeTruthy();
    expect(within(inviteList).getByText('Invited')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Revoke invite for joined@example.com' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Revoke invite for revoked@example.com' })).toBeNull();

    // Invalid email is blocked in the browser
    const emailInput = screen.getByLabelText('Invite email') as HTMLInputElement;
    fireEvent.change(emailInput, { target: { value: 'invalid-email' } });
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Enter a valid email address.');
    });
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/account/invites',
      expect.objectContaining({ method: 'POST' }),
    );

    // Valid email creates invite
    fireEvent.change(emailInput, { target: { value: 'new@example.com' } });
    fireEvent.change(screen.getByLabelText('Invite role'), { target: { value: 'admin' } });
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/account/invites',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ email: 'new@example.com', role: 'admin' }),
        }),
      );
      expect(screen.getByText('new@example.com')).toBeTruthy();
    });

    // Revoke requires confirmation dialog
    fireEvent.click(screen.getByRole('button', { name: 'Revoke invite for open@example.com' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/open@example.com/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith('/api/account/invites/revoke', expect.anything());

    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke invite' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/account/invites/revoke',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ email: 'open@example.com' }),
        }),
      );
      expect(screen.queryByRole('button', { name: 'Revoke invite for open@example.com' })).toBeNull();
    });
  });

  it('renders workspace sync states, hint text, Sync now button, and 10s polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    let syncMode: 'synced' | 'syncing' | 'offline' | 'error' = 'synced';
    let syncCalls = 0;
    const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000).toISOString();

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'u-1',
            email: 'member@example.com',
            display_name: 'Maya',
            avatar_url: null,
            role: 'member',
            active: true,
          },
        });
      }
      if (url === '/api/account/sync' && (!init?.method || init.method === 'GET')) {
        syncCalls += 1;
        if (syncMode === 'synced') {
          return jsonResponse({
            enabled: true,
            signed_in: true,
            last_sync_at: twoMinutesAgo,
            last_error: null,
            pending: 0,
            synced_count: 128,
            running: false,
          });
        }
        if (syncMode === 'syncing') {
          return jsonResponse({
            enabled: true,
            signed_in: true,
            last_sync_at: twoMinutesAgo,
            last_error: null,
            pending: 3,
            synced_count: 128,
            running: true,
          });
        }
        if (syncMode === 'offline') {
          return jsonResponse({
            enabled: true,
            signed_in: true,
            last_sync_at: twoMinutesAgo,
            last_error: 'Waiting for connection',
            pending: 2,
            synced_count: 128,
            running: false,
          });
        }
        return jsonResponse({
          enabled: true,
          signed_in: true,
          last_sync_at: twoMinutesAgo,
          last_error: 'Supabase HTTP 500',
          pending: 2,
          synced_count: 128,
          running: false,
        });
      }
      if (url === '/api/account/sync' && init?.method === 'POST') {
        syncMode = 'syncing';
        return jsonResponse({
          enabled: true,
          signed_in: true,
          last_sync_at: twoMinutesAgo,
          last_error: null,
          pending: 3,
          synced_count: 128,
          running: true,
        });
      }
      return jsonResponse({});
    });

    const { unmount } = render(<AccountPanel />);

    await waitFor(() => {
      expect(screen.getByText('Workspace sync: synced 2 minutes ago · 128 items')).toBeTruthy();
    });
    expect(
      screen.getByText(
        'Your admin can read a copy of your chats, drafts and research. Confidential chats are not copied.',
      ),
    ).toBeTruthy();

    // Clicking Sync now triggers POST /api/account/sync and shows Syncing...
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/account/sync',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(screen.getByText('Workspace sync: Syncing...')).toBeTruthy();
    });

    // 10s poll picks up Waiting for connection
    syncMode = 'offline';
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    await waitFor(() => {
      expect(screen.getByText('Workspace sync: Waiting for connection')).toBeTruthy();
    });

    // Next 10s poll picks up error message
    syncMode = 'error';
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    await waitFor(() => {
      expect(screen.getByText('Workspace sync: Supabase HTTP 500')).toBeTruthy();
    });

    // Unmounting stops the 10s polling
    const callsBeforeUnmount = syncCalls;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(syncCalls).toBe(callsBeforeUnmount);
  });

  it('shows media previews status line (including missing FFmpeg) and admin storage meter with 80% notice', async () => {
    let role: AccountRole = 'member';
    let hasFfmpeg = true;
    let storagePercent = 11.2;
    let storageBytes = 120 * 1024 * 1024;
    let storageWarning = false;

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'u-1',
            email: role === 'admin' ? 'admin@example.com' : 'member@example.com',
            display_name: role === 'admin' ? 'Admin' : 'Maya',
            avatar_url: null,
            role,
            active: true,
          },
        });
      }
      if (url === '/api/account/sync') {
        return jsonResponse({
          enabled: true,
          signed_in: true,
          last_sync_at: null,
          last_error: null,
          pending: 0,
          synced_count: 10,
          running: false,
        });
      }
      if (url === '/api/account/media') {
        return jsonResponse({
          ffmpeg: hasFfmpeg,
          uploaded: 12,
          waiting: 3,
          failed: 0,
          last_error: hasFfmpeg ? null : 'FFmpeg is not installed',
        });
      }
      if (url === '/api/account/storage') {
        return jsonResponse({
          bytes: storageBytes,
          files: 20,
          by_kind: {},
          limit_bytes: 1073741824,
          percent: storagePercent,
          warning: storageWarning,
        });
      }
      if (url === '/api/account/invites') {
        return jsonResponse({ invites: [] });
      }
      return jsonResponse({});
    });

    // Member view: shows previews line, does not request or render team storage.
    const view1 = render(<AccountPanel />);
    await waitFor(() => {
      expect(screen.getByText('Previews: 12 uploaded · 3 waiting')).toBeTruthy();
    });
    expect(screen.queryByText(/Team storage:/)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalledWith('/api/account/storage', expect.anything());
    view1.unmount();

    // Missing FFmpeg state
    hasFfmpeg = false;
    const view2 = render(<AccountPanel />);
    await waitFor(() => {
      expect(screen.getByText('Install FFmpeg to share previews')).toBeTruthy();
    });
    view2.unmount();

    // Admin view under 80%: shows Team storage and progress bar without warning notice
    role = 'admin';
    hasFfmpeg = true;
    const view3 = render(<AccountPanel />);
    await waitFor(() => {
      expect(screen.getByText('Team storage: 120 MB of 1 GB')).toBeTruthy();
    });
    expect(screen.getByRole('progressbar', { name: 'Team storage usage' })).toBeTruthy();
    expect(screen.queryByText(/Storage is almost full/)).toBeNull();
    view3.unmount();

    // Admin view at >= 80%: shows Backblaze B2 notice
    storageBytes = 860 * 1024 * 1024;
    storagePercent = 84.0;
    storageWarning = true;
    render(<AccountPanel />);
    await waitFor(() => {
      expect(screen.getByText('Team storage: 860 MB of 1 GB')).toBeTruthy();
      expect(
        screen.getByText('Storage is almost full. Video previews will move to Backblaze B2.'),
      ).toBeTruthy();
    });
  });
});
