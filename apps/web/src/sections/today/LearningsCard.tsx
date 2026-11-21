import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Card, Checkbox, Field, PillButton, TextArea, TextInput } from '@magnm/design/react';
import {
  addLearning,
  approveLearning,
  rejectLearning,
  retireLearning,
} from '../../api/learnings';
import type { ContentItem, LearningEvidence, LearningRecord } from '../../types';

const TARGET_AGENTS = ['writer', 'cmo', 'designer', 'video-producer', 'researcher'] as const;

export interface LearningsCardProps {
  learnings: LearningRecord[];
  contentItems: ContentItem[];
  loading: boolean;
  onRefresh: () => Promise<void> | void;
  onError: (message: string) => void;
}

function formatEvidenceNumbers(metrics: Record<string, number> | undefined): string {
  const entries = Object.entries(metrics || {});
  if (entries.length === 0) return 'no recorded metrics';
  return entries
    .slice(0, 5)
    .map(([k, v]) => `${k}: ${v}`)
    .join(', ');
}

function formatEvidenceItem(ev: LearningEvidence, titlesById: Map<string, string>): string {
  const title = ev.title || titlesById.get(ev.content_id) || ev.content_id;
  const plat = ev.platform ? ` (${ev.platform})` : '';
  const nums = formatEvidenceNumbers(ev.metrics);
  return `${title}${plat} — ${nums}`;
}

/** Today card for reviewing proposed learnings, managing approved learnings, and adding manual learnings. */
export function LearningsCard({
  learnings,
  contentItems,
  loading,
  onRefresh,
  onError,
}: LearningsCardProps) {
  const [draftTexts, setDraftTexts] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [newText, setNewText] = useState('');
  const [applyAll, setApplyAll] = useState(true);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const pendingLearnings = useMemo(
    () => learnings.filter((item) => item.status === 'pending'),
    [learnings],
  );
  const approvedLearnings = useMemo(
    () => learnings.filter((item) => item.status === 'approved'),
    [learnings],
  );

  const titlesById = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of contentItems) {
      map.set(item.id, item.title || item.id);
    }
    return map;
  }, [contentItems]);

  useEffect(() => {
    setDraftTexts((prev) => {
      const next = { ...prev };
      for (const item of pendingLearnings) {
        if (next[item.id] === undefined) {
          next[item.id] = item.text;
        }
      }
      return next;
    });
  }, [pendingLearnings]);

  const handleApprove = async (item: LearningRecord) => {
    const edited = (draftTexts[item.id] ?? item.text).trim();
    if (!edited) {
      onError('Learning text cannot be empty.');
      return;
    }
    setBusyId(item.id);
    try {
      await approveLearning(item.id, edited);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to approve learning.');
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async (id: string) => {
    setBusyId(id);
    try {
      await rejectLearning(id);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to reject learning.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRetire = async (id: string) => {
    setBusyId(id);
    try {
      await retireLearning(id);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to retire learning.');
    } finally {
      setBusyId(null);
    }
  };

  const handleToggleAgent = (agentId: string, checked: boolean) => {
    setApplyAll(false);
    setSelectedAgents((prev) => {
      if (checked) {
        return prev.includes(agentId) ? prev : [...prev, agentId];
      }
      return prev.filter((id) => id !== agentId);
    });
  };

  const handleAdd = async (event: FormEvent) => {
    event.preventDefault();
    const clean = newText.trim();
    if (!clean) {
      onError('Learning text is required.');
      return;
    }
    const targets = applyAll ? ['all'] : selectedAgents;
    if (targets.length === 0) {
      onError('Select at least one agent or All agents.');
      return;
    }
    setSubmitting(true);
    try {
      await addLearning({ text: clean, applies_to: targets });
      setNewText('');
      setApplyAll(true);
      setSelectedAgents([]);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add learning.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card outline aria-label="Learnings">
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">Analyst</div>
            <div className="studio-card-title">
              Learnings ({pendingLearnings.length} pending · {approvedLearnings.length} active)
            </div>
          </div>
        </div>

        <div className="studio-stack-xs">
          <div className="mg-label">Pending approval ({pendingLearnings.length})</div>
          {loading && learnings.length === 0 ? (
            <div className="mg-empty">Loading learnings.</div>
          ) : pendingLearnings.length === 0 ? (
            <div className="mg-empty">No pending learnings waiting for approval.</div>
          ) : (
            <div className="studio-stack-sm" role="list" aria-label="Pending learnings">
              {pendingLearnings.map((item) => {
                const isBusy = busyId === item.id;
                const val = draftTexts[item.id] ?? item.text;
                return (
                  <div key={item.id} role="listitem" className="studio-learning-item">
                    <Field label="Learning statement" htmlFor={`learning-edit-${item.id}`}>
                      <TextArea
                        id={`learning-edit-${item.id}`}
                        aria-label={`Edit learning ${item.id}`}
                        rows={2}
                        value={val}
                        onChange={(e) =>
                          setDraftTexts((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                      />
                    </Field>

                    <div className="studio-applies-chips" aria-label="Applies to agents">
                      {(item.applies_to || ['all']).map((ag) => (
                        <span key={ag} className="mg-chip">
                          @{ag}
                        </span>
                      ))}
                    </div>

                    {item.evidence && item.evidence.length > 0 ? (
                      <div className="studio-learning-evidence">
                        <div className="mg-label">Evidence</div>
                        {item.evidence.map((ev) => (
                          <div key={ev.content_id} className="mg-row-desc">
                            {formatEvidenceItem(ev, titlesById)}
                          </div>
                        ))}
                      </div>
                    ) : null}

                    <div className="studio-row-actions">
                      <PillButton
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleApprove(item)}
                      >
                        Approve
                      </PillButton>
                      <PillButton
                        variant="danger"
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleReject(item.id)}
                      >
                        Reject
                      </PillButton>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="studio-stack-xs">
          <div className="mg-label">Active learnings ({approvedLearnings.length})</div>
          {approvedLearnings.length === 0 ? (
            <div className="mg-empty">
              No active learnings yet. Approved learnings guide future drafts.
            </div>
          ) : (
            <div className="mg-rows" role="list" aria-label="Approved learnings">
              {approvedLearnings.map((item) => {
                const isBusy = busyId === item.id;
                return (
                  <div key={item.id} role="listitem" className="studio-learning-approved-row">
                    <div className="mg-row-main">
                      <span className="mg-row-title">{item.text}</span>
                      <div className="studio-applies-chips">
                        {(item.applies_to || ['all']).map((ag) => (
                          <span key={ag} className="mg-chip">
                            @{ag}
                          </span>
                        ))}
                      </div>
                    </div>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={isBusy}
                      onClick={() => void handleRetire(item.id)}
                    >
                      Retire
                    </PillButton>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <form className="studio-reminder-form" onSubmit={(e) => void handleAdd(e)}>
          <div className="mg-label">Add a learning</div>
          <div className="studio-inline-form">
            <TextInput
              aria-label="New learning text"
              placeholder="Write a pattern that worked for your team..."
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
            />
            <PillButton
              type="submit"
              size="sm"
              disabled={submitting || !newText.trim() || (!applyAll && selectedAgents.length === 0)}
            >
              {submitting ? 'Adding...' : 'Add learning'}
            </PillButton>
          </div>
          <div className="studio-applies-chips" aria-label="Target agents">
            <Checkbox
              label="all"
              checked={applyAll}
              onChange={(e) => {
                const checked = e.target.checked;
                setApplyAll(checked);
                if (checked) setSelectedAgents([]);
              }}
            />
            {TARGET_AGENTS.map((ag) => (
              <Checkbox
                key={ag}
                label={ag}
                checked={!applyAll && selectedAgents.includes(ag)}
                onChange={(e) => handleToggleAgent(ag, e.target.checked)}
              />
            ))}
          </div>
        </form>
      </div>
    </Card>
  );
}
