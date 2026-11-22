import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodingAgentsPanel } from './CodingAgentsPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('CodingAgentsPanel', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders confined, outdated, unconfined, adapter, and not-installed states and saves agent engine', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/coding/agents')) {
        return jsonResponse({
          agents: [
            {
              id: 'claude',
              name: 'Claude Code',
              command: 'claude',
              installed: true,
              path: '/usr/bin/claude',
              version: 'claude 2.1.0',
              enabled: true,
              confined: true,
              reason: null,
            },
            {
              id: 'codex',
              name: 'Codex',
              command: 'codex',
              installed: true,
              path: '/usr/bin/codex',
              version: 'codex 1.0',
              enabled: false,
              confined: false,
              reason: "Found, not enabled: this tool can't yet be confined to a workspace",
            },
            {
              id: 'gemini',
              name: 'Gemini CLI',
              command: 'gemini',
              installed: false,
              enabled: false,
              install_command: 'npm install -g @google/gemini-cli',
            },
          ],
          adapters: [
            {
              id: 'claude-code-acp',
              name: 'claude-code-acp',
              command: 'claude-code-acp',
              installed: true,
              path: '/usr/bin/claude-code-acp',
              enabled: false,
              confined: false,
              reason: "Found, not enabled: this tool can't yet be confined to a workspace",
            },
          ],
          agent_engines: {
            writer: 'ai',
          },
        });
      }
      if (url === '/api/coding/agent_engines' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          agent_id: 'writer',
          engine: 'claude',
          agent_engines: { writer: 'claude' },
        });
      }
      return jsonResponse({});
    });

    render(<CodingAgentsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Claude Code')).toBeTruthy();
    });

    expect(
      screen.getByText(
        /Magnm runs your own signed-in CLI on this laptop; it never sees your Claude, ChatGPT or Google login/i,
      ),
    ).toBeTruthy();
    expect(screen.getByText('Enabled (confined)')).toBeTruthy();
    expect(
      screen.getAllByText("Found, not enabled: this tool can't yet be confined to a workspace").length,
    ).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Install it, sign in once in your terminal/i)).toBeTruthy();

    // Unconfined codex must NOT be offered in the per-agent engine picker
    const picker = screen.getByLabelText('Engine for writer') as HTMLSelectElement;
    const optionValues = Array.from(picker.options).map((o) => o.value);
    expect(optionValues).toContain('ai');
    expect(optionValues).toContain('claude');
    expect(optionValues).not.toContain('codex');

    fireEvent.change(picker, { target: { value: 'claude' } });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/coding/agent_engines',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ agent_id: 'writer', engine: 'claude' }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Re-detect' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/coding/agents?refresh=1', expect.anything());
    });
  });

  it('shows update message when Claude Code lacks confinement flags', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse({
        agents: [
          {
            id: 'claude',
            name: 'Claude Code',
            command: 'claude',
            installed: true,
            path: '/usr/bin/claude',
            version: 'claude 1.0.0',
            enabled: false,
            confined: false,
            reason: 'Update Claude Code to use it here',
          },
        ],
        adapters: [],
        agent_engines: { writer: 'ai' },
      }),
    );

    render(<CodingAgentsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Update Claude Code to use it here')).toBeTruthy();
    });
  });
});
