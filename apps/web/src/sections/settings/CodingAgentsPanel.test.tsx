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

  it('renders Magnm MCP server toggle, real-path setup commands, daily count, and copy buttons', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: writeTextMock },
    });

    const claudeCmd =
      'claude mcp add magnm -- python3 /abs/repo/engine/mcp_server.py --data-dir /abs/data';
    const geminiJson = JSON.stringify(
      {
        mcpServers: {
          magnm: {
            command: 'python3',
            args: ['/abs/repo/engine/mcp_server.py', '--data-dir', '/abs/data'],
          },
        },
      },
      null,
      2,
    );
    const codexToml =
      '[mcp_servers.magnm]\ncommand = "python3"\nargs = ["/abs/repo/engine/mcp_server.py", "--data-dir", "/abs/data"]';

    const mcpClients = [
      {
        id: 'claude',
        name: 'Claude Code',
        format: 'cli',
        config_hint: 'Run in your terminal',
        snippet: claudeCmd,
      },
      {
        id: 'gemini',
        name: 'Gemini CLI',
        format: 'json',
        config_hint: 'Add to ~/.gemini/settings.json',
        snippet: geminiJson,
      },
      {
        id: 'codex',
        name: 'Codex',
        format: 'toml',
        config_hint: 'Add to ~/.codex/config.toml',
        snippet: codexToml,
      },
    ];

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
            },
            {
              id: 'gemini',
              name: 'Gemini CLI',
              command: 'gemini',
              installed: true,
              path: '/usr/bin/gemini',
              version: 'gemini 1.0.0',
              enabled: false,
              confined: false,
            },
            {
              id: 'codex',
              name: 'Codex',
              command: 'codex',
              installed: true,
              path: '/usr/bin/codex',
              version: 'codex 1.0.0',
              enabled: false,
              confined: false,
            },
          ],
          adapters: [],
          agent_engines: { writer: 'ai' },
          mcp: {
            writes_enabled: false,
            calls_today: 7,
            mcp_server_path: '/abs/repo/engine/mcp_server.py',
            data_dir: '/abs/data',
            clients: mcpClients,
          },
        });
      }
      if (url === '/api/coding/mcp' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}'));
        return jsonResponse({
          success: true,
          writes_enabled: Boolean(body.writes_enabled),
          calls_today: 7,
          mcp_server_path: '/abs/repo/engine/mcp_server.py',
          data_dir: '/abs/data',
          clients: mcpClients,
        });
      }
      return jsonResponse({});
    });

    render(<CodingAgentsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Magnm MCP server')).toBeTruthy();
    });

    expect(screen.getByText(/Magnm Studio must be running/i)).toBeTruthy();
    expect(screen.getByTestId('mcp-calls-today').textContent).toContain('7');
    expect(screen.getByText(claudeCmd)).toBeTruthy();
    expect(screen.getByText(/\[mcp_servers\.magnm\]/)).toBeTruthy();
    expect(screen.getByText(/"mcpServers"/)).toBeTruthy();
    expect(screen.queryByText(/engine_endpoint\.json/)).toBeNull();

    const toggle = screen.getByLabelText(
      'Allow coding agents to create drafts, notes and reminders',
    ) as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/coding/mcp',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ writes_enabled: true }),
        }),
      );
    });
    expect(toggle.checked).toBe(true);

    const copyClaudeBtn = screen.getByRole('button', { name: 'Copy Claude Code MCP config' });
    fireEvent.click(copyClaudeBtn);
    await waitFor(() => {
      expect(writeTextMock).toHaveBeenCalledWith(claudeCmd);
    });

    const copyCodexBtn = screen.getByRole('button', { name: 'Copy Codex MCP config' });
    fireEvent.click(copyCodexBtn);
    await waitFor(() => {
      expect(writeTextMock).toHaveBeenCalledWith(codexToml);
    });
  });
});
