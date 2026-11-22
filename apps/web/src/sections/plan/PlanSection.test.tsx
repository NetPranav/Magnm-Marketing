import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PlanSection } from './PlanSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('PlanSection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('changes the time and day of a timed schedule', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/status') {
        return jsonResponse({
          schedules: [{ id: 'cmo_daily', name: 'Daily CMO Cycle', command: 'daily_cycle', cron: '46 8 * * *', enabled: true }],
          editorial_plan: [],
        });
      }
      if (url === '/api/schedule/update') return jsonResponse({ success: true });
      return jsonResponse([]);
    });
    render(<PlanSection />);
    expect(await screen.findByText('Every day at 08:46 IST · never run')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Time (IST)'), { target: { value: '07:15' } });
    fireEvent.change(screen.getByLabelText('Day'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/schedule/update',
        expect.objectContaining({ body: JSON.stringify({ id: 'cmo_daily', enabled: true, time: '07:15', day: '5' }) }),
      );
    });
  });

  it('renders weekly plan, briefs, editorial calendar, schedules, and triggers main actions', async () => {
    const todayIso = new Date().toISOString().slice(0, 10);
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/workflows?')) {
        return jsonResponse([
          {
            id: 'wf-plan-1',
            kind: 'weekly_plan',
            status: 'done',
            current_step: 4,
            inputs: { week_start: '2026-11-23', platforms: ['linkedin', 'x'], posts_per_week: 2 },
            state: {
              proposed_slots: [
                {
                  date: todayIso,
                  platform: 'linkedin',
                  topic: 'Cero terminal safety',
                  angle: 'Asks before editing',
                  product_id: 'cero',
                },
              ],
            },
            gates: [],
            content_ids: [],
          },
        ]);
      }
      if (url.startsWith('/api/content?')) {
        return jsonResponse([
          {
            id: 'cnt-cal-1',
            type: 'post',
            platform: 'x',
            title: 'Shipped v2.2.3',
            status: 'posted',
            created_at: `${todayIso}T08:00:00Z`,
            updated_at: `${todayIso}T08:00:00Z`,
            posted_at: `${todayIso}T09:00:00Z`,
          },
        ]);
      }
      if (url === '/api/status') {
        return jsonResponse({
          schedules: [
            {
              id: 'market_scan',
              name: 'Market Scanner',
              command: 'market_scan',
              interval_hours: 6,
              description: 'Scans HN',
              enabled: true,
            },
          ],
          editorial_plan: [],
        });
      }
      if (url === '/api/workflows/start' && init?.method === 'POST') {
        return jsonResponse({ id: 'wf-plan-2', kind: 'weekly_plan', status: 'queued' });
      }
      if (url === '/api/schedule/toggle' && init?.method === 'POST') {
        return jsonResponse({ success: true });
      }
      if (url === '/api/schedule/update' && init?.method === 'POST') {
        return jsonResponse({ success: true });
      }
      return jsonResponse({});
    });

    render(<PlanSection />);

    await waitFor(() => {
      expect(screen.getAllByText('Cero terminal safety').length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText('MON')).toBeTruthy();
    expect(screen.getByText('Shipped v2.2.3')).toBeTruthy();
    expect(screen.getByText('Market Scanner')).toBeTruthy();

    // Start Plan my week workflow
    fireEvent.click(screen.getByRole('button', { name: 'Plan my week' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workflows/start',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            kind: 'weekly_plan',
            inputs: { posts_per_week: 3, platforms: ['linkedin', 'x'] },
          }),
        }),
      );
    });

    // Toggle schedule
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/schedule/toggle',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ id: 'market_scan' }),
        }),
      );
    });

    // Edit schedule via Dialog and save
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Interval (hours)'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/schedule/update',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ id: 'market_scan', enabled: true, interval_hours: 12 }),
        }),
      );
    });
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/status') {
        return jsonResponse({ error: 'Scheduler unreadable' }, 500);
      }
      return jsonResponse([]);
    });

    render(<PlanSection />);
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Scheduler unreadable');
    });
    expect(screen.getByText(/No weekly plan generated yet/i)).toBeTruthy();
    expect(screen.getByText('No schedules configured.')).toBeTruthy();
  });

  it('plans the week on a chosen repository and says so when it is confidential', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/repos') {
        return jsonResponse([
          { id: 'open', name: 'OpenRepo', kind: 'github', confidential: false, scope: 'personal' },
          { id: 'team-secret', name: 'SecretRepo', kind: 'github', confidential: true, scope: 'global' },
        ]);
      }
      if (url === '/api/status') return jsonResponse({ schedules: [], editorial_plan: [] });
      if (url === '/api/workflows/start') return jsonResponse({ id: 'wf-1', kind: 'weekly_plan', status: 'queued' });
      return jsonResponse([]);
    });
    render(<PlanSection />);
    const picker = (await screen.findByLabelText(/Repository/)) as HTMLSelectElement;
    await waitFor(() => expect(picker.options.length).toBe(3));
    fireEvent.change(picker, { target: { value: 'team-secret' } });
    expect(screen.getByText(/the whole plan runs on local AI only/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Plan my week' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workflows/start',
        expect.objectContaining({
          body: JSON.stringify({
            kind: 'weekly_plan',
            inputs: { posts_per_week: 3, platforms: ['linkedin', 'x'], repo_id: 'team-secret' },
          }),
        }),
      );
    });
  });
});
