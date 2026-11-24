import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { Card, Field, PillButton, TextArea, TextInput } from '@magnm/design/react';
import { contentFileUrl } from '../../api/review';
import type {
  ContentItem,
  PlatformCheckResult,
  ReviewComment,
  ReviewSuggestion,
} from '../../types';
import { PlatformPreview } from './PlatformPreview';
import { VideoRenderPanel } from './VideoRenderPanel';
import {
  BLOG_AI_ACTIONS,
  TEXT_AI_MORE,
  TEXT_AI_PRIMARY,
  buildBodySegments,
  computeDomSelectionOffsets,
  formatVideoTime,
} from './reviewUtils';

export interface TextSelectionAnchor {
  start: number;
  end: number;
  quote: string;
}

export interface VideoSeekRequest {
  file: string;
  t: number;
  seq: number;
}

export interface ContentPaneProps {
  item: ContentItem;
  body: string;
  currentVersion: number;
  comments: ReviewComment[];
  suggestions: ReviewSuggestion[];
  platformCheck: PlatformCheckResult;
  showPreviewToggle: boolean;
  mode: 'content' | 'preview';
  onModeChange: (mode: 'content' | 'preview') => void;
  selection: TextSelectionAnchor | null;
  onSelectionChange: (sel: TextSelectionAnchor | null) => void;
  moreMenuOpen: boolean;
  onMoreMenuOpenChange: (open: boolean) => void;
  onStartTextComment: (sel: TextSelectionAnchor, prefillMention?: boolean) => void;
  onRunAiAction: (action: string, sel?: TextSelectionAnchor | null, params?: Record<string, unknown>) => void;
  onSaveVersion: (nextBody: string, notes: string) => Promise<void>;
  onPickImagePin: (file: string, x: number, y: number) => void;
  onPickVideoTime: (file: string, t: number) => void;
  onFocusComment: (commentId: string) => void;
  videoSeekRequest: VideoSeekRequest | null;
  busyAction: string | null;
  /** Called when a render finished so the files attached to the item are reloaded. */
  onVideoChanged?: () => void;
}

function clamp01(val: number): number {
  if (!Number.isFinite(val)) return 0.5;
  return Math.round(Math.max(0, Math.min(1, val)) * 10000) / 10000;
}

function isVideoFile(kind: string | undefined, path: string): boolean {
  if (kind === 'video') return true;
  return /\.(mp4|webm|mov|m4v)$/i.test(path);
}

/** Left workspace pane: annotated text, selection toolbar, direct edit, image pins, video player, and preview toggle. */
export function ContentPane({
  item,
  body,
  currentVersion,
  comments,
  suggestions,
  platformCheck,
  showPreviewToggle,
  mode,
  onModeChange,
  selection,
  onSelectionChange,
  moreMenuOpen,
  onMoreMenuOpenChange,
  onStartTextComment,
  onRunAiAction,
  onSaveVersion,
  onPickImagePin,
  onPickVideoTime,
  onFocusComment,
  videoSeekRequest,
  busyAction,
  onVideoChanged,
}: ContentPaneProps) {
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState(body);
  const [versionNote, setVersionNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [videoTimes, setVideoTimes] = useState<Record<string, number>>({});

  const bodyViewRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const videoRefs = useRef<Record<string, HTMLVideoElement | null>>({});

  useEffect(() => {
    setDraftBody(body);
  }, [body, currentVersion]);

  useEffect(() => {
    if (!videoSeekRequest) return;
    const vid = videoRefs.current[videoSeekRequest.file];
    if (vid) {
      vid.currentTime = videoSeekRequest.t;
      setVideoTimes((prev) => ({ ...prev, [videoSeekRequest.file]: videoSeekRequest.t }));
      if (typeof vid.scrollIntoView === 'function') {
        vid.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [videoSeekRequest]);

  const { segments, trailingInsertions } = useMemo(
    () => buildBodySegments(body, comments, suggestions, currentVersion),
    [body, comments, suggestions, currentVersion],
  );

  const imageFiles = useMemo(
    () =>
      (item.files || []).filter(
        (f) => f && (f.kind === 'image' || f.kind === 'card') && Boolean(f.path),
      ),
    [item.files],
  );

  const videoFiles = useMemo(
    () =>
      (item.files || []).filter(
        (f) => f && Boolean(f.path) && isVideoFile(f.kind, f.path),
      ),
    [item.files],
  );

  const posterFile = useMemo(
    () => (item.files || []).find((f) => f && f.kind === 'poster' && Boolean(f.path)),
    [item.files],
  );

  const imagePinComments = useMemo(
    () =>
      comments.filter(
        (c) =>
          c.kind === 'image' &&
          typeof c.anchor?.file === 'string' &&
          typeof c.anchor?.x === 'number' &&
          typeof c.anchor?.y === 'number',
      ),
    [comments],
  );

  const isBlog =
    (item.type || '').toLowerCase() === 'blog' ||
    (item.platform || '').toLowerCase() === 'blog';

  const handleDomSelection = () => {
    if (editing || mode !== 'content') return;
    const sel = typeof window !== 'undefined' ? window.getSelection() : null;
    const computed = computeDomSelectionOffsets(bodyViewRef.current, body, sel);
    if (computed && computed.quote.trim()) {
      onSelectionChange(computed);
    }
  };

  const handleTextareaSelection = () => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart ?? 0;
    const end = el.selectionEnd ?? 0;
    if (end > start) {
      const source = draftBody;
      onSelectionChange({
        start,
        end,
        quote: source.slice(start, end),
      });
    }
  };

  const handleSaveEdit = async () => {
    setSaving(true);
    try {
      await onSaveVersion(draftBody, versionNote);
      setEditing(false);
      setVersionNote('');
      onSelectionChange(null);
    } finally {
      setSaving(false);
    }
  };

  const handleImageClick = (filePath: string, event: MouseEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = rect.width > 0 ? clamp01((event.clientX - rect.left) / rect.width) : 0.5;
    const y = rect.height > 0 ? clamp01((event.clientY - rect.top) / rect.height) : 0.5;
    onPickImagePin(filePath, x, y);
  };

  return (
    <div className="studio-stack-sm">
      <div className="studio-section-bar">
        {showPreviewToggle ? (
          <div className="studio-btn-group" role="group" aria-label="View mode">
            <PillButton
              size="sm"
              variant={mode === 'content' ? 'primary' : 'outline'}
              onClick={() => onModeChange('content')}
            >
              Content
            </PillButton>
            <PillButton
              size="sm"
              variant={mode === 'preview' ? 'primary' : 'outline'}
              onClick={() => onModeChange('preview')}
            >
              Preview
            </PillButton>
          </div>
        ) : (
          <span className="studio-section-title">Content</span>
        )}

        {mode === 'content' ? (
          <div className="studio-btn-group">
            {!editing ? (
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => {
                  setDraftBody(body);
                  setEditing(true);
                  onSelectionChange(null);
                }}
              >
                Edit text
              </PillButton>
            ) : (
              <PillButton
                variant="quiet"
                size="sm"
                onClick={() => {
                  setDraftBody(body);
                  setEditing(false);
                  onSelectionChange(null);
                }}
              >
                Cancel edit
              </PillButton>
            )}
          </div>
        ) : null}
      </div>

      {mode === 'preview' ? (
        <PlatformPreview item={item} body={body} platformCheck={platformCheck} />
      ) : (
        <>
          {selection ? (
            <Card outline className="studio-selection-toolbar" role="toolbar" aria-label="Selection actions">
              <div className="studio-selection-toolbar-top">
                <span className="studio-mono">
                  Selected [{selection.start}:{selection.end}]
                </span>
                <button
                  type="button"
                  className="mg-btn mg-btn--quiet mg-btn--sm"
                  onClick={() => {
                    onSelectionChange(null);
                    onMoreMenuOpenChange(false);
                  }}
                >
                  Clear
                </button>
              </div>
              <div className="studio-btn-group">
                <PillButton
                  size="sm"
                  onClick={() => onStartTextComment(selection, false)}
                >
                  Comment
                </PillButton>
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={() => onStartTextComment(selection, true)}
                >
                  @agent
                </PillButton>
                {TEXT_AI_PRIMARY.map((act) => (
                  <PillButton
                    key={act.id}
                    variant="outline"
                    size="sm"
                    disabled={busyAction !== null}
                    onClick={() => onRunAiAction(act.id, selection)}
                  >
                    {act.label}
                  </PillButton>
                ))}
                <div className="studio-more-wrap">
                  <PillButton
                    variant="outline"
                    size="sm"
                    aria-expanded={moreMenuOpen}
                    aria-haspopup="menu"
                    onClick={() => onMoreMenuOpenChange(!moreMenuOpen)}
                  >
                    More...
                  </PillButton>
                  {moreMenuOpen ? (
                    <div className="studio-more-menu" role="menu" aria-label="More AI actions">
                      {TEXT_AI_MORE.map((act) => (
                        <button
                          key={act.id}
                          type="button"
                          role="menuitem"
                          className="studio-more-item"
                          disabled={busyAction !== null}
                          onClick={() => {
                            onMoreMenuOpenChange(false);
                            onRunAiAction(act.id, selection);
                          }}
                        >
                          {act.label}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            </Card>
          ) : null}

          <Card outline>
            {editing ? (
              <div className="studio-stack-sm">
                <Field label={`Editing v${currentVersion}`} htmlFor="review-body-editor">
                  <TextArea
                    ref={textareaRef}
                    id="review-body-editor"
                    rows={12}
                    value={draftBody}
                    onChange={(e) => setDraftBody(e.target.value)}
                    onSelect={handleTextareaSelection}
                    onMouseUp={handleTextareaSelection}
                    onKeyUp={handleTextareaSelection}
                  />
                </Field>
                <div className="studio-inline-form">
                  <TextInput
                    aria-label="Version note"
                    placeholder="Short note for this version (optional)"
                    value={versionNote}
                    onChange={(e) => setVersionNote(e.target.value)}
                  />
                  <PillButton
                    size="sm"
                    disabled={saving}
                    onClick={() => void handleSaveEdit()}
                  >
                    {saving ? 'Saving...' : 'Save new version'}
                  </PillButton>
                </div>
              </div>
            ) : (
              <div className="studio-stack-sm">
                <div
                  ref={bodyViewRef}
                  className="studio-review-body"
                  data-testid="review-body-view"
                  onMouseUp={handleDomSelection}
                  onKeyUp={handleDomSelection}
                >
                  {body.length === 0 ? (
                    <span className="studio-hint">Empty body. Click Edit text to write content.</span>
                  ) : (
                    <>
                      {segments.map((seg) => {
                        if (seg.start === seg.end && seg.suggestion) {
                          return (
                            <ins
                              key={seg.key}
                              data-suggestion-ins="true"
                              className="studio-diff-ins"
                            >
                              {seg.suggestion.replacement}
                            </ins>
                          );
                        }
                        if (seg.suggestion) {
                          return (
                            <span key={seg.key}>
                              <del
                                data-raw-start={seg.start}
                                data-raw-end={seg.end}
                                className="studio-diff-del"
                              >
                                {seg.text}
                              </del>
                              {seg.suggestion.replacement ? (
                                <ins data-suggestion-ins="true" className="studio-diff-ins">
                                  {seg.suggestion.replacement}
                                </ins>
                              ) : null}
                            </span>
                          );
                        }
                        if (seg.commentIds.length > 0) {
                          return (
                            <mark
                              key={seg.key}
                              data-raw-start={seg.start}
                              data-raw-end={seg.end}
                              className="studio-review-highlight"
                              onClick={() => onFocusComment(seg.commentIds[0])}
                            >
                              {seg.text}
                            </mark>
                          );
                        }
                        return (
                          <span key={seg.key} data-raw-start={seg.start} data-raw-end={seg.end}>
                            {seg.text}
                          </span>
                        );
                      })}
                      {trailingInsertions.map((sug) => (
                        <ins key={`trail-${sug.id}`} data-suggestion-ins="true" className="studio-diff-ins">
                          {sug.replacement}
                        </ins>
                      ))}
                    </>
                  )}
                </div>

                <div className="studio-review-doc-actions">
                  <span className="studio-mono">
                    {body.length} chars · Select text to comment or run AI edits
                  </span>
                  <div className="studio-btn-group">
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyAction !== null}
                      onClick={() => onRunAiAction('stronger_hook', null)}
                    >
                      Stronger hook
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyAction !== null}
                      onClick={() => onRunAiAction('fit_platform', null)}
                    >
                      Fit platform
                    </PillButton>
                    {isBlog
                      ? BLOG_AI_ACTIONS.map((act) => (
                          <PillButton
                            key={act.id}
                            variant="outline"
                            size="sm"
                            disabled={busyAction !== null}
                            onClick={() => onRunAiAction(act.id, null)}
                          >
                            {act.label}
                          </PillButton>
                        ))
                      : null}
                  </div>
                </div>
              </div>
            )}
          </Card>

          {imageFiles.length > 0 ? (
            <section className="studio-stack-sm" aria-label="Attached images">
              <h3 className="studio-section-title">Images (click to pin a comment)</h3>
              {imageFiles.map((entry) => {
                const pins = imagePinComments.filter((c) => c.anchor.file === entry.path);
                return (
                  <Card key={entry.path} className="studio-stack-xs">
                    <div className="studio-section-bar">
                      <span className="studio-mono">{entry.path}</span>
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={busyAction !== null}
                        onClick={() => onRunAiAction('alt_text', null, { file: entry.path })}
                      >
                        Generate alt text
                      </PillButton>
                    </div>
                    <div className="studio-review-media-frame">
                      <img
                        src={contentFileUrl(item.id, entry.path)}
                        alt={entry.alt_text || entry.path}
                        className="studio-review-image"
                        onClick={(e) => handleImageClick(entry.path, e)}
                      />
                      {pins.map((pin, idx) => (
                        <button
                          key={pin.id}
                          type="button"
                          className="studio-image-pin"
                          style={{
                            left: `${(pin.anchor.x ?? 0.5) * 100}%`,
                            top: `${(pin.anchor.y ?? 0.5) * 100}%`,
                          }}
                          aria-label={`Pin ${idx + 1}: ${pin.body}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onFocusComment(pin.id);
                          }}
                        >
                          {idx + 1}
                        </button>
                      ))}
                    </div>
                    <div className="studio-hint">
                      Alt text: {entry.alt_text ? entry.alt_text : 'Missing'}
                    </div>
                  </Card>
                );
              })}
            </section>
          ) : null}

          {item.type === 'script' ? (
            <VideoRenderPanel contentId={item.id} hasVideo={videoFiles.length > 0} onChanged={onVideoChanged} />
          ) : null}

          {videoFiles.length > 0 ? (
            <section className="studio-stack-sm" aria-label="Attached videos">
              <h3 className="studio-section-title">Video</h3>
              {videoFiles.map((entry) => {
                const curTime = videoTimes[entry.path] ?? 0;
                return (
                  <Card key={entry.path} className="studio-stack-xs">
                    <div className="studio-section-bar">
                      <span className="studio-mono">{entry.path}</span>
                      <PillButton
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          const vid = videoRefs.current[entry.path];
                          const actualTime = vid && Number.isFinite(vid.currentTime) ? vid.currentTime : curTime;
                          onPickVideoTime(entry.path, actualTime);
                        }}
                      >
                        Comment at {formatVideoTime(curTime)}
                      </PillButton>
                    </div>
                    <video
                      ref={(el) => {
                        videoRefs.current[entry.path] = el;
                      }}
                      controls
                      preload="metadata"
                      poster={posterFile ? contentFileUrl(item.id, posterFile.path) : undefined}
                      src={contentFileUrl(item.id, entry.path)}
                      className="studio-review-video"
                      onTimeUpdate={(e) => {
                        const t = e.currentTarget.currentTime;
                        setVideoTimes((prev) => ({ ...prev, [entry.path]: t }));
                      }}
                      onSeeked={(e) => {
                        const t = e.currentTarget.currentTime;
                        setVideoTimes((prev) => ({ ...prev, [entry.path]: t }));
                      }}
                    />
                  </Card>
                );
              })}
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
