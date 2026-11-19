import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BottomBar,
  NumberedTabs,
  PillButton,
  SheetHeader,
  StatusDot,
} from '@magnm/design/react';
import {
  applyAllComments,
  buildPostingKit,
  createReviewComment,
  decideAllSuggestions,
  decideSuggestion,
  deleteReviewComment,
  diffReviewVersions,
  getReview,
  markReviewPosted,
  markReviewReady,
  restoreReviewVersion,
  runAiAction,
  saveReviewVersion,
  updateChecklistItem,
  updateReviewComment,
} from '../../api/review';
import { getRoster, type RosterAgent } from '../../api/team';
import { useFallbackPoll, useStudioEvents } from '../../events';
import type {
  JobRecord,
  PostingKitResult,
  ReviewCommentAnchor,
  ReviewDiffResponse,
  ReviewFinding,
  ReviewPayload,
} from '../../types';
import {
  CommentsSuggestionsPane,
  type PendingAnchorDraft,
} from './CommentsSuggestionsPane';
import {
  ContentPane,
  type TextSelectionAnchor,
  type VideoSeekRequest,
} from './ContentPane';
import {
  MarkPostedDialog,
  PostingKitDialog,
  RestoreConfirmDialog,
  ShortcutsDialog,
} from './ReviewDialogs';
import { ChecklistPanel, VersionsPanel } from './ReviewRails';
import { PlatformPreview } from './PlatformPreview';
import { isEditableElement, mapContentStatusState } from './reviewUtils';

export interface ReviewWorkspaceProps {
  contentId: string;
  onBackToList: () => void;
}

type LayoutMode = 'wide' | 'medium' | 'narrow';
type NarrowTabId = 'content' | 'comments' | 'checklist' | 'versions' | 'preview';
type MediumTabId = 'comments' | 'checklist' | 'versions';

function readLayoutMode(): LayoutMode {
  if (typeof window === 'undefined') return 'wide';
  if (typeof window.matchMedia === 'function') {
    if (window.matchMedia('(max-width: 899px)').matches) return 'narrow';
    if (window.matchMedia('(max-width: 1199px)').matches) return 'medium';
    return 'wide';
  }
  const width = window.innerWidth;
  if (width > 0 && width < 900) return 'narrow';
  return 'wide';
}

function useLayoutMode(): LayoutMode {
  const [mode, setMode] = useState<LayoutMode>(readLayoutMode);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sync = () => setMode(readLayoutMode());
    sync();
    window.addEventListener('resize', sync);

    const mqs: MediaQueryList[] = [];
    if (typeof window.matchMedia === 'function') {
      for (const q of ['(max-width: 899px)', '(max-width: 1199px)']) {
        const mq = window.matchMedia(q);
        mqs.push(mq);
        if (typeof mq.addEventListener === 'function') {
          mq.addEventListener('change', sync);
        }
      }
    }

    return () => {
      window.removeEventListener('resize', sync);
      for (const mq of mqs) {
        if (typeof mq.removeEventListener === 'function') {
          mq.removeEventListener('change', sync);
        }
      }
    };
  }, []);

  return mode;
}

const NARROW_TABS: Array<{ id: NarrowTabId; label: string }> = [
  { id: 'content', label: 'Content' },
  { id: 'comments', label: 'Comments' },
  { id: 'checklist', label: 'Checklist' },
  { id: 'versions', label: 'Versions' },
  { id: 'preview', label: 'Preview' },
];

/** Review Studio workspace (#/review/<id>) with responsive 3-area / tabbed / numbered mobile layout. */
export function ReviewWorkspace({ contentId, onBackToList }: ReviewWorkspaceProps) {
  const layoutMode = useLayoutMode();

  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [desktopMode, setDesktopMode] = useState<'content' | 'preview'>('content');
  const [mediumTab, setMediumTab] = useState<MediumTabId>('comments');
  const [narrowTab, setNarrowTab] = useState<NarrowTabId>('content');

  const [commentFilter, setCommentFilter] = useState<'open' | 'resolved' | 'all'>('open');
  const [focusedCommentId, setFocusedCommentId] = useState<string | null>(null);
  const [replyTargetId, setReplyTargetId] = useState<string | null>(null);
  const [pendingAnchor, setPendingAnchor] = useState<PendingAnchorDraft | null>(null);
  const [composerText, setComposerText] = useState('');

  const [selection, setSelection] = useState<TextSelectionAnchor | null>(null);
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);

  const [diff, setDiff] = useState<ReviewDiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [restoreVersionTarget, setRestoreVersionTarget] = useState<number | null>(null);

  const [postingKit, setPostingKit] = useState<PostingKitResult | null>(null);
  const [postingKitOpen, setPostingKitOpen] = useState(false);
  const [postedDialogOpen, setPostedDialogOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [trackedJobIds, setTrackedJobIds] = useState<string[]>([]);
  const [inlineWarnings, setInlineWarnings] = useState<ReviewFinding[]>([]);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [videoSeekRequest, setVideoSeekRequest] = useState<VideoSeekRequest | null>(null);

  const loadWorkspace = useCallback(async () => {
    setError(null);
    try {
      const [revRes, rosterRes] = await Promise.allSettled([
        getReview(contentId),
        getRoster(),
      ]);
      if (revRes.status === 'fulfilled') {
        setReview(revRes.value);
      } else {
        setError(revRes.reason instanceof Error ? revRes.reason.message : 'Could not load review.');
      }
      if (rosterRes.status === 'fulfilled' && Array.isArray(rosterRes.value)) {
        setRoster(rosterRes.value);
      }
    } finally {
      setLoading(false);
    }
  }, [contentId]);

  useEffect(() => {
    setLoading(true);
    setDiff(null);
    setSelection(null);
    setPendingAnchor(null);
    setFocusedCommentId(null);
    setReplyTargetId(null);
    setInlineWarnings([]);
    setActiveJob(null);
    setTrackedJobIds([]);
    void loadWorkspace();
  }, [loadWorkspace]);

  const reloadReviewOnly = useCallback(async () => {
    try {
      const next = await getReview(contentId);
      setReview(next);
    } catch {
      // Keep current state on background refresh errors.
    }
  }, [contentId]);

  const eventsState = useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (eventName === 'review.updated' && data && typeof data === 'object') {
          const payload = data as { content_id?: string; warnings?: ReviewFinding[] };
          if (payload.content_id === contentId) {
            if (Array.isArray(payload.warnings)) {
              setInlineWarnings(payload.warnings);
            }
            void reloadReviewOnly();
          }
        }
        if (eventName === 'job' && data && typeof data === 'object') {
          const job = data as JobRecord;
          if (
            trackedJobIds.includes(job.id) ||
            (job.name === 'review_action' && activeJob?.id === job.id)
          ) {
            setActiveJob(job);
            if (job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') {
              void reloadReviewOnly();
            }
          }
        }
      },
      [contentId, trackedJobIds, activeJob?.id, reloadReviewOnly],
    ),
  );

  const hasActiveJob =
    activeJob !== null && (activeJob.status === 'queued' || activeJob.status === 'running');

  useFallbackPoll(
    useCallback(() => {
      void reloadReviewOnly();
    }, [reloadReviewOnly]),
    hasActiveJob,
    eventsState,
    5000,
  );

  const visibleComments = useMemo(() => {
    const all = review?.comments ?? [];
    if (commentFilter === 'open') return all.filter((c) => c.status === 'open');
    if (commentFilter === 'resolved') return all.filter((c) => c.status !== 'open');
    return all;
  }, [review?.comments, commentFilter]);

  const trackJob = (job: JobRecord | undefined, fallbackId?: string) => {
    const jid = job?.id || fallbackId;
    if (!jid) return;
    setTrackedJobIds((prev) => (prev.includes(jid) ? prev : [...prev, jid]));
    if (job) {
      setActiveJob(job);
    } else {
      setActiveJob({
        id: jid,
        name: 'review_action',
        status: 'queued',
        progress: 0,
        message: 'Queued...',
      });
    }
  };

  const handleSaveVersion = async (nextBody: string, notes: string) => {
    setError(null);
    setBusyAction('save-version');
    try {
      await saveReviewVersion(contentId, nextBody, notes);
      await reloadReviewOnly();
      setNotice('Saved new version.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save version.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleStartTextComment = (sel: TextSelectionAnchor, prefillMention = false) => {
    setPendingAnchor({
      kind: 'text',
      anchor: { start: sel.start, end: sel.end, quote: sel.quote },
      label: `Text [${sel.start}:${sel.end}] "${sel.quote.slice(0, 48)}${sel.quote.length > 48 ? '...' : ''}"`,
    });
    if (prefillMention && !composerText.trim()) {
      setComposerText('@');
    }
    setSelection(null);
    setMoreMenuOpen(false);
    if (layoutMode === 'narrow') setNarrowTab('comments');
    if (layoutMode === 'medium') setMediumTab('comments');
  };

  const handlePickImagePin = (file: string, x: number, y: number) => {
    setPendingAnchor({
      kind: 'image',
      anchor: { file, x, y },
      label: `Image pin on ${file} (${x.toFixed(2)}, ${y.toFixed(2)})`,
    });
    if (layoutMode === 'narrow') setNarrowTab('comments');
    if (layoutMode === 'medium') setMediumTab('comments');
  };

  const handlePickVideoTime = (file: string, t: number) => {
    const rounded = Math.round(t * 100) / 100;
    setPendingAnchor({
      kind: 'video',
      anchor: { file, t: rounded },
      label: `Video timestamp on ${file} at ${Math.floor(rounded)}s`,
    });
    if (layoutMode === 'narrow') setNarrowTab('comments');
    if (layoutMode === 'medium') setMediumTab('comments');
  };

  const handleFocusComment = (commentId: string) => {
    setFocusedCommentId(commentId);
    if (layoutMode === 'narrow') setNarrowTab('comments');
    if (layoutMode === 'medium') setMediumTab('comments');
  };

  const handleSubmitComment = async (
    body: string,
    kind: 'text' | 'image' | 'video' | 'general',
    anchor?: ReviewCommentAnchor,
    parentId?: string,
  ) => {
    setError(null);
    setBusyAction('comment');
    try {
      const created = await createReviewComment(contentId, {
        kind,
        anchor,
        body,
        parent_id: parentId ?? null,
      });
      if (created.job_id) {
        trackJob(undefined, created.job_id);
      }
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post comment.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleUpdateComment = useCallback(
    async (commentId: string, payload: { body?: string; status?: 'open' | 'resolved' | 'applied' }) => {
      setError(null);
      setBusyAction('update-comment');
      try {
        await updateReviewComment(contentId, commentId, payload);
        await reloadReviewOnly();
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to update comment.');
      } finally {
        setBusyAction(null);
      }
    },
    [contentId, reloadReviewOnly],
  );

  const handleDeleteComment = async (commentId: string) => {
    setError(null);
    setBusyAction('delete-comment');
    try {
      await deleteReviewComment(contentId, commentId);
      if (focusedCommentId === commentId) setFocusedCommentId(null);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete comment.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleRunAiAction = async (
    action: string,
    sel?: TextSelectionAnchor | null,
    params?: Record<string, unknown>,
  ) => {
    setError(null);
    setNotice(null);
    setBusyAction(`ai:${action}`);
    try {
      const res = await runAiAction(
        contentId,
        action,
        sel ? { start: sel.start, end: sel.end, quote: sel.quote } : undefined,
        params,
      );
      trackJob(res.job, res.job_id);
      setSelection(null);
      setMoreMenuOpen(false);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start AI action.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleApplyAll = async (asSuggestions: boolean) => {
    setError(null);
    setNotice(null);
    setBusyAction('apply_all');
    try {
      const res = await applyAllComments(contentId, asSuggestions);
      trackJob(res.job, res.job_id);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run Apply all.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleDecideSuggestion = async (suggestionId: string, decision: 'accept' | 'reject') => {
    setError(null);
    setBusyAction(`sug:${suggestionId}`);
    try {
      await decideSuggestion(contentId, suggestionId, decision);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update suggestion.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleDecideAllSuggestions = async (decision: 'accept' | 'reject') => {
    setError(null);
    setBusyAction(`sug-all:${decision}`);
    try {
      await decideAllSuggestions(contentId, decision);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update suggestions.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleToggleChecklist = async (itemKey: string, nextChecked: boolean) => {
    setError(null);
    try {
      const res = await updateChecklistItem(contentId, {
        item_key: itemKey,
        checked: nextChecked,
      });
      setReview((prev) => (prev ? { ...prev, checklist: res.checklist } : prev));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update checklist.');
    }
  };

  const handleAddChecklist = async (label: string) => {
    setError(null);
    try {
      const res = await updateChecklistItem(contentId, { label, checked: false });
      setReview((prev) => (prev ? { ...prev, checklist: res.checklist } : prev));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add checklist item.');
    }
  };

  const handleCompareVersions = async (fromVer: number, toVer: number) => {
    setError(null);
    setDiffLoading(true);
    try {
      const res = await diffReviewVersions(contentId, fromVer, toVer);
      setDiff(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load diff.');
    } finally {
      setDiffLoading(false);
    }
  };

  const handleConfirmRestore = async (version: number) => {
    setError(null);
    setBusyAction('restore');
    try {
      await restoreReviewVersion(contentId, version);
      setRestoreVersionTarget(null);
      await reloadReviewOnly();
      setNotice(`Restored v${version} as a new version.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to restore version.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleMarkReady = async () => {
    setError(null);
    setBusyAction('ready');
    try {
      const res = await markReviewReady(contentId);
      setInlineWarnings(res.warnings || []);
      await reloadReviewOnly();
      setNotice(
        res.warnings && res.warnings.length > 0
          ? `Marked ready with ${res.warnings.length} advisory warning(s).`
          : 'Marked ready to post.',
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to mark ready.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleBuildPostingKit = async () => {
    setError(null);
    setBusyAction('posting_kit');
    try {
      const kit = await buildPostingKit(contentId);
      setPostingKit(kit);
      setPostingKitOpen(true);
      await reloadReviewOnly();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to build posting kit.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleMarkPosted = async (url: string, platform: string) => {
    setError(null);
    setBusyAction('posted');
    try {
      await markReviewPosted(contentId, url, platform);
      setPostedDialogOpen(false);
      await reloadReviewOnly();
      setNotice('Marked as posted.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to mark as posted.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleSeekVideo = (file: string, t: number) => {
    setVideoSeekRequest((prev) => ({ file, t, seq: (prev?.seq ?? 0) + 1 }));
    if (layoutMode === 'narrow') setNarrowTab('content');
    if (layoutMode !== 'narrow') setDesktopMode('content');
  };

  // Keyboard shortcuts: J/K navigate comments, R reply, E resolve/reopen, ? help, Escape closes popovers.
  useEffect(() => {
    const anyModalOpen =
      postingKitOpen || postedDialogOpen || restoreVersionTarget !== null || shortcutsOpen;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!event.defaultPrevented) {
          setMoreMenuOpen(false);
          setSelection(null);
          setReplyTargetId(null);
          setShortcutsOpen(false);
        }
        return;
      }

      if (anyModalOpen) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isEditableElement(event.target)) return;

      const key = event.key.toLowerCase();
      if (event.key === '?') {
        event.preventDefault();
        setShortcutsOpen((prev) => !prev);
        return;
      }

      if (key === 'j' || key === 'k') {
        if (visibleComments.length === 0) return;
        event.preventDefault();
        const curIdx = visibleComments.findIndex((c) => c.id === focusedCommentId);
        const nextIdx =
          key === 'j'
            ? curIdx < 0
              ? 0
              : Math.min(visibleComments.length - 1, curIdx + 1)
            : curIdx < 0
              ? visibleComments.length - 1
              : Math.max(0, curIdx - 1);
        setFocusedCommentId(visibleComments[nextIdx].id);
        return;
      }

      if (key === 'r') {
        const target =
          visibleComments.find((c) => c.id === focusedCommentId) || visibleComments[0];
        if (!target) return;
        event.preventDefault();
        setFocusedCommentId(target.id);
        setReplyTargetId(target.id);
        if (layoutMode === 'narrow') setNarrowTab('comments');
        if (layoutMode === 'medium') setMediumTab('comments');
        return;
      }

      if (key === 'e') {
        const target =
          visibleComments.find((c) => c.id === focusedCommentId) || visibleComments[0];
        if (!target) return;
        event.preventDefault();
        void handleUpdateComment(target.id, {
          status: target.status === 'open' ? 'resolved' : 'open',
        });
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [
    postingKitOpen,
    postedDialogOpen,
    restoreVersionTarget,
    shortcutsOpen,
    visibleComments,
    focusedCommentId,
    layoutMode,
    handleUpdateComment,
  ]);

  if (loading && !review) {
    return (
      <div className="studio-stack">
        <SheetHeader page={6} total={9} subtitle="Review" title="Loading..." />
        <div className="mg-empty">Loading review workspace...</div>
      </div>
    );
  }

  if (!review) {
    return (
      <div className="studio-stack">
        <SheetHeader page={6} total={9} subtitle="Review" title="Review not found" />
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}
        <div>
          <PillButton variant="outline" size="sm" onClick={onBackToList}>
            Back to Review list
          </PillButton>
        </div>
      </div>
    );
  }

  const warnings = review.warnings || [];
  const productLabel = review.product?.name || review.item.product_id || 'General';
  const platformLabel = review.item.platform || 'general';

  const contentPaneEl = (
    <ContentPane
      item={review.item}
      body={review.body}
      currentVersion={review.current_version}
      comments={review.comments}
      suggestions={review.suggestions}
      platformCheck={review.platform_check}
      showPreviewToggle={layoutMode !== 'narrow'}
      mode={desktopMode}
      onModeChange={setDesktopMode}
      selection={selection}
      onSelectionChange={setSelection}
      moreMenuOpen={moreMenuOpen}
      onMoreMenuOpenChange={setMoreMenuOpen}
      onStartTextComment={handleStartTextComment}
      onRunAiAction={(act, sel, params) => void handleRunAiAction(act, sel, params)}
      onSaveVersion={handleSaveVersion}
      onPickImagePin={handlePickImagePin}
      onPickVideoTime={handlePickVideoTime}
      onFocusComment={handleFocusComment}
      videoSeekRequest={videoSeekRequest}
      busyAction={busyAction}
    />
  );

  const commentsPaneEl = (
    <CommentsSuggestionsPane
      comments={review.comments}
      suggestions={review.suggestions}
      roster={roster}
      filter={commentFilter}
      onFilterChange={setCommentFilter}
      focusedCommentId={focusedCommentId}
      onFocusComment={setFocusedCommentId}
      replyTargetId={replyTargetId}
      onReplyTargetChange={setReplyTargetId}
      pendingAnchor={pendingAnchor}
      onClearPendingAnchor={() => setPendingAnchor(null)}
      composerText={composerText}
      onComposerTextChange={setComposerText}
      onSubmitComment={handleSubmitComment}
      onUpdateComment={handleUpdateComment}
      onDeleteComment={handleDeleteComment}
      onDecideSuggestion={handleDecideSuggestion}
      onDecideAllSuggestions={handleDecideAllSuggestions}
      onApplyAll={handleApplyAll}
      onSeekVideo={handleSeekVideo}
      activeJob={activeJob}
      inlineWarnings={inlineWarnings}
      busyAction={busyAction}
    />
  );

  const checklistEl = (
    <ChecklistPanel
      checklist={review.checklist}
      warnings={warnings}
      onToggleItem={handleToggleChecklist}
      onAddManualItem={handleAddChecklist}
    />
  );

  const versionsEl = (
    <VersionsPanel
      versions={review.versions}
      currentVersion={review.current_version}
      roster={roster}
      diff={diff}
      diffLoading={diffLoading}
      onCompare={(fromVer, toVer) => void handleCompareVersions(fromVer, toVer)}
      onRequestRestore={(ver) => setRestoreVersionTarget(ver)}
    />
  );

  return (
    <div className="studio-stack studio-review-workspace">
      <div className="studio-section-bar">
        <PillButton variant="outline" size="sm" onClick={onBackToList}>
          Back to Review
        </PillButton>
        <div className="studio-btn-group">
          {review.confidential ? <span className="mg-chip">Local AI only</span> : null}
          <PillButton
            variant="quiet"
            size="sm"
            onClick={() => setShortcutsOpen(true)}
          >
            Shortcuts (?)
          </PillButton>
        </div>
      </div>

      <SheetHeader
        page={6}
        total={9}
        subtitle={
          <span className="studio-review-header-meta">
            <span>{productLabel}</span>
            <span>·</span>
            <span>{platformLabel}</span>
            <span>·</span>
            <span>v{review.current_version}</span>
            <span>·</span>
            <StatusDot
              state={mapContentStatusState(review.item.status)}
              label={review.item.status}
            />
          </span>
        }
        title={review.item.title || review.item.id}
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      {layoutMode === 'narrow' ? (
        <div className="studio-stack-sm" data-testid="review-narrow-layout">
          <NumberedTabs
            items={NARROW_TABS}
            current={narrowTab}
            onSelect={(id) => setNarrowTab(id as NarrowTabId)}
            ariaLabel="Review sections"
          />
          {narrowTab === 'content' ? contentPaneEl : null}
          {narrowTab === 'comments' ? commentsPaneEl : null}
          {narrowTab === 'checklist' ? checklistEl : null}
          {narrowTab === 'versions' ? versionsEl : null}
          {narrowTab === 'preview' ? (
            <PlatformPreview
              item={review.item}
              body={review.body}
              platformCheck={review.platform_check}
            />
          ) : null}
        </div>
      ) : layoutMode === 'medium' ? (
        <div className="studio-review-grid-medium" data-testid="review-medium-layout">
          <div className="studio-review-col">{contentPaneEl}</div>
          <div className="studio-review-col studio-stack-sm">
            <div className="studio-btn-group" role="tablist" aria-label="Review side tabs">
              <PillButton
                size="sm"
                role="tab"
                aria-selected={mediumTab === 'comments'}
                variant={mediumTab === 'comments' ? 'primary' : 'outline'}
                onClick={() => setMediumTab('comments')}
              >
                Comments ({review.comments.length})
              </PillButton>
              <PillButton
                size="sm"
                role="tab"
                aria-selected={mediumTab === 'checklist'}
                variant={mediumTab === 'checklist' ? 'primary' : 'outline'}
                onClick={() => setMediumTab('checklist')}
              >
                Checklist
              </PillButton>
              <PillButton
                size="sm"
                role="tab"
                aria-selected={mediumTab === 'versions'}
                variant={mediumTab === 'versions' ? 'primary' : 'outline'}
                onClick={() => setMediumTab('versions')}
              >
                Versions ({review.versions.length})
              </PillButton>
            </div>
            {mediumTab === 'comments' ? commentsPaneEl : null}
            {mediumTab === 'checklist' ? checklistEl : null}
            {mediumTab === 'versions' ? versionsEl : null}
          </div>
        </div>
      ) : (
        <div className="studio-review-grid-wide" data-testid="review-wide-layout">
          <div className="studio-review-col">{contentPaneEl}</div>
          <div className="studio-review-col">{commentsPaneEl}</div>
          <aside className="studio-review-rail studio-stack" aria-label="Checklist and versions">
            {checklistEl}
            {versionsEl}
          </aside>
        </div>
      )}

      <BottomBar
        caption={
          warnings.length > 0
            ? `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`
            : 'Ready to post'
        }
        value={`v${review.current_version} · ${review.item.status}`}
      >
        <PillButton
          variant="outline"
          size="sm"
          disabled={busyAction !== null}
          onClick={() => void handleMarkReady()}
        >
          Mark ready
        </PillButton>
        <PillButton
          variant="outline"
          size="sm"
          disabled={busyAction !== null}
          onClick={() => void handleBuildPostingKit()}
        >
          Build posting kit
        </PillButton>
        <PillButton
          variant="outline"
          size="sm"
          disabled
          title="Available after phone pairing (Phase 4)"
        >
          Send to my phone
        </PillButton>
        <PillButton
          size="sm"
          disabled={busyAction !== null}
          onClick={() => setPostedDialogOpen(true)}
        >
          Mark as posted
        </PillButton>
      </BottomBar>

      <PostingKitDialog
        open={postingKitOpen}
        onClose={() => setPostingKitOpen(false)}
        kit={postingKit}
        item={review.item}
        body={review.body}
      />

      <MarkPostedDialog
        open={postedDialogOpen}
        onClose={() => setPostedDialogOpen(false)}
        defaultPlatform={review.item.platform || 'linkedin'}
        submitting={busyAction === 'posted'}
        onSubmit={(url, plat) => void handleMarkPosted(url, plat)}
      />

      <RestoreConfirmDialog
        version={restoreVersionTarget}
        onClose={() => setRestoreVersionTarget(null)}
        submitting={busyAction === 'restore'}
        onConfirm={(ver) => void handleConfirmRestore(ver)}
      />

      <ShortcutsDialog
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
      />
    </div>
  );
}
