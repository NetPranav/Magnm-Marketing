import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TodaySection } from './TodaySection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('TodaySection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders active sessions, open gates, pending facts, waiting drafts, activity, and cancels jobs', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/sessions') && (!init?.method || init.method === 'GET')) {
        return jsonResponse([
          {
            id: 'sess-1',
            agent_id: 'writer',
            status: 'waiting_user',
            title: 'Draft announcement',
            pending_question: {
              question: 'Use formal or casual tone?',
              options: ['Formal', 'Casual'],
            },
          },
        ]);
      }
      if (url.startsWith('/api/workflows')) {
        return jsonResponse([
          {
            id: 'wf-1',
            kind: 'post',
            status: 'waiting_user',
            current_step: 2,
            inputs: {},
            state: {},
            gates: [
              {
                id: 'gate-1',
                run_id: 'wf-1',
                step: 2,
                question: 'Approve LinkedIn draft?',
                options: ['Approve', 'Revise'],
                status: 'pending',
              },
            ],
          },
        ]);
      }
      if (url.startsWith('/api/facts')) {
        return jsonResponse([{ id: 'f1', product_id: 'cero', text: 'Runs locally', source_kind: 'repo', status: 'pending' }]);
      }
      if (url.startsWith('/api/content')) {
        return jsonResponse([
          {
            id: 'cnt-1',
            type: 'post',
            platform: 'linkedin',
            title: 'Cero launch post',
            status: 'ready',
            created_by: 'writer',
            created_at: '2026-11-19T08:00:00Z',
            updated_at: '2026-11-19T08:00:00Z',
          },
        ]);
      }
      if (url === '/api/jobs/cancel' && init?.method === 'POST') {
        return jsonResponse({ success: true, job: { id: 'job-run', status: 'cancelled' } });
      }
      if (url === '/api/gates/gate-1/answer' && init?.method === 'POST') {
        return jsonResponse({ gate: { id: 'gate-1', status: 'answered' }, run: { id: 'wf-1' } });
      }
      if (url === '/api/sessions/sess-1/answer' && init?.method === 'POST') {
        return jsonResponse({ success: true, session_id: 'sess-1', status: 'running' });
      }
      return jsonResponse({});
    });

    render(
      <TodaySection
        health={{ ok: true }}
        status={{
          env_status: { default_provider: 'nvidia_nim', nvidia_nim_global: true, nvidia_nim_roles_configured: 7 },
          models_matrix: new Array(7).fill({ has_key: true }),
          activity_log: ['2026-11-19T08:30:00Z | CEO -> writer | draft_post | outputs/posts/p1.md'],
        }}
        jobs={[
          { id: 'job-run', name: 'market_scan', status: 'running', progress: 40, message: 'Scanning HN' },
          { id: 'job-done', name: 'daily_cycle', status: 'done', progress: 100, message: 'Done' },
        ]}
        loading={false}
        error={null}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Approve LinkedIn draft?')).toBeTruthy();
    });

    expect(screen.getByText('Use formal or casual tone?')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Cero launch post/i }).getAttribute('href')).toBe('#/review/cnt-1');
    expect(screen.getByRole('link', { name: 'Review in Repos' }).getAttribute('href')).toBe('#/repos');
    expect(screen.getByText('draft_post')).toBeTruthy();

    // Cancel running job
    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelButtons[0]);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ id: 'job-run' }),
        }),
      );
    });

    // Answer gate
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/gates/gate-1/answer',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ answer: 'Approve', feedback: null }),
        }),
      );
    });

    // Answer waiting session option
    fireEvent.click(screen.getByRole('button', { name: 'Formal' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-1/answer',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ answer: 'Formal' }),
        }),
      );
    });
  });

  it('renders empty and error states cleanly', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse({ error: 'Database unavailable' }, 500),
    );

    render(
      <TodaySection
        health={{ ok: false }}
        status={null}
        jobs={[]}
        loading={false}
        error="Engine offline"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Engine offline');
    });
    expect(screen.getByText('Nothing is running right now.')).toBeTruthy();
    expect(screen.getByText('No drafts waiting for review right now.')).toBeTruthy();
    expect(screen.getByText('No recent activity logged yet.')).toBeTruthy();
  });
});
