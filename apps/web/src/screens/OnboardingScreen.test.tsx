import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { OnboardingScreen } from './OnboardingScreen';
import { installMemoryStorage } from '../testStorage';
import { hasSeenOnboarding, markOnboardingSeen, resetOnboarding } from '../onboarding';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const NOT_INVITED = 'This Google account is not invited to Magnm Studio. Ask your admin to invite it.';

describe('onboarding state', () => {
  beforeEach(() => installMemoryStorage());

  it('is unseen on first run, remembered once seen, and can be reset from Settings', () => {
    expect(hasSeenOnboarding()).toBe(false);
    markOnboardingSeen();
    expect(hasSeenOnboarding()).toBe(true);
    resetOnboarding();
    expect(hasSeenOnboarding()).toBe(false);
  });
});

describe('OnboardingScreen', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('welcomes a new person, says sign-up is invite-only, and offers both ways forward', () => {
    render(<OnboardingScreen onSignedIn={() => {}} onContinue={() => {}} />);
    expect(screen.getByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(screen.getByText(/Ask your admin to invite your Google account/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign in with Google' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Continue without signing in' })).toBeTruthy();
  });

  it('starts the existing Google flow and shows the server refusal for an account that is not invited', async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      calls.push(url);
      if (url === '/api/account/login/start') return jsonResponse({ authorize_url: 'https://accounts.example/auth' });
      if (url === '/api/account') {
        return jsonResponse({ configured: true, signed_in: false, user: null, last_error: NOT_INVITED });
      }
      return jsonResponse({});
    });
    const onSignedIn = vi.fn();
    render(<OnboardingScreen onSignedIn={onSignedIn} onContinue={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'Sign in with Google' }));
    await screen.findByText(/Waiting for Google sign-in/i);
    expect(calls).toContain('/api/account/login/start');

    await vi.advanceTimersByTimeAsync(2100);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(NOT_INVITED);
    expect(onSignedIn).not.toHaveBeenCalled();
    // The person can try another account.
    expect(screen.getByRole('button', { name: 'Sign in with Google' })).toBeTruthy();
  });

  it('reports a finished sign-in', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/account/login/start') return jsonResponse({ authorize_url: 'https://accounts.example/auth' });
      if (url === '/api/account') {
        return jsonResponse({
          configured: true, signed_in: true,
          user: { id: 'u1', email: 'a@example.com', display_name: 'A', avatar_url: null, role: 'member', active: true },
        });
      }
      return jsonResponse({});
    });
    const onSignedIn = vi.fn();
    render(<OnboardingScreen onSignedIn={onSignedIn} onContinue={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in with Google' }));
    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
  });

  it('lets local work continue without signing in', () => {
    const onContinue = vi.fn();
    render(<OnboardingScreen onSignedIn={() => {}} onContinue={onContinue} />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue without signing in' }));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('shows a plain error when sign-in cannot start', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse({ error: 'Team sign-in is not set up on this device.' }, 400));
    render(<OnboardingScreen onSignedIn={() => {}} onContinue={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in with Google' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Team sign-in is not set up on this device.');
  });
});
