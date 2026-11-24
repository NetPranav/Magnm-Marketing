import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, PillButton } from '@magnm/design/react';
import {
  cancelRender,
  downloadVoice,
  getRenderStatus,
  startRender,
  videoProblem,
  waitForJob,
  type RenderStatus,
  type VideoProblem,
} from '../../api/video';

export interface VideoRenderPanelProps {
  contentId: string;
  /** True once a rendered video is attached (the button then says "Render again"). */
  hasVideo: boolean;
  /** Called when a render finished, so the Review page can reload the attached files. */
  onChanged?: () => void;
}

const POLL_MS = 1500;

function isActive(status: RenderStatus | null): boolean {
  return status?.state === 'queued' || status?.state === 'running';
}

/**
 * Render video / Render again for a script: progress while it renders, Cancel, and plain-word errors. When the voice is
 * not installed it offers Download voice, or rendering without a voice. The video itself plays in the Video section.
 */
export function VideoRenderPanel({ contentId, hasVideo, onChanged }: VideoRenderPanelProps) {
  const [status, setStatus] = useState<RenderStatus | null>(null);
  const [problem, setProblem] = useState<VideoProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const alive = useRef(true);
  const lastState = useRef<string | null>(null);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await getRenderStatus(contentId);
      if (!alive.current) return;
      setStatus(next);
      if (lastState.current && lastState.current !== 'done' && next.state === 'done') {
        onChangedRef.current?.();
      }
      lastState.current = next.state;
    } catch {
      // A status read that fails is not worth alarming anyone about; the button still works.
    }
  }, [contentId]);

  useEffect(() => {
    lastState.current = null;
    setStatus(null);
    setProblem(null);
    setVoiceNote(null);
    void refresh();
  }, [contentId, refresh]);

  const active = isActive(status);
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const render = async (voice: boolean) => {
    setBusy(true);
    setProblem(null);
    setVoiceNote(null);
    try {
      const res = await startRender(contentId, { voice });
      if (!alive.current) return;
      lastState.current = res.status.state;
      setStatus(res.status);
    } catch (err) {
      if (alive.current) setProblem(videoProblem(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const cancel = async () => {
    setBusy(true);
    try {
      const res = await cancelRender(contentId);
      if (alive.current) setStatus(res.status);
    } catch (err) {
      if (alive.current) setProblem(videoProblem(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const fetchVoice = async () => {
    setBusy(true);
    setProblem(null);
    setVoiceNote('Downloading the voice...');
    try {
      const res = await downloadVoice();
      if (res.job) {
        const finished = await waitForJob(res.job.id, (job) => {
          if (alive.current) setVoiceNote(job.message || 'Downloading the voice...');
        });
        if (finished && finished.status !== 'done') {
          if (alive.current) {
            setVoiceNote(null);
            setProblem({ message: finished.error || 'The voice could not be downloaded.', code: 'no_voice' });
          }
          return;
        }
      }
      if (alive.current) setVoiceNote('The voice is installed. Press Render video.');
    } catch (err) {
      if (alive.current) {
        setVoiceNote(null);
        setProblem({ ...videoProblem(err), code: 'no_voice' });
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const failedMessage = status?.state === 'failed' ? status.error || status.message || 'The render failed.' : null;
  const failedCode = status?.state === 'failed' ? status.error_code || '' : '';
  const shownProblem: VideoProblem | null = problem ?? (failedMessage ? { message: failedMessage, code: failedCode } : null);
  const needsVoice = shownProblem?.code === 'no_voice' || shownProblem?.code === 'no_library';
  const percent = Math.max(0, Math.min(100, Math.round(status?.percent ?? 0)));

  return (
    <section className="studio-stack-sm" aria-label="Render video">
      <div className="studio-section-bar">
        <h3 className="studio-section-title">Finished video</h3>
        {!active ? (
          <PillButton size="sm" disabled={busy} onClick={() => void render(true)}>
            {busy ? 'Starting...' : hasVideo ? 'Render again' : 'Render video'}
          </PillButton>
        ) : null}
      </div>
      <Card className="studio-stack-xs">
        {active ? (
          <div className="studio-stack-xs" role="status" aria-live="polite">
            <progress aria-label="Render progress" max={100} value={percent} />
            <span className="studio-hint">
              {status?.state === 'queued' ? 'Waiting for another video to finish...' : status?.message || 'Rendering...'}
              {status?.state === 'running' ? ` (${percent}%)` : ''}
            </span>
            <div className="studio-btn-group">
              <PillButton variant="outline" size="sm" disabled={busy} onClick={() => void cancel()}>
                Cancel render
              </PillButton>
            </div>
          </div>
        ) : (
          <p className="studio-hint">
            {hasVideo
              ? 'Render again after you edit the script. The new video replaces the old one.'
              : 'Makes 9:16 and 16:9 videos with a local voice-over and captions. It takes about a minute and nothing leaves this computer.'}
          </p>
        )}
        {voiceNote ? (
          <div className="studio-hint" role="status">
            {voiceNote}
          </div>
        ) : null}
        {shownProblem && !active ? (
          <div className="mg-notice" role="alert">
            <div>{shownProblem.message}</div>
            {needsVoice ? (
              <div className="studio-btn-group">
                <PillButton size="sm" disabled={busy} onClick={() => void fetchVoice()}>
                  Download voice
                </PillButton>
                <PillButton variant="outline" size="sm" disabled={busy} onClick={() => void render(false)}>
                  Render without a voice
                </PillButton>
              </div>
            ) : null}
          </div>
        ) : null}
        {status?.state === 'cancelled' && !active ? <p className="studio-hint">The render was cancelled.</p> : null}
      </Card>
    </section>
  );
}
