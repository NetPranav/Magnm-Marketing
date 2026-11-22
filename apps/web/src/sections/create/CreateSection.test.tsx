import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CreateSection } from './CreateSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('CreateSection', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders workflows with gates and review links, starts workflow, approves gate, and runs quick check', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/workflows?')) {
        return jsonResponse([
          {
            id: 'wf-blog-1',
            kind: 'blog',
            status: 'waiting_user',
            current_step: 1,
            inputs: { topic: 'Cero architecture' },
            state: {},
            steps: [
              { index: 0, name: 'write_outline', status: 'done' },
              { index: 1, name: 'approve_outline', status: 'waiting_user' },
            ],
            gates: [
              {
                id: 'gate-1',
                run_id: 'wf-blog-1',
                step: 1,
                question: 'Approve the outline?',
                options: ['Approve', 'Revise'],
                status: 'pending',
              },
            ],
            content_ids: ['cnt-blog-1'],
          },
        ]);
      }
      if (url === '/api/product_rules') {
        return jsonResponse({
          version: 1,
          show_levels: { everything: 'Allowed', teasers_only: 'Teasers only' },
          products: [
            { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
            { id: 'lazylayout', name: 'LazyLayout', status: 'in_development', show: 'teasers_only', confidential: true },
          ],
        });
      }
      if (url.startsWith('/api/content?')) {
        return jsonResponse([{ id: 'cnt-1', type: 'post', title: 'Release notes', status: 'ready', created_at: '', updated_at: '' }]);
      }
      if (url === '/api/assets') {
        return jsonResponse([{ id: 'asset-1', name: 'terminal.png', file_group: 'image' }]);
      }
      if (url === '/api/workflows/start' && init?.method === 'POST') {
        return jsonResponse({
          id: 'wf-post-2',
          kind: 'post',
          status: 'queued',
          current_step: 0,
          inputs: { brief: 'Announce Cero v2.2.3' },
          state: {},
          gates: [],
          content_ids: [],
        });
      }
      if (url === '/api/gates/gate-1/answer' && init?.method === 'POST') {
        return jsonResponse({
          gate: { id: 'gate-1', status: 'answered', answer: 'Approve' },
          run: {
            id: 'wf-blog-1',
            kind: 'blog',
            status: 'running',
            current_step: 2,
            inputs: { topic: 'Cero architecture' },
            state: {},
            steps: [
              { index: 0, name: 'write_outline', status: 'done' },
              { index: 1, name: 'approve_outline', status: 'done' },
            ],
            gates: [],
            content_ids: ['cnt-blog-1'],
          },
        });
      }
      if (url === '/api/check_text' && init?.method === 'POST') {
        return jsonResponse({
          warnings: [{ product: 'cero', severity: 'warn', message: "Blocked term 'ROS' is not allowed for Cero." }],
        });
      }
      if (url === '/api/cards/render' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          post_id: 'custom-card',
          rendered_files: ['4x5.png'],
        });
      }
      if (url === '/api/pr/draft_reply' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          provider: 'nvidia_nim',
          model_used: 'minimax-m3.1-flash',
          draft: 'DRAFT RESPONSE: Cero is local-first.',
        });
      }
      return jsonResponse({});
    });

    render(<CreateSection />);

    await waitFor(() => {
      expect(screen.getByText('Cero architecture')).toBeTruthy();
    });

    const reviewLink = screen.getByRole('link', { name: 'Review cnt-blog-1' });
    expect(reviewLink.getAttribute('href')).toBe('#/review/cnt-blog-1');

    // Start a post workflow
    fireEvent.change(screen.getByLabelText('Product'), { target: { value: 'cero' } });
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'Announce Cero v2.2.3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start workflow' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workflows/start',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            kind: 'post',
            inputs: {
              product_id: 'cero',
              brief: 'Announce Cero v2.2.3',
              platforms: ['linkedin', 'x'],
              with_image: false,
            },
          }),
        }),
      );
    });

    // Approve pending gate inline
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

    // Quick text check
    fireEvent.change(screen.getByLabelText('Platform limits (optional)'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Draft text to check'), { target: { value: 'Cero ROS' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check text' }));
    await waitFor(() => {
      expect(screen.getByText(/Blocked term 'ROS'/)).toBeTruthy();
    });

    // Switch to Card Studio via RowList and render
    const cardRow = screen.getByRole('button', { name: /Visual card/i });
    fireEvent.click(cardRow);
    expect(cardRow.getAttribute('aria-current')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Render cards' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/cards/render',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Switch to PR reply
    fireEvent.click(screen.getByRole('button', { name: /PR \/ crisis reply/i }));
    fireEvent.change(screen.getByLabelText('Incoming comment or inquiry'), {
      target: { value: 'Does Cero upload code?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Draft reply' }));
    await waitFor(() => {
      expect(screen.getByText('DRAFT RESPONSE: Cero is local-first.')).toBeTruthy();
    });
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/workflows?')) {
        return jsonResponse({ error: 'Workflows database offline' }, 500);
      }
      if (url === '/api/product_rules') {
        return jsonResponse({ version: 1, show_levels: {}, products: [] });
      }
      return jsonResponse([]);
    });

    render(<CreateSection />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Workflows database offline');
    });
    expect(screen.getByText(/No workflow runs yet/i)).toBeTruthy();
  });

  describe('repository picker', () => {
    function mockCreate(calls: Array<{ url: string; init?: RequestInit }>, repos: unknown[]) {
      return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : input.toString();
        calls.push({ url, init });
        if (url === '/api/repos') return jsonResponse(repos);
        if (url === '/api/product_rules') return jsonResponse({ version: 1, show_levels: {}, products: [] });
        if (url === '/api/workflows/start' && init?.method === 'POST') {
          return jsonResponse({ id: 'wf-1', kind: 'post', status: 'queued', current_step: 0, inputs: {}, state: {}, gates: [], content_ids: [] });
        }
        return jsonResponse([]);
      });
    }

    const REPOS = [
      { id: 'open', name: 'OpenRepo', kind: 'github', confidential: false, scope: 'personal' },
      { id: 'team-secret', name: 'SecretRepo', kind: 'github', confidential: true, scope: 'global' },
    ];

    it('lists own and team repos, labels a confidential one before starting, and sends repo_id', async () => {
      const calls: Array<{ url: string; init?: RequestInit }> = [];
      mockCreate(calls, REPOS);
      render(<CreateSection />);
      const picker = (await screen.findByLabelText(/Repository/)) as HTMLSelectElement;
      await waitFor(() => expect(picker.options.length).toBe(3));
      expect(Array.from(picker.options).map((o) => o.textContent)).toEqual([
        'No repository',
        'OpenRepo',
        'SecretRepo (team) (confidential)',
      ]);
      expect(screen.queryByText('CONFIDENTIAL REPOSITORY')).toBeNull();

      fireEvent.change(picker, { target: { value: 'team-secret' } });
      expect(screen.getByText('CONFIDENTIAL REPOSITORY')).toBeTruthy();
      expect(screen.getByText(/local AI only, from the first step/i)).toBeTruthy();

      fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'A calm update' } });
      fireEvent.click(screen.getByRole('button', { name: 'Start workflow' }));
      await waitFor(() => {
        const post = calls.find((c) => c.url === '/api/workflows/start');
        expect(JSON.parse(String(post?.init?.body)).inputs.repo_id).toBe('team-secret');
      });
    });

    it('sends no repo_id when none is picked and hints at adding one when there are none', async () => {
      const calls: Array<{ url: string; init?: RequestInit }> = [];
      mockCreate(calls, []);
      render(<CreateSection />);
      await screen.findByLabelText(/Repository/);
      expect(screen.getByText(/Add a repository in Repos/i)).toBeTruthy();
      fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'A calm update' } });
      fireEvent.click(screen.getByRole('button', { name: 'Start workflow' }));
      await waitFor(() => {
        const post = calls.find((c) => c.url === '/api/workflows/start');
        expect(JSON.parse(String(post?.init?.body)).inputs.repo_id).toBeUndefined();
      });
    });

    it('offers the picker for blog, video and weekly plan but not repurpose, and starts with no product-named defaults', async () => {
      const calls: Array<{ url: string; init?: RequestInit }> = [];
      mockCreate(calls, REPOS);
      render(<CreateSection />);
      await screen.findByLabelText(/Repository/);
      for (const title of ['Blog article', 'Video script', 'Weekly plan']) {
        fireEvent.click(screen.getByText(title));
        expect(screen.getByLabelText(/Repository/)).toBeTruthy();
      }
      fireEvent.click(screen.getByText('Repurpose content'));
      expect(screen.queryByLabelText(/Repository/)).toBeNull();
      fireEvent.click(screen.getByText('Visual card'));
      expect((screen.getByLabelText(/Top kicker/i) as HTMLInputElement).value).toBe('');
    });
  });
});
