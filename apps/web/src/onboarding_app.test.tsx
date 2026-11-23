import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';
import { installMemoryStorage } from './testStorage';
import { hasSeenOnboarding, markOnboardingSeen } from './onboarding';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockEngine(account: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    if (url === '/api/health') return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/x' });
    if (url === '/api/account') return jsonResponse(account);
    if (url === '/api/status') return jsonResponse({ env_status: {}, models_matrix: [], current_job: { running: false } });
    if (url.startsWith('/api/jobs')) return jsonResponse([]);
    return jsonResponse({ error: 'Not found' }, 404);
  });
}

const SIGNED_OUT = { configured: true, signed_in: false, user: null };
const SIGNED_IN = {
  configured: true,
  signed_in: true,
  user: { id: 'u1', email: 'a@example.com', display_name: 'A', avatar_url: null, role: 'member', active: true },
};

describe('first-run onboarding in the laptop app', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
    installMemoryStorage();
    window.location.hash = '#/today';
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the welcome screen on first run and lets local work continue, remembering the choice', async () => {
    mockEngine(SIGNED_OUT);
    const first = render(<App />);
    expect(await screen.findByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Studio sections' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Continue without signing in' }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull());
    expect(screen.getAllByRole('navigation', { name: 'Studio sections' }).length).toBeGreaterThan(0);
    expect(hasSeenOnboarding()).toBe(true);
    first.unmount();

    render(<App />);
    await screen.findAllByRole('navigation', { name: 'Studio sections' });
    expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull();
  });

  it('does not show it again once seen, and not to someone already signed in', async () => {
    markOnboardingSeen();
    mockEngine(SIGNED_OUT);
    const seen = render(<App />);
    await screen.findAllByRole('navigation', { name: 'Studio sections' });
    expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull();
    seen.unmount();

    installMemoryStorage();
    cleanup();
    vi.restoreAllMocks();
    mockEngine(SIGNED_IN);
    render(<App />);
    await screen.findAllByRole('navigation', { name: 'Studio sections' });
    expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull();
  });

  it('is skipped when team sign-in is not set up on this device', async () => {
    mockEngine({ configured: false, signed_in: false, user: null });
    render(<App />);
    await screen.findAllByRole('navigation', { name: 'Studio sections' });
    expect(screen.queryByRole('heading', { name: /welcome to magnm studio/i })).toBeNull();
  });

  it('comes back only from Settings > Account', async () => {
    markOnboardingSeen();
    mockEngine(SIGNED_OUT);
    window.location.hash = '#/settings';
    render(<App />);
    const again = await screen.findByRole('button', { name: 'Show the welcome screen' });
    fireEvent.click(again);
    expect(await screen.findByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(hasSeenOnboarding()).toBe(false);
  });
});
