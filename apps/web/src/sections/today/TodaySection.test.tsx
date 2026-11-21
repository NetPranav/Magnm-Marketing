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

  it('renders Weekly summary card states (none yet, generate now, generated, skipped narrative, missing results -> Add results) and Learnings (edit+approve, reject, retire, add)', async () => {
    let summariesState: Array<Record<string, unknown>> = [];
    let learningsState: Array<Record<string, unknown>> = [
      {
        id: 'lrn-pend-1',
        text: 'LinkedIn carousels got 18 saves vs 6 saves on single images.',
        evidence: [
          {
            content_id: 'cnt-top',
            metric_id: 'met-top',
            title: 'Carousel Guide',
            platform: 'linkedin',
            metrics: { views: 1200, likes: 45, saves: 18 },
          },
          {
            content_id: 'cnt-bot',
            metric_id: 'met-bot',
            title: 'Single Image',
            platform: 'linkedin',
            metrics: { views: 600, likes: 15, saves: 6 },
          },
        ],
        applies_to: ['writer', 'designer'],
        status: 'pending',
        source_summary_id: 'wsum-1',
        created_at: '2026-11-22T09:00:00Z',
        decided_at: null,
      },
      {
        id: 'lrn-pend-2',
        text: 'Post twice on Sunday nights.',
        evidence: [
          { content_id: 'cnt-top', title: 'Carousel Guide', platform: 'linkedin', metrics: { likes: 45 } },
          { content_id: 'cnt-bot', title: 'Single Image', platform: 'linkedin', metrics: { likes: 15 } },
        ],
        applies_to: ['cmo'],
        status: 'pending',
        source_summary_id: 'wsum-1',
        created_at: '2026-11-22T09:01:00Z',
        decided_at: null,
      },
      {
        id: 'lrn-app-1',
        text: 'Keep X hooks under 120 chars.',
        evidence: [],
        applies_to: ['writer'],
        status: 'approved',
        source_summary_id: null,
        created_at: '2026-11-20T09:00:00Z',
        decided_at: '2026-11-20T09:00:00Z',
      },
    ];

    const generatedSummary = {
      id: 'wsum-1',
      week_start: '2026-11-16',
      created_at: '2026-11-23T03:30:00Z',
      data: {
        week_start: '2026-11-16',
        week_end: '2026-11-22',
        week_label: '16-22 Nov',
      },
      highlights: {
        platform_rows: [
          {
            platform: 'linkedin',
            posts_count: 3,
            with_results_count: 2,
            totals: { views: 1800, likes: 60, comments: 4, shares: 2, saves: 24, engagement: 90 },
            averages: { views: 900, likes: 30, engagement: 45 },
            prev_posts_count: 1,
            prev_totals: { engagement: 45 },
            wow_change: { engagement: 100.0 },
          },
          {
            platform: 'x',
            posts_count: 1,
            with_results_count: 0,
            totals: { views: 0, likes: 0, comments: 0, shares: 0, saves: 0, engagement: 0 },
            averages: {},
            prev_posts_count: 0,
            prev_totals: {},
            wow_change: { engagement: 'n/a' },
          },
        ],
        top_post: {
          content_id: 'cnt-top',
          title: 'Carousel Guide',
          platform: 'linkedin',
          posted_url: 'https://linkedin.com/posts/top',
          metrics: { views: 1200, likes: 45, saves: 18 },
          engagement: 63,
          score: 0.0525,
          score_unit: 'per_view',
        },
        bottom_post: {
          content_id: 'cnt-bot',
          title: 'Single Image',
          platform: 'linkedin',
          posted_url: 'https://linkedin.com/posts/bot',
          metrics: { views: 600, likes: 15, saves: 6 },
          engagement: 21,
          score: 0.035,
          score_unit: 'per_view',
        },
        missing_results: [
          {
            content_id: 'cnt-miss',
            title: 'Missing X Thread',
            platform: 'x',
            posted_at: '2026-11-20T10:00:00Z',
            posted_url: 'https://x.com/miss',
          },
        ],
        counts: {
          posts_posted: 4,
          posts_with_results: 2,
          posts_missing_results: 1,
          drafts_created: 5,
          research_notes_added: 2,
          reminders_done: 1,
          reminders_missed: 0,
        },
      },
      narrative: 'During 16-22 Nov, LinkedIn engagement rose 100%.\n1. Focus on carousels.',
      status: 'ready',
    };

    const olderSkippedSummary = {
      ...generatedSummary,
      id: 'wsum-0',
      week_start: '2026-11-09',
      data: { week_start: '2026-11-09', week_end: '2026-11-15', week_label: '9-15 Nov' },
      narrative: 'Narrative skipped: it mentioned numbers that are not in the data.',
      status: 'narrative_skipped',
    };

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/sessions')) return jsonResponse([]);
      if (url.startsWith('/api/workflows')) return jsonResponse([]);
      if (url.startsWith('/api/facts')) return jsonResponse([]);
      if (url.startsWith('/api/content')) {
        return jsonResponse([
          {
            id: 'cnt-miss',
            type: 'post',
            platform: 'x',
            title: 'Missing X Thread',
            status: 'posted',
            created_at: '2026-11-20T09:00:00Z',
            updated_at: '2026-11-20T09:00:00Z',
          },
        ]);
      }
      if (url.startsWith('/api/reminders')) return jsonResponse({ reminders: [] });
      if (url.startsWith('/api/results/proposals')) return jsonResponse({ proposals: [] });
      if (url.startsWith('/api/summaries?') && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ summaries: summariesState });
      }
      if (url === '/api/summaries/generate' && init?.method === 'POST') {
        summariesState = [generatedSummary, olderSkippedSummary];
        return jsonResponse({ summary: generatedSummary, learnings: [] });
      }
      if (url === '/api/learnings' && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ learnings: learningsState });
      }
      if (url === '/api/learnings/lrn-pend-1/approve' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { text?: string };
        const updated = { ...learningsState[0], status: 'approved', text: body.text || learningsState[0].text };
        learningsState = [updated, ...learningsState.slice(1)];
        return jsonResponse({ learning: updated });
      }
      if (url === '/api/learnings/lrn-pend-2/reject' && init?.method === 'POST') {
        const updated = { ...learningsState[1], status: 'rejected' };
        learningsState = [learningsState[0], updated, learningsState[2]];
        return jsonResponse({ learning: updated });
      }
      if (url === '/api/learnings/lrn-app-1/retire' && init?.method === 'POST') {
        const updated = { ...learningsState[2], status: 'retired' };
        learningsState = [learningsState[0], learningsState[1], updated];
        return jsonResponse({ learning: updated });
      }
      if (url === '/api/learnings' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { text: string; applies_to: string[] };
        const created = {
          id: 'lrn-manual-1',
          text: body.text,
          evidence: [],
          applies_to: body.applies_to,
          status: 'approved',
          source_summary_id: null,
          created_at: '2026-11-22T10:00:00Z',
          decided_at: '2026-11-22T10:00:00Z',
        };
        learningsState = [created, ...learningsState];
        return jsonResponse({ learning: created });
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

    // 1. Empty state ("none yet")
    await waitFor(() => {
      expect(screen.getByText(/No weekly summary generated yet/i)).toBeTruthy();
    });

    // 2. Generate now -> displays table, top/bottom post links, missing results, and narrative
    fireEvent.click(screen.getByRole('button', { name: 'Generate now' }));
    await waitFor(() => {
      expect(screen.getByText('Weekly summary · 16-22 Nov')).toBeTruthy();
    });
    expect(screen.getByRole('table', { name: 'Per-platform performance' })).toBeTruthy();
    expect(screen.getByText('+100%')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Carousel Guide' }).getAttribute('href')).toBe(
      'https://linkedin.com/posts/top',
    );
    expect(screen.getByRole('link', { name: 'Single Image' }).getAttribute('href')).toBe(
      'https://linkedin.com/posts/bot',
    );
    expect(screen.getByText(/During 16-22 Nov, LinkedIn engagement rose 100%/)).toBeTruthy();

    // 3. Switch to older summary with AI-skipped narrative note
    fireEvent.click(screen.getByRole('button', { name: /Older summaries/i }));
    fireEvent.click(screen.getByRole('button', { name: '9-15 Nov' }));
    expect(
      screen.getByText('Narrative skipped: it mentioned numbers that are not in the data.'),
    ).toBeTruthy();

    // 4. Missing results Add results button opens T5.1 AddResultsDialog pre-selected with cnt-miss
    const missingList = screen.getByRole('list', { name: 'Posts missing results' });
    const addResultsBtn = missingList.querySelector('button');
    expect(addResultsBtn).toBeTruthy();
    fireEvent.click(addResultsBtn!);
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect((screen.getByLabelText('Post') as HTMLSelectElement).value).toBe('cnt-miss');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    // 5. Learnings: evidence shows post titles & numbers, edit text then approve
    const pendingLearnings = screen.getByRole('list', { name: 'Pending learnings' });
    expect(pendingLearnings.textContent).toContain('Carousel Guide (linkedin) — views: 1200, likes: 45, saves: 18');

    const editBox = screen.getByLabelText('Edit learning lrn-pend-1');
    fireEvent.change(editBox, { target: { value: 'Carousels beat single images on LinkedIn saves.' } });
    const approveButtons = screen.getAllByRole('button', { name: 'Approve' });
    fireEvent.click(approveButtons[0]);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/learnings/lrn-pend-1/approve',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ text: 'Carousels beat single images on LinkedIn saves.' }),
        }),
      );
    });

    // Reject second pending learning
    const rejectButtons = screen.getAllByRole('button', { name: 'Reject' });
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/learnings/lrn-pend-2/reject',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Retire the originally approved learning (lrn-app-1)
    const approvedList = screen.getByRole('list', { name: 'Approved learnings' });
    const appRow = Array.from(approvedList.querySelectorAll('[role="listitem"]')).find((el) =>
      el.textContent?.includes('Keep X hooks under 120 chars.'),
    );
    expect(appRow).toBeTruthy();
    const retireBtn = appRow!.querySelector('button');
    expect(retireBtn).toBeTruthy();
    fireEvent.click(retireBtn!);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/learnings/lrn-app-1/retire',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Add manual learning
    fireEvent.change(screen.getByLabelText('New learning text'), {
      target: { value: 'Always include code output in developer posts.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add learning' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/learnings',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            text: 'Always include code output in developer posts.',
            applies_to: ['all'],
          }),
        }),
      );
    });
  });
});
