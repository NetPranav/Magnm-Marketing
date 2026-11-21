import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  Card,
  Checkbox,
  Dialog,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  StatusDot,
  TextInput,
  type StatusState,
} from '@magnm/design/react';
import {
  clearAdminAiKeys,
  getAdminAi,
  shareLocalAiKeys,
  testAdminAiKeys,
  updateAdminAiLimits,
  updateAdminAiModel,
  type AdminAiKeyLabel,
  type AdminAiOverview,
  type AdminAiTestResult,
  type AdminMember,
} from '../../api/admin';
import { memberDisplayName } from './adminUtils';

export interface AdminAiKeysPanelProps {
  members: AdminMember[];
}

type KeyConfirmAction = 'share' | 'clear' | null;

function formatCooldownTime(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function describeKeyHealth(entry: AdminAiKeyLabel): { state: StatusState; label: string } {
  if (entry.cooldown_until) {
    const untilMs = new Date(entry.cooldown_until).getTime();
    if (!Number.isNaN(untilMs) && untilMs > Date.now()) {
      return {
        state: 'idle',
        label: `Cooling down until ${formatCooldownTime(entry.cooldown_until)}`,
      };
    }
  }
  if (entry.last_status !== null && entry.last_status >= 200 && entry.last_status < 300) {
    return { state: 'online', label: 'OK' };
  }
  if (entry.last_error) {
    const short = entry.last_error.length > 80 ? `${entry.last_error.slice(0, 77)}...` : entry.last_error;
    return { state: 'error', label: `Failing: ${short}` };
  }
  if (entry.last_status !== null && entry.last_status >= 400) {
    return { state: 'error', label: `Failing (HTTP ${entry.last_status})` };
  }
  if (entry.last_ok_at) {
    return { state: 'online', label: 'OK' };
  }
  return { state: 'off', label: 'Not used yet' };
}

function clampPct(used: number, limit: number): number {
  if (!limit || limit <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
}

/** Admin AI keys tab: Vault key pool, daily limits, member usage, and allowed models. */
export function AdminAiKeysPanel({ members }: AdminAiKeysPanelProps) {
  const [data, setData] = useState<AdminAiOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<KeyConfirmAction>(null);
  const [testResult, setTestResult] = useState<AdminAiTestResult | null>(null);

  const [defaultRequests, setDefaultRequests] = useState('200');
  const [defaultTokens, setDefaultTokens] = useState('400000');
  const [overrideMemberId, setOverrideMemberId] = useState('');
  const [overrideRequests, setOverrideRequests] = useState('200');
  const [overrideTokens, setOverrideTokens] = useState('400000');
  const [modelCaps, setModelCaps] = useState<Record<string, string>>({});

  const syncFormState = useCallback((overview: AdminAiOverview) => {
    setDefaultRequests(String(overview.default_limit?.daily_requests ?? 200));
    setDefaultTokens(String(overview.default_limit?.daily_tokens ?? 400000));
    const nextCaps: Record<string, string> = {};
    for (const m of overview.models || []) {
      nextCaps[m.model] = String(m.max_tokens_cap);
    }
    setModelCaps(nextCaps);
  }, []);

  const loadAi = useCallback(async () => {
    setError(null);
    try {
      const res = await getAdminAi();
      setData(res);
      syncFormState(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AI keys settings.');
    } finally {
      setLoading(false);
    }
  }, [syncFormState]);

  useEffect(() => {
    void loadAi();
  }, [loadAi]);

  useEffect(() => {
    if (!overrideMemberId && members.length > 0) {
      setOverrideMemberId(members[0].id);
    }
  }, [members, overrideMemberId]);

  const handleConfirmKeyAction = async () => {
    if (!confirmAction) return;
    setError(null);
    setNotice(null);
    setBusy(confirmAction);
    try {
      if (confirmAction === 'share') {
        const res = await shareLocalAiKeys();
        setNotice(`Shared ${res.count} ${res.count === 1 ? 'key' : 'keys'} with the team.`);
      } else {
        await clearAdminAiKeys();
        setNotice('Removed all shared team keys.');
      }
      setConfirmAction(null);
      await loadAi();
    } catch (err) {
      setConfirmAction(null);
      setError(err instanceof Error ? err.message : 'Could not update shared AI keys.');
    } finally {
      setBusy(null);
    }
  };

  const handleTestKeys = async () => {
    setError(null);
    setTestResult(null);
    setBusy('test');
    try {
      const res = await testAdminAiKeys();
      setTestResult(res);
      // The test changes key health and today's usage, so show the fresh values.
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Test request failed.');
    } finally {
      setBusy(null);
    }
  };

  const handleSaveDefaultLimits = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    const reqs = Number(defaultRequests);
    const toks = Number(defaultTokens);
    if (!Number.isInteger(reqs) || reqs < 1 || reqs > 100000) {
      setError('Daily requests limit must be between 1 and 100,000.');
      return;
    }
    if (!Number.isInteger(toks) || toks < 1 || toks > 100000000) {
      setError('Daily tokens limit must be between 1 and 100,000,000.');
      return;
    }
    setBusy('default-limit');
    try {
      await updateAdminAiLimits({ member_id: null, daily_requests: reqs, daily_tokens: toks });
      setNotice('Saved team default limits.');
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save default limits.');
    } finally {
      setBusy(null);
    }
  };

  const handleSaveMemberOverride = async (e: FormEvent) => {
    e.preventDefault();
    if (!overrideMemberId) {
      setError('Select a member first.');
      return;
    }
    setError(null);
    setNotice(null);
    const reqs = Number(overrideRequests);
    const toks = Number(overrideTokens);
    if (!Number.isInteger(reqs) || reqs < 1 || reqs > 100000) {
      setError('Member requests limit must be between 1 and 100,000.');
      return;
    }
    if (!Number.isInteger(toks) || toks < 1 || toks > 100000000) {
      setError('Member tokens limit must be between 1 and 100,000,000.');
      return;
    }
    setBusy('override-limit');
    try {
      await updateAdminAiLimits({ member_id: overrideMemberId, daily_requests: reqs, daily_tokens: toks });
      setNotice('Saved member limit override.');
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save member limit override.');
    } finally {
      setBusy(null);
    }
  };

  const handleResetMemberOverride = async (memberId: string) => {
    setError(null);
    setNotice(null);
    setBusy(`reset-${memberId}`);
    try {
      await updateAdminAiLimits({ member_id: memberId, reset: true });
      setNotice('Reset member to team default limits.');
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reset member limit.');
    } finally {
      setBusy(null);
    }
  };

  const handleToggleModel = async (model: string, nextEnabled: boolean, currentCap: number) => {
    setError(null);
    setNotice(null);
    const rawCap = Number(modelCaps[model] ?? currentCap);
    const cap = Number.isInteger(rawCap) && rawCap >= 1 && rawCap <= 32768 ? rawCap : currentCap;
    setBusy(`model-${model}`);
    try {
      await updateAdminAiModel({ model, enabled: nextEnabled, max_tokens_cap: cap });
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update model.');
    } finally {
      setBusy(null);
    }
  };

  const handleSaveModelCap = async (model: string, enabled: boolean) => {
    setError(null);
    setNotice(null);
    const cap = Number(modelCaps[model]);
    if (!Number.isInteger(cap) || cap < 1 || cap > 32768) {
      setError('Max tokens cap must be between 1 and 32,768.');
      return;
    }
    setBusy(`model-${model}`);
    try {
      await updateAdminAiModel({ model, enabled, max_tokens_cap: cap });
      setNotice(`Updated ${model}.`);
      await loadAi();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update model.');
    } finally {
      setBusy(null);
    }
  };

  const localCount = typeof data?.local_key_count === 'number' ? data.local_key_count : null;
  const shareConfirmMessage =
    localCount !== null && localCount > 0
      ? `Replace the team's shared keys with the ${localCount} keys on this laptop? Members will use them for AI. Keys are stored encrypted and can't be read back.`
      : "Replace the team's shared keys with this laptop's keys? Members will use them for AI. Keys are stored encrypted and can't be read back.";

  return (
    <section className="studio-section" aria-labelledby="admin-ai-heading">
      <div>
        <h2 id="admin-ai-heading" className="studio-section-title">
          AI keys
        </h2>
        <div className="studio-hint">
          Shared NVIDIA NIM keys stored encrypted in Vault, daily member limits, usage, and allowed models.
        </div>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="mg-notice" role="status">
          {notice}
        </div>
      ) : null}

      {loading && !data ? (
        <div className="mg-empty">Loading AI keys settings.</div>
      ) : data ? (
        <div className="studio-stack">
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-section-bar">
                <div>
                  <span className="mg-label">Team keys</span>
                  <div className="studio-hint">
                    Keys are stored encrypted and can never be read back.
                  </div>
                </div>
                <div className="studio-row-actions">
                  <PillButton
                    size="sm"
                    disabled={busy !== null}
                    onClick={() => setConfirmAction('share')}
                  >
                    Share this laptop&apos;s keys
                  </PillButton>
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={busy !== null}
                    onClick={() => void handleTestKeys()}
                  >
                    {busy === 'test' ? 'Testing...' : 'Test team keys'}
                  </PillButton>
                  <PillButton
                    variant="danger"
                    size="sm"
                    disabled={busy !== null || data.keys.length === 0}
                    onClick={() => setConfirmAction('clear')}
                  >
                    Remove all team keys
                  </PillButton>
                </div>
              </div>

              {testResult ? (
                <div className="studio-mono" role="status">
                  {testResult.ok
                    ? `OK in ${testResult.latency_ms} ms with ${testResult.model}`
                    : testResult.error || `Failed (HTTP ${testResult.status})`}
                </div>
              ) : null}

              {data.keys.length === 0 ? (
                <div className="studio-hint">
                  No shared AI keys yet. Share this laptop&apos;s keys so members can run AI without local keys.
                </div>
              ) : (
                <RowList aria-label="Team AI keys">
                  {data.keys.map((item) => {
                    const health = describeKeyHealth(item);
                    return (
                      <Row
                        key={item.label}
                        title={<span className="studio-mono">{item.label}</span>}
                        description={
                          item.last_ok_at ? (
                            <span className="studio-mono">
                              Last OK: {formatCooldownTime(item.last_ok_at)}
                            </span>
                          ) : undefined
                        }
                        meta={<StatusDot state={health.state} label={health.label} />}
                      />
                    );
                  })}
                </RowList>
              )}
            </div>
          </Card>

          <Card outline>
            <div className="studio-stack-sm">
              <span className="mg-label">Limits</span>
              <form className="studio-inline-form studio-account-invite-form" onSubmit={(e) => void handleSaveDefaultLimits(e)}>
                <Field label="Default requests / day" htmlFor="admin-ai-default-req">
                  <TextInput
                    id="admin-ai-default-req"
                    type="number"
                    min={1}
                    max={100000}
                    value={defaultRequests}
                    onChange={(e) => setDefaultRequests(e.target.value)}
                  />
                </Field>
                <Field label="Default tokens / day" htmlFor="admin-ai-default-tok">
                  <TextInput
                    id="admin-ai-default-tok"
                    type="number"
                    min={1}
                    max={100000000}
                    value={defaultTokens}
                    onChange={(e) => setDefaultTokens(e.target.value)}
                  />
                </Field>
                <PillButton type="submit" size="sm" disabled={busy === 'default-limit'}>
                  {busy === 'default-limit' ? 'Saving...' : 'Save default'}
                </PillButton>
              </form>

              {members.length > 0 ? (
                <form className="studio-inline-form studio-account-invite-form" onSubmit={(e) => void handleSaveMemberOverride(e)}>
                  <Field label="Member override" htmlFor="admin-ai-override-member">
                    <Select
                      id="admin-ai-override-member"
                      value={overrideMemberId}
                      onChange={(e) => setOverrideMemberId(e.target.value)}
                    >
                      {members.map((m) => (
                        <option key={m.id} value={m.id}>
                          {memberDisplayName(m)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Requests / day" htmlFor="admin-ai-override-req">
                    <TextInput
                      id="admin-ai-override-req"
                      type="number"
                      min={1}
                      max={100000}
                      value={overrideRequests}
                      onChange={(e) => setOverrideRequests(e.target.value)}
                    />
                  </Field>
                  <Field label="Tokens / day" htmlFor="admin-ai-override-tok">
                    <TextInput
                      id="admin-ai-override-tok"
                      type="number"
                      min={1}
                      max={100000000}
                      value={overrideTokens}
                      onChange={(e) => setOverrideTokens(e.target.value)}
                    />
                  </Field>
                  <PillButton type="submit" variant="outline" size="sm" disabled={busy === 'override-limit'}>
                    {busy === 'override-limit' ? 'Saving...' : 'Set override'}
                  </PillButton>
                </form>
              ) : null}

              {data.member_limits.length === 0 ? (
                <div className="studio-hint">No per-member overrides set.</div>
              ) : (
                <RowList aria-label="Member limit overrides">
                  {data.member_limits.map((ovr) => (
                    <Row
                      key={ovr.owner_id}
                      title={ovr.display_name}
                      description={
                        <span className="studio-mono">
                          {ovr.daily_requests.toLocaleString()} requests/day · {ovr.daily_tokens.toLocaleString()} tokens/day
                        </span>
                      }
                      meta={
                        <PillButton
                          variant="quiet"
                          size="sm"
                          disabled={busy === `reset-${ovr.owner_id}`}
                          aria-label={`Reset limit for ${ovr.display_name}`}
                          onClick={() => void handleResetMemberOverride(ovr.owner_id)}
                        >
                          Reset
                        </PillButton>
                      }
                    />
                  ))}
                </RowList>
              )}
            </div>
          </Card>

          <Card outline>
            <div className="studio-stack-sm">
              <div className="studio-section-bar">
                <span className="mg-label">Usage</span>
                <span className="studio-mono">UTC day: {data.today_utc}</span>
              </div>
              {data.usage.length === 0 ? (
                <div className="studio-hint">No member usage recorded yet.</div>
              ) : (
                <div className="studio-table-wrap">
                  <table className="studio-table" aria-label="Member AI usage">
                    <thead>
                      <tr>
                        <th>Member</th>
                        <th>Today requests</th>
                        <th>Today tokens</th>
                        <th>7-day total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.usage.map((row) => {
                        const reqPct = clampPct(row.today.requests, row.limit.daily_requests);
                        const tokPct = clampPct(row.today.tokens, row.limit.daily_tokens);
                        return (
                          <tr key={row.owner_id}>
                            <td>
                              <div>{row.display_name}</div>
                              {row.limit.is_override ? (
                                <span className="studio-mono">Custom limit</span>
                              ) : null}
                            </td>
                            <td>
                              <div className="studio-stack-xs">
                                <span className="studio-mono">
                                  {row.today.requests} / {row.limit.daily_requests}
                                </span>
                                <div
                                  className="studio-progress-track"
                                  role="progressbar"
                                  aria-label={`${row.display_name} today requests`}
                                  aria-valuemin={0}
                                  aria-valuemax={100}
                                  aria-valuenow={reqPct}
                                >
                                  <div className="studio-progress-fill" style={{ width: `${reqPct}%` }} />
                                </div>
                              </div>
                            </td>
                            <td>
                              <div className="studio-stack-xs">
                                <span className="studio-mono">
                                  {row.today.tokens.toLocaleString()} / {row.limit.daily_tokens.toLocaleString()}
                                </span>
                                <div
                                  className="studio-progress-track"
                                  role="progressbar"
                                  aria-label={`${row.display_name} today tokens`}
                                  aria-valuemin={0}
                                  aria-valuemax={100}
                                  aria-valuenow={tokPct}
                                >
                                  <div className="studio-progress-fill" style={{ width: `${tokPct}%` }} />
                                </div>
                              </div>
                            </td>
                            <td className="studio-mono">
                              {row.seven_days.requests} req · {row.seven_days.tokens.toLocaleString()} tok
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </Card>

          <Card outline>
            <div className="studio-stack-sm">
              <span className="mg-label">Models</span>
              {data.models.length === 0 ? (
                <div className="studio-hint">No allowed models configured.</div>
              ) : (
                <RowList aria-label="Allowed AI models">
                  {data.models.map((item) => (
                    <Row
                      key={item.model}
                      title={<span className="studio-mono-wrap">{item.model}</span>}
                      meta={
                        <div className="studio-row-actions">
                          <Checkbox
                            label="Enabled"
                            aria-label={`Enable ${item.model}`}
                            checked={item.enabled}
                            disabled={busy === `model-${item.model}`}
                            onChange={(e) =>
                              void handleToggleModel(item.model, e.target.checked, item.max_tokens_cap)
                            }
                          />
                          <TextInput
                            type="number"
                            min={1}
                            max={32768}
                            className="studio-admin-cap-input"
                            aria-label={`Max tokens cap for ${item.model}`}
                            value={modelCaps[item.model] ?? String(item.max_tokens_cap)}
                            onChange={(e) =>
                              setModelCaps((prev) => ({
                                ...prev,
                                [item.model]: e.target.value,
                              }))
                            }
                          />
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busy === `model-${item.model}`}
                            aria-label={`Save ${item.model}`}
                            onClick={() => void handleSaveModelCap(item.model, item.enabled)}
                          >
                            Save
                          </PillButton>
                        </div>
                      }
                    />
                  ))}
                </RowList>
              )}
            </div>
          </Card>
        </div>
      ) : null}

      <Dialog
        open={Boolean(confirmAction)}
        onClose={() => setConfirmAction(null)}
        title={confirmAction === 'share' ? "Share this laptop's keys" : 'Remove all team keys'}
        subtitle="Shared AI keys"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmAction(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant={confirmAction === 'clear' ? 'danger' : 'primary'}
              size="sm"
              disabled={busy === 'share' || busy === 'clear'}
              onClick={() => void handleConfirmKeyAction()}
            >
              {busy === 'share' || busy === 'clear' ? 'Working...' : 'Confirm'}
            </PillButton>
          </>
        }
      >
        <p className="mg-sub">
          {confirmAction === 'share'
            ? shareConfirmMessage
            : 'Remove all shared AI keys? Members without their own keys will stop using team AI.'}
        </p>
      </Dialog>
    </section>
  );
}
