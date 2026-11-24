import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AutomationCard } from './AutomationCard';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const STATE = {
  paused: false, paused_since: null, paused_by: null, missed: [], running: false, local_ai: false,
  allowed_kinds: ['post', 'blog', 'video_script'], allowed_platforms: ['x', 'linkedin', 'instagram', 'youtube'],
  settings: { kinds: ['post'], platforms: ['linkedin', 'x'], skip_recent_days: 7 },
  schedule: { id: 'auto_pipeline', enabled: false, cron: '30 10 * * *' },
  history: [
    {
      id: 'ap_1', started_at: '2026-11-24T10:30:00+00:00', trigger: 'schedule', status: 'ready', why: 'Random pick from 3 repositories (skipped: 1 archived).',
      repo: { id: 'alpha', name: 'Alpha app', scope: 'team', confidential: false },
      steps: [{ name: 'pick', status: 'done' }], runs: [],
    },
  ],
};

describe('AutomationCard', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows the run history with the repo picked, its scope and why', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse(STATE));
    render(<AutomationCard />);
    expect(await screen.findByText('Alpha app (team)')).toBeTruthy();
    expect(screen.getByText(/Random pick from 3 repositories/)).toBeTruthy();
    expect(screen.getByText('Ready to review')).toBeTruthy();
  });

  it('saves settings, runs now and flips the switch', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/automation/pause') return jsonResponse({ ...STATE, paused: true });
      return jsonResponse(STATE);
    });
    render(<AutomationCard />);
    await screen.findByText('Automatic work');
    fireEvent.click(screen.getByLabelText('Blog article'));
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/automation/settings',
        expect.objectContaining({ body: JSON.stringify({ kinds: ['post', 'blog'], platforms: ['linkedin', 'x'], skip_recent_days: 7 }) }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/automation/run', expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/automation/pause', expect.objectContaining({ body: JSON.stringify({ paused: true }) })));
  });

  it('lists missed runs, which are only run when the owner chooses', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse({ ...STATE, missed: [{ id: 'market_scan', name: 'Market scan', due_at: '2026-11-24T08:00:00+00:00' }] }),
    );
    render(<AutomationCard />);
    expect(await screen.findByText('Missed: Market scan')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/automation/missed')).toBe(false);
  });

  it('stays hidden when the engine has no automatic work (older engine) or answers with junk', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse([]));
    const { container } = render(<AutomationCard />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe('');
  });
});
