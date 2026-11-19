import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Card,
  Dialog,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  SheetHeader,
  StatusDot,
  TextInput,
  ThemeToggle,
  type StatusState,
} from '@magnm/design/react';
import {
  KEYCHAIN_EXTERNAL_NOTE,
  getHealth,
  getKeyPool,
  getLatestBakeoff,
  getStatus,
  importLegacyFolder,
  installBrowser,
  listJobs,
  runSetupCheck,
  saveEnvConfig,
  testRoleModel,
  type ImportLegacyResult,
  type TestAiResult,
} from '../../api/settings';
import {
  deleteSecret,
  isDesktop,
  listSecrets,
  type SecretStatus,
} from '../../desktop';
import { useFallbackPoll, useStudioEvents } from '../../events';
import { PitchBasePricesPanel } from './PitchBasePricesPanel';
import type {
  BakeoffModelStat,
  BakeoffResult,
  EngineStatusInfo,
  HealthInfo,
  JobRecord,
  KeyPoolEntry,
  SetupCheckResult,
} from '../../types';

const NIM_KEY_OPTIONS = [
  'NVIDIA_NIM_API_KEY',
  'NVIDIA_NIM_API_KEY1',
  'NVIDIA_NIM_API_KEY2',
  'NVIDIA_NIM_API_KEY3',
  'NVIDIA_NIM_API_KEY4',
  'NVIDIA_NIM_API_KEY5',
  'NVIDIA_NIM_API_KEY6',
  'NVIDIA_NIM_API_KEY7',
  'NVIDIA_NIM_API_KEY8',
  'NVIDIA_NIM_API_KEY9',
  'NVIDIA_NIM_API_KEY10',
] as const;

const PROVIDER_KEY_OPTIONS = [
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
  'GEMINI_API_KEY',
  'GROQ_API_KEY',
  'GITHUB_TOKEN',
] as const;

function mapSetupState(status: string): StatusState {
  switch (status) {
    case 'ok':
      return 'online';
    case 'warn':
      return 'idle';
    case 'missing':
      return 'error';
    default:
      return 'off';
  }
}

function mapPoolState(entry: KeyPoolEntry): { state: StatusState; label: string } {
  if (entry.disabled_reason) {
    return { state: 'error', label: 'Rejected' };
  }
  if (entry.cooling_down_s > 0) {
    return { state: 'idle', label: `Cooling (${entry.cooling_down_s}s)` };
  }
  if (entry.healthy) {
    return { state: 'online', label: 'Healthy' };
  }
  return { state: 'idle', label: 'Cooling' };
}

function deriveBakeoffRows(bakeoff: BakeoffResult | null): BakeoffModelStat[] {
  if (!bakeoff) return [];
  if (bakeoff.summary && Object.keys(bakeoff.summary).length > 0) {
    return Object.values(bakeoff.summary);
  }
  const outputs = bakeoff.outputs || {};
  const byLabel = new Map<string, { latencies: number[]; warns: number; errors: number; count: number }>();
  for (const entries of Object.values(outputs)) {
    for (const entry of entries || []) {
      const key = entry.label || 'Option';
      const cur = byLabel.get(key) ?? { latencies: [], warns: 0, errors: 0, count: 0 };
      cur.count += 1;
      if (typeof entry.latency_ms === 'number') cur.latencies.push(entry.latency_ms);
      if (entry.error) cur.errors += 1;
      const findings = entry.auto_review?.findings;
      if (Array.isArray(findings)) cur.warns += findings.length;
      byLabel.set(key, cur);
    }
  }
  return Array.from(byLabel.entries()).map(([model, agg]) => {
    const sorted = [...agg.latencies].sort((a, b) => a - b);
    const mid = sorted.length > 0 ? sorted[Math.floor(sorted.length / 2)] : 0;
    return {
      model,
      count: agg.count,
      median_latency_ms: mid,
      auto_review_warns: agg.warns,
      errors: agg.errors,
    };
  });
}

/** Settings section: engines/models, keys status & entry, setup check, import, bake-off, appearance, and about. */
export function SettingsSection() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [status, setStatus] = useState<EngineStatusInfo | null>(null);
  const [keyPool, setKeyPool] = useState<KeyPoolEntry[]>([]);
  const [setupCheck, setSetupCheck] = useState<SetupCheckResult | null>(null);
  const [bakeoff, setBakeoff] = useState<BakeoffResult | null>(null);
  const [browserJob, setBrowserJob] = useState<JobRecord | null>(null);

  // Gateway & role model inputs
  const [defaultProvider, setDefaultProvider] = useState('nvidia_nim');
  const [nimBaseUrl, setNimBaseUrl] = useState('https://integrate.api.nvidia.com/v1');
  const [nimFallbackModel, setNimFallbackModel] = useState('');
  const [roleModels, setRoleModels] = useState<Record<string, string>>({});
  const [roleTests, setRoleTests] = useState<Record<string, TestAiResult>>({});

  const desktopMode = isDesktop();

  // Key entry form (never prefilled, always password + autocomplete off, cleared after save)
  const [newKeyName, setNewKeyName] = useState('NVIDIA_NIM_API_KEY');
  const [newKeyValue, setNewKeyValue] = useState('');
  const [keyNotice, setKeyNotice] = useState<string | null>(null);
  const [keychainNotice, setKeychainNotice] = useState<string | null>(null);
  const [desktopSecrets, setDesktopSecrets] = useState<SecretStatus[]>([]);
  const [desktopKeyValues, setDesktopKeyValues] = useState<Record<string, string>>({});
  const [confirmRemoveKey, setConfirmRemoveKey] = useState<string | null>(null);

  // Legacy import form
  const [legacyPath, setLegacyPath] = useState('');
  const [legacyResult, setLegacyResult] = useState<ImportLegacyResult | null>(null);

  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const syncGatewayInputs = useCallback((st: EngineStatusInfo | null) => {
    const env = st?.env_status;
    if (env?.default_provider) setDefaultProvider(env.default_provider);
    if (env?.nvidia_nim_base_url) setNimBaseUrl(env.nvidia_nim_base_url);
    if (env?.nvidia_nim_fallback_model) setNimFallbackModel(env.nvidia_nim_fallback_model);

    const matrix = st?.models_matrix || [];
    setRoleModels((prev) => {
      const next = { ...prev };
      for (const row of matrix) {
        if (next[row.role_id] === undefined) {
          next[row.role_id] = row.model || row.primary || '';
        }
      }
      return next;
    });
  }, []);

  const loadAll = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      getHealth(),
      getStatus(),
      getKeyPool(),
      runSetupCheck(),
      getLatestBakeoff(),
      listJobs(10),
      desktopMode ? listSecrets() : Promise.resolve<SecretStatus[]>([]),
    ]);

    let firstErr: string | null = null;
    const [healthRes, statusRes, poolRes, setupRes, bakeoffRes, jobsRes, secretsRes] = results;

    if (healthRes.status === 'fulfilled') {
      setHealth(healthRes.value);
    } else if (healthRes.reason instanceof Error) {
      firstErr = firstErr ?? healthRes.reason.message;
    }

    if (statusRes.status === 'fulfilled') {
      setStatus(statusRes.value);
      syncGatewayInputs(statusRes.value);
    } else if (statusRes.reason instanceof Error) {
      firstErr = firstErr ?? statusRes.reason.message;
    }

    if (poolRes.status === 'fulfilled') {
      setKeyPool(Array.isArray(poolRes.value) ? poolRes.value : []);
    } else if (poolRes.reason instanceof Error) {
      firstErr = firstErr ?? poolRes.reason.message;
    }

    if (setupRes.status === 'fulfilled') {
      setSetupCheck(setupRes.value);
    }

    if (bakeoffRes.status === 'fulfilled') {
      setBakeoff(bakeoffRes.value);
    }

    if (jobsRes.status === 'fulfilled' && Array.isArray(jobsRes.value)) {
      const activeInstall = jobsRes.value.find(
        (j) => j.name === 'install_browser' && (j.status === 'running' || j.status === 'queued'),
      );
      if (activeInstall) {
        setBrowserJob(activeInstall);
      }
    }

    if (desktopMode) {
      if (secretsRes.status === 'fulfilled') {
        setDesktopSecrets(Array.isArray(secretsRes.value) ? secretsRes.value : []);
      } else if (secretsRes.reason instanceof Error) {
        firstErr = firstErr ?? secretsRes.reason.message;
      }
    }

    setError(firstErr);
    setLoading(false);
  }, [desktopMode, syncGatewayInputs]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const eventsState = useStudioEvents(
    useCallback((eventName, data) => {
      if (eventName === 'job' && data && typeof data === 'object') {
        const job = data as JobRecord;
        if (job.name === 'install_browser') {
          setBrowserJob(job);
          if (job.status === 'done' || job.status === 'failed') {
            void runSetupCheck()
              .then((res) => setSetupCheck(res))
              .catch(() => {});
          }
        }
      }
    }, []),
  );

  const browserJobActive = Boolean(
    browserJob && (browserJob.status === 'running' || browserJob.status === 'queued'),
  );

  useFallbackPoll(
    useCallback(() => {
      void listJobs(10)
        .then((jobs) => {
          const match = (jobs || []).find((j) =>
            browserJob ? j.id === browserJob.id : j.name === 'install_browser',
          );
          if (match) {
            setBrowserJob(match);
            if (match.status === 'done' || match.status === 'failed') {
              void runSetupCheck()
                .then((res) => setSetupCheck(res))
                .catch(() => {});
            }
          }
        })
        .catch(() => {});
    }, [browserJob]),
    browserJobActive,
    eventsState,
    5000,
  );

  const handleSaveGateway = async () => {
    setError(null);
    setNotice(null);
    setBusyId('save-gateway');
    try {
      const updates: Record<string, string> = {
        AI_DEFAULT_PROVIDER: defaultProvider,
      };
      if (nimBaseUrl.trim()) updates.NVIDIA_NIM_BASE_URL = nimBaseUrl.trim();
      if (nimFallbackModel.trim()) updates.NIM_FALLBACK_MODEL = nimFallbackModel.trim();
      const res = await saveEnvConfig(updates);
      if (!res.success) {
        if (res.error_code === 'use_desktop_keychain') {
          setKeychainNotice(KEYCHAIN_EXTERNAL_NOTE);
          setError(KEYCHAIN_EXTERNAL_NOTE);
        } else {
          setError(res.error || 'Failed to save gateway settings.');
        }
        return;
      }
      const nextStatus = await getStatus();
      setStatus(nextStatus);
      setNotice('Saved gateway settings.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save gateway settings.');
    } finally {
      setBusyId(null);
    }
  };

  const handleSaveRoleModel = async (roleId: string, modelEnv: string | undefined) => {
    if (!modelEnv) return;
    const val = (roleModels[roleId] || '').trim();
    if (!val) {
      setError('Enter a model ID before saving.');
      return;
    }
    setError(null);
    setNotice(null);
    setBusyId(`model-${roleId}`);
    try {
      const res = await saveEnvConfig({ [modelEnv]: val });
      if (!res.success) {
        if (res.error_code === 'use_desktop_keychain') {
          setKeychainNotice(KEYCHAIN_EXTERNAL_NOTE);
          setError(KEYCHAIN_EXTERNAL_NOTE);
        } else {
          setError(res.error || 'Failed to save model setting.');
        }
        return;
      }
      const nextStatus = await getStatus();
      setStatus(nextStatus);
      setNotice(`Saved model for ${roleId}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save model.');
    } finally {
      setBusyId(null);
    }
  };

  const handleTestRole = async (roleId: string) => {
    setError(null);
    setBusyId(`test-${roleId}`);
    try {
      const res = await testRoleModel(roleId);
      setRoleTests((prev) => ({ ...prev, [roleId]: res }));
    } catch (err) {
      setRoleTests((prev) => ({
        ...prev,
        [roleId]: {
          success: false,
          role_id: roleId,
          error: err instanceof Error ? err.message : 'Test failed.',
        },
      }));
    } finally {
      setBusyId(null);
    }
  };

  const handleSaveKey = async () => {
    const trimmedVal = newKeyValue.trim();
    if (!trimmedVal) {
      setError('Enter a key value before saving.');
      return;
    }
    setError(null);
    setKeyNotice(null);
    setKeychainNotice(null);
    setBusyId('save-key');
    try {
      const res = await saveEnvConfig({ [newKeyName]: trimmedVal });
      if (!res.success) {
        if (res.error_code === 'use_desktop_keychain') {
          setNewKeyValue('');
          setKeychainNotice(KEYCHAIN_EXTERNAL_NOTE);
        } else {
          setError(res.error || 'Failed to save key.');
        }
        return;
      }
      setNewKeyValue('');
      setKeyNotice(`Saved ${newKeyName}.`);
      const [nextStatus, nextPool] = await Promise.all([getStatus(), getKeyPool()]);
      setStatus(nextStatus);
      setKeyPool(Array.isArray(nextPool) ? nextPool : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save key.');
    } finally {
      setBusyId(null);
    }
  };

  const handleSaveDesktopKey = async (name: string) => {
    const trimmedVal = (desktopKeyValues[name] || '').trim();
    if (!trimmedVal) {
      setError('Enter a key value before saving.');
      return;
    }
    setError(null);
    setKeychainNotice(null);
    setDesktopKeyValues((prev) => ({ ...prev, [name]: '' }));
    setKeyNotice('Saved. Restarting engine...');
    setBusyId(`save-key-${name}`);
    try {
      const res = await saveEnvConfig({ [name]: trimmedVal });
      if (!res.success) {
        setKeyNotice(null);
        if (res.error_code === 'use_desktop_keychain') {
          setKeychainNotice(KEYCHAIN_EXTERNAL_NOTE);
        } else {
          setError(res.error || 'Failed to save key.');
        }
        return;
      }
      const nextSecrets = await listSecrets();
      setDesktopSecrets(Array.isArray(nextSecrets) ? nextSecrets : []);
    } catch (err) {
      setKeyNotice(null);
      setError(err instanceof Error ? err.message : 'Failed to save key.');
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmRemoveKey = async () => {
    const target = confirmRemoveKey;
    if (!target) return;
    setConfirmRemoveKey(null);
    setError(null);
    setKeychainNotice(null);
    setKeyNotice('Removed. Restarting engine...');
    setBusyId(`remove-key-${target}`);
    try {
      await deleteSecret(target);
      const nextSecrets = await listSecrets();
      setDesktopSecrets(Array.isArray(nextSecrets) ? nextSecrets : []);
    } catch (err) {
      setKeyNotice(null);
      setError(err instanceof Error ? err.message : 'Failed to remove key.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRunSetupCheck = async () => {
    setError(null);
    setBusyId('setup-check');
    try {
      const res = await runSetupCheck();
      setSetupCheck(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup check failed.');
    } finally {
      setBusyId(null);
    }
  };

  const handleInstallBrowser = async () => {
    setError(null);
    setBusyId('install-browser');
    try {
      const res = await installBrowser();
      if (res.job) {
        setBrowserJob(res.job);
      }
      setNotice(res.message || 'Queued Chromium browser installation.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start browser install.');
    } finally {
      setBusyId(null);
    }
  };

  const handleImportLegacy = async () => {
    const path = legacyPath.trim();
    if (!path) {
      setError('Enter the path to an existing Magnm-Marketing folder.');
      return;
    }
    setError(null);
    setLegacyResult(null);
    setBusyId('import-legacy');
    try {
      const res = await importLegacyFolder(path);
      setLegacyResult(res);
      if (!res.success) {
        setError(res.error || 'Import failed.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed.');
    } finally {
      setBusyId(null);
    }
  };

  const matrix = status?.models_matrix || [];
  const catalogSuggestions = status?.nim_catalog_suggestions || [];
  const env = status?.env_status;
  const bakeoffRows = useMemo(() => deriveBakeoffRows(bakeoff), [bakeoff]);

  const roleKeyOptions = useMemo(
    () => matrix.map((r) => r.key_env).filter((k): k is string => Boolean(k)),
    [matrix],
  );

  const providerRows = desktopMode
    ? desktopSecrets.length > 0
      ? [
          {
            name: 'NVIDIA NIM',
            configured: desktopSecrets.some(
              (s) => s.name.startsWith('NVIDIA_NIM_API_KEY') && s.is_set,
            ),
          },
          {
            name: 'OpenAI',
            configured: desktopSecrets.some((s) => s.name === 'OPENAI_API_KEY' && s.is_set),
          },
          {
            name: 'Anthropic',
            configured: desktopSecrets.some((s) => s.name === 'ANTHROPIC_API_KEY' && s.is_set),
          },
          {
            name: 'Gemini',
            configured: desktopSecrets.some((s) => s.name === 'GEMINI_API_KEY' && s.is_set),
          },
          {
            name: 'Groq',
            configured: desktopSecrets.some((s) => s.name === 'GROQ_API_KEY' && s.is_set),
          },
        ]
      : []
    : env
      ? [
          { name: 'NVIDIA NIM', configured: Boolean(env.nvidia_nim_global) },
          { name: 'OpenAI', configured: Boolean(env.openai) },
          { name: 'Anthropic', configured: Boolean(env.anthropic) },
          { name: 'Gemini', configured: Boolean(env.gemini) },
          { name: 'Groq', configured: Boolean(env.groq) },
        ]
      : [];

  return (
    <div className="studio-stack">
      <SheetHeader
        page={10}
        total={10}
        subtitle="Configuration"
        title="Settings"
        lead="AI engines and models per role, key status and entry, setup diagnostics, legacy import, bake-off results, and appearance."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      <section className="studio-section" aria-labelledby="settings-models-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">01</span>
          <h2 id="settings-models-heading" className="studio-section-title">
            Engines and models
          </h2>
        </div>

        <Card>
          <div className="studio-stack-sm">
            <div className="studio-grid-2">
              <Field label="Default provider" htmlFor="settings-default-provider">
                <Select
                  id="settings-default-provider"
                  value={defaultProvider}
                  onChange={(e) => setDefaultProvider(e.target.value)}
                >
                  <option value="nvidia_nim">nvidia_nim</option>
                  <option value="auto">auto</option>
                  <option value="openai">openai</option>
                  <option value="anthropic">anthropic</option>
                  <option value="gemini">gemini</option>
                  <option value="ollama">ollama</option>
                </Select>
              </Field>
              <Field label="NVIDIA NIM base URL" htmlFor="settings-nim-url">
                <TextInput
                  id="settings-nim-url"
                  value={nimBaseUrl}
                  onChange={(e) => setNimBaseUrl(e.target.value)}
                />
              </Field>
            </div>
            <div className="studio-inline-form">
              <TextInput
                aria-label="Fallback model"
                placeholder="Fallback model ID"
                value={nimFallbackModel}
                onChange={(e) => setNimFallbackModel(e.target.value)}
              />
              <PillButton
                size="sm"
                disabled={busyId === 'save-gateway'}
                onClick={() => void handleSaveGateway()}
              >
                {busyId === 'save-gateway' ? 'Saving...' : 'Save gateway'}
              </PillButton>
            </div>
          </div>
        </Card>

        <datalist id="settings-nim-catalog">
          {catalogSuggestions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>

        {loading && matrix.length === 0 ? (
          <div className="mg-empty">Loading agent role models.</div>
        ) : matrix.length === 0 ? (
          <div className="mg-empty">No role models returned by the engine.</div>
        ) : (
          <div className="studio-stack-sm">
            {matrix.map((role) => {
              const testRes = roleTests[role.role_id];
              return (
                <Card key={role.role_id} outline>
                  <div className="studio-role-card">
                    <div className="studio-card-head">
                      <div>
                        <div className="mg-label">
                          {role.code || role.role_id}
                          {role.tier ? ` · ${role.tier}` : ''}
                        </div>
                        <div className="studio-card-title">{role.name || role.role_id}</div>
                      </div>
                      <StatusDot
                        state={role.has_key ? 'online' : 'off'}
                        label={role.has_key ? 'Key configured' : 'Key missing'}
                      />
                    </div>

                    <div className="studio-mono-wrap">
                      In use: {role.primary || role.model || 'Unset'}
                      {role.backup ? ` · Backup: ${role.backup}` : ''}
                    </div>

                    {role.warnings && role.warnings.length > 0 ? (
                      <div className="studio-hint">{role.warnings.join(' ')}</div>
                    ) : null}

                    <div className="studio-role-controls">
                      <TextInput
                        aria-label={`Model for ${role.role_id}`}
                        list="settings-nim-catalog"
                        value={roleModels[role.role_id] ?? role.model ?? ''}
                        onChange={(e) =>
                          setRoleModels((prev) => ({
                            ...prev,
                            [role.role_id]: e.target.value,
                          }))
                        }
                      />
                      {role.model_env ? (
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={busyId === `model-${role.role_id}`}
                          onClick={() => void handleSaveRoleModel(role.role_id, role.model_env)}
                        >
                          Save
                        </PillButton>
                      ) : null}
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={busyId === `test-${role.role_id}`}
                        onClick={() => void handleTestRole(role.role_id)}
                      >
                        {busyId === `test-${role.role_id}` ? 'Testing...' : 'Test'}
                      </PillButton>
                    </div>

                    {testRes ? (
                      <div className="studio-mono-wrap">
                        {testRes.success
                          ? `OK · ${testRes.latency_ms ?? 0}ms · ${testRes.model_used || testRes.primary || 'ready'}${testRes.fallback_used ? ' (fallback)' : ''}`
                          : `Failed: ${testRes.error || 'Endpoint check failed'}`}
                      </div>
                    ) : null}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="settings-keys-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">02</span>
          <h2 id="settings-keys-heading" className="studio-section-title">
            Keys
          </h2>
        </div>

        {keychainNotice ? (
          <div className="mg-notice" role="status">
            {keychainNotice}
          </div>
        ) : null}

        {providerRows.length > 0 ? (
          <Card>
            <div className="studio-stack-sm">
              <span className="mg-label">Provider status</span>
              <div className="studio-chip-row">
                {providerRows.map((p) => (
                  <span key={p.name} className="mg-chip">
                    {p.name}: {p.configured ? 'Configured' : 'Not configured'}
                  </span>
                ))}
              </div>
            </div>
          </Card>
        ) : null}

        {!desktopMode && keyPool.length === 0 ? (
          <div className="mg-empty">No NVIDIA NIM keys in pool yet.</div>
        ) : keyPool.length > 0 ? (
          <RowList aria-label="NVIDIA key pool health">
            {keyPool.map((entry) => {
              const poolStatus = mapPoolState(entry);
              return (
                <Row
                  key={entry.label}
                  title={entry.label}
                  description={
                    <span className="studio-mono">
                      Configured · {entry.requests_last_min} req/min · {entry.ok} ok ·{' '}
                      {entry.errors} errors
                    </span>
                  }
                  meta={<StatusDot state={poolStatus.state} label={poolStatus.label} />}
                />
              );
            })}
          </RowList>
        ) : null}

        {desktopMode ? (
          <Card outline>
            <div className="studio-stack-sm">
              <div className="mg-label">Desktop keychain</div>
              <p className="studio-hint">
                Keys are stored in the desktop keychain and never displayed again after saving.
                Saving or removing a key restarts the engine.
              </p>
              {keyNotice ? (
                <div className="mg-notice" role="status">
                  {keyNotice}
                </div>
              ) : null}
              {loading && desktopSecrets.length === 0 ? (
                <div className="mg-empty">Loading desktop keychain keys.</div>
              ) : desktopSecrets.length === 0 ? (
                <div className="mg-empty">No keychain keys reported.</div>
              ) : (
                <RowList aria-label="Desktop keychain keys">
                  {desktopSecrets.map((entry) => (
                    <Row
                      key={entry.name}
                      title={entry.name}
                      meta={
                        <StatusDot
                          state={entry.is_set ? 'online' : 'off'}
                          label={entry.is_set ? 'Configured' : 'Not configured'}
                        />
                      }
                    >
                      <div className="studio-role-controls">
                        <TextInput
                          type="password"
                          autoComplete="off"
                          name={`keychain-${entry.name}`}
                          aria-label={`Key value for ${entry.name}`}
                          placeholder={entry.is_set ? 'Replace key...' : 'Paste key...'}
                          value={desktopKeyValues[entry.name] ?? ''}
                          onChange={(e) =>
                            setDesktopKeyValues((prev) => ({
                              ...prev,
                              [entry.name]: e.target.value,
                            }))
                          }
                        />
                        <PillButton
                          size="sm"
                          disabled={busyId === `save-key-${entry.name}`}
                          onClick={() => void handleSaveDesktopKey(entry.name)}
                        >
                          Save
                        </PillButton>
                        {entry.is_set ? (
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busyId === `remove-key-${entry.name}`}
                            onClick={() => setConfirmRemoveKey(entry.name)}
                          >
                            Remove
                          </PillButton>
                        ) : null}
                      </div>
                    </Row>
                  ))}
                </RowList>
              )}
            </div>
          </Card>
        ) : (
          <Card outline>
            <div className="studio-stack-sm">
              <div className="mg-label">Add or replace a key</div>
              <p className="studio-hint">
                Keys are stored on this device and never displayed again after saving.
              </p>
              <div className="studio-grid-2">
                <Field label="Key slot" htmlFor="settings-key-slot">
                  <Select
                    id="settings-key-slot"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                  >
                    {NIM_KEY_OPTIONS.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                    {PROVIDER_KEY_OPTIONS.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                    {roleKeyOptions.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Key value" htmlFor="settings-key-value">
                  <TextInput
                    id="settings-key-value"
                    type="password"
                    autoComplete="off"
                    name="api-key-entry"
                    placeholder="Paste key..."
                    value={newKeyValue}
                    onChange={(e) => setNewKeyValue(e.target.value)}
                  />
                </Field>
              </div>
              <div className="studio-section-bar">
                <PillButton
                  size="sm"
                  disabled={busyId === 'save-key'}
                  onClick={() => void handleSaveKey()}
                >
                  {busyId === 'save-key' ? 'Saving...' : 'Save key'}
                </PillButton>
                {keyNotice ? <span className="studio-mono">{keyNotice}</span> : null}
              </div>
            </div>
          </Card>
        )}
      </section>

      <section className="studio-section" aria-labelledby="settings-setup-heading">
        <div className="studio-section-bar">
          <div className="studio-settings-head">
            <span className="studio-settings-num">03</span>
            <h2 id="settings-setup-heading" className="studio-section-title">
              Setup check
            </h2>
          </div>
          <div className="studio-btn-group">
            <PillButton
              variant="outline"
              size="sm"
              disabled={busyId === 'setup-check'}
              onClick={() => void handleRunSetupCheck()}
            >
              {busyId === 'setup-check' ? 'Checking...' : 'Run check'}
            </PillButton>
            <PillButton
              variant="outline"
              size="sm"
              disabled={busyId === 'install-browser' || browserJobActive}
              onClick={() => void handleInstallBrowser()}
            >
              {browserJobActive ? 'Installing browser...' : 'Install browser'}
            </PillButton>
          </div>
        </div>

        {browserJob ? (
          <Card>
            <div className="studio-section-bar">
              <span className="studio-mono">
                install_browser: {browserJob.message || browserJob.status}
              </span>
              <StatusDot
                state={
                  browserJob.status === 'done'
                    ? 'online'
                    : browserJob.status === 'failed'
                      ? 'error'
                      : 'working'
                }
                label={browserJob.status}
                live={browserJobActive}
              />
            </div>
          </Card>
        ) : null}

        {loading && !setupCheck ? (
          <div className="mg-empty">Running environment setup checks.</div>
        ) : !setupCheck || setupCheck.checks.length === 0 ? (
          <div className="mg-empty">No setup check results available.</div>
        ) : (
          <RowList aria-label="Setup checks">
            {setupCheck.checks.map((item) => (
              <Row
                key={item.id}
                title={item.label}
                description={
                  <div className="studio-stack-xs">
                    <span>{item.detail}</span>
                    {item.fix ? <span className="studio-hint">Fix: {item.fix}</span> : null}
                  </div>
                }
                meta={<StatusDot state={mapSetupState(item.status)} label={item.status} />}
              />
            ))}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="settings-import-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">04</span>
          <h2 id="settings-import-heading" className="studio-section-title">
            Import old folder
          </h2>
        </div>

        <Card>
          <div className="studio-stack-sm">
            <div className="studio-inline-form">
              <TextInput
                aria-label="Old Magnm-Marketing folder path"
                placeholder="/path/to/Magnm-Marketing"
                value={legacyPath}
                onChange={(e) => setLegacyPath(e.target.value)}
              />
              <PillButton
                size="sm"
                disabled={busyId === 'import-legacy'}
                onClick={() => void handleImportLegacy()}
              >
                {busyId === 'import-legacy' ? 'Importing...' : 'Import'}
              </PillButton>
            </div>
            <span className="studio-hint">
              Copies outputs, config, agents, and SQLite state without overwriting existing files.
            </span>
            {legacyResult?.success ? (
              <div className="studio-mono-wrap">
                Imported {legacyResult.files_copied ?? 0} files
                {legacyResult.db
                  ? ` · ${legacyResult.db.messages ?? 0} messages · ${legacyResult.db.assets ?? 0} assets · ${legacyResult.db.schedules ?? 0} schedules`
                  : ''}
              </div>
            ) : null}
          </div>
        </Card>
      </section>

      <section className="studio-section" aria-labelledby="settings-bakeoff-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">05</span>
          <h2 id="settings-bakeoff-heading" className="studio-section-title">
            Bake-off
          </h2>
        </div>

        {loading && !bakeoff ? (
          <div className="mg-empty">Loading latest bake-off results.</div>
        ) : !bakeoff || bakeoffRows.length === 0 ? (
          <div className="mg-empty">No bake-off results yet.</div>
        ) : (
          <div className="studio-stack-sm">
            <div className="studio-mono">
              Role: {bakeoff.role || 'writer'}
              {bakeoff.timestamp ? ` · Run: ${bakeoff.timestamp}` : ''}
            </div>
            <div className="studio-table-wrap">
              <table className="studio-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Briefs</th>
                    <th>Median latency</th>
                    <th>Warnings</th>
                    <th>Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {bakeoffRows.map((row) => (
                    <tr key={row.model}>
                      <td className="studio-mono-wrap">{row.model}</td>
                      <td className="mg-num">{row.count}</td>
                      <td className="mg-num">{row.median_latency_ms} ms</td>
                      <td className="mg-num">{row.auto_review_warns}</td>
                      <td className="mg-num">{row.errors}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="settings-appearance-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">06</span>
          <h2 id="settings-appearance-heading" className="studio-section-title">
            Appearance
          </h2>
        </div>

        <Card>
          <div className="studio-section-bar">
            <div>
              <div className="studio-card-title">Theme</div>
              <div className="studio-hint">
                Light is the default; dark flips the monochrome ink and paper palette.
              </div>
            </div>
            <ThemeToggle />
          </div>
        </Card>
      </section>

      <section className="studio-section" aria-labelledby="settings-about-heading">
        <div className="studio-settings-head">
          <span className="studio-settings-num">07</span>
          <h2 id="settings-about-heading" className="studio-section-title">
            About
          </h2>
        </div>

        <Card>
          <div className="studio-stack-sm">
            <div className="studio-facts">
              <div className="studio-fact">
                <span className="studio-fact-label">Engine version</span>
                <span className="studio-fact-value">{health?.version || 'Unknown'}</span>
              </div>
              <div className="studio-fact">
                <span className="studio-fact-label">Data folder</span>
                <span className="studio-mono-wrap">{health?.data_root || 'Unknown'}</span>
              </div>
            </div>
            <div>
              <a className="studio-inline-link" href="/legacy">
                Old dashboard
              </a>
            </div>
          </div>
        </Card>
      </section>

      <PitchBasePricesPanel />

      <Dialog
        open={Boolean(confirmRemoveKey)}
        onClose={() => setConfirmRemoveKey(null)}
        title="Remove key"
        subtitle="Desktop keychain"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmRemoveKey(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              disabled={Boolean(confirmRemoveKey && busyId === `remove-key-${confirmRemoveKey}`)}
              onClick={() => void handleConfirmRemoveKey()}
            >
              {confirmRemoveKey && busyId === `remove-key-${confirmRemoveKey}`
                ? 'Removing...'
                : 'Remove key'}
            </PillButton>
          </>
        }
      >
        {confirmRemoveKey ? (
          <p className="mg-sub">
            Remove <span className="studio-mono">{confirmRemoveKey}</span> from the desktop
            keychain? This restarts the engine.
          </p>
        ) : null}
      </Dialog>
    </div>
  );
}
