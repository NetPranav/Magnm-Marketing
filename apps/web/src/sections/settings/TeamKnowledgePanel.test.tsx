import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { TeamKnowledgePanel } from './TeamKnowledgePanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('TeamKnowledgePanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('hides the card when signed out', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({ configured: true, signed_in: false, user: null });
      }
      return jsonResponse({});
    });

    const { container } = render(<TeamKnowledgePanel />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/account', expect.anything());
    });
    expect(container.textContent).toBe('');
    expect(fetchMock).not.toHaveBeenCalledWith('/api/team/knowledge', expect.anything());
  });

  it('renders member view with relative update info, error, and no publish/apply buttons', async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();

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
      if (url === '/api/team/knowledge') {
        return jsonResponse({
          signed_in: true,
          role: 'member',
          items: [
            {
              key: 'brand_kit',
              label: 'Brand kit',
              managed_by_team: true,
              role: 'member',
              local_changed_since_publish: false,
              remote_updated_at: twoHoursAgo,
              remote_updated_by: 'Founder',
              applied_at: twoHoursAgo,
              remote_newer: false,
              last_error: null,
            },
            {
              key: 'product_rules',
              label: 'Product rules',
              managed_by_team: true,
              role: 'member',
              local_changed_since_publish: false,
              remote_updated_at: twoHoursAgo,
              remote_updated_by: 'Founder',
              applied_at: null,
              remote_newer: false,
              last_error: 'Invalid product rules: bad status',
            },
          ],
        });
      }
      return jsonResponse({});
    });

    render(<TeamKnowledgePanel />);

    await waitFor(() => {
      expect(screen.getByText('Brand kit')).toBeTruthy();
    });
    expect(
      screen.getAllByText('Managed by your admin · updated 2 hours ago by Founder').length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('alert').textContent).toContain('Invalid product rules: bad status');
    expect(screen.queryByRole('button', { name: /Publish/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Apply/i })).toBeNull();
  });

  it('renders admin view with publish, changed indicator, and apply with backup confirmation', async () => {
    const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
    let appliedTemplates = false;

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'a-1',
            email: 'admin@example.com',
            display_name: 'Founder',
            avatar_url: null,
            role: 'admin',
            active: true,
          },
        });
      }
      if (url === '/api/team/knowledge') {
        return jsonResponse({
          signed_in: true,
          role: 'admin',
          items: [
            {
              key: 'brand_kit',
              label: 'Brand kit',
              managed_by_team: false,
              role: 'admin',
              local_changed_since_publish: true,
              published_at: threeDaysAgo,
              remote_updated_at: threeDaysAgo,
              remote_updated_by: 'Founder',
              applied_at: threeDaysAgo,
              remote_newer: false,
              last_error: null,
            },
            {
              key: 'templates',
              label: 'Templates',
              managed_by_team: false,
              role: 'admin',
              local_changed_since_publish: false,
              published_at: threeDaysAgo,
              remote_updated_at: new Date().toISOString(),
              remote_updated_by: 'CoFounder',
              applied_at: threeDaysAgo,
              remote_newer: !appliedTemplates,
              last_error: null,
            },
          ],
        });
      }
      if (url === '/api/team/knowledge/brand_kit/publish' && init?.method === 'POST') {
        return jsonResponse({
          ok: true,
          status: {
            signed_in: true,
            role: 'admin',
            items: [
              {
                key: 'brand_kit',
                label: 'Brand kit',
                managed_by_team: false,
                role: 'admin',
                local_changed_since_publish: false,
                published_at: new Date().toISOString(),
                remote_updated_at: new Date().toISOString(),
                remote_updated_by: 'Founder',
                applied_at: new Date().toISOString(),
                remote_newer: false,
                last_error: null,
              },
              {
                key: 'templates',
                label: 'Templates',
                managed_by_team: false,
                role: 'admin',
                local_changed_since_publish: false,
                published_at: threeDaysAgo,
                remote_updated_at: new Date().toISOString(),
                remote_updated_by: 'CoFounder',
                applied_at: threeDaysAgo,
                remote_newer: !appliedTemplates,
                last_error: null,
              },
            ],
          },
        });
      }
      if (url === '/api/team/knowledge/templates/apply' && init?.method === 'POST') {
        appliedTemplates = true;
        return jsonResponse({
          ok: true,
          status: {
            signed_in: true,
            role: 'admin',
            items: [
              {
                key: 'brand_kit',
                label: 'Brand kit',
                managed_by_team: false,
                role: 'admin',
                local_changed_since_publish: false,
                published_at: new Date().toISOString(),
                remote_updated_at: new Date().toISOString(),
                remote_updated_by: 'Founder',
                applied_at: new Date().toISOString(),
                remote_newer: false,
                last_error: null,
              },
              {
                key: 'templates',
                label: 'Templates',
                managed_by_team: false,
                role: 'admin',
                local_changed_since_publish: false,
                published_at: threeDaysAgo,
                remote_updated_at: new Date().toISOString(),
                remote_updated_by: 'CoFounder',
                applied_at: new Date().toISOString(),
                remote_newer: false,
                last_error: null,
              },
            ],
          },
        });
      }
      return jsonResponse({});
    });

    render(<TeamKnowledgePanel />);

    await waitFor(() => {
      expect(screen.getAllByText('Published 3 days ago').length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText('Changed since last publish')).toBeTruthy();
    expect(screen.getByText('Team version is newer')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Publish Brand kit to team' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/team/knowledge/brand_kit/publish',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(screen.queryByText('Changed since last publish')).toBeNull();

    // Apply team version requires confirmation mentioning backup first
    fireEvent.click(screen.getByRole('button', { name: 'Apply team version for Templates' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/backed up first/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith('/api/team/knowledge/templates/apply', expect.anything());

    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply team version' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/team/knowledge/templates/apply',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    await waitFor(() => {
      expect(screen.queryByText('Team version is newer')).toBeNull();
    });
  });
});
