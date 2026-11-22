import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  Card,
  Checkbox,
  Dialog,
  Field,
  PillButton,
  Select,
  TextInput,
} from '@magnm/design/react';
import { ApiError } from '../../api';
import { confirmPosting, preparePosting } from '../../api/posting';
import type {
  ContentItem,
  PostingAttemptRecord,
  PostingConfirmResult,
  PostingPlatform,
  PostingPrepareResult,
} from '../../types';

export interface PostWithApiDialogProps {
  open: boolean;
  onClose: () => void;
  item: ContentItem;
  attempts?: PostingAttemptRecord[];
  onPosted: (res: PostingConfirmResult) => void;
  onAttemptUpdated?: () => void;
}

function defaultPostingPlatform(raw?: string | null): PostingPlatform {
  const norm = (raw || '').trim().toLowerCase();
  if (norm === 'linkedin' || norm === 'li') return 'linkedin';
  if (norm === 'instagram' || norm === 'ig') return 'instagram';
  if (norm === 'x' || norm === 'twitter') return 'x';
  return 'linkedin';
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** Confirmation dialog for official-API posting. "Post publicly now" is never focused by default and Enter never confirms. */
export function PostWithApiDialog({
  open,
  onClose,
  item,
  attempts = [],
  onPosted,
  onAttemptUpdated,
}: PostWithApiDialogProps) {
  const [platform, setPlatform] = useState<PostingPlatform>(() =>
    defaultPostingPlatform(item.platform),
  );
  const [mediaUrl, setMediaUrl] = useState('');
  const [aiGenerated, setAiGenerated] = useState(false);
  const [ackRepost, setAckRepost] = useState(false);
  const [ackUnknown, setAckUnknown] = useState(false);

  const [prepared, setPrepared] = useState<PostingPrepareResult | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unknownError, setUnknownError] = useState<string | null>(null);

  // Previews and posts to other platforms never hide what happened on this one.
  const executed = attempts.filter(
    (a) => a.platform === platform && a.status !== 'prepared' && a.status !== 'cancelled' && a.status !== 'expired',
  );
  const lastAttempt = executed[0];
  const isAlreadyPosted = item.status === 'posted' || executed.some((a) => a.status === 'posted');
  const hasUnknownAttempt = lastAttempt?.status === 'unknown';
  const requestSeqRef = useRef(0);

  const runPrepare = useCallback(async () => {
    const seq = ++requestSeqRef.current;
    if (!open) return;
    setPrepared(null);
    setPreparing(false);
    if (platform === 'instagram' && !mediaUrl.trim()) {
      setPrepared(null);
      setError(null);
      return;
    }
    if (isAlreadyPosted && !ackRepost) {
      setPrepared(null);
      return;
    }
    if (hasUnknownAttempt && !ackUnknown) {
      setPrepared(null);
      return;
    }
    setPreparing(true);
    setError(null);
    try {
      const res = await preparePosting({
        content_id: item.id,
        platform,
        media_url: platform === 'instagram' ? mediaUrl.trim() : undefined,
        ai_generated: aiGenerated,
        acknowledge_repost: ackRepost,
        acknowledge_unknown: ackUnknown,
      });
      if (seq !== requestSeqRef.current) return; // An older answer must not replace what is selected now.
      setPrepared(res);
    } catch (err) {
      if (seq !== requestSeqRef.current) return;
      setPrepared(null);
      setError(err instanceof Error ? err.message : 'Could not prepare post.');
    } finally {
      if (seq === requestSeqRef.current) setPreparing(false);
    }
  }, [open, item.id, platform, mediaUrl, aiGenerated, isAlreadyPosted, ackRepost, hasUnknownAttempt, ackUnknown]);

  useEffect(() => {
    if (!open) return;
    setPlatform(defaultPostingPlatform(item.platform));
    setMediaUrl('');
    setAiGenerated(false);
    setAckRepost(false);
    setAckUnknown(false);
    setPrepared(null);
    setError(null);
    setUnknownError(null);
    requestSeqRef.current += 1;
  }, [open, item.id, item.platform]);

  useEffect(() => {
    if (!open) return;
    if (platform === 'instagram') {
      // Instagram is checked on a click: drop any preview, or one still loading, whenever an option changes.
      requestSeqRef.current += 1;
      setPrepared(null);
      setPreparing(false);
      return;
    }
    void runPrepare();
  }, [open, platform, aiGenerated, ackRepost, ackUnknown, runPrepare]);

  const handleConfirm = async () => {
    if (!prepared || confirming || prepared.platform !== platform || prepared.content_id !== item.id) return;
    setConfirming(true);
    setError(null);
    setUnknownError(null);
    try {
      const res = await confirmPosting({
        confirmation_id: prepared.confirmation_id,
        payload_hash: prepared.payload_hash,
        media_url: prepared.media_url ?? undefined,
        media_paths: prepared.media_paths ?? undefined,
        ai_generated: prepared.ai_generated,
        acknowledge_repost: ackRepost,
        acknowledge_unknown: ackUnknown,
      });
      onPosted(res);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Publishing failed.';
      const isUnknown =
        err instanceof ApiError &&
        typeof err.data === 'object' &&
        err.data !== null &&
        ((err.data as { attempt_status?: string; uncertain?: boolean }).attempt_status === 'unknown' ||
          Boolean((err.data as { uncertain?: boolean }).uncertain));
      if (isUnknown) {
        setUnknownError(msg);
      } else {
        setError(msg);
      }
      setPrepared(null);
      onAttemptUpdated?.();
    } finally {
      setConfirming(false);
    }
  };

  useEffect(() => {
    if (!open) return undefined;
    const onDocKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Enter') return;
      const target = event.target as HTMLElement | null;
      if (!target?.closest('[role="dialog"]')) return;
      const isConfirmBtn = target.getAttribute('data-confirm-post-btn') === 'true';
      const tag = target.tagName.toLowerCase();
      if (isConfirmBtn || tag === 'input' || tag === 'select' || tag === 'div') {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    document.addEventListener('keydown', onDocKeyDown, true);
    return () => document.removeEventListener('keydown', onDocKeyDown, true);
  }, [open]);

  // Enter must NEVER trigger confirmation anywhere inside this dialog.
  const handleKeyDownCapture = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter') {
      const target = event.target as HTMLElement | null;
      const isConfirmBtn = target?.getAttribute('data-confirm-post-btn') === 'true';
      const tag = target?.tagName?.toLowerCase();
      if (isConfirmBtn || tag === 'input' || tag === 'select' || tag === 'div') {
        event.preventDefault();
        event.stopPropagation();
      }
    }
  };

  const canConfirm =
    Boolean(prepared) &&
    prepared?.platform === platform &&
    prepared?.content_id === item.id &&
    !preparing &&
    !confirming &&
    (!isAlreadyPosted || ackRepost) &&
    (!hasUnknownAttempt || ackUnknown);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      subtitle="Official API publishing"
      title="Post with API"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            data-confirm-post-btn="true"
            disabled={!canConfirm}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                e.stopPropagation();
              }
            }}
            onClick={() => void handleConfirm()}
          >
            {confirming ? 'Posting...' : 'Post publicly now'}
          </PillButton>
        </>
      }
    >
      <div className="studio-stack-sm" onKeyDownCapture={handleKeyDownCapture}>
        {unknownError || hasUnknownAttempt ? (
          <div className="mg-notice" role="alert">
            <strong>Post status: unknown.</strong>{' '}
            {unknownError ||
              lastAttempt?.error ||
              'Check the platform before trying again.'}
          </div>
        ) : null}

        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}

        <Field label="Target platform" htmlFor="post-api-platform">
          <Select
            id="post-api-platform"
            value={platform}
            onChange={(e) => {
              requestSeqRef.current += 1;
              setPrepared(null);
              setPlatform(e.target.value as PostingPlatform);
            }}
          >
            <option value="linkedin">linkedin</option>
            <option value="instagram">instagram</option>
            <option value="x">x</option>
          </Select>
        </Field>

        {platform === 'instagram' ? (
          <div className="studio-stack-xs">
            <Field
              label="Public HTTPS media URL (JPEG or MP4, max 100 MB)"
              htmlFor="post-api-ig-url"
              hint="Magnm never uploads local files or makes them public."
            >
              <TextInput
                id="post-api-ig-url"
                placeholder="https://example.com/media.jpg"
                value={mediaUrl}
                onChange={(e) => {
                  requestSeqRef.current += 1;
                  setPreparing(false);
                  setPrepared(null);
                  setMediaUrl(e.target.value);
                }}
              />
            </Field>
            <div>
              <PillButton
                variant="outline"
                size="sm"
                disabled={!mediaUrl.trim() || preparing}
                onClick={() => void runPrepare()}
              >
                {preparing ? 'Checking URL...' : 'Check media & preview'}
              </PillButton>
            </div>
          </div>
        ) : null}

        <Checkbox
          label="AI-generated (disclose as made with AI)"
          checked={aiGenerated}
          onChange={(e) => {
            requestSeqRef.current += 1;
            setPrepared(null);
            setAiGenerated(e.target.checked);
          }}
        />
        {platform === 'linkedin' ? (
          <div className="studio-hint">
            LinkedIn's API has no AI label, so this is not sent. Say it in the text if it applies.
          </div>
        ) : null}

        {isAlreadyPosted ? (
          <Checkbox
            label="Post again (this item is already marked as posted)"
            checked={ackRepost}
            onChange={(e) => setAckRepost(e.target.checked)}
          />
        ) : null}

        {hasUnknownAttempt ? (
          <Checkbox
            label="I checked the platform and confirmed the previous unknown attempt did not publish"
            checked={ackUnknown}
            onChange={(e) => setAckUnknown(e.target.checked)}
          />
        ) : null}

        {preparing ? <div className="studio-hint">Preparing preview and checks...</div> : null}

        {prepared ? (
          <div className="studio-stack-sm">
            <Card outline className="studio-stack-xs">
              <div className="mg-label">Destination account</div>
              <div>
                <strong>{prepared.platform}</strong> ·{' '}
                {prepared.account.display_name ||
                  prepared.account.username ||
                  prepared.account.account_id}
                {prepared.account.account_id ? (
                  <span className="studio-mono"> ({prepared.account.account_id})</span>
                ) : null}
              </div>
            </Card>

            <Card outline className="studio-stack-xs">
              <div className="mg-label">Exact text to publish</div>
              <pre className="studio-pre">{prepared.final_text}</pre>
            </Card>

            <Card outline className="studio-stack-xs">
              <div className="mg-label">Media</div>
              {prepared.media.length === 0 ? (
                <div className="studio-hint">Text only (no media attached).</div>
              ) : (
                <div className="studio-stack-xs">
                  {prepared.media.map((m, idx) => (
                    <div key={`${m.sha256}-${idx}`} className="studio-mono-wrap">
                      {m.preview_data_uri ? (
                        <img
                          src={m.preview_data_uri}
                          alt="Image that will be published"
                          style={{ display: 'block', maxWidth: '100%', maxHeight: 320 }}
                        />
                      ) : m.url && m.mime_type === 'video/mp4' ? (
                        <video
                          src={m.url}
                          controls
                          preload="none"
                          aria-label="Video that will be published"
                          style={{ display: 'block', maxWidth: '100%', maxHeight: 320 }}
                        />
                      ) : null}
                      {m.path || m.url} · {formatBytes(m.bytes)} ({m.bytes} bytes) · {m.mime_type} · sha256: {m.sha256}
                      {m.alt_text ? ` · alt: ${m.alt_text}` : ''}
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card outline className="studio-stack-xs">
              <div className="mg-label">Estimated cost</div>
              <div>{prepared.cost.summary}</div>
            </Card>

            {prepared.warnings.length > 0 ? (
              <Card outline className="studio-stack-xs">
                <div className="mg-label">Warnings ({prepared.warnings.length})</div>
                <ul className="studio-social-risks">
                  {prepared.warnings.map((w, idx) => (
                    <li key={`${w.code}-${idx}`} className="studio-hint">
                      {w.message}
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
