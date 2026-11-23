import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RepoEditDialog, changedFields } from './RepoEditDialog';
import { ReposSection } from './ReposSection';
import type { RepoRecord } from '../../types';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const LOCAL: RepoRecord = {
  id: 'demo-tool',
  name: 'Demo Tool',
  kind: 'local',
  status: 'in_development',
  branch: 'main',
  local_path: '/work/demo-tool',
  marketing_angle: 'Calm.',
  confidential: false,
  scope: 'personal',
};

describe('changedFields', () => {
  const form = (over = {}) => ({
    name: 'Demo Tool', id: 'demo-tool', branch: 'main', status: 'in_development', angle: 'Calm.',
    confidential: false, path: '/work/demo-tool', ...over,
  });

  it('sends only what changed', () => {
    expect(changedFields(LOCAL, form())).toEqual({});
    expect(changedFields(LOCAL, form({ name: ' New ', branch: '' }))).toEqual({ name: 'New', branch: '' });
    expect(changedFields(LOCAL, form({ id: 'moved', confidential: true, path: '/work/other' }))).toEqual({
      id: 'moved', confidential: true, local_path: '/work/other',
    });
  });

  it('never sends a path for a GitHub repo', () => {
    const gh: RepoRecord = { ...LOCAL, kind: 'github', github: 'acme/demo', local_path: undefined };
    expect(changedFields(gh, form({ path: '/etc' }))).toEqual({});
  });
});

describe('RepoEditDialog', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('posts only the changed fields and reports the saved repo', async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
      return jsonResponse({ success: true, repo: { ...LOCAL, name: 'Renamed' } });
    });
    const onSaved = vi.fn();
    render(<RepoEditDialog repo={LOCAL} onClose={() => {}} onSaved={onSaved} />);
    const save = screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed' } });
    expect((screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls).toEqual([{ url: '/api/repos/demo-tool/update', body: { name: 'Renamed' } }]);
  });

  it('warns that an id change moves the references and shows the engine refusal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ error: 'That id is already used by another repository.' }, 400));
    render(<RepoEditDialog repo={LOCAL} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(document.getElementById('repo-edit-id') as HTMLInputElement, { target: { value: 'taken' } });
    expect(screen.getByText(/moves this repo's snapshots, facts and references/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/already used/));
  });

  it('has no folder field for a GitHub repo and notes a refused phone path for a local one', () => {
    const { rerender } = render(
      <RepoEditDialog repo={{ ...LOCAL, kind: 'github', github: 'acme/demo' }} onClose={() => {}} onSaved={() => {}} />,
    );
    expect(screen.queryByLabelText('Local folder')).toBeNull();
    rerender(
      <RepoEditDialog repo={{ ...LOCAL, cloud: { synced: true, path_rejected: true } }} onClose={() => {}} onSaved={() => {}} />,
    );
    expect(screen.getByText(/sent from your phone was refused/)).toBeTruthy();
  });
});

describe('Edit button on the repos list', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows Edit on your own repos and not on team repos', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/repos') {
        return jsonResponse([
          LOCAL,
          { id: 'team-app', name: 'Team App', kind: 'github', github: 'acme/team-app', scope: 'global', status: 'released' },
        ]);
      }
      if (url.startsWith('/api/shipped') || url.startsWith('/api/facts')) return jsonResponse([]);
      return jsonResponse({});
    });
    render(<ReposSection />);
    await waitFor(() => expect(screen.getAllByText('Demo Tool').length).toBeGreaterThan(0));
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getAllByText('Edit Demo Tool').length).toBeGreaterThan(0);
  });
});
