import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Card,
  Checkbox,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  SheetHeader,
  StatusDot,
  TextInput,
} from '@magnm/design/react';
import {
  answerSession,
  askResearcher,
  cancelSession,
  createWatchTopic,
  deleteWatchTopic,
  getEngineStatus,
  listResearchItems,
  listSessions,
  listWatchTopics,
  startMarketScan,
  startWatchTopicsScan,
  toggleWatchTopic,
  type BrowserFrameEvent,
  type ResearchItem,
  type WatchTopic,
} from '../../api/research';
import {
  createSocialRun,
  getSocialRun,
  getSocialStatus,
  listSocialRuns,
} from '../../api/social';
import { postMessage } from '../../api/team';
import { SocialRunCard } from '../../components/SocialRunCard';
import { useStudioEvents } from '../../events';
import type {
  AgentSession,
  JobRecord,
  ResearchFindingItem,
  SocialPlatform,
  SocialRecipe,
  SocialRunRecord,
  SocialStatusInfo,
} from '../../types';
import { extractDomain, extractUrls, formatTimestamp, isSafeHttpUrl } from '../../ui/format';
import { SafeMarkdown } from '../../ui/SafeMarkdown';

const SOURCE_OPTIONS = ['hn', 'reddit', 'github', 'devto', 'web'] as const;

interface CombinedRadarItem {
  key: string;
  title: string;
  url: string;
  domain: string;
  source: string;
  snippet: string;
  topicId: string | null;
  topicLabel: string;
  score: number | null;
  foundAt: string;
}

interface LiveFrameState extends BrowserFrameEvent {
  version: number;
  live: boolean;
}

/** Research section: ask Researcher, live browser frame, radar items with filters, and watch topics. */
export function ResearchSection() {
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answerText, setAnswerText] = useState<string | null>(null);
  const [answerSessionId, setAnswerSessionId] = useState<string | null>(null);
  const [liveStep, setLiveStep] = useState<{
    sessionId: string;
    step: number;
    toolNames: string[];
    preview: string;
  } | null>(null);

  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [waitingAnswer, setWaitingAnswer] = useState('');
  const [liveFrame, setLiveFrame] = useState<LiveFrameState | null>(null);
  const [activeScanJob, setActiveScanJob] = useState<JobRecord | null>(null);

  const [researchItems, setResearchItems] = useState<ResearchItem[]>([]);
  const [radarFindings, setRadarFindings] = useState<ResearchFindingItem[]>([]);
  const [watchTopics, setWatchTopics] = useState<WatchTopic[]>([]);

  const [sourceFilter, setSourceFilter] = useState('all');
  const [topicFilter, setTopicFilter] = useState('all');

  const [newQuery, setNewQuery] = useState('');
  const [newSources, setNewSources] = useState<string[]>(['hn', 'reddit', 'github', 'devto']);
  const [newCadence, setNewCadence] = useState('24');
  const [newDays, setNewDays] = useState('7');

  const [socialStatus, setSocialStatus] = useState<SocialStatusInfo | null>(null);
  const [socialRuns, setSocialRuns] = useState<SocialRunRecord[]>([]);
  const [socialPlatform, setSocialPlatform] = useState<SocialPlatform>('x');
  const [socialRecipe, setSocialRecipe] = useState<SocialRecipe>('search');
  const [socialArgument, setSocialArgument] = useState('');
  const [socialSubmitting, setSocialSubmitting] = useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const upsertSocialRun = useCallback((incoming: SocialRunRecord) => {
    setSocialRuns((prev) => {
      const idx = prev.findIndex((r) => r.id === incoming.id);
      if (idx === -1) return [incoming, ...prev];
      return prev.map((r, i) => (i === idx ? { ...r, ...incoming } : r));
    });
  }, []);

  const loadAll = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      listResearchItems(100),
      listWatchTopics(),
      getEngineStatus(),
      listSessions(10),
      getSocialStatus(),
      listSocialRuns(50),
    ]);

    let firstErr: string | null = null;
    const [itemsRes, topicsRes, statusRes, sessRes, socStatusRes, socRunsRes] = results;

    if (itemsRes.status === 'fulfilled') {
      setResearchItems(Array.isArray(itemsRes.value) ? itemsRes.value : []);
    } else if (itemsRes.reason instanceof Error) {
      firstErr = firstErr ?? itemsRes.reason.message;
    }

    if (topicsRes.status === 'fulfilled') {
      setWatchTopics(Array.isArray(topicsRes.value) ? topicsRes.value : []);
    } else if (topicsRes.reason instanceof Error) {
      firstErr = firstErr ?? topicsRes.reason.message;
    }

    if (statusRes.status === 'fulfilled') {
      const findings = statusRes.value?.research_findings?.items;
      setRadarFindings(Array.isArray(findings) ? findings : []);
    } else if (statusRes.reason instanceof Error) {
      firstErr = firstErr ?? statusRes.reason.message;
    }

    if (sessRes.status === 'fulfilled') {
      setSessions(Array.isArray(sessRes.value) ? sessRes.value : []);
    }

    if (socStatusRes.status === 'fulfilled') {
      setSocialStatus(socStatusRes.value);
    }

    if (socRunsRes.status === 'fulfilled') {
      setSocialRuns(Array.isArray(socRunsRes.value) ? socRunsRes.value : []);
    }

    setError(firstErr);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (eventName === 'browser_frame' && data && typeof data === 'object') {
          const frame = data as BrowserFrameEvent;
          if (frame.screenshot_path) {
            setLiveFrame({
              session_id: frame.session_id,
              url: frame.url,
              screenshot_path: frame.screenshot_path,
              version: Date.now(),
              live: true,
            });
          }
          return;
        }

        if (eventName === 'agent_step' && data && typeof data === 'object') {
          const ev = data as {
            session_id?: string;
            agent_id?: string;
            step?: number;
            tool_names?: string[];
            text_preview?: string;
          };
          if (ev.agent_id === 'researcher') {
            setLiveStep({
              sessionId: ev.session_id || '',
              step: ev.step ?? 1,
              toolNames: Array.isArray(ev.tool_names) ? ev.tool_names : [],
              preview: ev.text_preview || '',
            });
          }
          return;
        }

        if (
          eventName === 'agent_waiting' ||
          eventName === 'agent_done' ||
          eventName === 'agent_failed' ||
          eventName === 'agent_cancelled'
        ) {
          const ev = (data && typeof data === 'object' ? data : {}) as {
            session_id?: string;
            agent_id?: string;
          };
          if (ev.agent_id === 'researcher' && eventName !== 'agent_waiting') {
            setLiveStep(null);
            setLiveFrame((prev) =>
              prev && (!ev.session_id || prev.session_id === ev.session_id)
                ? { ...prev, live: false }
                : prev,
            );
          }
          void listSessions(10)
            .then((next) => setSessions(Array.isArray(next) ? next : []))
            .catch(() => {});
          return;
        }

        if (eventName === 'job' && data && typeof data === 'object') {
          const job = data as JobRecord;
          if (job.name === 'market_scan' || job.name === 'watch_topics') {
            setActiveScanJob(job);
            if (job.status === 'done') {
              void loadAll();
            }
          }
          return;
        }

        if (eventName === 'research_digest') {
          void loadAll();
          return;
        }

        if (eventName === 'social_run.status' && data && typeof data === 'object') {
          const ev = data as Partial<SocialRunRecord>;
          if (!ev.id) return;
          void getSocialRun(ev.id)
            .then((full) => upsertSocialRun(full))
            .catch(() => {});
        }
      },
      [loadAll, upsertSocialRun],
    ),
  );

  const topicMap = useMemo(() => {
    const map = new Map<string, WatchTopic>();
    for (const topic of watchTopics) {
      map.set(topic.id, topic);
    }
    return map;
  }, [watchTopics]);

  const combinedItems = useMemo<CombinedRadarItem[]>(() => {
    const seenUrls = new Set<string>();
    const merged: CombinedRadarItem[] = [];

    for (const item of researchItems) {
      if (!isSafeHttpUrl(item.url)) continue;
      seenUrls.add(item.url);
      const topic = item.topic_id ? topicMap.get(item.topic_id) : undefined;
      merged.push({
        key: item.id,
        title: item.title || item.url,
        url: item.url,
        domain: extractDomain(item.url),
        source: item.source || 'web',
        snippet: item.snippet || '',
        topicId: item.topic_id ?? null,
        topicLabel: topic?.query || item.topic_id || 'Watch topic',
        score: typeof item.score === 'number' ? item.score : null,
        foundAt: item.found_at || item.published_at || '',
      });
    }

    radarFindings.forEach((f, idx) => {
      if (!isSafeHttpUrl(f.url) || seenUrls.has(f.url)) return;
      seenUrls.add(f.url);
      merged.push({
        key: `finding-${idx}-${f.url}`,
        title: f.title || f.url,
        url: f.url,
        domain: extractDomain(f.url),
        source: f.source || 'radar',
        snippet: f.engagement || f.snippet || '',
        topicId: f.query ? `query:${f.query}` : null,
        topicLabel: f.query || 'Market scan',
        score: typeof f.scoring?.score === 'number' ? f.scoring.score : null,
        foundAt: f.created_at || '',
      });
    });

    return merged;
  }, [researchItems, radarFindings, topicMap]);

  const availableSources = useMemo(() => {
    const set = new Set<string>();
    for (const item of combinedItems) {
      if (item.source) set.add(item.source);
    }
    return Array.from(set).sort();
  }, [combinedItems]);

  const availableTopicFilters = useMemo(() => {
    const map = new Map<string, string>();
    for (const topic of watchTopics) {
      map.set(topic.id, topic.query);
    }
    for (const item of combinedItems) {
      if (item.topicId && !map.has(item.topicId)) {
        map.set(item.topicId, item.topicLabel);
      }
    }
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [watchTopics, combinedItems]);

  const filteredItems = useMemo(
    () =>
      combinedItems.filter((item) => {
        if (sourceFilter !== 'all' && item.source !== sourceFilter) return false;
        if (topicFilter !== 'all' && item.topicId !== topicFilter) return false;
        return true;
      }),
    [combinedItems, sourceFilter, topicFilter],
  );

  const answerSources = useMemo(() => (answerText ? extractUrls(answerText) : []), [answerText]);

  const activeResearcherSessions = useMemo(
    () =>
      sessions.filter(
        (s) => s.agent_id === 'researcher' && (s.status === 'running' || s.status === 'waiting_user'),
      ),
    [sessions],
  );

  const handleAskResearcher = async () => {
    const clean = question.trim();
    if (!clean) return;
    setAsking(true);
    setError(null);
    setNotice(null);
    try {
      const res = await askResearcher(clean);
      if (!res.success) {
        setError(res.error || 'Research request failed.');
      } else {
        setAnswerText(res.waiting_user ? res.question || '' : res.response || '');
        setAnswerSessionId(res.session_id || null);
        setQuestion('');
        await loadAll();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Research request failed.');
    } finally {
      setAsking(false);
    }
  };

  const handleStartScan = async (kind: 'market' | 'watch') => {
    setError(null);
    setNotice(null);
    setBusyId(kind);
    try {
      const res = kind === 'market' ? await startMarketScan() : await startWatchTopicsScan();
      setActiveScanJob(res.job);
      setNotice(res.message || 'Scan queued.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start scan.');
    } finally {
      setBusyId(null);
    }
  };

  const handleAddTopic = async () => {
    const clean = newQuery.trim();
    if (!clean) return;
    setError(null);
    setBusyId('add-topic');
    try {
      const cadence = Math.max(1, Number.parseInt(newCadence, 10) || 24);
      const days = Math.max(1, Number.parseInt(newDays, 10) || 7);
      await createWatchTopic({
        query: clean,
        sources: newSources.length > 0 ? newSources : null,
        cadence_hours: cadence,
        days,
      });
      setNewQuery('');
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add watch topic.');
    } finally {
      setBusyId(null);
    }
  };

  const handleToggleTopic = async (topicId: string) => {
    setError(null);
    setBusyId(topicId);
    try {
      await toggleWatchTopic(topicId);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle watch topic.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDeleteTopic = async (topicId: string) => {
    setError(null);
    setBusyId(topicId);
    try {
      await deleteWatchTopic(topicId);
      if (topicFilter === topicId) setTopicFilter('all');
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove watch topic.');
    } finally {
      setBusyId(null);
    }
  };

  const handleStopBrowserSession = async (sessionId?: string | null) => {
    if (!sessionId) return;
    setError(null);
    setBusyId(sessionId);
    try {
      await cancelSession(sessionId);
      setLiveFrame((prev) => (prev ? { ...prev, live: false } : null));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop browsing session.');
    } finally {
      setBusyId(null);
    }
  };

  const handleAnswerResearcher = async (sessionId: string, answerVal: string) => {
    const clean = answerVal.trim();
    if (!clean) return;
    setBusyId(sessionId);
    setError(null);
    try {
      await answerSession(sessionId, clean);
      setWaitingAnswer('');
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume researcher.');
    } finally {
      setBusyId(null);
    }
  };

  const handlePitch = async (item: CombinedRadarItem) => {
    setBusyId(item.key);
    setError(null);
    setNotice(null);
    try {
      const scorePart = item.score !== null ? ` (${item.score}/625)` : '';
      await postMessage(
        `@cmo Opportunity Radar hit${scorePart}: "${item.title}" (${item.url}). Evaluate positioning and delegate next steps if worth pursuing.`,
      );
      setNotice(`Pitched "${item.title}" to @cmo in Team room.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to pitch item to @cmo.');
    } finally {
      setBusyId(null);
    }
  };

  const toggleSourceChoice = (src: string) => {
    setNewSources((prev) =>
      prev.includes(src) ? prev.filter((item) => item !== src) : [...prev, src],
    );
  };

  const handleCreateSocialRun = async () => {
    const clean = socialArgument.trim();
    if (!clean) return;
    setSocialSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const res = await createSocialRun({
        platform: socialPlatform,
        recipe: socialRecipe,
        argument: clean,
      });
      if (res?.run) {
        upsertSocialRun(res.run);
        setSocialArgument('');
        setNotice(`Created social run ${res.run.id}. Press Start on the card when ready.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create social research run.');
    } finally {
      setSocialSubmitting(false);
    }
  };

  return (
    <div className="studio-stack">
      <SheetHeader
        page={3}
        total={10}
        subtitle="Intelligence"
        title="Research"
        lead="Ask the Researcher, watch live browser captures, filter radar findings, and manage watch topics."
      />

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

      <section className="studio-section" aria-labelledby="research-ask-heading">
        <h2 id="research-ask-heading" className="studio-section-title">
          Ask researcher
        </h2>
        <Card>
          <div className="studio-stack-sm">
            <Field label="Research question" htmlFor="research-question-input">
              <TextInput
                id="research-question-input"
                placeholder="What are developers saying about local-first CLI tools this week?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !asking && question.trim()) {
                    e.preventDefault();
                    void handleAskResearcher();
                  }
                }}
              />
            </Field>
            <div className="studio-btn-group">
              <PillButton
                disabled={asking || !question.trim()}
                onClick={() => void handleAskResearcher()}
              >
                {asking ? 'Researching...' : 'Start research'}
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                disabled={busyId === 'market'}
                onClick={() => void handleStartScan('market')}
              >
                Scan ecosystem
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                disabled={busyId === 'watch'}
                onClick={() => void handleStartScan('watch')}
              >
                Run watch topics
              </PillButton>
            </div>
          </div>
        </Card>

        {liveStep || (activeScanJob && (activeScanJob.status === 'running' || activeScanJob.status === 'queued')) ? (
          <Card outline>
            {liveStep ? (
              <div className="studio-stack-sm">
                <div className="studio-card-head">
                  <StatusDot
                    state="working"
                    label={`Researcher step ${liveStep.step}`}
                    live
                  />
                  {liveStep.toolNames.length > 0 ? (
                    <span className="mg-chip">{liveStep.toolNames.join(', ')}</span>
                  ) : null}
                </div>
                {liveStep.preview ? <p className="mg-sub">{liveStep.preview}</p> : null}
              </div>
            ) : null}

            {activeScanJob && (activeScanJob.status === 'running' || activeScanJob.status === 'queued') ? (
              <div className="studio-card-head">
                <StatusDot
                  state="working"
                  label={`${activeScanJob.name} ${activeScanJob.progress}%`}
                  live
                />
                <span className="studio-mono">{activeScanJob.message}</span>
              </div>
            ) : null}
          </Card>
        ) : null}

        {activeResearcherSessions
          .filter((s) => s.status === 'waiting_user' && s.pending_question)
          .map((sess) => (
            <Card key={sess.id} selected>
              <div className="studio-card-head">
                <div>
                  <div className="mg-label">Researcher question</div>
                  <div className="studio-card-title">{sess.pending_question?.question}</div>
                </div>
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={busyId === sess.id}
                  onClick={() => void handleStopBrowserSession(sess.id)}
                >
                  Cancel
                </PillButton>
              </div>
              <div className="studio-inline-form">
                <TextInput
                  aria-label="Answer researcher question"
                  placeholder="Type your answer..."
                  value={waitingAnswer}
                  onChange={(e) => setWaitingAnswer(e.target.value)}
                />
                <PillButton
                  size="sm"
                  disabled={busyId === sess.id || !waitingAnswer.trim()}
                  onClick={() => void handleAnswerResearcher(sess.id, waitingAnswer)}
                >
                  Resume
                </PillButton>
              </div>
            </Card>
          ))}

        {answerText ? (
          <Card outline>
            <div className="studio-card-head">
              <div className="mg-label">Research findings</div>
              {answerSessionId ? (
                <span className="studio-mono">{answerSessionId}</span>
              ) : null}
            </div>
            <SafeMarkdown text={answerText} />
            {answerSources.length > 0 ? (
              <div className="studio-sources-block">
                <div className="mg-label">Sources</div>
                <RowList aria-label="Research sources">
                  {answerSources.map((src) => (
                    <Row
                      key={src.url}
                      title={
                        <a href={src.url} target="_blank" rel="noopener noreferrer">
                          {src.label}
                        </a>
                      }
                      meta={src.domain}
                    />
                  ))}
                </RowList>
              </div>
            ) : null}
          </Card>
        ) : null}
      </section>

      {liveFrame ? (
        <section className="studio-section" aria-labelledby="research-browser-heading">
          <h2 id="research-browser-heading" className="studio-section-title">
            Research browser
          </h2>
          <Card outline>
            <div className="studio-card-head">
              <div className="studio-row-actions">
                <StatusDot
                  state={liveFrame.live ? 'working' : 'online'}
                  label={liveFrame.live ? 'Live' : 'Captured'}
                  live={liveFrame.live}
                />
                {liveFrame.url ? (
                  <span className="studio-mono">{extractDomain(liveFrame.url) || liveFrame.url}</span>
                ) : null}
              </div>
              <div className="studio-btn-group">
                {liveFrame.live && liveFrame.session_id ? (
                  <PillButton
                    variant="danger"
                    size="sm"
                    disabled={busyId === liveFrame.session_id}
                    onClick={() => void handleStopBrowserSession(liveFrame.session_id)}
                  >
                    Stop
                  </PillButton>
                ) : (
                  <PillButton
                    variant="quiet"
                    size="sm"
                    onClick={() => setLiveFrame(null)}
                  >
                    Dismiss
                  </PillButton>
                )}
              </div>
            </div>
            <img
              className="studio-browser-frame"
              src={`/api/browser_frame?path=${encodeURIComponent(liveFrame.screenshot_path)}&v=${liveFrame.version}`}
              alt="Live research browser view"
            />
          </Card>
        </section>
      ) : null}

      <section className="studio-section" aria-labelledby="research-radar-heading">
        <div className="studio-section-bar">
          <h2 id="research-radar-heading" className="studio-section-title">
            Research items &amp; radar
          </h2>
          <div className="studio-filter-row">
            <Select
              aria-label="Filter by source"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
            >
              <option value="all">All sources</option>
              {availableSources.map((src) => (
                <option key={src} value={src}>
                  {src}
                </option>
              ))}
            </Select>
            <Select
              aria-label="Filter by topic"
              value={topicFilter}
              onChange={(e) => setTopicFilter(e.target.value)}
            >
              <option value="all">All topics</option>
              {availableTopicFilters.map((topic) => (
                <option key={topic.id} value={topic.id}>
                  {topic.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {loading && combinedItems.length === 0 ? (
          <div className="mg-empty">Loading research items.</div>
        ) : filteredItems.length === 0 ? (
          <div className="mg-empty">
            No research items match the current filters. Run a scan or ask the Researcher above.
          </div>
        ) : (
          <RowList aria-label="Research items">
            {filteredItems.map((item) => (
              <Row
                key={item.key}
                title={
                  <a href={item.url} target="_blank" rel="noopener noreferrer">
                    {item.title}
                  </a>
                }
                description={
                  <div className="studio-stack-xs">
                    {item.snippet ? <span>{item.snippet}</span> : null}
                    <div className="studio-chip-row">
                      <span className="mg-chip">{item.source}</span>
                      {item.topicLabel ? <span className="mg-chip">{item.topicLabel}</span> : null}
                      {item.score !== null ? (
                        <span className="mg-chip">Score {item.score}</span>
                      ) : null}
                      {item.foundAt ? (
                        <span className="studio-mono">{formatTimestamp(item.foundAt)}</span>
                      ) : null}
                    </div>
                  </div>
                }
                meta={
                  <div className="studio-row-actions">
                    <span className="studio-mono">{item.domain}</span>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyId === item.key}
                      onClick={() => void handlePitch(item)}
                    >
                      Pitch
                    </PillButton>
                  </div>
                }
              />
            ))}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="research-topics-heading">
        <h2 id="research-topics-heading" className="studio-section-title">
          Watch topics
        </h2>
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-topic-form-grid">
              <Field label="Query" htmlFor="watch-query-input">
                <TextInput
                  id="watch-query-input"
                  placeholder="e.g. local LLM terminal tools"
                  value={newQuery}
                  onChange={(e) => setNewQuery(e.target.value)}
                />
              </Field>
              <Field label="Cadence (hours)" htmlFor="watch-cadence-input">
                <TextInput
                  id="watch-cadence-input"
                  type="number"
                  min={1}
                  value={newCadence}
                  onChange={(e) => setNewCadence(e.target.value)}
                />
              </Field>
              <Field label="Lookback (days)" htmlFor="watch-days-input">
                <TextInput
                  id="watch-days-input"
                  type="number"
                  min={1}
                  value={newDays}
                  onChange={(e) => setNewDays(e.target.value)}
                />
              </Field>
            </div>
            <div className="studio-chip-row">
              {SOURCE_OPTIONS.map((src) => (
                <Checkbox
                  key={src}
                  label={src}
                  checked={newSources.includes(src)}
                  onChange={() => toggleSourceChoice(src)}
                />
              ))}
            </div>
            <div>
              <PillButton
                size="sm"
                disabled={busyId === 'add-topic' || !newQuery.trim()}
                onClick={() => void handleAddTopic()}
              >
                Add watch topic
              </PillButton>
            </div>
          </div>
        </Card>

        {loading && watchTopics.length === 0 ? (
          <div className="mg-empty">Loading watch topics.</div>
        ) : watchTopics.length === 0 ? (
          <div className="mg-empty">No watch topics configured yet.</div>
        ) : (
          <RowList aria-label="Watch topics">
            {watchTopics.map((topic) => (
              <Row
                key={topic.id}
                title={topic.query}
                description={
                  <span className="studio-mono">
                    {topic.sources && topic.sources.length > 0
                      ? topic.sources.join(', ')
                      : 'all sources'}{' '}
                    · every {topic.cadence_hours}h · {topic.days}d window
                    {topic.last_run ? ` · last ${formatTimestamp(topic.last_run)}` : ''}
                  </span>
                }
                meta={
                  <div className="studio-row-actions">
                    <StatusDot
                      state={topic.enabled ? 'online' : 'off'}
                      label={topic.enabled ? 'Active' : 'Paused'}
                    />
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyId === topic.id}
                      onClick={() => void handleToggleTopic(topic.id)}
                    >
                      {topic.enabled ? 'Pause' : 'Enable'}
                    </PillButton>
                    <PillButton
                      variant="danger"
                      size="sm"
                      disabled={busyId === topic.id}
                      onClick={() => void handleDeleteTopic(topic.id)}
                    >
                      Remove
                    </PillButton>
                  </div>
                }
              />
            ))}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="research-social-heading">
        <h2 id="research-social-heading" className="studio-section-title">
          Social research
        </h2>
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-grid-2">
              <Field label="Platform" htmlFor="social-form-platform">
                <Select
                  id="social-form-platform"
                  value={socialPlatform}
                  onChange={(e) => setSocialPlatform(e.target.value as SocialPlatform)}
                >
                  <option value="instagram">Instagram</option>
                  <option value="x">X</option>
                  <option value="linkedin">LinkedIn</option>
                </Select>
              </Field>
              <Field label="Recipe" htmlFor="social-form-recipe">
                <Select
                  id="social-form-recipe"
                  value={socialRecipe}
                  onChange={(e) => setSocialRecipe(e.target.value as SocialRecipe)}
                >
                  <option value="profile_posts">profile_posts</option>
                  <option value="search">search</option>
                  <option value="post_replies">post_replies</option>
                  <option value="trending">trending</option>
                </Select>
              </Field>
            </div>

            <Field
              label="Argument"
              htmlFor="social-form-argument"
              hint={
                socialRecipe === 'profile_posts'
                  ? 'Account handle (e.g. @handle)'
                  : socialRecipe === 'post_replies'
                    ? 'Exact https post URL'
                    : socialRecipe === 'trending'
                      ? 'Topic keyword'
                      : 'Search query'
              }
            >
              <TextInput
                id="social-form-argument"
                placeholder={
                  socialRecipe === 'post_replies'
                    ? 'https://x.com/user/status/123456'
                    : socialRecipe === 'profile_posts'
                      ? 'handle'
                      : 'developer tools'
                }
                value={socialArgument}
                onChange={(e) => setSocialArgument(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !socialSubmitting && socialArgument.trim()) {
                    e.preventDefault();
                    void handleCreateSocialRun();
                  }
                }}
              />
            </Field>

            {socialStatus?.platforms?.[socialPlatform] &&
            !socialStatus.platforms[socialPlatform].enabled ? (
              <div className="studio-hint">
                {socialPlatform} is disabled. Enable it in Settings &gt; Social research first.
              </div>
            ) : null}

            <div className="studio-section-bar">
              <span className="studio-hint">
                Creates an awaiting_start run; nothing runs until you press Start.
              </span>
              <PillButton
                size="sm"
                disabled={socialSubmitting || !socialArgument.trim()}
                onClick={() => void handleCreateSocialRun()}
              >
                {socialSubmitting ? 'Creating...' : 'Create social run'}
              </PillButton>
            </div>
          </div>
        </Card>

        {socialRuns.length === 0 ? (
          <div className="mg-empty">No social research runs yet.</div>
        ) : (
          <div className="studio-stack-sm" aria-label="Social research runs">
            {socialRuns.map((run) => (
              <SocialRunCard key={run.id} run={run} onRunUpdated={upsertSocialRun} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
