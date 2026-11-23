import { useState } from 'react';
import {
  Card,
  Field,
  Label,
  PillButton,
  Row,
  RowList,
  Select,
  TextArea,
  TextInput,
} from '@magnm/design/react';
import { stateLabel } from '../actions';
import { copyExpiryLabel, formatCopySize, isCopyUsable, type PhoneCopy } from '../phoneCopies';
import type { DraftComment, DraftFileEntry, DraftItem, PhoneAction, QueueItemKind } from '../types';

const SHAREABLE_EXTENSIONS = ['.mp4', '.webm', '.png', '.jpg', '.jpeg', '.webp', '.pdf'];

export interface ShareableDraftFile {
  path: string;
  label: string;
  kind: string;
}

export function extractShareableDraftFiles(files: DraftFileEntry[] | undefined): ShareableDraftFile[] {
  if (!Array.isArray(files)) return [];
  const out: ShareableDraftFile[] = [];
  const seen = new Set<string>();
  for (const entry of files) {
    const rawPath = typeof entry === 'string' ? entry : entry?.path;
    if (typeof rawPath !== 'string') continue;
    const cleanPath = rawPath.trim();
    if (!cleanPath || seen.has(cleanPath)) continue;
    const lower = cleanPath.toLowerCase();
    if (!SHAREABLE_EXTENSIONS.some((ext) => lower.endsWith(ext))) continue;
    seen.add(cleanPath);
    const parts = cleanPath.split(/[\\/]/);
    const label = parts[parts.length - 1] || cleanPath;
    const kind = typeof entry === 'object' && entry?.kind ? String(entry.kind) : 'file';
    out.push({ path: cleanPath, label, kind });
  }
  return out;
}

export interface DraftFileRow {
  /** The path as the laptop knows it (live file) or as the cloud copy was made. */
  path: string;
  label: string;
  kind: string;
  /** The laptop lists this file on the draft right now. */
  live: boolean;
  copy: PhoneCopy | null;
}

function normalizeFilePath(raw: string): string {
  return raw.replace(/\\/g, '/').replace(/^(\.\/)+/, '').replace(/\/{2,}/g, '/');
}

/** Files on the draft (from the laptop) joined with their cloud copies; a copy alone is still listed. */
export function mergeFileRows(live: ShareableDraftFile[], copies: PhoneCopy[]): DraftFileRow[] {
  const byPath = new Map<string, PhoneCopy>();
  for (const copy of copies) byPath.set(normalizeFilePath(copy.file_path), copy);
  const rows: DraftFileRow[] = [];
  const seen = new Set<string>();
  for (const file of live) {
    const key = normalizeFilePath(file.path);
    seen.add(key);
    rows.push({ path: file.path, label: file.label, kind: file.kind, live: true, copy: byPath.get(key) ?? null });
  }
  for (const copy of copies) {
    const key = normalizeFilePath(copy.file_path);
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ path: copy.file_path, label: copy.file_name, kind: copy.kind, live: false, copy });
  }
  return rows;
}

export interface DraftsTabProps {
  drafts: DraftItem[];
  selectedDraft: DraftItem | null;
  laptopReachable: boolean;
  savedCopyLabel: string | null;
  canQueueOffline: boolean;
  /** Actions queued on this phone for the laptop (this account's only). */
  phoneActions?: PhoneAction[];
  /** Signed in: comment and mark-posted can be queued through the cloud while the laptop is away. */
  canQueue?: boolean;
  /** Full-quality cloud copies of this account's finished draft files (the laptop keeps them 14 days). */
  phoneCopies?: PhoneCopy[];
  ownerUid?: string | null;
  busyAction: string | null;
  errorMessage: string | null;
  onSelectDraft: (id: string) => void;
  onCloseDraft: () => void;
  onAddComment: (contentId: string, body: string, version?: number | null) => Promise<boolean>;
  onMarkPosted: (contentId: string, url?: string) => Promise<boolean>;
  onCopyText: (text: string) => Promise<boolean>;
  onShareOut: (title: string, text: string) => Promise<boolean>;
  /** Get a file on the phone: the direct laptop path when reachable, else the cloud copy. */
  onGetOnPhone?: (contentId: string, filePath: string, title?: string) => Promise<boolean>;
  onCreateOfflineItem: (input: {
    kind: QueueItemKind;
    title: string;
    body: string;
    platform?: string;
  }) => Promise<boolean>;
}

/** Pick the body of current_version by version number, or highest version number, never array position. */
export function resolveDraftBody(draft: DraftItem): string {
  const versions = draft.versions ?? [];
  if (versions.length > 0 && draft.current_version != null) {
    const match = versions.find(
      (v) => (v.version ?? v.number) === draft.current_version && typeof v.body === 'string' && v.body.length > 0,
    );
    if (match?.body) return match.body;
  }
  if (draft.body && draft.body.trim()) return draft.body;
  if (versions.length > 0) {
    let bestVer = -Infinity;
    let bestBody = '';
    for (const v of versions) {
      const num = Number(v.version ?? v.number ?? 0);
      if (typeof v.body === 'string' && v.body.length > 0 && num >= bestVer) {
        bestVer = num;
        bestBody = v.body;
      }
    }
    if (bestBody) return bestBody;
  }
  return '';
}

function countComments(comments: DraftComment[]): number {
  let total = 0;
  for (const c of comments) {
    total += 1;
    if (Array.isArray(c.replies) && c.replies.length > 0) {
      total += countComments(c.replies);
    }
  }
  return total;
}

function flattenComments(comments: DraftComment[]): DraftComment[] {
  return comments.flatMap((c) => [c, ...flattenComments(c.replies ?? [])]);
}

function CommentThread({ comment }: { comment: DraftComment }) {
  const replies = Array.isArray(comment.replies) ? comment.replies : [];
  return (
    <div className="mob-msg">
      <span className="mob-mono">{comment.author || 'reviewer'}</span>
      <p className="mob-body-text">
        {comment.confidential ? 'Confidential comment' : comment.body}
      </p>
      {replies.length > 0 ? (
        <div className="mob-comment-replies">
          {replies.map((reply) => (
            <CommentThread key={reply.id} comment={reply} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function DraftsTab({
  drafts,
  selectedDraft,
  laptopReachable,
  savedCopyLabel,
  canQueueOffline,
  phoneActions = [],
  canQueue = false,
  phoneCopies = [],
  ownerUid = null,
  busyAction,
  errorMessage,
  onSelectDraft,
  onCloseDraft,
  onAddComment,
  onMarkPosted,
  onCopyText,
  onShareOut,
  onGetOnPhone,
  onCreateOfflineItem,
}: DraftsTabProps) {
  const [commentText, setCommentText] = useState('');
  const [postedUrl, setPostedUrl] = useState('');
  const [composerOpen, setComposerOpen] = useState(false);
  const [newKind, setNewKind] = useState<QueueItemKind>('draft');
  const [newPlatform, setNewPlatform] = useState('linkedin');
  const [newTitle, setNewTitle] = useState('');
  const [newBody, setNewBody] = useState('');
  const [composerError, setComposerError] = useState<string | null>(null);

  async function handleSaveNew() {
    if (!newTitle.trim()) return;
    setComposerError(null);
    try {
      const ok = await onCreateOfflineItem({
        kind: newKind,
        title: newTitle.trim(),
        body: newBody,
        platform: newKind === 'draft' ? newPlatform : undefined,
      });
      if (ok) {
        setNewTitle('');
        setNewBody('');
        setComposerOpen(false);
      }
    } catch (err) {
      setComposerError(err instanceof Error ? err.message : 'Could not save item.');
    }
  }

  if (selectedDraft) {
    if (selectedDraft.confidential) {
      return (
        <section aria-label="Draft detail" className="mob-stack">
          <div className="mob-actions">
            <PillButton variant="outline" size="sm" onClick={onCloseDraft}>
              Back to drafts
            </PillButton>
          </div>
          <Card>
            <div className="mob-stack-sm">
              <Label>Confidential item</Label>
              <p className="mob-body-text">
                Confidential drafts stay stubs on mobile. Open Magnm Studio on your laptop to read, comment, or mark posted.
              </p>
            </div>
          </Card>
        </section>
      );
    }

    const bodyText = resolveDraftBody(selectedDraft);
    const warnings = selectedDraft.warnings ?? [];
    const comments = selectedDraft.comments ?? [];
    const totalCommentCount = countComments(comments);
    const fileRows = mergeFileRows(
      extractShareableDraftFiles(selectedDraft.files),
      phoneCopies.filter((c) => c.content_id === selectedDraft.id),
    );
    const canAct = laptopReachable || canQueue;
    // Actions made on this phone for this draft. A comment the laptop applied is hidden once its own copy arrives.
    const phoneComments = flattenComments(comments)
      .filter((c) => (c.author ?? '').startsWith('phone:'))
      .map((c) => (c.body ?? '').trim());
    const draftActions = phoneActions.filter(
      (a) =>
        a.target_id === selectedDraft.id &&
        (a.kind === 'draft_comment' || a.kind === 'draft_posted') &&
        !(a.kind === 'draft_comment' && a.state === 'applied' && phoneComments.includes((a.payload.body ?? '').trim())),
    );

    return (
      <section aria-label="Draft detail" className="mob-stack">
        <div className="mob-row-between">
          <PillButton variant="outline" size="sm" onClick={onCloseDraft}>
            Back to drafts
          </PillButton>
          <span className="mob-mono">
            {selectedDraft.platform || 'post'} · {selectedDraft.status || 'draft'}
          </span>
        </div>

        {!laptopReachable && savedCopyLabel ? (
          <div className="mg-notice">{savedCopyLabel}</div>
        ) : null}

        {errorMessage ? (
          <div className="mob-banner-error" role="alert">
            {errorMessage}
          </div>
        ) : null}

        <Card>
          <div className="mob-stack-sm">
            <h2 className="mob-card-title">{selectedDraft.title || 'Untitled draft'}</h2>
            <p className="mob-body-text">{bodyText || '(Empty draft body)'}</p>
            <div className="mob-actions">
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => void onCopyText(bodyText)}
              >
                Copy
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => void onShareOut(selectedDraft.title || 'Magnm Draft', bodyText)}
              >
                Share
              </PillButton>
            </div>
          </div>
        </Card>

        {fileRows.length > 0 ? (
          <Card>
            <div className="mob-stack-sm">
              <Label>Files ({fileRows.length})</Label>
              <RowList>
                {fileRows.map((file) => {
                  const fileBusy = busyAction === `share-file:${file.path}`;
                  const cloudOk = file.copy ? isCopyUsable(file.copy, ownerUid ?? '') : false;
                  const laptopOk = laptopReachable && file.live;
                  const expired = Boolean(file.copy) && !cloudOk;
                  const details = [file.kind];
                  if (file.copy) details.push(formatCopySize(file.copy.bytes), copyExpiryLabel(file.copy));
                  return (
                    <Row
                      key={file.path}
                      title={file.label}
                      description={details.join(' · ')}
                      meta={
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={!(laptopOk || cloudOk) || fileBusy || !onGetOnPhone}
                          title={
                            laptopOk || cloudOk
                              ? undefined
                              : expired
                                ? 'This copy has expired. Connect to your laptop to get the file.'
                                : 'Connect to your laptop to get this file'
                          }
                          onClick={() => {
                            if (onGetOnPhone) {
                              void onGetOnPhone(
                                selectedDraft.id,
                                file.path,
                                selectedDraft.title || file.label,
                              );
                            }
                          }}
                        >
                          {fileBusy ? 'Preparing...' : 'Get on phone'}
                        </PillButton>
                      }
                    />
                  );
                })}
              </RowList>
            </div>
          </Card>
        ) : null}

        {warnings.length > 0 ? (
          <Card outline>
            <div className="mob-stack-sm">
              <Label>Reviewer warnings</Label>
              {warnings.map((w, idx) => (
                <div key={idx} className="mg-notice">
                  {typeof w === 'string' ? w : w.message || 'Warning'}
                </div>
              ))}
            </div>
          </Card>
        ) : null}

        <Card>
          <div className="mob-stack-sm">
            <Label>Comments ({totalCommentCount})</Label>
            {comments.map((cmt) => (
              <CommentThread key={cmt.id} comment={cmt} />
            ))}
            {draftActions.map((action) => (
              <div key={action.id} className="mob-msg">
                <div className="mob-row-between">
                  <span className="mob-mono">
                    {action.kind === 'draft_comment' ? 'From this phone' : 'Mark as posted from this phone'}
                  </span>
                  <span className="mg-chip">{stateLabel(action)}</span>
                </div>
                {action.kind === 'draft_comment' ? (
                  <p className="mob-body-text">{action.payload.body}</p>
                ) : action.payload.url ? (
                  <p className="mob-mono">{action.payload.url}</p>
                ) : null}
                {action.state === 'rejected' && action.result ? (
                  <p className="mob-mono">{action.result}</p>
                ) : null}
              </div>
            ))}
            {!canAct ? (
              <div className="mg-notice">Connect to your laptop or sign in to comment or mark this as posted.</div>
            ) : null}
            <Field label="Add comment" htmlFor="draft-comment-input">
              <TextArea
                id="draft-comment-input"
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                placeholder="Write review feedback..."
              />
            </Field>
            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!canAct || !commentText.trim() || busyAction === 'comment'}
                onClick={() => {
                  void (async () => {
                    const ok = await onAddComment(
                      selectedDraft.id,
                      commentText.trim(),
                      selectedDraft.current_version,
                    );
                    if (ok) {
                      setCommentText('');
                    }
                  })();
                }}
              >
                Add comment
              </PillButton>
            </div>
          </div>
        </Card>

        <Card>
          <div className="mob-stack-sm">
            <Label>Posting</Label>
            {selectedDraft.posted_url ? (
              <p className="mob-mono">Posted URL: {selectedDraft.posted_url}</p>
            ) : null}
            <Field label="Posted link (optional)" htmlFor="draft-posted-url">
              <TextInput
                id="draft-posted-url"
                value={postedUrl}
                onChange={(e) => setPostedUrl(e.target.value)}
                placeholder="https://..."
              />
            </Field>
            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!canAct || busyAction === 'posted'}
                onClick={() => {
                  void (async () => {
                    const ok = await onMarkPosted(selectedDraft.id, postedUrl.trim() || undefined);
                    if (ok) {
                      setPostedUrl('');
                    }
                  })();
                }}
              >
                Mark as posted
              </PillButton>
            </div>
          </div>
        </Card>
      </section>
    );
  }

  return (
    <section aria-label="Drafts" className="mob-stack">
      <div className="mob-row-between">
        <Label>Drafts</Label>
        <PillButton
          variant="outline"
          size="sm"
          disabled={!canQueueOffline}
          onClick={() => {
            setComposerError(null);
            setComposerOpen((prev) => !prev);
          }}
        >
          {composerOpen ? 'Close' : 'New item'}
        </PillButton>
      </div>

      {!laptopReachable && savedCopyLabel ? (
        <div className="mg-notice">{savedCopyLabel}</div>
      ) : null}

      {errorMessage ? (
        <div className="mob-banner-error" role="alert">
          {errorMessage}
        </div>
      ) : null}

      {composerOpen ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>{laptopReachable ? 'Send to laptop inbox' : 'Queue offline'}</Label>
            {composerError ? (
              <div className="mob-banner-error" role="alert">
                {composerError}
              </div>
            ) : null}
            <Field label="Kind" htmlFor="new-item-kind">
              <Select
                id="new-item-kind"
                value={newKind}
                onChange={(e) => setNewKind(e.target.value as QueueItemKind)}
              >
                <option value="draft">Draft</option>
                <option value="note">Note</option>
                <option value="idea">Idea</option>
              </Select>
            </Field>
            {newKind === 'draft' ? (
              <Field label="Platform" htmlFor="new-item-platform">
                <Select
                  id="new-item-platform"
                  value={newPlatform}
                  onChange={(e) => setNewPlatform(e.target.value)}
                >
                  <option value="linkedin">LinkedIn</option>
                  <option value="x">X</option>
                  <option value="instagram">Instagram</option>
                  <option value="youtube">YouTube</option>
                </Select>
              </Field>
            ) : null}
            <Field label="Title" htmlFor="new-item-title">
              <TextInput
                id="new-item-title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Post title or topic"
              />
            </Field>
            <Field label="Text" htmlFor="new-item-body">
              <TextArea
                id="new-item-body"
                value={newBody}
                onChange={(e) => setNewBody(e.target.value)}
                placeholder="Write draft, note, or idea..."
              />
            </Field>
            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!newTitle.trim() || busyAction === 'create-item'}
                onClick={() => void handleSaveNew()}
              >
                {laptopReachable ? 'Send to laptop' : 'Queue offline'}
              </PillButton>
            </div>
          </div>
        </Card>
      ) : null}

      {drafts.length === 0 ? (
        <div className="mg-empty">
          No drafts available yet. Pair with your laptop or create an offline item above.
        </div>
      ) : (
        <RowList>
          {drafts.map((item) => (
            <Row
              key={item.id}
              title={item.confidential ? 'Confidential item' : item.title || 'Untitled draft'}
              description={
                item.confidential
                  ? 'Confidential stub'
                  : `${item.platform || 'post'} · ${item.status || 'draft'}`
              }
              meta={item.current_version ? `v${item.current_version}` : undefined}
              onClick={() => onSelectDraft(item.id)}
            />
          ))}
        </RowList>
      )}
    </section>
  );
}
