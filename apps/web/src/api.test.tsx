import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { ApiError, api, onUnauthorized } from './api';
import { App } from './App';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('api client', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.location.hash = '';
  });

  it('sends same-origin credentials and parses JSON responses', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ created: 'job-1' }));

    const resGet = await api.get<{ ok: boolean }>('/api/health');
    expect(resGet).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/health', {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      body: undefined,
    });

    const resPost = await api.post<{ created: string }>('/api/run', { job: 'daily_cycle' });
    expect(resPost).toEqual({ created: 'job-1' });
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/run', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ job: 'daily_cycle' }),
    });
  });

  it('throws typed ApiError with engine error message on non-OK responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({ error: 'Job not found' }, 404),
    );

    let caught: unknown = null;
    try {
      await api.get('/api/jobs/missing');
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(404);
    expect((caught as ApiError).message).toBe('Job not found');
  });

  it('notifies unauthorized listeners on 401 and switches App to Session ended', async () => {
    const unauthSpy = vi.fn();
    const unsubscribe = onUnauthorized(unauthSpy);

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      if (url === '/api/health') {
        return jsonResponse({ ok: true, service: 'magnm-engine' }, 200);
      }
      return jsonResponse({ error: 'unauthorized' }, 401);
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Session ended' })).toBeTruthy();
    });
    expect(unauthSpy).toHaveBeenCalled();
    expect(screen.getByText(/Reopen Magnm Studio/i)).toBeTruthy();
    unsubscribe();
  });
});
