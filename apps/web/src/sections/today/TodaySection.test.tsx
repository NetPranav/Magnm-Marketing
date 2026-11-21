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

  it('renders Reminders card (overdue first, done, snooze, delete, add) and Results card pending list', async () => {
    const pastIso = '2026-11-20T09:00:00Z';
    const futureIso = '2099-12-01T09:00:00Z';
    const remindersList = [
      {
        id: 'rem-future',
        title: 'Upcoming check-in',
        note: 'Later note',
        due_at: futureIso,
        kind: 'custom',
        content_id: null,
        created_by: 'user',
        created_at: '2026-11-19T08:00:00Z',
        done_at: null,
        snoozed_until: null,
        fired_at: null,
      },
      {
        id: 'rem-overdue',
        title: 'Add results for Launch Post (day 1)',
        note: 'LinkedIn stats',
        due_at: pastIso,
        kind: 'results_due',
        content_id: 'cnt-posted-1',
        created_by: 'analyst',
        created_at: '2026-11-19T08:00:00Z',
        done_at: null,
        snoozed_until: null,
        fired_at: null,
      },
    ];
    const proposalsList = [
      {
        id: 'mprop-1',
        content_id: 'cnt-posted-1',
        platform: 'linkedin',
        metrics: { views: 1200, likes: 45 },
        evidence: { views: '1.2K', likes: '45' },
        image_path: 'assets/results/res_1.png',
        status: 'pending',
        created_at: '2026-11-21T10:00:00Z',
        decided_at: null,
      },
    ];

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/sessions')) return jsonResponse([]);
      if (url.startsWith('/api/workflows')) return jsonResponse([]);
      if (url.startsWith('/api/facts')) return jsonResponse([]);
      if (url.startsWith('/api/content')) {
        return jsonResponse([
          {
            id: 'cnt-posted-1',
            type: 'post',
            platform: 'linkedin',
            title: 'Launch Post',
            status: 'posted',
            created_at: '2026-11-19T08:00:00Z',
            updated_at: '2026-11-19T08:00:00Z',
          },
        ]);
      }
      if (url === '/api/reminders?status=open') {
        return jsonResponse({ reminders: remindersList });
      }
      if (url === '/api/reminders' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { title: string; due_at: string; note?: string };
        const created = {
          id: 'rem-new',
          title: body.title,
          note: body.note || '',
          due_at: futureIso,
          kind: 'custom',
          content_id: null,
          created_by: 'user',
          created_at: '2026-11-22T00:00:00Z',
          done_at: null,
          snoozed_until: null,
          fired_at: null,
        };
        remindersList.push(created);
        return jsonResponse({ reminder: created });
      }
      if (url === '/api/reminders/rem-overdue/done' && init?.method === 'POST') {
        return jsonResponse({ reminder: { ...remindersList[1], done_at: '2026-11-22T01:00:00Z' } });
      }
      if (url === '/api/reminders/rem-overdue/snooze' && init?.method === 'POST') {
        return jsonResponse({ reminder: { ...remindersList[1], snoozed_until: futureIso } });
      }
      if (url === '/api/reminders/rem-future' && init?.method === 'DELETE') {
        return jsonResponse({ success: true, id: 'rem-future' });
      }
      if (url === '/api/results/proposals?status=pending') {
        return jsonResponse({ proposals: proposalsList });
      }
      if (url === '/api/results/proposals/mprop-1/confirm' && init?.method === 'POST') {
        return jsonResponse({
          proposal: { ...proposalsList[0], status: 'saved' },
          metric: { id: 'met-1', source: 'screenshot' },
        });
      }
      if (url === '/api/results/proposals/mprop-1/discard' && init?.method === 'POST') {
        return jsonResponse({ proposal: { ...proposalsList[0], status: 'discarded' } });
      }
      return jsonResponse({});
    });

    render(
      <TodaySection
        health={{ ok: true }}
        status={{ env_status: {}, models_matrix: [], activity_log: [] }}
        jobs={[]}
        loading={false}
        error={null}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Add results for Launch Post (day 1)')).toBeTruthy();
    });

    // Overdue reminder comes first and is marked overdue
    const remItems = screen
      .getByRole('list', { name: 'Open reminders' })
      .querySelectorAll('[role="listitem"]');
    expect(remItems.length).toBe(2);
    expect(remItems[0].textContent).toContain('Add results for Launch Post (day 1)');
    expect(remItems[0].getAttribute('data-overdue')).toBe('true');
    expect(remItems[1].textContent).toContain('Upcoming check-in');
    expect(remItems[1].getAttribute('data-overdue')).toBe('false');

    // Results card lists pending proposal
    const pendingList = screen.getByRole('list', { name: 'Pending results' });
    expect(pendingList.textContent).toContain('Launch Post · linkedin');
    expect(pendingList.textContent).toContain('views: 1200');

    // Snooze 1 hour
    const snoozeButtons = screen.getAllByRole('button', { name: 'Snooze' });
    fireEvent.click(snoozeButtons[0]);
    fireEvent.click(screen.getByRole('button', { name: '1 hour' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/reminders/rem-overdue/snooze',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ minutes: 60 }),
        }),
      );
    });

    // Done
    const doneButtons = screen.getAllByRole('button', { name: 'Done' });
    fireEvent.click(doneButtons[0]);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/reminders/rem-overdue/done',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Delete
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(deleteButtons[1]);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/reminders/rem-future',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });

    // Add reminder form
    fireEvent.change(screen.getByLabelText('Reminder title'), {
      target: { value: 'Follow up with designer' },
    });
    fireEvent.change(screen.getByLabelText('Reminder due time'), {
      target: { value: '2026-11-28T10:30' },
    });
    fireEvent.change(screen.getByLabelText('Reminder note'), {
      target: { value: 'Send card specs' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add reminder' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/reminders',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            title: 'Follow up with designer',
            due_at: '2026-11-28T10:30',
            note: 'Send card specs',
          }),
        }),
      );
    });

    // Open Add results dialog from Results card Review button
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('Seen: 1.2K')).toBeTruthy();
    // Never auto-saves without clicking Save results
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/results/proposals/mprop-1/confirm',
      expect.anything(),
    );

    // Edit views and save
    fireEvent.change(screen.getByLabelText('Views'), { target: { value: '1350' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/results/proposals/mprop-1/confirm',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            metrics: { views: 1350, likes: 45 },
            content_id: 'cnt-posted-1',
            platform: 'linkedin',
          }),
        }),
      );
    });

    // Discard directly from Results card
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/results/proposals/mprop-1/discard',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });
});
