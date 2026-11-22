import { useCallback, useEffect, useRef, useState, type MouseEvent, type WheelEvent } from 'react';
import { Dialog, PillButton, TextInput } from '@magnm/design/react';
import {
  fetchSocialLiveFrameBlob,
  openSocialLiveFullWindow,
  postSocialLiveInput,
  socialChallengeContinue,
} from '../api/social';
import type { SocialRunRecord } from '../types';
import {
  SOCIAL_VIEWPORT_HEIGHT,
  SOCIAL_VIEWPORT_WIDTH,
  mapClientPointToViewport,
} from './socialLiveGeometry';

const KEY_BUTTONS = ['Enter', 'Tab', 'Backspace', 'Escape', 'Space', 'ArrowUp', 'ArrowDown'] as const;

export interface SocialLiveModalProps {
  open: boolean;
  run: SocialRunRecord | null;
  onClose: () => void;
  onCheckStatus?: () => void;
  onCleared?: (run?: SocialRunRecord) => void;
}

/** Modal live browser panel for solving waiting_user social checks with 1280x800 letterboxed pointer mapping. */
export function SocialLiveModal({
  open,
  run,
  onClose,
  onCheckStatus,
  onCleared,
}: SocialLiveModalProps) {
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [typedText, setTypedText] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const revokeUrl = useCallback((url: string | null) => {
    if (url && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
      URL.revokeObjectURL(url);
    }
  }, []);

  // The parent passes a new function on every render; that must not restart frame polling.
  const onCheckStatusRef = useRef(onCheckStatus);
  onCheckStatusRef.current = onCheckStatus;

  useEffect(() => {
    if (!open || !run || run.status !== 'waiting_user') {
      setFrameUrl((prev) => {
        revokeUrl(prev);
        return null;
      });
      setTypedText('');
      setError(null);
      setStatusMsg(null);
      return undefined;
    }

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let activeAbort: AbortController | null = null;

    const pollOnce = async () => {
      if (disposed) return;
      activeAbort = new AbortController();
      try {
        const blob = await fetchSocialLiveFrameBlob(activeAbort.signal);
        if (disposed) return;
        const nextUrl =
          typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function'
            ? URL.createObjectURL(blob)
            : '';
        if (nextUrl) {
          setFrameUrl((prev) => {
            revokeUrl(prev);
            return nextUrl;
          });
        }
        setError(null);
      } catch (err) {
        if (disposed || (err instanceof DOMException && err.name === 'AbortError')) {
          return;
        }
        setError(err instanceof Error ? err.message : 'Live frame unavailable.');
        onCheckStatusRef.current?.();
      } finally {
        activeAbort = null;
      }

      if (!disposed) {
        timer = setTimeout(() => {
          void pollOnce();
        }, 500);
      }
    };

    void pollOnce();

    return () => {
      disposed = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
      if (activeAbort) {
        activeAbort.abort();
      }
      setFrameUrl((prev) => {
        revokeUrl(prev);
        return null;
      });
    };
  }, [open, run?.id, run?.status, revokeUrl]);

  const sendInput = async (payload: Parameters<typeof postSocialLiveInput>[0]) => {
    setError(null);
    try {
      await postSocialLiveInput(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Input failed.');
      onCheckStatus?.();
    }
  };

  const handleImageClick = (e: MouseEvent<HTMLImageElement>) => {
    const pt = mapClientPointToViewport(
      e.clientX,
      e.clientY,
      e.currentTarget.getBoundingClientRect(),
      SOCIAL_VIEWPORT_WIDTH,
      SOCIAL_VIEWPORT_HEIGHT,
    );
    if (!pt) return;
    void sendInput({ action: 'click', x: pt.x, y: pt.y, button: 'left' });
  };

  const handleImageWheel = (e: WheelEvent<HTMLImageElement>) => {
    e.preventDefault();
    const dx = Math.max(-2000, Math.min(2000, e.deltaX));
    const dy = Math.max(-2000, Math.min(2000, e.deltaY));
    if (dx === 0 && dy === 0) return;
    void sendInput({ action: 'scroll', delta_x: dx, delta_y: dy });
  };

  const handleSendText = async () => {
    const value = typedText;
    if (!value) return;
    setTypedText('');
    setBusy('text');
    try {
      await sendInput({ action: 'text', text: value });
    } finally {
      setBusy(null);
    }
  };

  const handleOpenWindow = async () => {
    setBusy('open_window');
    setError(null);
    try {
      await openSocialLiveFullWindow();
      setStatusMsg('Opened browser in a full window on your desktop.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open full window.');
    } finally {
      setBusy(null);
    }
  };

  const handleDone = async () => {
    if (!run) return;
    setBusy('done');
    setError(null);
    setStatusMsg(null);
    try {
      const res = await socialChallengeContinue(run.id);
      if (res.ok && res.cleared) {
        onCleared?.(res.run);
        onClose();
        return;
      }
      setStatusMsg(
        `Check still active (${res.challenge_kind || 'pending'}). Finish solving and tap Done again.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not verify security check.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog
      open={open && Boolean(run)}
      onClose={onClose}
      title={run ? `Solve check (${run.platform})` : 'Live browser view'}
      subtitle={run?.challenge_kind ? `Security check: ${run.challenge_kind}` : 'Live control'}
      className="studio-social-live-dialog"
      actions={
        <div className="studio-row-actions">
          <PillButton
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void handleOpenWindow()}
          >
            {busy === 'open_window' ? 'Opening...' : 'Open in full window'}
          </PillButton>
          <PillButton size="sm" disabled={busy !== null} onClick={() => void handleDone()}>
            {busy === 'done' ? 'Checking...' : 'Done'}
          </PillButton>
        </div>
      }
    >
      <div className="studio-stack-sm">
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}
        {statusMsg ? (
          <div className="mg-notice" role="status">
            {statusMsg}
          </div>
        ) : null}

        <div className="studio-social-live-stage">
          {frameUrl ? (
            <img
              className="studio-social-live-img"
              src={frameUrl}
              alt="Live social browser view"
              onClick={handleImageClick}
              onWheel={handleImageWheel}
            />
          ) : (
            <div className="studio-hint">Waiting for browser frame...</div>
          )}
        </div>

        <div className="studio-inline-form">
          <TextInput
            type="password"
            autoComplete="off"
            aria-label="Masked live text input"
            placeholder="Type code or text (masked)..."
            value={typedText}
            onChange={(e) => setTypedText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && typedText) {
                e.preventDefault();
                void handleSendText();
              }
            }}
          />
          <PillButton
            size="sm"
            disabled={!typedText || busy !== null}
            onClick={() => void handleSendText()}
          >
            Send text
          </PillButton>
        </div>

        <div className="studio-chip-row" aria-label="Live keyboard and scroll controls">
          {KEY_BUTTONS.map((keyName) => (
            <PillButton
              key={keyName}
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => void sendInput({ action: 'key', key: keyName })}
            >
              {keyName}
            </PillButton>
          ))}
          <PillButton
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void sendInput({ action: 'scroll', delta_x: 0, delta_y: -300 })}
          >
            Scroll up
          </PillButton>
          <PillButton
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void sendInput({ action: 'scroll', delta_x: 0, delta_y: 300 })}
          >
            Scroll down
          </PillButton>
        </div>
      </div>
    </Dialog>
  );
}
