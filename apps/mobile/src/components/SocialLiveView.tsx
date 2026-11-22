import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from 'react';
import { PillButton, TextInput } from '@magnm/design/react';
import {
  fetchLaptopSocialLiveFrame,
  postLaptopSocialChallenge,
  postLaptopSocialLiveInput,
} from '../api';
import type { NativeBridge } from '../native';
import { getStateGeneration } from '../storage';
import {
  SOCIAL_VIEWPORT_HEIGHT,
  SOCIAL_VIEWPORT_WIDTH,
  mapClientPointToViewport,
} from '../socialLiveGeometry';
import type { PairingConfig, SocialLiveInputPayload, SocialRunRecord } from '../types';

const MOBILE_KEY_BUTTONS = ['Enter', 'Tab', 'Backspace', 'Escape'] as const;
const DRAG_THRESHOLD_PX = 8;

export interface SocialLiveViewProps {
  open: boolean;
  foreground: boolean;
  pairing: PairingConfig | null;
  run: SocialRunRecord | null;
  bridge: NativeBridge;
  sessionGen: number;
  getSessionGen: () => number;
  onClose: () => void;
  onCleared: (run?: SocialRunRecord) => void;
  onAuthError?: (err: unknown) => void;
}

/** Full-screen phone live view for waiting_user checks: tap, drag-to-scroll, masked input, Back closes. */
export function SocialLiveView({
  open,
  foreground,
  pairing,
  run,
  bridge,
  sessionGen,
  getSessionGen,
  onClose,
  onCleared,
  onAuthError,
}: SocialLiveViewProps) {
  const [frameSrc, setFrameSrc] = useState<string | null>(null);
  const [typedText, setTypedText] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const handledPointerUpRef = useRef(false);

  // The parent passes new functions on every render; that must not restart polling or erase what is being typed.
  const getSessionGenRef = useRef(getSessionGen);
  getSessionGenRef.current = getSessionGen;
  const onAuthErrorRef = useRef(onAuthError);
  onAuthErrorRef.current = onAuthError;

  // Wipe frame & typed text immediately when closed or backgrounded.
  useEffect(() => {
    if (!open || !foreground || !pairing || !run || run.status !== 'waiting_user') {
      setFrameSrc(null);
      setTypedText('');
      setError(null);
      setStatusMsg(null);
      return undefined;
    }

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const startStateGen = getStateGeneration();

    const isStale = () =>
      disposed || getSessionGenRef.current() !== sessionGen || getStateGeneration() !== startStateGen;

    const pollOnce = async () => {
      if (isStale()) return;
      try {
        const frame = await fetchLaptopSocialLiveFrame(pairing, bridge);
        if (isStale()) return;
        const mime = frame.content_type || 'image/jpeg';
        setFrameSrc(`data:${mime};base64,${frame.frame_base64}`);
        setError(null);
      } catch (err) {
        if (isStale()) return;
        onAuthErrorRef.current?.(err);
        setError(err instanceof Error ? err.message : 'Live frame unavailable.');
      }

      if (!isStale()) {
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
      setFrameSrc(null);
      setTypedText('');
    };
  }, [
    open,
    foreground,
    pairing,
    run?.id,
    run?.status,
    bridge,
    sessionGen,
  ]);

  if (!open || !run || !pairing) {
    return null;
  }

  async function sendInput(payload: SocialLiveInputPayload) {
    if (!pairing) return;
    setError(null);
    const startStateGen = getStateGeneration();
    try {
      await postLaptopSocialLiveInput(pairing, payload, bridge);
      if (getSessionGen() !== sessionGen || getStateGeneration() !== startStateGen) return;
    } catch (err) {
      if (getSessionGen() !== sessionGen || getStateGeneration() !== startStateGen) return;
      onAuthError?.(err);
      setError(err instanceof Error ? err.message : 'Input failed.');
    }
  }

  function handlePointerDown(e: PointerEvent<HTMLImageElement>) {
    if (!Number.isFinite(e.clientX) || !Number.isFinite(e.clientY)) {
      pointerDownRef.current = null;
      return;
    }
    pointerDownRef.current = { x: e.clientX, y: e.clientY };
    handledPointerUpRef.current = false;
  }

  function handlePointerUp(e: PointerEvent<HTMLImageElement>) {
    const start = pointerDownRef.current;
    pointerDownRef.current = null;
    if (!start || !Number.isFinite(e.clientX) || !Number.isFinite(e.clientY)) return;
    handledPointerUpRef.current = true;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    const dist = Math.hypot(dx, dy);

    if (dist > DRAG_THRESHOLD_PX) {
      const scrollX = Math.max(-2000, Math.min(2000, Math.round(-dx * 2)));
      const scrollY = Math.max(-2000, Math.min(2000, Math.round(-dy * 2)));
      void sendInput({ action: 'scroll', delta_x: scrollX, delta_y: scrollY });
      return;
    }

    const pt = mapClientPointToViewport(
      e.clientX,
      e.clientY,
      e.currentTarget.getBoundingClientRect(),
      SOCIAL_VIEWPORT_WIDTH,
      SOCIAL_VIEWPORT_HEIGHT,
    );
    if (!pt) return;
    void sendInput({ action: 'click', x: pt.x, y: pt.y, button: 'left' });
  }

  function handleImageClick(e: MouseEvent<HTMLImageElement>) {
    if (handledPointerUpRef.current) {
      handledPointerUpRef.current = false;
      return;
    }
    const pt = mapClientPointToViewport(
      e.clientX,
      e.clientY,
      e.currentTarget.getBoundingClientRect(),
      SOCIAL_VIEWPORT_WIDTH,
      SOCIAL_VIEWPORT_HEIGHT,
    );
    if (!pt) return;
    void sendInput({ action: 'click', x: pt.x, y: pt.y, button: 'left' });
  }

  async function handleSendText() {
    const value = typedText;
    if (!value) return;
    setTypedText('');
    setBusy('text');
    try {
      await sendInput({ action: 'text', text: value });
    } finally {
      setBusy(null);
    }
  }

  async function handleDone() {
    if (!pairing || !run) return;
    setBusy('done');
    setError(null);
    setStatusMsg(null);
    const startStateGen = getStateGeneration();
    try {
      const res = await postLaptopSocialChallenge(pairing, 'continue', run.id, bridge);
      if (getSessionGen() !== sessionGen || getStateGeneration() !== startStateGen) return;
      if (res.ok && res.cleared) {
        setFrameSrc(null);
        setTypedText('');
        onCleared(res.run);
        return;
      }
      setStatusMsg(
        `Check still active (${res.challenge_kind || 'pending'}). Finish solving and tap Done again.`,
      );
    } catch (err) {
      if (getSessionGen() !== sessionGen || getStateGeneration() !== startStateGen) return;
      onAuthError?.(err);
      setError(err instanceof Error ? err.message : 'Could not verify security check.');
    } finally {
      setBusy(null);
    }
  }

  function handleBackClose() {
    setFrameSrc(null);
    setTypedText('');
    onClose();
  }

  return (
    <div
      className="mob-social-live"
      role="dialog"
      aria-modal="true"
      aria-label={`Live check for ${run.platform}`}
    >
      <div className="mob-social-live-head">
        <PillButton variant="outline" size="sm" onClick={handleBackClose}>
          Back
        </PillButton>
        <div className="mob-mono">
          {run.platform} · {run.challenge_kind || 'security check'}
        </div>
        <PillButton size="sm" disabled={busy !== null} onClick={() => void handleDone()}>
          {busy === 'done' ? 'Checking...' : 'Done'}
        </PillButton>
      </div>

      {error ? (
        <div className="mob-banner-error" role="alert">
          {error}
        </div>
      ) : null}
      {statusMsg ? (
        <div className="mg-notice" role="status">
          {statusMsg}
        </div>
      ) : null}

      <div className="mob-social-live-stage">
        {frameSrc ? (
          <img
            className="mob-social-live-img"
            src={frameSrc}
            alt="Live laptop browser view"
            onPointerDown={handlePointerDown}
            onPointerUp={handlePointerUp}
            onClick={handleImageClick}
          />
        ) : (
          <div className="mob-mono">Waiting for live frame...</div>
        )}
      </div>

      <div className="mob-social-live-controls">
        <div className="mob-actions">
          <TextInput
            type="password"
            autoComplete="off"
            aria-label="Masked live text input"
            placeholder="Type code or text (masked)..."
            value={typedText}
            onChange={(e) => setTypedText(e.target.value)}
          />
          <PillButton
            size="sm"
            disabled={!typedText || busy !== null}
            onClick={() => void handleSendText()}
          >
            Send
          </PillButton>
        </div>

        <div className="mob-actions">
          {MOBILE_KEY_BUTTONS.map((keyName) => (
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
    </div>
  );
}
