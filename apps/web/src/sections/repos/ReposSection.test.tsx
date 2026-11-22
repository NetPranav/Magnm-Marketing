import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReposSection } from './ReposSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ReposSection', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders repos, shipped items, approves edited fact, and saves product rules with LazyLayout guard', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/repos' && (!init?.method || init.method === 'GET')) {
        return jsonResponse([
          {
            id: 'cero',
            name: 'Cero',
            kind: 'local',
            status: 'released',
            version: '2.2.3',
            confidential: false,
            local_path: '/repos/cero',
            last_sync: '2026-11-19T08:00:00Z',
            recent_commits: [{ hash: 'abc1234', message: 'feat: prompt before edit' }],
          },
          {
            id: 'lazylayout',
            name: 'LazyLayout',
            kind: 'local',
            status: 'in_development',
            confidential: true,
            local_path: '/repos/lazylayout',
          },
        ]);
      }
      if (url.startsWith('/api/shipped?')) {
        return jsonResponse([
          { repo_id: 'cero', kind: 'release', title: 'v2.2.3', url: 'https://github.com/magnm/cero/releases/tag/v2.2.3', date: '2026-11-19T07:00:00Z' },
        ]);
      }
      if (url.startsWith('/api/facts?')) {
        return jsonResponse([
          {
            id: 'fact-1',
            product_id: 'cero',
            text: 'Cero asks before editing files.',
            source_kind: 'repo_sync',
            status: 'pending',
          },
        ]);
      }
      if (url === '/api/product_rules' && (!init?.method || init.method === 'GET')) {
        return jsonResponse({
          version: 1,
          show_levels: {
            everything: 'Agents may describe and show the product.',
            text_only: 'Text only.',
            teasers_only: 'Teasers only.',
            nothing: 'Nothing.',
          },
          products: [
            {
              id: 'cero',
              name: 'Cero',
              status: 'released',
              show: 'everything',
              blocked_terms: ['ROS'],
              confidential: false,
              notes: 'Verified facts only.',
            },
            {
              id: 'lazylayout',
              name: 'LazyLayout',
              status: 'in_development',
              show: 'teasers_only',
              blocked_terms: [],
              confidential: true,
              notes: 'Keep teasers only.',
            },
          ],
        });
      }
      if (url === '/api/facts/fact-1/decide' && init?.method === 'POST') {
        return jsonResponse({ id: 'fact-1', status: 'approved', success: true });
      }
      if (url === '/api/product_rules' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          product: { id: 'cero', status: 'released', show: 'text_only' },
          rules: {
            version: 1,
            show_levels: { everything: 'Allowed', text_only: 'Text only', teasers_only: 'Teasers only', nothing: 'Nothing' },
            products: [
              { id: 'cero', name: 'Cero', status: 'released', show: 'text_only', blocked_terms: ['ROS', 'beta'], confidential: false },
              { id: 'lazylayout', name: 'LazyLayout', status: 'in_development', show: 'teasers_only', blocked_terms: [], confidential: true },
            ],
          },
        });
      }
      return jsonResponse({});
    });

    render(<ReposSection />);

    await waitFor(() => {
      expect(screen.getByText(/feat: prompt before edit/)).toBeTruthy();
    });

    expect(screen.getAllByText('CONFIDENTIAL').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('link', { name: 'v2.2.3' })).toBeTruthy();

    // Edit-then-approve pending fact
    fireEvent.change(screen.getByLabelText(/Fact text/i), {
      target: { value: 'Cero always asks before modifying files.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/facts/fact-1/decide',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            status: 'approved',
            text: 'Cero always asks before modifying files.',
          }),
        }),
      );
    });

    // Save product rule for Cero
    const editButtons = screen.getAllByRole('button', { name: 'Edit rules' });
    fireEvent.click(editButtons[0]);
    fireEvent.change(screen.getByLabelText('What may be shown'), { target: { value: 'text_only' } });
    fireEvent.change(screen.getByLabelText('Blocked words (comma-separated)'), {
      target: { value: 'ROS, beta' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/product_rules',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            product_id: 'cero',
            status: 'released',
            show: 'text_only',
            blocked_terms: ['ROS', 'beta'],
            confidential: false,
            notes: 'Verified facts only.',
          }),
        }),
      );
    });

    // LazyLayout rule edit requires explicit warning confirmation when changed away from teasers_only
    const updatedEditButtons = screen.getAllByRole('button', { name: 'Edit rules' });
    fireEvent.click(updatedEditButtons[1]);
    fireEvent.change(screen.getByLabelText('What may be shown'), { target: { value: 'everything' } });
    expect(screen.getByRole('alert').textContent).toContain('LazyLayout is unreleased');
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));
    expect(screen.getByRole('button', { name: /Confirm override/i })).toBeTruthy();
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/repos') {
        return jsonResponse({ error: 'Repos config broken' }, 500);
      }
      if (url === '/api/product_rules') {
        return jsonResponse({ version: 1, show_levels: {}, products: [] });
      }
      return jsonResponse([]);
    });

    render(<ReposSection />);
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Repos config broken');
    });
    expect(screen.getByText(/No repositories connected yet/i)).toBeTruthy();
    expect(screen.getByText(/No pending product facts/i)).toBeTruthy();
  });

  it('shows product rules read-only with Managed by your admin note when signed in as a member', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'm-1',
            email: 'member@example.com',
            display_name: 'Maya',
            avatar_url: null,
            role: 'member',
            active: true,
          },
        });
      }
      if (url === '/api/product_rules') {
        return jsonResponse({
          version: 1,
          show_levels: { everything: 'Allowed' },
          products: [
            {
              id: 'cero',
              name: 'Cero',
              status: 'released',
              show: 'everything',
              blocked_terms: ['ROS'],
              confidential: false,
            },
          ],
        });
      }
      return jsonResponse([]);
    });

    render(<ReposSection />);

    await waitFor(() => {
      expect(screen.getByText('Cero')).toBeTruthy();
      expect(screen.getByText('Managed by your admin')).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Edit rules' })).toBeNull();
  });

  describe('team repositories', () => {
    const TEAM_REPO = {
      id: 'team-app',
      global_id: 'team-app',
      name: 'Team App',
      kind: 'github',
      github: 'acme/team-app',
      status: 'in_development',
      confidential: false,
      scope: 'global',
      recent_commits: [],
    };
    const OWN_REPO = {
      id: 'mine',
      name: 'My Project',
      kind: 'github',
      github: 'me/mine',
      status: 'in_development',
      confidential: false,
      scope: 'personal',
      recent_commits: [],
    };

    function mockServer(role: 'admin' | 'member', calls: Array<{ url: string; init?: RequestInit }> = []) {
      return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : input.toString();
        calls.push({ url, init });
        if (url === '/api/account') {
          return jsonResponse({
            configured: true,
            signed_in: true,
            deactivated: false,
            user: { id: 'u-1', email: 'u@example.com', display_name: 'U', avatar_url: null, role, active: true },
          });
        }
        if (url === '/api/repos' && (!init?.method || init.method === 'GET')) {
          return jsonResponse([TEAM_REPO, OWN_REPO]);
        }
        if (url === '/api/product_rules') {
          return jsonResponse({ version: 1, show_levels: {}, products: [] });
        }
        if (url.startsWith('/api/team/repos')) {
          return jsonResponse({ success: true, repo: TEAM_REPO, id: 'team-app' });
        }
        return jsonResponse([]);
      });
    }

    it('shows a member the team repo as managed by the admin, with no way to edit or remove it', async () => {
      mockServer('member');
      render(<ReposSection />);
      await waitFor(() => expect(screen.getAllByText('Team App').length).toBeGreaterThan(0));
      expect(screen.getByText('Team')).toBeTruthy();
      expect(screen.getAllByText('Managed by your admin').length).toBeGreaterThanOrEqual(2);
      expect(screen.queryByRole('button', { name: /whole team/i })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Share with team' })).toBeNull();
      // Their own repo can still be removed.
      expect(screen.getAllByRole('button', { name: 'Remove' }).length).toBe(1);
    });

    it('lets an admin add for the whole team, share their own repo, and remove a team repo after confirming', async () => {
      const calls: Array<{ url: string; init?: RequestInit }> = [];
      mockServer('admin', calls);
      render(<ReposSection />);
      await waitFor(() => expect(screen.getAllByText('Team App').length).toBeGreaterThan(0));

      // Add for the whole team: GitHub only.
      fireEvent.click(screen.getByRole('button', { name: 'Add for the whole team' }));
      expect(screen.queryByText('Local folder path')).toBeNull();
      fireEvent.change(screen.getByLabelText(/GitHub repository/i), { target: { value: 'acme/new-one' } });
      fireEvent.click(screen.getAllByRole('button', { name: 'Add for the whole team' }).pop() as HTMLElement);
      await waitFor(() => {
        const post = calls.find((c) => c.url === '/api/team/repos' && c.init?.method === 'POST');
        expect(post).toBeTruthy();
        expect(JSON.parse(String(post?.init?.body))).toMatchObject({ github: 'acme/new-one', confidential: false });
      });

      // Share their own GitHub repo.
      fireEvent.click(screen.getByRole('button', { name: 'Share with team' }));
      await waitFor(() => {
        const post = calls.find((c) => c.url === '/api/team/repos/promote');
        expect(JSON.parse(String(post?.init?.body))).toEqual({ repo_id: 'mine' });
      });

      // Removing a team repo needs a confirmation that says it affects everyone.
      fireEvent.click(screen.getByRole('button', { name: 'Remove for the whole team' }));
      expect(screen.getByText(/loses it from their list/i)).toBeTruthy();
      expect(calls.some((c) => c.url === '/api/team/repos/team-app/remove')).toBe(false);
      fireEvent.click(screen.getByRole('button', { name: 'Remove for everyone' }));
      await waitFor(() => {
        expect(calls.some((c) => c.url === '/api/team/repos/team-app/remove' && c.init?.method === 'POST')).toBe(true);
      });
    });

    it('does not offer team actions to someone who is not an admin', async () => {
      mockServer('member');
      render(<ReposSection />);
      await waitFor(() => expect(screen.getAllByText('Team App').length).toBeGreaterThan(0));
      expect(screen.queryByRole('button', { name: 'Add for the whole team' })).toBeNull();
    });

    it('uses neutral placeholders instead of a product name', async () => {
      mockServer('member');
      render(<ReposSection />);
      await waitFor(() => expect(screen.getAllByText('Team App').length).toBeGreaterThan(0));
      fireEvent.click(screen.getByRole('button', { name: 'Add repo' }));
      expect(screen.getByPlaceholderText('My project')).toBeTruthy();
      expect(document.body.innerHTML.toLowerCase()).not.toContain('placeholder="cero');
      expect((screen.getByPlaceholderText('my-product') as HTMLInputElement).value).toBe('');
    });
  });
});
