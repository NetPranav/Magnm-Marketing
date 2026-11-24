import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Checkbox, Field, PillButton, Select } from '@magnm/design/react';
import {
  downloadVoice,
  fetchVoiceSample,
  getVideoStatus,
  saveVideoSettings,
  videoProblem,
  waitForJob,
  type VideoSettings,
  type VideoStatus,
} from '../../api/video';

/**
 * Settings > Video: the voice-over voice (with Play sample), speaking speed, burned-in captions on or off, optional FLUX
 * illustrations, and Download voice (about 120 MB, with progress). The voice runs on this computer.
 */
export function VideoSettingsPanel() {
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadNote, setDownloadNote] = useState<string | null>(null);
  const [downloadPct, setDownloadPct] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const next = await getVideoStatus();
      if (alive.current) {
        setStatus(next);
        setError(null);
      }
    } catch (err) {
      if (alive.current) setError(videoProblem(err).message);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const save = async (changes: Partial<VideoSettings>) => {
    setError(null);
    try {
      const res = await saveVideoSettings(changes);
      if (alive.current) setStatus((prev) => (prev ? { ...prev, settings: res.settings } : prev));
    } catch (err) {
      if (alive.current) setError(videoProblem(err).message);
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    setError(null);
    setDownloadPct(0);
    setDownloadNote('Starting the voice download...');
    try {
      const res = await downloadVoice();
      if (res.job) {
        const finished = await waitForJob(res.job.id, (job) => {
          if (!alive.current) return;
          setDownloadPct(job.progress);
          setDownloadNote(job.message || 'Downloading the voice...');
        });
        if (finished && finished.status !== 'done' && alive.current) {
          setError(finished.error || 'The voice could not be downloaded. Check your internet connection and try again.');
        }
      }
      await load();
    } catch (err) {
      if (alive.current) setError(videoProblem(err).message);
    } finally {
      if (alive.current) {
        setDownloading(false);
        setDownloadNote(null);
        setDownloadPct(null);
      }
    }
  };

  const handlePlaySample = async () => {
    if (!status) return;
    setPlaying(true);
    setError(null);
    try {
      const blob = await fetchVoiceSample(status.settings.voice, status.settings.speed);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (err) {
      if (alive.current) setError(videoProblem(err).message);
    } finally {
      if (alive.current) setPlaying(false);
    }
  };

  const settings = status?.settings;
  const voice = status?.voice;

  return (
    <section className="studio-section" aria-labelledby="settings-video-heading">
      <div className="studio-section-bar">
        <h2 id="settings-video-heading" className="studio-section-title">
          Video
        </h2>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          <p className="studio-hint">
            Finished videos with a voice-over are made on this computer. Nothing is uploaded and no AI service is used for the voice.
          </p>

          <div className="studio-facts">
            <div className="studio-fact">
              <span className="studio-fact-label">Voice</span>
              <span className="studio-fact-value">
                {voice ? (voice.installed ? 'Installed' : `Not installed (about ${voice.size_mb} MB to download)`) : '...'}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">ffmpeg</span>
              <span className="studio-fact-value">{status ? (status.ffmpeg ? 'Found' : 'Not found, install it to render videos') : '...'}</span>
            </div>
          </div>

          {voice && !voice.installed ? (
            <div className="studio-stack-sm">
              {downloading ? (
                <div className="studio-stack-xs" role="status" aria-live="polite">
                  <progress aria-label="Voice download progress" max={100} value={downloadPct ?? 0} />
                  <span className="studio-hint">{downloadNote}</span>
                </div>
              ) : (
                <div className="studio-btn-group">
                  <PillButton size="sm" onClick={() => void handleDownload()}>
                    Download voice
                  </PillButton>
                </div>
              )}
            </div>
          ) : null}

          {settings && voice ? (
            <>
              <Field label="Voice" htmlFor="video-voice">
                <Select
                  id="video-voice"
                  value={settings.voice}
                  onChange={(e) => void save({ voice: e.target.value })}
                >
                  {voice.voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label={`Speed ${settings.speed.toFixed(2)}x`} htmlFor="video-speed">
                <input
                  id="video-speed"
                  type="range"
                  min={voice.min_speed}
                  max={voice.max_speed}
                  step={0.05}
                  value={settings.speed}
                  onChange={(e) => setStatus((prev) => (prev ? { ...prev, settings: { ...prev.settings, speed: Number(e.target.value) } } : prev))}
                  onMouseUp={() => void save({ speed: settings.speed })}
                  onTouchEnd={() => void save({ speed: settings.speed })}
                  onKeyUp={() => void save({ speed: settings.speed })}
                />
              </Field>

              <div className="studio-btn-group">
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={playing || !voice.installed}
                  onClick={() => void handlePlaySample()}
                >
                  {playing ? 'Playing...' : 'Play sample'}
                </PillButton>
              </div>

              <Checkbox
                label="Burn captions into the video (captions.srt is always saved)"
                checked={settings.captions}
                onChange={(e) => void save({ captions: e.target.checked })}
              />
              <Checkbox
                label="Draw illustrations with FLUX when a scene has no screenshot (uses your NVIDIA credits, off by default)"
                checked={settings.flux}
                onChange={(e) => void save({ flux: e.target.checked })}
              />
            </>
          ) : null}
        </div>
      </Card>
    </section>
  );
}
