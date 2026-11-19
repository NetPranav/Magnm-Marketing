import { useEffect, useMemo, useRef, useState } from 'react';
import { Card, PillButton, Select, TextArea } from '@magnm/design/react';
import type { RosterAgent } from '../../api/team';
import type {
  JobRecord,
  ReviewComment,
  ReviewCommentAnchor,
  ReviewFinding,
  ReviewSuggestion,
} from '../../types';
import { formatTimestamp } from '../../ui/format';
import { MentionComposer, type MentionOption } from '../../ui/MentionComposer';
import { formatVideoTime, resolveAuthorName } from './reviewUtils';

export interface PendingAnchorDraft {
  kind: 'text' | 'image' | 'video' | 'general';
  anchor?: ReviewCommentAnchor;
  label: string;
}

export interface CommentsSuggestionsPaneProps {
  comments: ReviewComment[];
  suggestions: ReviewSuggestion[];
  roster: RosterAgent[];
  filter: 'open' | 'resolved' | 'all';
  onFilterChange: (next: 'open' | 'resolved' | 'all') => void;
  focusedCommentId: string | null;
  onFocusComment: (id: string | null) => void;
  replyTargetId: string | null;
  onReplyTargetChange: (id: string | null) => void;
  pendingAnchor: PendingAnchorDraft | null;
  onClearPendingAnchor: () => void;
  composerText: string;
  onComposerTextChange: (next: string) => void;
  onSubmitComment: (
    body: string,
    kind: 'text' | 'image' | 'video' | 'general',
    anchor?: ReviewCommentAnchor,
    parentId?: string,
  ) => Promise<void>;
  onUpdateComment: (
    commentId: string,
    payload: { body?: string; status?: 'open' | 'resolved' | 'applied' },
  ) => Promise<void>;
  onDeleteComment: (commentId: string) => Promise<void>;
  onDecideSuggestion: (suggestionId: string, decision: 'accept' | 'reject') => Promise<void>;
  onDecideAllSuggestions: (decision: 'accept' | 'reject') => Promise<void>;
  onApplyAll: (asSuggestions: boolean) => Promise<void>;
  onSeekVideo: (file: string, t: number) => void;
  activeJob: JobRecord | null;
  inlineWarnings: ReviewFinding[];
  busyAction: string | null;
}

/** Middle-right workspace pane: pending suggestions, Apply all progress, and threaded comments. */
export function CommentsSuggestionsPane({
  comments,
  suggestions,
  roster,
  filter,
  onFilterChange,
  focusedCommentId,
  onFocusComment,
  replyTargetId,
  onReplyTargetChange,
  pendingAnchor,
  onClearPendingAnchor,
  composerText,
  onComposerTextChange,
  onSubmitComment,
  onUpdateComment,
  onDeleteComment,
  onDecideSuggestion,
  onDecideAllSuggestions,
  onApplyAll,
  onSeekVideo,
  activeJob,
  inlineWarnings,
  busyAction,
}: CommentsSuggestionsPaneProps) {
  const [applyMode, setApplyMode] = useState<'new_version' | 'suggestions'>('new_version');
  const [replyText, setReplyText] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingBody, setEditingBody] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const commentCardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!focusedCommentId) return;
    const el = commentCardRefs.current[focusedCommentId];
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' });
    }
  }, [focusedCommentId]);

  const mentionOptions = useMemo<MentionOption[]>(
    () => roster.map((a) => ({ id: a.id, name: a.name, role: a.role })),
    [roster],
  );

  const imagePinNumbers = useMemo(() => {
    const map = new Map<string, number>();
    let n = 0;
    for (const c of comments) {
      if (c.kind === 'image' && c.anchor?.file) {
        n += 1;
        map.set(c.id, n);
      }
    }
    return map;
  }, [comments]);

  const counts = useMemo(() => {
    const open = comments.filter((c) => c.status === 'open').length;
    const resolved = comments.filter((c) => c.status !== 'open').length;
    return { open, resolved, all: comments.length };
  }, [comments]);

  const visibleComments = useMemo(() => {
    if (filter === 'open') return comments.filter((c) => c.status === 'open');
    if (filter === 'resolved') return comments.filter((c) => c.status !== 'open');
    return comments;
  }, [comments, filter]);

  const handleMainSubmit = async () => {
    const clean = composerText.trim();
    if (!clean) return;
    setSubmitting(true);
    try {
      await onSubmitComment(
        clean,
        pendingAnchor?.kind ?? 'general',
        pendingAnchor?.anchor,
        undefined,
      );
      onComposerTextChange('');
      onClearPendingAnchor();
    } finally {
      setSubmitting(false);
    }
  };

  const handleReplySubmit = async (parentId: string, parentKind: ReviewComment['kind']) => {
    const clean = replyText.trim();
    if (!clean) return;
    setSubmitting(true);
    try {
      await onSubmitComment(clean, parentKind, undefined, parentId);
      setReplyText('');
      onReplyTargetChange(null);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSaveEdit = async (commentId: string) => {
    const clean = editingBody.trim();
    if (!clean) return;
    setSubmitting(true);
    try {
      await onUpdateComment(commentId, { body: clean });
      setEditingId(null);
      setEditingBody('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="studio-stack">
      {suggestions.length > 0 ? (
        <section className="studio-stack-sm" aria-label="Pending suggestions">
          <div className="studio-section-bar">
            <h2 className="studio-section-title">
              Suggestions ({suggestions.length})
            </h2>
            <div className="studio-btn-group">
              <PillButton
                size="sm"
                disabled={busyAction !== null}
                onClick={() => void onDecideAllSuggestions('accept')}
              >
                Accept all
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                disabled={busyAction !== null}
                onClick={() => void onDecideAllSuggestions('reject')}
              >
                Reject all
              </PillButton>
            </div>
          </div>

          {suggestions.map((sug) => (
            <Card key={sug.id} outline className="studio-stack-xs">
              <div className="studio-msg-head">
                <span className="mg-label">{resolveAuthorName(sug.author, roster)}</span>
                {sug.reason ? <span className="studio-mono">{sug.reason}</span> : null}
              </div>
              <div className="studio-suggestion-diff">
                <div className="studio-suggestion-line">
                  <span className="studio-mono">Original: </span>
                  <del className="studio-diff-del">{sug.original || '(insertion)'}</del>
                </div>
                <div className="studio-suggestion-line">
                  <span className="studio-mono">Replacement: </span>
                  <ins className="studio-diff-ins">{sug.replacement || '(delete)'}</ins>
                </div>
              </div>
              <div className="studio-btn-group">
                <PillButton
                  size="sm"
                  disabled={busyAction !== null}
                  onClick={() => void onDecideSuggestion(sug.id, 'accept')}
                >
                  Accept
                </PillButton>
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={busyAction !== null}
                  onClick={() => void onDecideSuggestion(sug.id, 'reject')}
                >
                  Reject
                </PillButton>
              </div>
            </Card>
          ))}
        </section>
      ) : null}

      <section className="studio-stack-sm" aria-label="Comments">
        <div className="studio-section-bar">
          <h2 className="studio-section-title">Comments</h2>
          <div className="studio-btn-group" role="group" aria-label="Comment filter">
            <PillButton
              size="sm"
              variant={filter === 'open' ? 'primary' : 'outline'}
              onClick={() => onFilterChange('open')}
            >
              Open ({counts.open})
            </PillButton>
            <PillButton
              size="sm"
              variant={filter === 'resolved' ? 'primary' : 'outline'}
              onClick={() => onFilterChange('resolved')}
            >
              Resolved ({counts.resolved})
            </PillButton>
            <PillButton
              size="sm"
              variant={filter === 'all' ? 'primary' : 'outline'}
              onClick={() => onFilterChange('all')}
            >
              All ({counts.all})
            </PillButton>
          </div>
        </div>

        <Card className="studio-stack-xs">
          <div className="studio-section-bar">
            <span className="mg-label">Apply all open comments</span>
            <div className="studio-btn-group">
              <Select
                aria-label="Apply all mode"
                value={applyMode}
                onChange={(e) => setApplyMode(e.target.value as 'new_version' | 'suggestions')}
                className="studio-apply-select"
              >
                <option value="new_version">New version</option>
                <option value="suggestions">As suggestions</option>
              </Select>
              <PillButton
                size="sm"
                disabled={counts.open === 0 || busyAction !== null}
                onClick={() => void onApplyAll(applyMode === 'suggestions')}
              >
                Apply all
              </PillButton>
            </div>
          </div>

          {activeJob ? (
            <div className="studio-job-banner" role="status" aria-live="polite">
              <div className="studio-section-bar">
                <span className="studio-mono">
                  {activeJob.status} · {activeJob.progress}%
                </span>
                <span className="studio-mono">{activeJob.actor || activeJob.name}</span>
              </div>
              <div className="studio-hint">{activeJob.message || activeJob.error || 'Running...'}</div>
              <div className="studio-progress-track">
                <div
                  className="studio-progress-fill"
                  style={{ width: `${Math.max(0, Math.min(100, activeJob.progress ?? 0))}%` }}
                />
              </div>
            </div>
          ) : null}

          {inlineWarnings.length > 0 ? (
            <div className="mg-notice studio-stack-xs" role="status">
              <span className="mg-label">Reviewer notes (advisory)</span>
              {inlineWarnings.map((w, idx) => (
                <div key={`inline-warn-${idx}`} className="studio-hint">
                  {w.message}
                </div>
              ))}
            </div>
          ) : null}
        </Card>

        <Card outline className="studio-stack-xs">
          {pendingAnchor ? (
            <div className="studio-section-bar">
              <span className="studio-mono">{pendingAnchor.label}</span>
              <PillButton variant="quiet" size="sm" onClick={onClearPendingAnchor}>
                Clear anchor
              </PillButton>
            </div>
          ) : null}
          <MentionComposer
            value={composerText}
            onChange={onComposerTextChange}
            onSubmit={() => void handleMainSubmit()}
            agents={mentionOptions}
            placeholder={
              pendingAnchor
                ? 'Write a comment for this anchor (use @agent to ask an agent)...'
                : 'Add a review comment (use @writer, @reviewer, etc.)...'
            }
            submitLabel="Comment"
            sending={submitting}
            ariaLabel="New comment composer"
          />
        </Card>

        {visibleComments.length === 0 ? (
          <div className="mg-empty">No {filter === 'all' ? '' : `${filter} `}comments.</div>
        ) : (
          <div className="studio-stack-sm">
            {visibleComments.map((cmt) => {
              const isFocused = focusedCommentId === cmt.id;
              const isOwn = cmt.author === 'user';
              const isResolved = cmt.status !== 'open';
              const pinNum = imagePinNumbers.get(cmt.id);

              return (
                <div
                  key={cmt.id}
                  ref={(el) => {
                    commentCardRefs.current[cmt.id] = el;
                  }}
                >
                  <Card
                    selected={isFocused}
                    outline={!isFocused}
                    className="studio-stack-xs"
                    aria-current={isFocused ? 'true' : undefined}
                    onClick={() => onFocusComment(cmt.id)}
                  >
                  <div className="studio-msg-head">
                    <div className="studio-msg-author-row">
                      <span className="mg-label">{resolveAuthorName(cmt.author, roster)}</span>
                      <span className="mg-chip">{cmt.status}</span>
                      <span className="studio-mono">v{cmt.version}</span>
                    </div>
                    <span className="studio-mono">{formatTimestamp(cmt.created_at)}</span>
                  </div>

                  {cmt.kind === 'text' && cmt.anchor?.quote ? (
                    <div className="studio-anchor-note">
                      <span className="studio-mono">&ldquo;{cmt.anchor.quote}&rdquo;</span>
                      {cmt.anchor.orphaned ? (
                        <span className="mg-chip">
                          Orphaned — text changed in newer version
                        </span>
                      ) : null}
                    </div>
                  ) : null}

                  {cmt.kind === 'image' && cmt.anchor?.file ? (
                    <div className="studio-anchor-note">
                      <span className="mg-chip">
                        Pin #{pinNum ?? '?'} · {cmt.anchor.file} ({(cmt.anchor.x ?? 0).toFixed(2)},{' '}
                        {(cmt.anchor.y ?? 0).toFixed(2)})
                      </span>
                    </div>
                  ) : null}

                  {cmt.kind === 'video' && typeof cmt.anchor?.t === 'number' ? (
                    <div className="studio-anchor-note">
                      <button
                        type="button"
                        className="mg-chip studio-chip-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (cmt.anchor.file && typeof cmt.anchor.t === 'number') {
                            onSeekVideo(cmt.anchor.file, cmt.anchor.t);
                          }
                        }}
                      >
                        Seek {formatVideoTime(cmt.anchor.t)}
                      </button>
                    </div>
                  ) : null}

                  {editingId === cmt.id ? (
                    <div className="studio-stack-xs" onClick={(e) => e.stopPropagation()}>
                      <TextArea
                        rows={2}
                        aria-label="Edit comment body"
                        value={editingBody}
                        onChange={(e) => setEditingBody(e.target.value)}
                      />
                      <div className="studio-btn-group">
                        <PillButton
                          size="sm"
                          disabled={submitting || !editingBody.trim()}
                          onClick={() => void handleSaveEdit(cmt.id)}
                        >
                          Save
                        </PillButton>
                        <PillButton
                          variant="quiet"
                          size="sm"
                          onClick={() => {
                            setEditingId(null);
                            setEditingBody('');
                          }}
                        >
                          Cancel
                        </PillButton>
                      </div>
                    </div>
                  ) : (
                    <p className="studio-comment-body">{cmt.body}</p>
                  )}

                  {cmt.replies && cmt.replies.length > 0 ? (
                    <div className="studio-comment-replies">
                      {cmt.replies.map((rep) => (
                        <div key={rep.id} className="studio-comment-reply">
                          <div className="studio-msg-head">
                            <span className="mg-label">
                              {resolveAuthorName(rep.author, roster)}
                            </span>
                            <span className="studio-mono">{formatTimestamp(rep.created_at)}</span>
                          </div>
                          <p className="studio-comment-body">{rep.body}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="studio-btn-group" onClick={(e) => e.stopPropagation()}>
                    <PillButton
                      variant="quiet"
                      size="sm"
                      onClick={() => {
                        onFocusComment(cmt.id);
                        onReplyTargetChange(replyTargetId === cmt.id ? null : cmt.id);
                        setReplyText('');
                      }}
                    >
                      Reply
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyAction !== null}
                      onClick={() =>
                        void onUpdateComment(cmt.id, {
                          status: isResolved ? 'open' : 'resolved',
                        })
                      }
                    >
                      {isResolved ? 'Reopen' : 'Resolve'}
                    </PillButton>
                    {isOwn ? (
                      <>
                        <PillButton
                          variant="quiet"
                          size="sm"
                          onClick={() => {
                            setEditingId(cmt.id);
                            setEditingBody(cmt.body);
                          }}
                        >
                          Edit
                        </PillButton>
                        <PillButton
                          variant="danger"
                          size="sm"
                          disabled={busyAction !== null}
                          onClick={() => void onDeleteComment(cmt.id)}
                        >
                          Delete
                        </PillButton>
                      </>
                    ) : null}
                  </div>

                  {replyTargetId === cmt.id ? (
                    <div className="studio-reply-box" onClick={(e) => e.stopPropagation()}>
                      <MentionComposer
                        value={replyText}
                        onChange={setReplyText}
                        onSubmit={() => void handleReplySubmit(cmt.id, cmt.kind)}
                        agents={mentionOptions}
                        placeholder="Write a reply (@agent to mention)..."
                        submitLabel="Reply"
                        sending={submitting}
                        ariaLabel="Reply composer"
                      />
                    </div>
                  ) : null}
                  </Card>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
