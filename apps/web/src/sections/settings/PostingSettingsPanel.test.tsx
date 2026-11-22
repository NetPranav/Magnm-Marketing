import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PostingSettingsPanel } from './PostingSettingsPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('PostingSettingsPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows setup steps, connects LinkedIn/Instagram/X, clears token fields, refreshes Instagram, and disconnects', async () => {
    let liConnected = false;
    let igConnected = false;
    let igRefreshed = false;
    let xOAuthStarted = false;
    const calls: Array<{ url: string; body?: unknown }> = [];

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });

      if (url === '/api/posting/status') {
        return jsonResponse({
          platforms: {
            linkedin: {
              platform: 'linkedin',
              connected: liConnected,
              account_id: liConnected ? 'urn:li:person:1' : null,
              display_name: liConnected ? 'Jane Doe' : null,
              days_until_expiry: liConnected ? 5 : null,
              expiring_soon: liConnected,
              expired: false,
            },
            instagram: {
              platform: 'instagram',
              connected: igConnected,
              account_id: igConnected ? 'ig-1' : null,
              display_name: igConnected ? '@cero_ig' : null,
              days_until_expiry: igConnected ? (igRefreshed ? 60 : 12) : null,
              expiring_soon: false,
              expired: false,
            },
            x: {
              platform: 'x',
              connected: false,
              oauth_active: xOAuthStarted,
            },
          },
        });
      }
      if (url === '/api/posting/connect/linkedin') {
        liConnected = true;
        return jsonResponse({ ok: true });
      }
      if (url === '/api/posting/connect/instagram') {
        igConnected = true;
        return jsonResponse({ ok: true });
      }
      if (url === '/api/posting/refresh/instagram') {
        igRefreshed = true;
        return jsonResponse({ ok: true });
      }
      if (url === '/api/posting/connect/x/start') {
        xOAuthStarted = true;
        return jsonResponse({
          authorize_url: 'https://x.com/i/oauth2/authorize?state=s1',
          redirect_uri: 'http://127.0.0.1:47831/callback',
          expires_at: '2026-11-22T12:05:00Z',
          callback_port: 47831,
        });
      }
      if (url === '/api/posting/disconnect') {
        if ((body as { platform?: string })?.platform === 'linkedin') liConnected = false;
        return jsonResponse({ ok: true });
      }
      return jsonResponse({});
    });

    vi.stubGlobal('open', vi.fn());

    render(<PostingSettingsPanel />);

    expect(await screen.findByText(/LinkedIn Developer Portal Token Generator/i)).toBeTruthy();
    expect(screen.getByText(/Instagram Professional account/i)).toBeTruthy();
    expect(screen.getByText(/127\.0\.0\.1:47831\/callback/)).toBeTruthy();

    // Connect LinkedIn: token field is password + autocomplete off + cleared on save
    const liInput = screen.getByLabelText('LinkedIn access token') as HTMLInputElement;
    expect(liInput.type).toBe('password');
    expect(liInput.autocomplete).toBe('off');
    fireEvent.change(liInput, { target: { value: 'CANARY_UI_LI_TOK' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0]);

    await waitFor(() => {
      expect(liInput.value).toBe('');
      expect(screen.getByText(/LinkedIn token expires in 5 day\(s\)\./)).toBeTruthy();
    });

    // Connect Instagram and trigger explicit refresh
    const igInput = screen.getByLabelText('Instagram access token') as HTMLInputElement;
    fireEvent.change(igInput, { target: { value: 'CANARY_UI_IG_TOK' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0]);

    const refreshBtn = await screen.findByRole('button', { name: 'Refresh token' });
    fireEvent.click(refreshBtn);
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/posting/refresh/instagram')).toBe(true);
    });

    // Start X OAuth
    const xInput = screen.getByLabelText('Client ID') as HTMLInputElement;
    fireEvent.change(xInput, { target: { value: 'x-pub-client' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect with X' }));
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/posting/connect/x/start')).toBe(true);
    });

    // Disconnect LinkedIn
    fireEvent.click(screen.getAllByRole('button', { name: 'Disconnect' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm disconnect' }));
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/posting/disconnect')).toBe(true);
    });
  });
});
