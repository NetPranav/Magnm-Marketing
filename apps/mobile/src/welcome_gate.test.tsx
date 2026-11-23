import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { App } from './App';
import { SESSION_SECRET_KEY, PKCE_SECRET_KEY, AUTH_REDIRECT_URL } from './auth';
import { createPreviewNativeBridge } from './native';
import { getPublicSupabaseConfig } from './net';
import { saveCachedProductRules } from './cache';

const SUPA = getPublicSupabaseConfig();
const SESSION = { access_token: 'jwt', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: 'uid-1', email: 'a@example.com' };

function encodeQr(obj: unknown): string {
  const b64 = btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return `magnm-pair:${b64}`;
}

describe('phone sign-in is required first', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => cleanup());

  it('shows a welcome screen instead of the app until someone signs in, and says sign-up is invite-only', async () => {
    const opened: string[] = [];
    const bridge = createPreviewNativeBridge({ onOpenBrowser: (url) => opened.push(url) });
    render(<App bridge={bridge} />);

    expect(await screen.findByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(screen.getByText(/Ask your admin to invite your Google account/)).toBeTruthy();
    expect(screen.queryByRole('tab', { name: /Today/i })).toBeNull();
    expect(screen.queryByRole('tab', { name: /More/i })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Sign in with Google' }));
    await waitFor(() => expect(opened).toHaveLength(1));
    expect(opened[0]).toContain(`${SUPA.url}/auth/v1/authorize`);
  });

  it('shows the refusal message when the Google account is not invited', async () => {
    const bridge = createPreviewNativeBridge({ httpHandler: async () => ({ status: 200, headers: {}, body: '{}' }) });
    render(<App bridge={bridge} />);
    await screen.findByRole('heading', { name: /welcome to magnm studio/i });
    await bridge.secureStore.set(PKCE_SECRET_KEY, JSON.stringify({ state: 'st', verifier: 'v', createdAt: Date.now() }));
    bridge.emitDeepLink(
      `${AUTH_REDIRECT_URL}?s=st&error=server_error&error_description=${encodeURIComponent('Database error saving new user')}`,
    );
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/not invited/i);
    expect(alert.textContent).toMatch(/Ask your admin to invite/i);
    expect(screen.getByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
  });

  it('opens the app once signed in, with pairing still available under More', async () => {
    const bridge = createPreviewNativeBridge({ httpHandler: async () => ({ status: 404, headers: {}, body: '{}' }) });
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify(SESSION));
    saveCachedProductRules('uid-1', { version: 1, show_levels: { everything: 'All' }, products: [] });
    render(<App bridge={bridge} />);
    fireEvent.click(await screen.findByRole('tab', { name: /More/i }));
    expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull();
    expect(screen.getByLabelText('Pairing code (magnm-pair:...)')).toBeTruthy();
  });
});

describe('phone pairing by code', () => {
  beforeEach(async () => window.localStorage.clear());
  afterEach(() => cleanup());

  async function openMore(scan: string | null) {
    const bridge = createPreviewNativeBridge({
      scanQrResult: scan,
      httpHandler: async () => ({ status: 404, headers: {}, body: '{}' }),
    });
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify(SESSION));
    saveCachedProductRules('uid-1', { version: 1, show_levels: { everything: 'All' }, products: [] });
    render(<App bridge={bridge} />);
    fireEvent.click(await screen.findByRole('tab', { name: /More/i }));
    return bridge;
  }

  it('says a cancelled scan was cancelled and points to pasting the code', async () => {
    await openMore(null);
    fireEvent.click(screen.getByRole('button', { name: 'Scan QR' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/scan was cancelled/i);
    expect(alert.textContent).toMatch(/paste the pairing code/i);
  });

  it('pastes a code and Continue moves on to naming the phone', async () => {
    await openMore(null);
    const qr = encodeQr({ v: 1, host: '100.70.1.2', port: 8788, code: 'abc123' });
    fireEvent.change(screen.getByLabelText('Pairing code (magnm-pair:...)'), { target: { value: qr } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByText(/Pair with this laptop\? 100\.70\.1\.2:8788/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps the page in place when a personal key is saved or removed (nothing reloads)', async () => {
    await openMore(null);
    const shell = document.querySelector('.mob-shell');
    fireEvent.change(screen.getByPlaceholderText('nvapi-...'), { target: { value: 'nvapi-test-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await screen.findByRole('button', { name: 'Remove key' });
    expect(document.querySelector('.mob-shell')).toBe(shell);
    expect(screen.getByRole('tab', { name: /More/i }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Remove key' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove key' })).toBeNull());
    expect(document.querySelector('.mob-shell')).toBe(shell);
  });
});
