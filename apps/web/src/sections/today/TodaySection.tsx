import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Card,
  PillButton,
  Row,
  RowList,
  SheetHeader,
  StatusDot,
  TextInput,
  type StatusState,
} from '@magnm/design/react';
import {
  answerGate,
  answerSession,
  cancelJob,
  cancelSession,
  listContent,
  listPendingFacts,
  listSessions,
  listWorkflows,
} from '../../api/today';
import { listLearnings } from '../../api/learnings';
import { listReminders } from '../../api/reminders';
import { discardResultsProposal, listResultsProposals } from '../../api/results';
import { generateSummary, listSummaries } from '../../api/summaries';
import { AddResultsDialog } from '../../components/AddResultsDialog';
import { useStudioEvents } from '../../events';
import type {
  AgentSession,
  ContentItem,
  EngineStatusInfo,
  HealthInfo,
  JobRecord,
  LearningRecord,
  MetricProposal,
  ProductFact,
  ReminderRecord,
  WeeklySummaryRecord,
  WorkflowGate,
  WorkflowRun,
} from '../../types';
import { formatTimestamp } from '../../ui/format';
import { LearningsCard } from './LearningsCard';
import { RemindersCard } from './RemindersCard';
import { ResultsCard } from './ResultsCard';
import { WeeklySummaryCard } from './WeeklySummaryCard';

export interface TodaySectionProps {
  health: HealthInfo | null;
  status: EngineStatusInfo | null;
  jobs: JobRecord[];
  loading: boolean;
  error: string | null;
  focusParam?: string | null;
  onRefreshSummary?: () => void;
}

function mapJobState(jobStatus: string): StatusState {
  switch (jobStatus) {
    case 'running':
      return 'working';
    case 'queued':
      return 'idle';
    case 'done':
      return 'online';
    case 'failed':
      return 'error';
    default:
      return 'off';
  }
}

function formatJobLabel(job: JobRecord): string {
  if (job.status === 'running') {
    return `Running ${job.progress}%`;
  }
  switch (job.status) {
    case 'done':
      return 'Done';
    case 'failed':
      return 'Failed';
    case 'cancelled':
      return 'Cancelled';
    case 'queued':
      return 'Queued';
    default:
      return job.status;
  }
}

function formatProviderSummary(status: EngineStatusInfo | null): string {
  const env = status?.env_status;
  if (!env) return 'Unknown';
  const configured = Boolean(
    env.nvidia_nim_global || env.team_ai || env.openai || env.anthropic || env.gemini || env.groq,
  );
  return configured ? 'Yes' : 'No';
}

function formatRolesReady(status: EngineStatusInfo | null): string {
  const env = status?.env_status;
  if (!env || typeof env.nvidia_nim_roles_configured !== 'number') {
    return 'Unknown';
  }
  const total = Array.isArray(status?.models_matrix) ? status.models_matrix.length : 0;
  if (total > 0) {
    return `${env.nvidia_nim_roles_configured} of ${total}`;
  }
  return String(env.nvidia_nim_roles_configured);
}

function parseActivityLine(line: string): {
  ts: string;
  route: string;
  task: string;
  file: string;
} {
  const parts = line.split('|').map((p) => p.trim());
  return {
    ts: parts[0] || '',
    route: parts[1] || line,
    task: parts[2] || '',
    file: parts[3] || '',
  };
}

/** Today home screen: live jobs/sessions, pending approvals/facts/drafts, and recent activity. */
export function TodaySection({
  health,
  status,
  jobs,
  loading,
  error,
  focusParam,
  onRefreshSummary,
}: TodaySectionProps) {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowRun[]>([]);
  const [pendingFacts, setPendingFacts] = useState<ProductFact[]>([]);
  const [contentItems, setContentItems] = useState<ContentItem[]>([]);
  const [reminders, setReminders] = useState<ReminderRecord[]>([]);
  const [proposals, setProposals] = useState<MetricProposal[]>([]);
  const [summaries, setSummaries] = useState<WeeklySummaryRecord[]>([]);
  const [selectedSummaryId, setSelectedSummaryId] = useState<string | null>(null);
  const [generatingSummary, setGeneratingSummary] = useState(false);
  const [learnings, setLearnings] = useState<LearningRecord[]>([]);
  const [resultsDialog, setResultsDialog] = useState<{
    open: boolean;
    contentId?: string | null;
    platform?: string | null;
    proposal?: MetricProposal | null;
  }>({ open: false });
  const [auxLoading, setAuxLoading] = useState(true);
  const [auxError, setAuxError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [sessionAnswers, setSessionAnswers] = useState<Record<string, string>>({});
  const [gateFeedback, setGateFeedback] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!focusParam) return;
    if (focusParam.startsWith('summary/')) {
      const targetId = focusParam.slice('summary/'.length).trim();
      if (targetId) {
        setSelectedSummaryId(targetId);
      }
    }
  }, [focusParam]);

  const refreshReminders = useCallback(async () => {
    try {
      const next = await listReminders('open');
      setReminders(next);
    } catch {
      // Non-fatal during partial refresh
    }
  }, []);

  const refreshProposals = useCallback(async () => {
    try {
      const next = await listResultsProposals('pending');
      setProposals(next);
    } catch {
      // Non-fatal during partial refresh
    }
  }, []);

  const refreshSummaries = useCallback(async () => {
    try {
      const next = await listSummaries();
      setSummaries(next);
    } catch {
      // Non-fatal during partial refresh
    }
  }, []);

  const refreshLearnings = useCallback(async () => {
    try {
      const next = await listLearnings();
      setLearnings(next);
    } catch {
      // Non-fatal during partial refresh
    }
  }, []);

  const loadWorkItems = useCallback(async () => {
    setAuxError(null);
    const results = await Promise.allSettled([
      listSessions(20),
      listWorkflows(20),
      listPendingFacts(),
      listContent(50),
      listReminders('open'),
      listResultsProposals('pending'),
      listSummaries(),
      listLearnings(),
    ]);

    let firstErr: string | null = null;
    const [sessRes, wfRes, factsRes, contentRes, remRes, propRes, sumRes, lrnRes] = results;

    if (sessRes.status === 'fulfilled') {
      setSessions(Array.isArray(sessRes.value) ? sessRes.value : []);
    } else if (sessRes.reason instanceof Error) {
      firstErr = firstErr ?? sessRes.reason.message;
    }

    if (wfRes.status === 'fulfilled') {
      setWorkflows(Array.isArray(wfRes.value) ? wfRes.value : []);
    } else if (wfRes.reason instanceof Error) {
      firstErr = firstErr ?? wfRes.reason.message;
    }

    if (factsRes.status === 'fulfilled') {
      setPendingFacts(Array.isArray(factsRes.value) ? factsRes.value : []);
    } else if (factsRes.reason instanceof Error) {
      firstErr = firstErr ?? factsRes.reason.message;
    }

    if (contentRes.status === 'fulfilled') {
      setContentItems(Array.isArray(contentRes.value) ? contentRes.value : []);
    } else if (contentRes.reason instanceof Error) {
      firstErr = firstErr ?? contentRes.reason.message;
    }

    if (remRes.status === 'fulfilled') {
      setReminders(Array.isArray(remRes.value) ? remRes.value : []);
    } else if (remRes.reason instanceof Error) {
      firstErr = firstErr ?? remRes.reason.message;
    }

    if (propRes.status === 'fulfilled') {
      setProposals(Array.isArray(propRes.value) ? propRes.value : []);
    } else if (propRes.reason instanceof Error) {
      firstErr = firstErr ?? propRes.reason.message;
    }

    if (sumRes.status === 'fulfilled') {
      setSummaries(Array.isArray(sumRes.value) ? sumRes.value : []);
    }

    if (lrnRes.status === 'fulfilled') {
      setLearnings(Array.isArray(lrnRes.value) ? lrnRes.value : []);
    }

    setAuxError(firstErr);
    setAuxLoading(false);
  }, []);

  useEffect(() => {
    void loadWorkItems();
  }, [loadWorkItems]);

  useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (
          eventName === 'agent_step' ||
          eventName === 'agent_waiting' ||
          eventName === 'agent_done' ||
          eventName === 'agent_failed' ||
          eventName === 'agent_cancelled'
        ) {
          void listSessions(20)
            .then((next) => setSessions(Array.isArray(next) ? next : []))
            .catch(() => {});
        } else if (
          eventName === 'workflow_step' ||
          eventName === 'workflow_waiting' ||
          eventName === 'workflow_done' ||
          eventName === 'workflow_failed'
        ) {
          void listWorkflows(20)
            .then((next) => setWorkflows(Array.isArray(next) ? next : []))
            .catch(() => {});
          void listContent(50)
            .then((next) => setContentItems(Array.isArray(next) ? next : []))
            .catch(() => {});
        } else if (eventName === 'content_status' && data && typeof data === 'object') {
          void listContent(50)
            .then((next) => setContentItems(Array.isArray(next) ? next : []))
            .catch(() => {});
          void refreshReminders();
        } else if (eventName === 'notification' && data && typeof data === 'object') {
          const kind = (data as { kind?: string }).kind;
          if (kind === 'reminder') {
            void refreshReminders();
          } else if (kind === 'results_pending') {
            void refreshProposals();
          } else if (kind === 'weekly_summary') {
            void refreshSummaries();
            void refreshLearnings();
          } else if (kind === 'learning_proposed') {
            void refreshLearnings();
          }
        }
      },
      [refreshLearnings, refreshProposals, refreshReminders, refreshSummaries],
    ),
  );

  const activeJobs = useMemo(
    () => jobs.filter((job) => job.status === 'running' || job.status === 'queued'),
    [jobs],
  );

  const recentJobs = useMemo(
    () => jobs.filter((job) => job.status !== 'running' && job.status !== 'queued'),
    [jobs],
  );

  const activeSessions = useMemo(
    () => sessions.filter((s) => s.status === 'running' || s.status === 'waiting_user'),
    [sessions],
  );

  const openGates = useMemo(() => {
    const out: Array<{ run: WorkflowRun; gate: WorkflowGate }> = [];
    for (const run of workflows) {
      for (const gate of run.gates || []) {
        if (gate.status === 'pending') {
          out.push({ run, gate });
        }
      }
    }
    return out;
  }, [workflows]);

  const waitingDrafts = useMemo(
    () =>
      contentItems.filter(
        (item) =>
          item.status !== 'posted' && item.type !== 'review' && item.type !== 'plan',
      ),
    [contentItems],
  );

  const activityLines = status?.activity_log || [];

  const handleCancelJob = async (jobId: string) => {
    setActionError(null);
    setBusyId(jobId);
    try {
      await cancelJob(jobId);
      onRefreshSummary?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to cancel job.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelSession = async (sessionId: string) => {
    setActionError(null);
    setBusyId(sessionId);
    try {
      await cancelSession(sessionId);
      const next = await listSessions(20);
      setSessions(Array.isArray(next) ? next : []);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to cancel session.');
    } finally {
      setBusyId(null);
    }
  };

  const handleAnswerSession = async (sessionId: string, overrideAnswer?: string) => {
    const answer = (overrideAnswer ?? sessionAnswers[sessionId] ?? '').trim();
    if (!answer) {
      setActionError('Please enter an answer to resume the session.');
      return;
    }
    setActionError(null);
    setBusyId(sessionId);
    try {
      await answerSession(sessionId, answer);
      setSessionAnswers((prev) => ({ ...prev, [sessionId]: '' }));
      const next = await listSessions(20);
      setSessions(Array.isArray(next) ? next : []);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to resume session.');
    } finally {
      setBusyId(null);
    }
  };

  const handleAnswerGate = async (gateId: string, option: string) => {
    const fb = (gateFeedback[gateId] || '').trim();
    if ((option.toLowerCase() === 'revise' || option.toLowerCase() === 'change') && !fb) {
      setActionError(`Please add feedback notes before choosing ${option}.`);
      return;
    }
    setActionError(null);
    setBusyId(gateId);
    try {
      await answerGate(gateId, option, fb || null);
      setGateFeedback((prev) => ({ ...prev, [gateId]: '' }));
      const next = await listWorkflows(20);
      setWorkflows(Array.isArray(next) ? next : []);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to answer gate.');
    } finally {
      setBusyId(null);
    }
  };

  const handleGenerateSummary = async () => {
    setActionError(null);
    setGeneratingSummary(true);
    try {
      const res = await generateSummary();
      await Promise.all([refreshSummaries(), refreshLearnings()]);
      if (res?.summary?.id) {
        setSelectedSummaryId(res.summary.id);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to generate weekly summary.');
    } finally {
      setGeneratingSummary(false);
    }
  };

  const env = status?.env_status;
  const providerFlags: Array<{ name: string; ready: boolean }> = env
    ? [
        {
          name: !env.nvidia_nim_global && env.team_ai ? 'Team AI' : 'NVIDIA NIM',
          ready: Boolean(env.nvidia_nim_global || env.team_ai),
        },
        { name: 'OpenAI', ready: Boolean(env.openai) },
        { name: 'Anthropic', ready: Boolean(env.anthropic) },
        { name: 'Gemini', ready: Boolean(env.gemini) },
        { name: 'Groq', ready: Boolean(env.groq) },
      ]
    : [];

  const combinedError = error || auxError || actionError;

  return (
    <div className="studio-stack">
      <SheetHeader
        page={1}
        total={10}
        subtitle="Magnm Studio"
        title="Today"
        lead="What is happening now, items waiting for your sign-off, and recent activity."
      />

      {combinedError ? (
        <div className="mg-notice" role="alert">
          {combinedError}
        </div>
      ) : null}

      <section className="studio-section" aria-labelledby="today-now-heading">
        <h2 id="today-now-heading" className="studio-section-title">
          What is happening now
        </h2>
        {loading && auxLoading ? (
          <div className="mg-empty">Checking running jobs and agent sessions.</div>
        ) : activeJobs.length === 0 && activeSessions.length === 0 ? (
          <div className="mg-empty">Nothing is running right now.</div>
        ) : (
          <div className="studio-stack-sm">
            {activeJobs.length > 0 ? (
              <RowList aria-label="Running jobs">
                {activeJobs.map((job) => (
                  <Row
                    key={job.id}
                    title={job.name}
                    description={job.message || 'Queued'}
                    meta={
                      <div className="studio-row-actions">
                        <StatusDot state={mapJobState(job.status)} label={formatJobLabel(job)} live />
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={busyId === job.id}
                          onClick={() => void handleCancelJob(job.id)}
                        >
                          Cancel
                        </PillButton>
                      </div>
                    }
                  />
                ))}
              </RowList>
            ) : null}

            {activeSessions.map((sess) => {
              const isWaiting = sess.status === 'waiting_user';
              const q = sess.pending_question;
              return (
                <Card key={sess.id} outline>
                  <div className="studio-card-head">
                    <div>
                      <div className="mg-label">@{sess.agent_id} session</div>
                      <div className="studio-card-title">{sess.title || sess.id}</div>
                    </div>
                    <div className="studio-row-actions">
                      <StatusDot
                        state={isWaiting ? 'idle' : 'working'}
                        label={isWaiting ? 'Paused for you' : `Working (step ${sess.steps ?? 0})`}
                        live
                      />
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={busyId === sess.id}
                        onClick={() => void handleCancelSession(sess.id)}
                      >
                        Cancel
                      </PillButton>
                    </div>
                  </div>

                  {isWaiting && q ? (
                    <div className="studio-gate-box">
                      <p className="mg-sub">{q.question}</p>
                      {q.options && q.options.length > 0 ? (
                        <div className="studio-btn-group">
                          {q.options.map((opt) => (
                            <PillButton
                              key={opt}
                              variant="outline"
                              size="sm"
                              disabled={busyId === sess.id}
                              onClick={() => void handleAnswerSession(sess.id, opt)}
                            >
                              {opt}
                            </PillButton>
                          ))}
                        </div>
                      ) : null}
                      <div className="studio-inline-form">
                        <TextInput
                          aria-label={`Answer for ${sess.agent_id}`}
                          placeholder="Type your answer to resume..."
                          value={sessionAnswers[sess.id] ?? ''}
                          onChange={(e) =>
                            setSessionAnswers((prev) => ({ ...prev, [sess.id]: e.target.value }))
                          }
                        />
                        <PillButton
                          size="sm"
                          disabled={busyId === sess.id || !(sessionAnswers[sess.id] || '').trim()}
                          onClick={() => void handleAnswerSession(sess.id)}
                        >
                          Resume
                        </PillButton>
                      </div>
                    </div>
                  ) : null}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="today-waiting-heading">
        <h2 id="today-waiting-heading" className="studio-section-title">
          Waiting for you
        </h2>

        <div className="studio-summary-grid">
          <Card>
            <div className="studio-fact">
              <span className="studio-fact-label">Open workflow gates</span>
              <span className="mg-bignum">{openGates.length}</span>
            </div>
          </Card>
          <Card>
            <div className="studio-fact">
              <span className="studio-fact-label">Pending product facts</span>
              <span className="mg-bignum">{pendingFacts.length}</span>
              <a className="studio-inline-link" href="#/repos">
                Review in Repos
              </a>
            </div>
          </Card>
          <Card>
            <div className="studio-fact">
              <span className="studio-fact-label">Drafts waiting for review</span>
              <span className="mg-bignum">{waitingDrafts.length}</span>
            </div>
          </Card>
        </div>

        {openGates.length > 0 ? (
          <div className="studio-stack-sm">
            {openGates.map(({ run, gate }) => (
              <Card key={gate.id} outline>
                <div className="studio-card-head">
                  <div>
                    <div className="mg-label">
                      Workflow gate / {run.kind}
                    </div>
                    <div className="studio-card-title">{gate.question}</div>
                  </div>
                  <StatusDot state="idle" label="Approval needed" />
                </div>
                <div className="studio-gate-box">
                  <TextInput
                    aria-label={`Gate feedback for ${gate.id}`}
                    placeholder="Optional feedback (required for Revise or Change)"
                    value={gateFeedback[gate.id] ?? ''}
                    onChange={(e) =>
                      setGateFeedback((prev) => ({ ...prev, [gate.id]: e.target.value }))
                    }
                  />
                  <div className="studio-btn-group">
                    {(gate.options || []).map((opt, idx) => (
                      <PillButton
                        key={opt}
                        variant={idx === 0 ? 'primary' : 'outline'}
                        size="sm"
                        disabled={busyId === gate.id}
                        onClick={() => void handleAnswerGate(gate.id, opt)}
                      >
                        {opt}
                      </PillButton>
                    ))}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        ) : null}

        {auxLoading && waitingDrafts.length === 0 ? (
          <div className="mg-empty">Loading drafts awaiting review.</div>
        ) : waitingDrafts.length === 0 ? (
          <div className="mg-empty">No drafts waiting for review right now.</div>
        ) : (
          <RowList aria-label="Drafts waiting for review">
            {waitingDrafts.slice(0, 8).map((item) => (
              <a
                key={item.id}
                className="mg-row"
                href={`#/review/${encodeURIComponent(item.id)}`}
              >
                <div className="mg-row-main">
                  <span className="mg-row-title">{item.title || item.id}</span>
                  <span className="mg-row-desc">
                    {item.type}
                    {item.platform ? ` / ${item.platform}` : ''}
                    {item.created_by ? ` · by @${item.created_by}` : ''}
                  </span>
                </div>
                <span className="mg-row-meta">{item.status}</span>
              </a>
            ))}
          </RowList>
        )}

        <RemindersCard
          reminders={reminders}
          loading={auxLoading}
          onRefresh={refreshReminders}
          onAddResults={(cid) => {
            const matched = contentItems.find((item) => item.id === cid);
            setActionError(null);
            setResultsDialog({
              open: true,
              contentId: cid,
              platform: matched?.platform ?? null,
              proposal: null,
            });
          }}
          onError={(msg) => setActionError(msg)}
        />

        <ResultsCard
          proposals={proposals}
          contentItems={contentItems}
          loading={auxLoading}
          onOpenNew={() => {
            setActionError(null);
            setResultsDialog({ open: true, contentId: null, platform: null, proposal: null });
          }}
          onReviewProposal={(prop) => {
            setActionError(null);
            setResultsDialog({
              open: true,
              contentId: prop.content_id ?? null,
              platform: prop.platform ?? null,
              proposal: prop,
            });
          }}
          onDiscardProposal={(proposalId) => {
            setActionError(null);
            void discardResultsProposal(proposalId)
              .then(() => refreshProposals())
              .catch((err) => {
                setActionError(err instanceof Error ? err.message : 'Failed to discard results.');
              });
          }}
        />

        <WeeklySummaryCard
          summaries={summaries}
          loading={auxLoading}
          generating={generatingSummary}
          selectedSummaryId={selectedSummaryId}
          onSelectSummary={(id) => setSelectedSummaryId(id)}
          onGenerateNow={() => void handleGenerateSummary()}
          onAddResults={(cid, plat) => {
            const matched = contentItems.find((item) => item.id === cid);
            setActionError(null);
            setResultsDialog({
              open: true,
              contentId: cid,
              platform: plat ?? matched?.platform ?? null,
              proposal: null,
            });
          }}
        />

        <LearningsCard
          learnings={learnings}
          contentItems={contentItems}
          loading={auxLoading}
          onRefresh={refreshLearnings}
          onError={(msg) => setActionError(msg)}
        />
      </section>

      <section className="studio-section" aria-labelledby="today-engine-heading">
        <h2 id="today-engine-heading" className="studio-section-title">
          Engine
        </h2>
        <Card>
          <div className="studio-facts">
            <div className="studio-fact">
              <span className="studio-fact-label">Engine</span>
              <span className="studio-fact-value">
                {health ? (health.ok ? 'Online' : 'Unavailable') : loading ? 'Checking' : 'Unavailable'}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Providers configured</span>
              <span className="studio-fact-value">
                {loading && !status ? 'Checking' : formatProviderSummary(status)}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Roles ready</span>
              <span className="studio-fact-value">
                {loading && !status ? 'Checking' : formatRolesReady(status)}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Default provider</span>
              <span className="studio-fact-value">
                {env?.default_provider || (loading && !status ? 'Checking' : 'Not set')}
              </span>
            </div>
          </div>

          {providerFlags.length > 0 ? (
            <div className="studio-provider-list" aria-label="Configured providers">
              {providerFlags.map((provider) => (
                <span key={provider.name} className="mg-chip">
                  {provider.name}: {provider.ready ? 'yes' : 'no'}
                </span>
              ))}
            </div>
          ) : null}
        </Card>
      </section>

      <section className="studio-section" aria-labelledby="today-activity-heading">
        <h2 id="today-activity-heading" className="studio-section-title">
          Recent activity
        </h2>
        {loading && activityLines.length === 0 ? (
          <div className="mg-empty">Loading recent activity.</div>
        ) : activityLines.length === 0 ? (
          <div className="mg-empty">No recent activity logged yet.</div>
        ) : (
          <RowList aria-label="Recent activity">
            {activityLines.map((line, index) => {
              const parsed = parseActivityLine(line);
              return (
                <Row
                  key={`${parsed.ts}-${index}`}
                  title={parsed.task || parsed.route}
                  description={parsed.file ? `${parsed.route} · ${parsed.file}` : parsed.route}
                  meta={formatTimestamp(parsed.ts)}
                />
              );
            })}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="today-jobs-heading">
        <h2 id="today-jobs-heading" className="studio-section-title">
          Recent jobs
        </h2>
        {loading && jobs.length === 0 ? (
          <div className="mg-empty">Loading recent jobs.</div>
        ) : recentJobs.length === 0 ? (
          <div className="mg-empty">
            {activeJobs.length > 0
              ? 'No completed jobs yet.'
              : 'No recent jobs yet. Jobs you start will appear here.'}
          </div>
        ) : (
          <RowList aria-label="Recent jobs">
            {recentJobs.map((job) => (
              <Row
                key={job.id}
                title={job.name}
                description={job.error || job.message || 'Waiting in queue'}
                meta={<StatusDot state={mapJobState(job.status)} label={formatJobLabel(job)} />}
              />
            ))}
          </RowList>
        )}
      </section>

      <AddResultsDialog
        open={resultsDialog.open}
        onClose={() => setResultsDialog({ open: false })}
        initialContentId={resultsDialog.contentId}
        initialPlatform={resultsDialog.platform}
        initialProposal={resultsDialog.proposal}
        contentItems={contentItems}
        onSaved={() => {
          void refreshProposals();
          void refreshReminders();
        }}
        onDiscarded={() => {
          void refreshProposals();
        }}
      />
    </div>
  );
}
