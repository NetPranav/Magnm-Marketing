import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { BoardSection } from './BoardSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const COLUMNS = [
  { id: 'queued', label: 'Queued' },
  { id: 'running', label: 'Running' },
  { id: 'waiting', label: 'Waiting for you' },
  { id: 'paused', label: 'Paused' },
  { id: 'done', label: 'Done' },
  { id: 'failed', label: 'Failed' },
];

function item(over: Record<string, unknown>) {
  return {
    id: 'run:wf_post_1_aaaaaa', kind: 'workflow', title: 'post: Share an update', column: 'running', status: 'running',
    agents: ['writer'], repo: 'alpha', repo_name: 'Alpha app', scope: 'personal', updated_at: '2026-11-24T10:00:00+00:00',
    age_days: 0, confidential: false, route: '#/create', automatic: true,
    detail: { steps: [{ name: 'write_drafts', status: 'done' }, { name: 'review', status: 'current' }] },
    actions: [{ id: 'pause', label: 'Pause' }, { id: 'cancel', label: 'Cancel' }], ...over,
  };
}

function board(items: unknown[], paused = false) {
  return {
    columns: COLUMNS, items, paused, generated_at: 'now',
    filters: { agents: ['writer'], repos: [{ id: 'alpha', name: 'Alpha app' }], kinds: ['workflow', 'approval'] },
  };
}

describe('BoardSection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows the six columns with items, repo scope and the automatic mark', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse(board([item({})])));
    render(<BoardSection onNavigate={() => undefined} />);
    for (const label of ['Queued', 'Running', 'Waiting for you', 'Paused', 'Done', 'Failed']) {
      expect(await screen.findByRole('region', { name: label })).toBeTruthy();
    }
    const running = screen.getByRole('region', { name: 'Running' });
    expect(within(running).getByText('post: Share an update')).toBeTruthy();
    expect(within(running).getByText(/Alpha app \(personal\)/)).toBeTruthy();
    expect(within(running).getByText(/automatic/)).toBeTruthy();
  });

  it('filters by agent, repo and kind through the engine', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse(board([item({})])));
    render(<BoardSection onNavigate={() => undefined} />);
    await screen.findByText('post: Share an update');
    fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'writer' } });
    fireEvent.change(screen.getByLabelText('Repo'), { target: { value: 'alpha' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'workflow' } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/board?agent=writer&repo=alpha&kind=workflow', expect.anything()));
  });

  it('opens an item, shows its steps and pauses it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/board/act') return jsonResponse({ run: { status: 'paused' } });
      return jsonResponse(board([item({})]));
    });
    render(<BoardSection onNavigate={() => undefined} />);
    fireEvent.click(await screen.findByText('post: Share an update'));
    const steps = await screen.findByRole('list', { name: 'Steps' });
    expect(within(steps).getByText('write drafts')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/board/act', expect.objectContaining({ body: JSON.stringify({ id: 'run:wf_post_1_aaaaaa', action: 'pause' }) })),
    );
  });

  it('sends an approval to the existing approval screen', async () => {
    const onNavigate = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/board/act') return jsonResponse({ route: '#/create' });
      return jsonResponse(
        board([
          item({
            id: 'gate:g1', kind: 'approval', title: 'Keep this outline?', column: 'waiting', status: 'waiting_user',
            detail: { options: ['Approve', 'Revise'] }, actions: [{ id: 'approve', label: 'Review and answer' }, { id: 'cancel', label: 'Cancel' }],
          }),
        ]),
      );
    });
    render(<BoardSection onNavigate={onNavigate} />);
    fireEvent.click(await screen.findByText('Keep this outline?'));
    fireEvent.click(screen.getByRole('button', { name: 'Review and answer' }));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('#/create'));
  });

  it('shows a failed job with its error and a Retry button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse(
        board([
          item({
            id: 'job:j1', kind: 'job', title: 'Codebase & GitHub Intelligence Sync', column: 'failed', status: 'failed', agents: [],
            repo: '', repo_name: '', scope: '', automatic: false, detail: { error: 'network down' }, actions: [{ id: 'retry', label: 'Retry' }],
          }),
        ]),
      ),
    );
    render(<BoardSection onNavigate={() => undefined} />);
    fireEvent.click(await screen.findByText('Codebase & GitHub Intelligence Sync'));
    expect(await screen.findByText('network down')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('shows the global switch and flips it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/automation/pause') return jsonResponse({ paused: true });
      return jsonResponse(board([], false));
    });
    render(<BoardSection onNavigate={() => undefined} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Pause automatic work' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/automation/pause', expect.objectContaining({ body: JSON.stringify({ paused: true }) })),
    );
  });

  it('renders confidential items as plain text, never as markup', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse(board([item({ title: '<img src=x onerror=alert(1)>', confidential: true })])),
    );
    const { container } = render(<BoardSection onNavigate={() => undefined} />);
    await screen.findByText('<img src=x onerror=alert(1)>');
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/confidential/)).toBeTruthy();
  });
});
