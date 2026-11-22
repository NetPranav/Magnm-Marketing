import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Card,
  Checkbox,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  StatusDot,
  TextArea,
  type StatusState,
} from '@magnm/design/react';
import {
  approveCodingRun,
  attachCodingOutFile,
  cancelCodingRun,
  getCodingAgents,
  getCodingOutFileUrl,
  getCodingRun,
  getCodingRunLog,
  listCodingRuns,
  saveCodingOutFile,
  startCodingRun,
} from '../../api/coding';
import { listContent } from '../../api/library';
import { listRepos } from '../../api/repos';
import { useFallbackPoll, useStudioEvents } from '../../events';
import type {
  CodingAgentInfo,
  CodingRunRecord,
  ContentItem,
  RepoRecord,
} from '../../types';
import { formatTimestamp } from '../../ui/format';

const MODE_DESCRIPTIONS: Record<'read_only' | 'write_out', string> = {
  read_only:
    'Read-only: selected repos are copied read-only; the agent analyses code and the engine saves the final answer to out/answer.md.',
  write_out:
    'Write to out/: selected repos stay read-only; writes are confined strictly to ./out/**.',
};

const SAFE_SAVE_EXTENSIONS = new Set(['md', 'txt', 'json', 'csv', 'png', 'jpg', 'webp']);

const VENDOR_BY_ENGINE: Record<string, string> = {
  claude: 'Anthropic',
  codex: 'OpenAI',
  agy: 'Google',
  gemini: 'Google',
};

function isSafeSaveFile(filename: string): boolean {
  const dot = filename.lastIndexOf('.');
  if (dot <= 0) return false;
  return SAFE_SAVE_EXTENSIONS.has(filename.slice(dot + 1).toLowerCase());
}

function mapRunStatus(status: string): { state: StatusState; label: string } {
  switch (status) {
    case 'awaiting_approval':
      return { state: 'idle', label: 'Awaiting approval' };
    case 'running':
      return { state: 'working', label: 'Running' };
    case 'queued':
      return { state: 'idle', label: 'Queued' };
    case 'done':
      return { state: 'online', label: 'Done' };
    case 'failed':
      return { state: 'error', label: 'Failed' };
    case 'cancelled':
      return { state: 'off', label: 'Cancelled' };
    default:
      return { state: 'idle', label: status || 'Unknown' };
  }
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 1024) return `${Math.max(0, size || 0)} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

function mergeRunList(prev: CodingRunRecord[], incoming: CodingRunRecord): CodingRunRecord[] {
  const idx = prev.findIndex((r) => r.id === incoming.id);
  if (idx === -1) return [incoming, ...prev];
  return prev.map((r, i) => (i === idx ? { ...r, ...incoming } : r));
}

function confidentialConsentText(repoNames: string[], engineId: string): string {
  const vendor = VENDOR_BY_ENGINE[engineId] || engineId || 'the AI provider';
  const joined = repoNames.length > 0 ? repoNames.join(', ') : 'confidential repo';
  return `This sends ${joined}'s code to ${vendor}`;
}

/** Coding runs panel in the Team section: approval gate, start run, live text log, stop, results, and history. */
export function CodingRunsPanel() {
  const [clis, setClis] = useState<CodingAgentInfo[]>([]);
  const [repos, setRepos] = useState<RepoRecord[]>([]);
  const [runs, setRuns] = useState<CodingRunRecord[]>([]);
  const [contentItems, setContentItems] = useState<ContentItem[]>([]);

  const [engine, setEngine] = useState('');
  const [selectedRepoIds, setSelectedRepoIds] = useState<string[]>([]);
  const [confirmConfidentialForm, setConfirmConfidentialForm] = useState(false);
  const [confirmConfidentialByRun, setConfirmConfidentialByRun] = useState<Record<string, boolean>>({});
  const [mode, setMode] = useState<'read_only' | 'write_out'>('read_only');
  const [prompt, setPrompt] = useState('');

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedRunDetail, setSelectedRunDetail] = useState<CodingRunRecord | null>(null);
  const [logText, setLogText] = useState('');
  const [logOffset, setLogOffset] = useState(0);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [savingFile, setSavingFile] = useState<string | null>(null);
  const [savedFiles, setSavedFiles] = useState<Record<string, boolean>>({});
  const [attachTargetByFile, setAttachTargetByFile] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const logPreRef = useRef<HTMLPreElement | null>(null);

  const enabledClis = useMemo(() => clis.filter((c) => c.installed && c.enabled), [clis]);
  const repoById = useMemo(() => {
    const map: Record<string, RepoRecord> = {};
    for (const r of repos) {
      map[r.id] = r;
    }
    return map;
  }, [repos]);

  const selectedConfidentialRepos = useMemo(
    () =>
      selectedRepoIds
        .map((id) => repoById[id])
        .filter((r): r is RepoRecord => Boolean(r && r.confidential)),
    [selectedRepoIds, repoById],
  );

  const awaitingApprovalRuns = useMemo(
    () => runs.filter((r) => r.status === 'awaiting_approval'),
    [runs],
  );

  const loadInitial = useCallback(async () => {
    setError(null);
    const [agentsRes, runsRes, reposRes, contentRes] = await Promise.allSettled([
      getCodingAgents(false),
      listCodingRuns(50),
      listRepos(),
      listContent(50),
    ]);

    if (agentsRes.status === 'fulfilled') {
      const list = Array.isArray(agentsRes.value?.agents) ? agentsRes.value.agents : [];
      setClis(list);
      const firstEnabled = list.find((c) => c.installed && c.enabled);
      setEngine((prev) => prev || firstEnabled?.id || '');
    }

    if (runsRes.status === 'fulfilled') {
      const list = Array.isArray(runsRes.value?.runs) ? runsRes.value.runs : [];
      setRuns(list);
      if (list.length > 0) {
        setSelectedRunId((prev) => prev ?? list[0].id);
      }
    } else if (runsRes.reason instanceof Error) {
      setError(runsRes.reason.message);
    }

    if (reposRes.status === 'fulfilled') {
      setRepos(Array.isArray(reposRes.value) ? reposRes.value : []);
    }

    if (contentRes.status === 'fulfilled') {
      setContentItems(Array.isArray(contentRes.value) ? contentRes.value : []);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  const selectRun = useCallback(async (runId: string) => {
    setSelectedRunId(runId);
    setError(null);
    try {
      const [detail, logRes] = await Promise.all([
        getCodingRun(runId),
        getCodingRunLog(runId, 0),
      ]);
      setSelectedRunDetail(detail);
      setRuns((prev) => mergeRunList(prev, detail));
      setLogText(logRes?.content || detail?.log_tail || '');
      setLogOffset(Number(logRes?.next_offset ?? 0));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load coding run.');
    }
  }, []);

  useEffect(() => {
    if (selectedRunId && (!selectedRunDetail || selectedRunDetail.id !== selectedRunId)) {
      void selectRun(selectedRunId);
    }
  }, [selectedRunId, selectedRunDetail, selectRun]);

  useEffect(() => {
    if (logPreRef.current) {
      logPreRef.current.scrollTop = logPreRef.current.scrollHeight;
    }
  }, [logText]);

  const activeRun = useMemo(
    () => runs.find((r) => r.id === selectedRunId) ?? selectedRunDetail ?? null,
    [runs, selectedRunId, selectedRunDetail],
  );

  const isSelectedActive = Boolean(
    activeRun && (activeRun.status === 'queued' || activeRun.status === 'running'),
  );

  const eventsState = useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (eventName === 'coding_run.status' && data && typeof data === 'object') {
          const incoming = data as CodingRunRecord;
          if (!incoming.id) return;
          if (incoming.confidential) {
            void getCodingRun(incoming.id)
              .then((full) => {
                setRuns((prev) => mergeRunList(prev, full));
                if (full.id === selectedRunId) {
                  setSelectedRunDetail(full);
                }
              })
              .catch(() => {});
            return;
          }
          setRuns((prev) => mergeRunList(prev, incoming));
          if (incoming.id === selectedRunId) {
            setSelectedRunDetail((prev) => ({ ...(prev || {}), ...incoming }));
            if (
              incoming.status === 'done' ||
              incoming.status === 'failed' ||
              incoming.status === 'cancelled'
            ) {
              void getCodingRun(incoming.id)
                .then((full) => {
                  setSelectedRunDetail(full);
                  setRuns((prev) => mergeRunList(prev, full));
                })
                .catch(() => {});
            }
          }
          return;
        }

        if (eventName === 'coding_run.log' && data && typeof data === 'object') {
          const payload = data as {
            run_id?: string;
            lines?: string[];
            offset?: number;
            confidential?: boolean;
          };
          if (payload.run_id && payload.run_id === selectedRunId) {
            if (payload.confidential) {
              void getCodingRunLog(selectedRunId, logOffset)
                .then((logRes) => {
                  if (logRes?.content) {
                    setLogText((prev) => (prev ? `${prev}${logRes.content}` : logRes.content));
                  }
                  if (typeof logRes?.next_offset === 'number') {
                    setLogOffset(logRes.next_offset);
                  }
                })
                .catch(() => {});
              return;
            }
            if (Array.isArray(payload.lines)) {
              const chunk = payload.lines.join('\n');
              if (chunk) {
                setLogText((prev) => (prev ? `${prev}\n${chunk}` : chunk));
              }
              if (typeof payload.offset === 'number') {
                setLogOffset(payload.offset);
              }
            }
          }
        }
      },
      [selectedRunId, logOffset],
    ),
  );

  useFallbackPoll(
    useCallback(() => {
      if (!selectedRunId) return;
      void Promise.all([getCodingRun(selectedRunId), getCodingRunLog(selectedRunId, logOffset)])
        .then(([detail, logRes]) => {
          setSelectedRunDetail(detail);
          setRuns((prev) => mergeRunList(prev, detail));
          if (logRes?.content) {
            setLogText((prev) => (prev ? `${prev}${logRes.content}` : logRes.content));
          }
          if (typeof logRes?.next_offset === 'number') {
            setLogOffset(logRes.next_offset);
          }
        })
        .catch(() => {});
    }, [selectedRunId, logOffset]),
    isSelectedActive,
    eventsState,
    1500,
  );

  const toggleRepo = (repoId: string) => {
    setSelectedRepoIds((prev) => {
      const next = prev.includes(repoId) ? prev.filter((id) => id !== repoId) : [...prev, repoId];
      if (!next.some((id) => repoById[id]?.confidential)) {
        setConfirmConfidentialForm(false);
      }
      return next;
    });
  };

  const handleStart = async () => {
    setError(null);
    setNotice(null);
    if (!engine) {
      setError('No confined coding CLI is enabled. Check Settings > Coding agents.');
      return;
    }
    if (!prompt.trim()) {
      setError('Enter a prompt before starting a run.');
      return;
    }
    if (selectedConfidentialRepos.length > 0 && !confirmConfidentialForm) {
      setError(
        confidentialConsentText(
          selectedConfidentialRepos.map((r) => r.name || r.id),
          engine,
        ),
      );
      return;
    }

    setSubmitting(true);
    try {
      const res = await startCodingRun({
        engine,
        prompt: prompt.trim(),
        repo_ids: selectedRepoIds,
        mode,
        confirm_confidential: selectedConfidentialRepos.length > 0 ? confirmConfidentialForm : false,
      });
      const created = res.run;
      setPrompt('');
      setConfirmConfidentialForm(false);
      setRuns((prev) => mergeRunList(prev, created));
      setSelectedRunId(created.id);
      setSelectedRunDetail(created);
      setLogText('');
      setLogOffset(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start coding run.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleApproveRun = async (run: CodingRunRecord) => {
    const runConfRepos = (run.repo_ids || [])
      .map((id) => repoById[id])
      .filter((r): r is RepoRecord => Boolean(r && r.confidential));
    const needsConfidentialConsent = Boolean(run.confidential) || runConfRepos.length > 0;
    const confirmed = Boolean(confirmConfidentialByRun[run.id]);

    if (needsConfidentialConsent && !confirmed) {
      setError(
        confidentialConsentText(
          runConfRepos.map((r) => r.name || r.id),
          run.engine,
        ),
      );
      return;
    }

    setApprovingId(run.id);
    setError(null);
    setNotice(null);
    try {
      const res = await approveCodingRun(run.id, {
        approval_hash: run.approval_hash ?? undefined,
        confirm_confidential: needsConfidentialConsent ? confirmed : false,
      });
      const updated = res.run;
      setRuns((prev) => mergeRunList(prev, updated));
      setSelectedRunId(updated.id);
      setSelectedRunDetail(updated);
      setLogText('');
      setLogOffset(0);
      setNotice(`Started coding run ${updated.id}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve coding run.');
    } finally {
      setApprovingId(null);
    }
  };

  const handleStop = async (targetRunId?: string) => {
    const id = targetRunId || activeRun?.id;
    if (!id) return;
    setStopping(true);
    setError(null);
    try {
      const res = await cancelCodingRun(id);
      if (res?.run) {
        if (res.run.id === selectedRunId) {
          setSelectedRunDetail(res.run);
        }
        setRuns((prev) => mergeRunList(prev, res.run));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop coding run.');
    } finally {
      setStopping(false);
    }
  };

  const handleSaveToLibrary = async (runId: string, filename: string) => {
    setSavingFile(filename);
    setError(null);
    setNotice(null);
    try {
      await saveCodingOutFile(runId, filename);
      setSavedFiles((prev) => ({ ...prev, [`${runId}:${filename}`]: true }));
      setNotice(`Saved ${filename} to Library.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save output file.');
    } finally {
      setSavingFile(null);
    }
  };

  const handleAttachToContent = async (runId: string, filename: string) => {
    const contentId = attachTargetByFile[`${runId}:${filename}`];
    if (!contentId) return;
    setSavingFile(filename);
    setError(null);
    setNotice(null);
    try {
      await attachCodingOutFile(runId, filename, contentId);
      setNotice(`Attached ${filename} to ${contentId}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to attach output file.');
    } finally {
      setSavingFile(null);
    }
  };

  return (
    <div className="studio-stack">
      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      {awaitingApprovalRuns.length > 0 ? (
        <section className="studio-section" aria-labelledby="coding-approval-heading">
          <div className="studio-section-bar">
            <h3 id="coding-approval-heading" className="studio-section-title">
              Approval required ({awaitingApprovalRuns.length})
            </h3>
          </div>
          <div className="studio-stack-sm">
            {awaitingApprovalRuns.map((pending) => {
              const confRepos = (pending.repo_ids || [])
                .map((id) => repoById[id])
                .filter((r): r is RepoRecord => Boolean(r && r.confidential));
              const needsConf = Boolean(pending.confidential) || confRepos.length > 0;
              const consentLabel = confidentialConsentText(
                confRepos.map((r) => r.name || r.id),
                pending.engine,
              );
              const repoNames = (pending.repo_ids || []).length
                ? (pending.repo_ids || []).map((id) => repoById[id]?.name || id).join(', ')
                : 'None';

              return (
                <Card key={pending.id} selected>
                  <div className="studio-stack-sm">
                    <div className="studio-card-head">
                      <div>
                        <div className="mg-label">
                          Requested{pending.requested_by ? ` by @${pending.requested_by}` : ''}
                        </div>
                        <div className="studio-card-title">Review before starting</div>
                      </div>
                      <StatusDot state="idle" label="Awaiting approval" />
                    </div>

                    <div className="studio-mono">
                      Engine: {pending.engine} · Mode: {pending.mode} · Repos: {repoNames}
                    </div>

                    <div className="studio-stack-xs">
                      <span className="mg-label">Complete prompt</span>
                      <pre className="studio-coding-summary-text">{pending.prompt}</pre>
                    </div>

                    {needsConf ? (
                      <Checkbox
                        label={consentLabel}
                        checked={Boolean(confirmConfidentialByRun[pending.id])}
                        onChange={(e) =>
                          setConfirmConfidentialByRun((prev) => ({
                            ...prev,
                            [pending.id]: e.target.checked,
                          }))
                        }
                      />
                    ) : null}

                    <div className="studio-row-actions">
                      <PillButton
                        disabled={
                          approvingId === pending.id ||
                          (needsConf && !confirmConfidentialByRun[pending.id])
                        }
                        onClick={() => void handleApproveRun(pending)}
                      >
                        {approvingId === pending.id ? 'Starting...' : 'Start'}
                      </PillButton>
                      <PillButton
                        variant="outline"
                        disabled={stopping || approvingId === pending.id}
                        onClick={() => void handleStop(pending.id)}
                      >
                        Cancel
                      </PillButton>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </section>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          <div className="studio-card-title">Start coding run</div>

          <div className="studio-grid-2">
            <Field label="Coding engine" htmlFor="coding-run-engine">
              <Select
                id="coding-run-engine"
                value={engine}
                onChange={(e) => setEngine(e.target.value)}
              >
                {enabledClis.length === 0 ? (
                  <option value="">No confined coding CLI enabled</option>
                ) : null}
                {enabledClis.map((cli) => (
                  <option key={cli.id} value={cli.id}>
                    {cli.name} ({cli.command})
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Permission mode"
              htmlFor="coding-run-mode"
              hint={MODE_DESCRIPTIONS[mode]}
            >
              <Select
                id="coding-run-mode"
                value={mode}
                onChange={(e) =>
                  setMode(e.target.value === 'write_out' ? 'write_out' : 'read_only')
                }
              >
                <option value="read_only">read_only — Analyse &amp; save to out/answer.md</option>
                <option value="write_out">write_out — Confined writes inside out/**</option>
              </Select>
            </Field>
          </div>

          <div className="studio-stack-xs">
            <span className="mg-label">Repositories (excluded by default; copied read-only)</span>
            {repos.length === 0 ? (
              <span className="studio-hint">No connected repositories yet.</span>
            ) : (
              <div className="studio-repo-checks">
                {repos.map((repo) => {
                  const hasLocal = Boolean(repo.local_path);
                  const confSuffix = repo.confidential ? ' [Confidential]' : '';
                  return (
                    <Checkbox
                      key={repo.id}
                      label={
                        hasLocal
                          ? `${repo.name} (${repo.id})${confSuffix}`
                          : `${repo.name} (no local path)${confSuffix}`
                      }
                      disabled={!hasLocal}
                      checked={selectedRepoIds.includes(repo.id)}
                      onChange={() => toggleRepo(repo.id)}
                    />
                  );
                })}
              </div>
            )}
          </div>

          {selectedConfidentialRepos.length > 0 ? (
            <Checkbox
              label={confidentialConsentText(
                selectedConfidentialRepos.map((r) => r.name || r.id),
                engine || 'claude',
              )}
              checked={confirmConfidentialForm}
              onChange={(e) => setConfirmConfidentialForm(e.target.checked)}
            />
          ) : null}

          <Field label="Prompt" htmlFor="coding-run-prompt">
            <TextArea
              id="coding-run-prompt"
              rows={3}
              placeholder="Describe the repo analysis, blog technical breakdown, or output to write in out/..."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </Field>

          <div className="studio-section-bar">
            <span className="studio-hint">
              Runs in an isolated workspace outside Magnm data and git trees with read-only repo
              copies and a writable out/ folder.
            </span>
            <PillButton
              disabled={
                submitting ||
                (selectedConfidentialRepos.length > 0 && !confirmConfidentialForm)
              }
              onClick={() => void handleStart()}
            >
              {submitting ? 'Starting...' : 'Start run'}
            </PillButton>
          </div>
        </div>
      </Card>

      {activeRun ? (
        <Card outline>
          <div className="studio-stack-sm">
            <div className="studio-card-head">
              <div>
                <div className="mg-label">
                  {activeRun.engine} · {activeRun.mode}
                  {activeRun.confidential ? ' · Confidential' : ''}
                </div>
                <div className="studio-card-title">{activeRun.prompt}</div>
              </div>
              <div className="studio-row-actions">
                {(() => {
                  const st = mapRunStatus(activeRun.status);
                  return <StatusDot state={st.state} label={st.label} live={isSelectedActive} />;
                })()}
                {isSelectedActive || activeRun.status === 'awaiting_approval' ? (
                  <PillButton
                    variant="danger"
                    size="sm"
                    disabled={stopping}
                    onClick={() => void handleStop()}
                  >
                    {stopping ? 'Stopping...' : 'Stop'}
                  </PillButton>
                ) : null}
              </div>
            </div>

            {activeRun.error ? <div className="studio-hint">Error: {activeRun.error}</div> : null}

            <div className="studio-stack-xs">
              <span className="mg-label">Live log</span>
              <pre ref={logPreRef} className="studio-coding-log" aria-label="Coding run log">
                {logText || (isSelectedActive ? 'Waiting for output...' : 'No log output.')}
              </pre>
            </div>

            {activeRun.summary ? (
              <div className="studio-stack-xs studio-coding-summary">
                <span className="mg-label">Summary</span>
                <pre className="studio-coding-summary-text">{activeRun.summary}</pre>
              </div>
            ) : null}

            {activeRun.out_files && activeRun.out_files.length > 0 ? (
              <div className="studio-stack-xs">
                <span className="mg-label">Output files (out/)</span>
                <RowList aria-label="Coding run output files">
                  {activeRun.out_files.map((file) => {
                    const fileKey = `${activeRun.id}:${file.name}`;
                    const saved = Boolean(savedFiles[fileKey]);
                    const safeForLibrary = isSafeSaveFile(file.name);
                    return (
                      <Row
                        key={file.name}
                        title={file.name}
                        description={
                          safeForLibrary
                            ? formatBytes(file.size)
                            : `${formatBytes(file.size)} · Download only (only md, txt, json, csv, png, jpg, webp can be saved to Library)`
                        }
                      >
                        <div className="studio-row-actions">
                          <a
                            className="mg-btn mg-btn-outline mg-btn-sm"
                            href={getCodingOutFileUrl(activeRun.id, file.name)}
                            download
                          >
                            Download
                          </a>
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={!safeForLibrary || savingFile === file.name}
                            onClick={() => void handleSaveToLibrary(activeRun.id, file.name)}
                          >
                            {saved ? 'Saved to Library' : 'Save to Library'}
                          </PillButton>
                          {safeForLibrary && contentItems.length > 0 ? (
                            <>
                              <Select
                                aria-label={`Attach ${file.name} to content`}
                                value={attachTargetByFile[fileKey] || ''}
                                onChange={(e) =>
                                  setAttachTargetByFile((prev) => ({
                                    ...prev,
                                    [fileKey]: e.target.value,
                                  }))
                                }
                              >
                                <option value="">Attach to content...</option>
                                {contentItems.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.title}
                                  </option>
                                ))}
                              </Select>
                              <PillButton
                                variant="quiet"
                                size="sm"
                                disabled={!attachTargetByFile[fileKey] || savingFile === file.name}
                                onClick={() => void handleAttachToContent(activeRun.id, file.name)}
                              >
                                Attach
                              </PillButton>
                            </>
                          ) : null}
                        </div>
                      </Row>
                    );
                  })}
                </RowList>
              </div>
            ) : null}
          </div>
        </Card>
      ) : null}

      <section className="studio-section" aria-labelledby="coding-run-history-heading">
        <div className="studio-section-bar">
          <h3 id="coding-run-history-heading" className="studio-section-title">
            Run history
          </h3>
        </div>

        {loading && runs.length === 0 ? (
          <div className="mg-empty">Loading coding runs.</div>
        ) : runs.length === 0 ? (
          <div className="mg-empty">No coding runs yet.</div>
        ) : (
          <RowList aria-label="Coding run history">
            {runs.map((item) => {
              const st = mapRunStatus(item.status);
              return (
                <Row
                  key={item.id}
                  selected={item.id === activeRun?.id}
                  onClick={() => void selectRun(item.id)}
                  title={item.prompt}
                  description={
                    <span className="studio-mono">
                      {item.engine} · {item.mode} · {formatTimestamp(item.created_at)}
                    </span>
                  }
                  meta={<StatusDot state={st.state} label={st.label} />}
                />
              );
            })}
          </RowList>
        )}
      </section>
    </div>
  );
}
