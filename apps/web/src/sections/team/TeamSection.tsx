import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, NumberedTabs, PillButton, SheetHeader, StatusDot, TextInput } from '@magnm/design/react';
import {
  answerSession,
  cancelSession,
  getChatHistory,
  getMessages,
  getRoster,
  listAssets,
  listSessions,
  postAgentChat,
  postMessage,
  type AssetRecord,
  type DirectChatTurn,
  type RosterAgent,
  type TeamMessage,
} from '../../api/team';
import { getSocialRun, listSocialRuns } from '../../api/social';
import { useStudioEvents } from '../../events';
import type { AgentSession, SocialRunRecord } from '../../types';
import { MentionComposer } from '../../ui/MentionComposer';
import { AgentRoster } from './AgentRoster';
import { CodingRunsPanel } from './CodingRunsPanel';
import { MemoryPanel } from './MemoryPanel';
import { MessageList, type DisplayMessage } from './MessageList';

interface StepProgress {
  sessionId: string;
  agentId: string;
  step: number;
  toolNames: string[];
  preview: string;
}

/** Team section: 7-agent roster, shared Team Room, 1-on-1 chat, coding runs, live progress, and memory. */
export function TeamSection() {
  const [teamTab, setTeamTab] = useState<'chat' | 'coding'>('chat');
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<'room' | string>('room');
  const [roomMessages, setRoomMessages] = useState<TeamMessage[]>([]);
  const [directHistory, setDirectHistory] = useState<DirectChatTurn[]>([]);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [waitingSessions, setWaitingSessions] = useState<AgentSession[]>([]);
  const [socialRuns, setSocialRuns] = useState<SocialRunRecord[]>([]);
  const [liveProgress, setLiveProgress] = useState<StepProgress | null>(null);

  const [composerText, setComposerText] = useState('');
  const [selectedAssetId, setSelectedAssetId] = useState('');
  const [waitingAnswers, setWaitingAnswers] = useState<Record<string, string>>({});

  const [loadingRoster, setLoadingRoster] = useState(true);
  const [loadingThread, setLoadingThread] = useState(true);
  const [sending, setSending] = useState(false);
  const [answeringId, setAnsweringId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showMemory, setShowMemory] = useState(true);

  const upsertSocialRun = useCallback((incoming: SocialRunRecord) => {
    setSocialRuns((prev) => {
      const idx = prev.findIndex((r) => r.id === incoming.id);
      if (idx === -1) return [incoming, ...prev];
      return prev.map((r, i) => (i === idx ? { ...r, ...incoming } : r));
    });
  }, []);

  const loadRosterAndAssets = useCallback(async () => {
    const [rosterRes, assetsRes, sessRes, socialRes] = await Promise.allSettled([
      getRoster(),
      listAssets(),
      listSessions(10, 'waiting_user'),
      listSocialRuns(50),
    ]);

    if (rosterRes.status === 'fulfilled') {
      setRoster(Array.isArray(rosterRes.value) ? rosterRes.value : []);
    } else if (rosterRes.reason instanceof Error) {
      setError((prev) => prev ?? rosterRes.reason.message);
    }

    if (assetsRes.status === 'fulfilled') {
      setAssets(Array.isArray(assetsRes.value) ? assetsRes.value : []);
    }

    if (sessRes.status === 'fulfilled') {
      setWaitingSessions(Array.isArray(sessRes.value) ? sessRes.value : []);
    }

    if (socialRes.status === 'fulfilled') {
      setSocialRuns(Array.isArray(socialRes.value) ? socialRes.value : []);
    }

    setLoadingRoster(false);
  }, []);

  const loadActiveThread = useCallback(async (target: 'room' | string) => {
    setLoadingThread(true);
    setError(null);
    try {
      if (target === 'room') {
        const msgs = await getMessages();
        setRoomMessages(Array.isArray(msgs) ? msgs : []);
      } else {
        const turns = await getChatHistory(target);
        setDirectHistory(Array.isArray(turns) ? turns : []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load messages.');
    } finally {
      setLoadingThread(false);
    }
  }, []);

  useEffect(() => {
    void loadRosterAndAssets();
  }, [loadRosterAndAssets]);

  useEffect(() => {
    void loadActiveThread(selectedTarget);
  }, [selectedTarget, loadActiveThread]);

  useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (eventName === 'message' && data && typeof data === 'object') {
          const msg = data as TeamMessage;
          setRoomMessages((prev) => {
            if (prev.some((m) => m.id === msg.id)) return prev;
            return [...prev, msg];
          });
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
          setLiveProgress({
            sessionId: ev.session_id || '',
            agentId: ev.agent_id || 'agent',
            step: ev.step ?? 1,
            toolNames: Array.isArray(ev.tool_names) ? ev.tool_names : [],
            preview: ev.text_preview || '',
          });
          void getRoster()
            .then((next) => setRoster(Array.isArray(next) ? next : []))
            .catch(() => {});
          return;
        }

        if (eventName === 'agent_waiting') {
          setLiveProgress(null);
          void Promise.all([getRoster(), listSessions(10, 'waiting_user')])
            .then(([nextRoster, nextWaiting]) => {
              setRoster(Array.isArray(nextRoster) ? nextRoster : []);
              setWaitingSessions(Array.isArray(nextWaiting) ? nextWaiting : []);
            })
            .catch(() => {});
          return;
        }

        if (
          eventName === 'agent_done' ||
          eventName === 'agent_failed' ||
          eventName === 'agent_cancelled'
        ) {
          setLiveProgress(null);
          void Promise.all([getRoster(), listSessions(10, 'waiting_user')])
            .then(([nextRoster, nextWaiting]) => {
              setRoster(Array.isArray(nextRoster) ? nextRoster : []);
              setWaitingSessions(Array.isArray(nextWaiting) ? nextWaiting : []);
            })
            .catch(() => {});
          if (selectedTarget !== 'room') {
            void getChatHistory(selectedTarget)
              .then((turns) => setDirectHistory(Array.isArray(turns) ? turns : []))
              .catch(() => {});
          }
          void listSocialRuns(50)
            .then((runs) => setSocialRuns(runs))
            .catch(() => {});
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
      [selectedTarget, upsertSocialRun],
    ),
  );

  const selectedAgent = useMemo(
    () => (selectedTarget === 'room' ? null : roster.find((a) => a.id === selectedTarget) ?? null),
    [roster, selectedTarget],
  );

  const visibleWaitingSessions = useMemo(() => {
    if (selectedTarget === 'room') return waitingSessions;
    return waitingSessions.filter((s) => s.agent_id === selectedTarget);
  }, [waitingSessions, selectedTarget]);

  const displayMessages: DisplayMessage[] = useMemo(() => {
    if (selectedTarget === 'room') {
      return roomMessages.map((m) => {
        const code = (m.sender_code || m.sender || '').trim();
        const name = (m.sender_name || m.sender || '').trim();
        const authorLabel =
          m.sender === 'CEO'
            ? 'CEO'
            : !code || code.toLowerCase() === name.toLowerCase()
              ? name
              : `${code} · ${name}`;
        return {
          key: m.id,
          sender: m.sender,
          authorLabel,
          timestamp: m.timestamp,
          text: m.text,
          sessionId: m.session_id,
          modelUsed: m.model_used,
          attachedAssets: m.attached_assets,
          contentIds: m.content_ids,
          waitingUser: m.waiting_user,
        };
      });
    }

    return directHistory.map((turn, idx) => ({
      key: `${turn.sender}-${turn.timestamp}-${idx}`,
      sender: turn.sender,
      authorLabel: turn.sender === 'user' ? 'You' : turn.name || `@${turn.sender}`,
      timestamp: turn.timestamp,
      text: turn.text,
      sessionId: turn.session_id,
      modelUsed: turn.model_used,
      attachedAssets: turn.attached_assets,
      contentIds: turn.content_ids,
      waitingUser: turn.waiting_user,
      redirectTo: turn.redirect_to,
    }));
  }, [selectedTarget, roomMessages, directHistory]);

  const handleSend = async () => {
    const clean = composerText.trim();
    if (!clean) return;
    const attached = selectedAssetId ? [selectedAssetId] : [];
    setSending(true);
    setError(null);

    try {
      if (selectedTarget === 'room') {
        const res = await postMessage(clean, attached);
        setComposerText('');
        setSelectedAssetId('');
        if (Array.isArray(res.replies) && res.replies.length > 0) {
          const refreshed = await getMessages();
          setRoomMessages(Array.isArray(refreshed) ? refreshed : []);
        }
      } else {
        const res = await postAgentChat(selectedTarget, clean, attached);
        if (!res.success && res.error) {
          setError(res.error);
        } else {
          setComposerText('');
          setSelectedAssetId('');
        }
        if (Array.isArray(res.history)) {
          setDirectHistory(res.history);
        } else {
          const refreshed = await getChatHistory(selectedTarget);
          setDirectHistory(Array.isArray(refreshed) ? refreshed : []);
        }
      }
      void loadRosterAndAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message.');
    } finally {
      setSending(false);
    }
  };

  const handleAnswerWaiting = async (sessionId: string, override?: string) => {
    const answer = (override ?? waitingAnswers[sessionId] ?? '').trim();
    if (!answer) {
      setError('Enter an answer before resuming.');
      return;
    }
    setAnsweringId(sessionId);
    setError(null);
    try {
      await answerSession(sessionId, answer);
      setWaitingAnswers((prev) => ({ ...prev, [sessionId]: '' }));
      await loadRosterAndAssets();
      await loadActiveThread(selectedTarget);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to answer session.');
    } finally {
      setAnsweringId(null);
    }
  };

  const handleCancelSession = async (sessionId: string) => {
    setAnsweringId(sessionId);
    setError(null);
    try {
      await cancelSession(sessionId);
      await loadRosterAndAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel session.');
    } finally {
      setAnsweringId(null);
    }
  };

  return (
    <div className="studio-stack">
      <SheetHeader
        page={2}
        total={10}
        subtitle="AI Team"
        title="Team"
        lead="Shared team room with @mentions, direct specialist chats, coding runs, and editable agent memory."
      />

      <NumberedTabs
        ariaLabel="Team views"
        items={[
          { id: 'chat', label: 'Team chat' },
          { id: 'coding', label: 'Coding runs' },
        ]}
        current={teamTab}
        onSelect={(id) => setTeamTab(id === 'coding' ? 'coding' : 'chat')}
      />

      {teamTab === 'coding' ? (
        <CodingRunsPanel />
      ) : (
        <>
          {error ? (
            <div className="mg-notice" role="alert">
              {error}
            </div>
          ) : null}

      <div className="studio-team-grid">
        <AgentRoster
          agents={roster}
          selectedTarget={selectedTarget}
          onSelect={(next) => setSelectedTarget(next)}
          loading={loadingRoster}
        />

        <div className="studio-team-main">
          <Card outline className="studio-team-header">
            <div className="studio-card-head">
              <div>
                <div className="mg-label">
                  {selectedAgent ? `@${selectedAgent.id}` : 'Shared room'}
                </div>
                <div className="studio-card-title">
                  {selectedAgent ? selectedAgent.name : 'Team room'}
                </div>
                <p className="mg-sub">
                  {selectedAgent
                    ? selectedAgent.role
                    : 'Mention an agent with @name (up to 3 per message). Unmentioned notes route to @cmo.'}
                </p>
              </div>
              {selectedAgent ? (
                <div className="studio-row-actions">
                  {selectedAgent.model_info?.model ? (
                    <span className="mg-chip">{selectedAgent.model_info.model}</span>
                  ) : null}
                  <PillButton
                    variant="outline"
                    size="sm"
                    onClick={() => setShowMemory((prev) => !prev)}
                  >
                    {showMemory ? 'Hide memory' : 'Show memory'}
                  </PillButton>
                </div>
              ) : null}
            </div>
          </Card>

          {liveProgress ? (
            <Card className="studio-progress-card">
              <div className="studio-card-head">
                <StatusDot
                  state="working"
                  label={`@${liveProgress.agentId} working (step ${liveProgress.step})`}
                  live
                />
                {liveProgress.toolNames.length > 0 ? (
                  <span className="mg-chip">{liveProgress.toolNames.join(', ')}</span>
                ) : null}
              </div>
              {liveProgress.preview ? <p className="mg-sub">{liveProgress.preview}</p> : null}
            </Card>
          ) : null}

          {visibleWaitingSessions.map((sess) => {
            const q = sess.pending_question;
            if (!q) return null;
            return (
              <Card key={sess.id} selected>
                <div className="studio-card-head">
                  <div>
                    <div className="mg-label">@{sess.agent_id} is waiting for your answer</div>
                    <div className="studio-card-title">{q.question}</div>
                  </div>
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={answeringId === sess.id}
                    onClick={() => void handleCancelSession(sess.id)}
                  >
                    Cancel
                  </PillButton>
                </div>

                <div className="studio-gate-box">
                  {q.options && q.options.length > 0 ? (
                    <div className="studio-btn-group">
                      {q.options.map((opt) => (
                        <PillButton
                          key={opt}
                          variant="outline"
                          size="sm"
                          disabled={answeringId === sess.id}
                          onClick={() => void handleAnswerWaiting(sess.id, opt)}
                        >
                          {opt}
                        </PillButton>
                      ))}
                    </div>
                  ) : null}
                  <div className="studio-inline-form">
                    <TextInput
                      aria-label={`Answer for ${sess.agent_id}`}
                      placeholder="Reply to resume session..."
                      value={waitingAnswers[sess.id] ?? ''}
                      onChange={(e) =>
                        setWaitingAnswers((prev) => ({ ...prev, [sess.id]: e.target.value }))
                      }
                    />
                    <PillButton
                      size="sm"
                      disabled={answeringId === sess.id || !(waitingAnswers[sess.id] || '').trim()}
                      onClick={() => void handleAnswerWaiting(sess.id)}
                    >
                      Answer
                    </PillButton>
                  </div>
                </div>
              </Card>
            );
          })}

          {loadingThread && displayMessages.length === 0 ? (
            <div className="mg-empty">Loading conversation.</div>
          ) : (
            <MessageList
              messages={displayMessages}
              emptyText={
                selectedAgent
                  ? `No 1-on-1 chat history with @${selectedAgent.id} yet.`
                  : 'No messages in the team room yet. Start with @cmo or another agent.'
              }
              onSelectAgent={(aid) => setSelectedTarget(aid)}
              socialRuns={socialRuns}
              onSocialRunUpdated={upsertSocialRun}
            />
          )}

          <MentionComposer
            value={composerText}
            onChange={setComposerText}
            onSubmit={() => void handleSend()}
            agents={roster.map((a) => ({ id: a.id, name: a.name, role: a.role }))}
            placeholder={
              selectedAgent
                ? `Message @${selectedAgent.id}...`
                : 'Message the team room (use @cmo, @writer, @researcher)...'
            }
            submitLabel={sending ? 'Running...' : 'Send'}
            sending={sending}
            assets={assets}
            selectedAssetId={selectedAssetId}
            onSelectAsset={setSelectedAssetId}
            enableMentions={selectedTarget === 'room'}
          />

          {selectedAgent && showMemory ? (
            <MemoryPanel agentId={selectedAgent.id} agentName={selectedAgent.name} />
          ) : null}
        </div>
      </div>
        </>
      )}
    </div>
  );
}
