import { useCallback, useEffect, useState } from 'react';
import { Card, Checkbox, PillButton, Row, RowList, Select, StatusDot } from '@magnm/design/react';
import { getCodingAgents, getMcpSettings, setAgentEngine, setMcpWritesEnabled } from '../../api/coding';
import type { CodingAgentInfo, McpSettingsInfo } from '../../types';

const AGENT_LABELS: Record<string, string> = {
  cmo: 'CMO',
  researcher: 'Researcher',
  writer: 'Writer',
  designer: 'Designer',
  'video-producer': 'Video Producer',
  reviewer: 'Reviewer',
  analyst: 'Analyst',
};

const UNCONFINED_REASON = "Found, not enabled: this tool can't yet be confined to a workspace";

/** Settings card: detected coding CLIs/adapters, confinement status, privacy note, re-detect, per-agent engine picker, and Magnm MCP server. */
export function CodingAgentsPanel() {
  const [clis, setClis] = useState<CodingAgentInfo[]>([]);
  const [adapters, setAdapters] = useState<CodingAgentInfo[]>([]);
  const [agentEngines, setAgentEngines] = useState<Record<string, string>>({});
  const [mcpSettings, setMcpSettings] = useState<McpSettingsInfo | null>(null);
  const [savingMcp, setSavingMcp] = useState(false);
  const [copiedClientId, setCopiedClientId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [savingAgent, setSavingAgent] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCoding = useCallback(async (refresh = false) => {
    if (refresh) {
      setRefreshing(true);
    }
    setError(null);
    try {
      const res = await getCodingAgents(refresh);
      setClis(Array.isArray(res?.agents) ? res.agents : []);
      setAdapters(Array.isArray(res?.adapters) ? res.adapters : []);
      setAgentEngines(
        res?.agent_engines && typeof res.agent_engines === 'object' ? res.agent_engines : {},
      );
      if (res?.mcp && typeof res.mcp === 'object') {
        setMcpSettings(res.mcp);
      } else {
        try {
          const mcpRes = await getMcpSettings();
          if (mcpRes && typeof mcpRes === 'object' && 'writes_enabled' in mcpRes) {
            setMcpSettings(mcpRes);
          }
        } catch {
          // Keep existing MCP state when engine mock omits it.
        }
      }
      if (refresh) {
        setNotice('Re-detected coding agents.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to detect coding agents.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadCoding(false);
  }, [loadCoding]);

  const handleEngineChange = async (agentId: string, engine: string) => {
    setError(null);
    setNotice(null);
    setSavingAgent(agentId);
    const prev = agentEngines[agentId] || 'ai';
    setAgentEngines((cur) => ({ ...cur, [agentId]: engine }));
    try {
      const res = await setAgentEngine(agentId, engine);
      if (res?.agent_engines && typeof res.agent_engines === 'object') {
        setAgentEngines(res.agent_engines);
      }
      setNotice(`Saved engine for ${AGENT_LABELS[agentId] || agentId}.`);
    } catch (err) {
      setAgentEngines((cur) => ({ ...cur, [agentId]: prev }));
      setError(err instanceof Error ? err.message : 'Failed to save agent engine.');
    } finally {
      setSavingAgent(null);
    }
  };

  const handleToggleMcpWrites = async (nextEnabled: boolean) => {
    setError(null);
    setNotice(null);
    setSavingMcp(true);
    const prev = mcpSettings;
    setMcpSettings((cur) => (cur ? { ...cur, writes_enabled: nextEnabled } : cur));
    try {
      const res = await setMcpWritesEnabled(nextEnabled);
      setMcpSettings({
        writes_enabled: Boolean(res.writes_enabled),
        calls_today: typeof res.calls_today === 'number' ? res.calls_today : (prev?.calls_today ?? 0),
        mcp_server_path: res.mcp_server_path ?? prev?.mcp_server_path ?? '',
        data_dir: res.data_dir ?? prev?.data_dir ?? '',
        clients: Array.isArray(res.clients) ? res.clients : (prev?.clients ?? []),
      });
      setNotice(
        nextEnabled
          ? 'Coding agents can now create drafts, notes, and reminders via MCP.'
          : 'MCP write tools disabled (read-only mode).',
      );
    } catch (err) {
      setMcpSettings(prev);
      setError(err instanceof Error ? err.message : 'Failed to update MCP write setting.');
    } finally {
      setSavingMcp(false);
    }
  };

  const handleCopySnippet = async (clientId: string, snippet: string) => {
    setError(null);
    try {
      if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) {
        throw new Error('Clipboard unavailable');
      }
      await navigator.clipboard.writeText(snippet);
      setCopiedClientId(clientId);
      setTimeout(() => {
        setCopiedClientId((cur) => (cur === clientId ? null : cur));
      }, 2000);
    } catch {
      setError('Could not copy MCP config to clipboard.');
    }
  };

  const enabledClis = clis.filter((c) => c.installed && c.enabled);
  const agentIds = Object.keys(agentEngines);
  const mcpClients = Array.isArray(mcpSettings?.clients) ? mcpSettings.clients : [];
  const mcpCallsToday = typeof mcpSettings?.calls_today === 'number' ? mcpSettings.calls_today : 0;

  return (
    <section className="studio-section" aria-labelledby="settings-coding-agents-heading">
      <div className="studio-section-bar">
        <div className="studio-settings-head">
          <span className="studio-settings-num">02b</span>
          <h2 id="settings-coding-agents-heading" className="studio-section-title">
            Coding agents
          </h2>
        </div>
        <PillButton
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={() => void loadCoding(true)}
        >
          {refreshing ? 'Detecting...' : 'Re-detect'}
        </PillButton>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      <Card>
        <div className="studio-stack-sm">
          <p className="studio-hint">
            Magnm runs your own signed-in CLI on this laptop; it never sees your Claude, ChatGPT or
            Google login
          </p>

          {loading && clis.length === 0 ? (
            <div className="mg-empty">Checking installed coding CLIs.</div>
          ) : clis.length === 0 ? (
            <div className="mg-empty">No coding CLI status returned by the engine.</div>
          ) : (
            <RowList aria-label="Detected coding CLIs">
              {clis.map((cli) => {
                const isEnabled = Boolean(cli.installed && cli.enabled);
                const statusText = cli.installed
                  ? isEnabled
                    ? 'Enabled (confined)'
                    : cli.reason || UNCONFINED_REASON
                  : null;
                return (
                  <Row
                    key={cli.id}
                    title={cli.name}
                    description={
                      cli.installed ? (
                        <div className="studio-stack-xs">
                          <span className="studio-mono">
                            {cli.version || cli.command}
                            {cli.path ? ` · ${cli.path}` : ''}
                          </span>
                          <span className="mg-chip">{statusText}</span>
                        </div>
                      ) : (
                        <div className="studio-stack-xs">
                          <span className="studio-hint">
                            Install it, sign in once in your terminal, then re-detect
                          </span>
                          {cli.install_command ? (
                            <code className="studio-mono">{cli.install_command}</code>
                          ) : null}
                        </div>
                      )
                    }
                    meta={
                      <StatusDot
                        state={!cli.installed ? 'off' : isEnabled ? 'online' : 'idle'}
                        label={!cli.installed ? 'Not installed' : isEnabled ? 'Enabled' : 'Not enabled'}
                      />
                    }
                  />
                );
              })}
              {adapters.map((adapter) => (
                <Row
                  key={`adapter-${adapter.id}`}
                  title={adapter.name}
                  description={
                    <div className="studio-stack-xs">
                      {adapter.path ? <span className="studio-mono">{adapter.path}</span> : null}
                      <span className="mg-chip">{adapter.reason || UNCONFINED_REASON}</span>
                    </div>
                  }
                  meta={<StatusDot state="idle" label="Not enabled" />}
                />
              ))}
            </RowList>
          )}
        </div>
      </Card>

      {agentIds.length > 0 ? (
        <Card outline>
          <div className="studio-stack-sm">
            <div className="mg-label">Engine picker per agent</div>
            <p className="studio-hint">
              Choose an enabled confined coding CLI to let an agent queue coding runs for your
              approval, or keep the AI gateway. Ordinary chat stays on the AI gateway.
            </p>
            <RowList aria-label="Agent engine picker">
              {agentIds.map((agentId) => {
                const current = agentEngines[agentId] || 'ai';
                const missingSelected =
                  current !== 'ai' && !enabledClis.some((c) => c.id === current);
                return (
                  <Row
                    key={agentId}
                    title={AGENT_LABELS[agentId] || agentId}
                    description={`@${agentId}`}
                  >
                    <Select
                      aria-label={`Engine for ${agentId}`}
                      value={current}
                      disabled={savingAgent === agentId}
                      onChange={(e) => void handleEngineChange(agentId, e.target.value)}
                    >
                      <option value="ai">AI gateway (default)</option>
                      {enabledClis.map((cli) => (
                        <option key={cli.id} value={cli.id}>
                          {cli.name} ({cli.command})
                        </option>
                      ))}
                      {missingSelected ? (
                        <option value={current}>{current} (not enabled)</option>
                      ) : null}
                    </Select>
                  </Row>
                );
              })}
            </RowList>
          </div>
        </Card>
      ) : null}

      <Card outline>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <div className="mg-label">Magnm MCP server</div>
            <span className="mg-chip" data-testid="mcp-calls-today">
              MCP calls today: {mcpCallsToday}
            </span>
          </div>
          <p className="studio-hint">
            Connect your own coding CLI to read drafts, research notes, reminders, weekly summaries,
            product rules, and repos. Magnm Studio must be running for the MCP tools to work.
          </p>
          <Checkbox
            label="Allow coding agents to create drafts, notes and reminders"
            checked={Boolean(mcpSettings?.writes_enabled)}
            disabled={savingMcp || mcpSettings === null}
            onChange={(e) => void handleToggleMcpWrites(e.target.checked)}
          />

          {mcpClients.length === 0 ? (
            <div className="studio-hint">
              No supported coding CLIs detected yet. Install Claude Code, Codex, Gemini CLI, or
              Antigravity, then click Re-detect to generate the exact setup command.
            </div>
          ) : (
            <div className="studio-stack-sm" aria-label="MCP setup commands">
              {mcpClients.map((client) => (
                <div key={client.id} className="studio-stack-xs">
                  <div className="studio-section-bar">
                    <div>
                      <strong>{client.name}</strong>{' '}
                      <span className="studio-hint">{client.config_hint}</span>
                    </div>
                    <PillButton
                      variant="outline"
                      size="sm"
                      aria-label={`Copy ${client.name} MCP config`}
                      onClick={() => void handleCopySnippet(client.id, client.snippet)}
                    >
                      {copiedClientId === client.id ? 'Copied' : 'Copy'}
                    </PillButton>
                  </div>
                  <pre className="studio-coding-summary-text">{client.snippet}</pre>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </section>
  );
}
