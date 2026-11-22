import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodingRunsPanel } from './CodingRunsPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('CodingRunsPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('validates form, requires confidential checkbox, approves pending runs, shows logs and safe out/ actions', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/coding/agents')) {
        return jsonResponse({
          agents: [
            {
              id: 'claude',
              name: 'Claude Code',
              command: 'claude',
              vendor: 'Anthropic',
              installed: true,
              enabled: true,
              confined: true,
            },
            {
              id: 'codex',
              name: 'Codex',
              command: 'codex',
              installed: true,
              enabled: false,
              confined: false,
              reason: "Found, not enabled: this tool can't yet be confined to a workspace",
            },
          ],
          agent_engines: {},
        });
      }
      if (url === '/api/repos') {
        return jsonResponse([
          { id: 'cero', name: 'Cero', product_id: 'cero', kind: 'local', confidential: false, status: 'released', local_path: '/tmp/cero' },
          { id: 'lazylayout', name: 'LazyLayout', product_id: 'lazylayout', kind: 'local', confidential: true, status: 'in_development', local_path: '/tmp/lazy' },
        ]);
      }
      if (url.startsWith('/api/content')) {
        return jsonResponse([]);
      }
      if (url.startsWith('/api/coding/runs?limit=')) {
        return jsonResponse({
          runs: [
            {
              id: 'crun_pending_1',
              engine: 'claude',
              mode: 'read_only',
              prompt: 'Full prompt requested by writer',
              repo_ids: ['cero'],
              status: 'awaiting_approval',
              created_at: '2026-11-22T05:00:00Z',
              approval_hash: 'abc123',
              requested_by: 'writer',
              out_files: [],
            },
            {
              id: 'crun_done_1',
              engine: 'claude',
              mode: 'write_out',
              prompt: 'Build chart and html',
              repo_ids: [],
              status: 'done',
              created_at: '2026-11-22T04:00:00Z',
              summary: 'Created out/answer.md and out/evil.html',
              out_files: [
                { name: 'answer.md', size: 120 },
                { name: 'evil.html', size: 48 },
              ],
            },
          ],
        });
      }
      if (url === '/api/coding/runs/crun_pending_1') {
        return jsonResponse({
          id: 'crun_pending_1',
          engine: 'claude',
          mode: 'read_only',
          prompt: 'Full prompt requested by writer',
          repo_ids: ['cero'],
          status: 'awaiting_approval',
          created_at: '2026-11-22T05:00:00Z',
          approval_hash: 'abc123',
          requested_by: 'writer',
          out_files: [],
          log_tail: '',
        });
      }
      if (url === '/api/coding/runs/crun_done_1') {
        return jsonResponse({
          id: 'crun_done_1',
          engine: 'claude',
          mode: 'write_out',
          prompt: 'Build chart and html',
          repo_ids: [],
          status: 'done',
          created_at: '2026-11-22T04:00:00Z',
          summary: 'Created out/answer.md and out/evil.html',
          out_files: [
            { name: 'answer.md', size: 120 },
            { name: 'evil.html', size: 48 },
          ],
          log_tail: 'Step 1 ok',
        });
      }
      if (url.includes('/log?from=')) {
        return jsonResponse({ run_id: 'crun_done_1', content: 'Step 1 ok', from: 0, next_offset: 9, status: 'done' });
      }
      if (url === '/api/coding/runs/crun_pending_1/approve' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          run: {
            id: 'crun_pending_1',
            engine: 'claude',
            mode: 'read_only',
            prompt: 'Full prompt requested by writer',
            repo_ids: ['cero'],
            status: 'running',
            created_at: '2026-11-22T05:00:00Z',
          },
        });
      }
      if (url === '/api/coding/runs/crun_pending_1/cancel' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          run: {
            id: 'crun_pending_1',
            engine: 'claude',
            mode: 'read_only',
            prompt: 'Full prompt requested by writer',
            repo_ids: ['cero'],
            status: 'cancelled',
            created_at: '2026-11-22T05:00:00Z',
          },
        });
      }
      if (url === '/api/coding/runs' && init?.method === 'POST') {
        return jsonResponse(
          {
            success: true,
            run: {
              id: 'crun_new_1',
              engine: 'claude',
              mode: 'read_only',
              prompt: 'Check LazyLayout',
              repo_ids: ['lazylayout'],
              status: 'queued',
              confidential: true,
              created_at: '2026-11-22T05:10:00Z',
            },
          },
          201,
        );
      }
      return jsonResponse({});
    });

    render(<CodingRunsPanel />);

    // Approval card shows complete prompt and Start button
    await waitFor(() => {
      expect(screen.getByText('Approval required (1)')).toBeTruthy();
    });
    expect(screen.getAllByText('Full prompt requested by writer').length).toBeGreaterThanOrEqual(1);

    // Approve pending run
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/coding/runs/crun_pending_1/approve',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ approval_hash: 'abc123', confirm_confidential: false }),
        }),
      );
    });

    // Stop active running run
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/coding/runs/crun_pending_1/cancel', expect.anything());
    });

    // Validation: empty prompt refuses submit
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(screen.getByRole('alert').textContent).toContain('Enter a prompt');

    // Confidential repo excluded by default and requires explicit extra checkbox
    const lazyCheckbox = screen.getByLabelText(/LazyLayout \(lazylayout\) \[Confidential\]/) as HTMLInputElement;
    expect(lazyCheckbox.checked).toBe(false);
    fireEvent.click(lazyCheckbox);

    const consentCheckbox = screen.getByLabelText("This sends LazyLayout's code to Anthropic") as HTMLInputElement;
    const startRunBtn = screen.getByRole('button', { name: 'Start run' }) as HTMLButtonElement;
    expect(startRunBtn.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Check LazyLayout' } });
    fireEvent.click(consentCheckbox);
    expect(startRunBtn.disabled).toBe(false);
    fireEvent.click(startRunBtn);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/coding/runs',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            engine: 'claude',
            prompt: 'Check LazyLayout',
            repo_ids: ['lazylayout'],
            mode: 'read_only',
            confirm_confidential: true,
          }),
        }),
      );
    });

    // Select completed run to check safe vs hostile output file buttons
    fireEvent.click(screen.getByText('Build chart and html'));
    await waitFor(() => {
      expect(screen.getByText('evil.html')).toBeTruthy();
    });
    const saveButtons = screen.getAllByRole('button', { name: 'Save to Library' }) as HTMLButtonElement[];
    expect(saveButtons.length).toBe(2);
    expect(saveButtons[0].disabled).toBe(false);
    expect(saveButtons[1].disabled).toBe(true);
  });
});
