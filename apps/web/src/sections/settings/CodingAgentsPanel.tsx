import { useCallback, useEffect, useState } from 'react';
import { Card, PillButton, Row, RowList, Select, StatusDot } from '@magnm/design/react';
import { getCodingAgents, setAgentEngine } from '../../api/coding';
import type { CodingAgentInfo } from '../../types';

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

/** Settings card: detected coding CLIs/adapters, confinement status, privacy note, re-detect, and per-agent engine picker. */
export function CodingAgentsPanel() {
  const [clis, setClis] = useState<CodingAgentInfo[]>([]);
  const [adapters, setAdapters] = useState<CodingAgentInfo[]>([]);
  const [agentEngines, setAgentEngines] = useState<Record<string, string>>({});
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

  const enabledClis = clis.filter((c) => c.installed && c.enabled);
  const agentIds = Object.keys(agentEngines);

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
    </section>
  );
}
