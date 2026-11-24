import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, PillButton } from '@magnm/design/react';
import {
  checkForUpdate,
  getUpdateStatus,
  installUpdate,
  listenUpdateAvailable,
  listenUpdateProgress,
  restartApp,
  updateErrorText,
  updatesAvailable,
  type UpdateInfo,
  type UpdateProgress,
  type UpdateStatus,
} from '../../desktopUpdates';

type Step = 'idle' | 'checking' | 'installing' | 'restart';

function lastCheckedText(seconds: number | null): string {
  if (!seconds) return 'Not yet';
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime()) ? 'Not yet' : date.toLocaleString();
}

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Settings > Updates (desktop app only): the installed version, "Check for updates", the new version and its notes,
 * "Download and install" with progress, then "Restart to finish". Nothing is installed without a click.
 */
export function UpdatesPanel() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [available, setAvailable] = useState<UpdateInfo | null>(null);
  const [step, setStep] = useState<Step>('idle');
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [upToDate, setUpToDate] = useState(false);
  const [statusFailed, setStatusFailed] = useState(false);
  const [installedVersion, setInstalledVersion] = useState<string | null>(null);
  const alive = useRef(true);
  const present = updatesAvailable();

  const refresh = useCallback(async () => {
    try {
      const next = await getUpdateStatus();
      if (!alive.current) return;
      setStatusFailed(next === null);
      if (next) {
        setStatus(next);
        setAvailable((prev) => prev ?? next.available);
      }
    } catch {
      // Reading the saved status is not something to alarm anyone about; the buttons still work.
      if (alive.current) setStatusFailed(true);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    if (!present) return undefined;
    void refresh();
    const offAvailable = listenUpdateAvailable((info) => {
      if (alive.current) setAvailable(info);
    });
    const offProgress = listenUpdateProgress((p) => {
      if (alive.current) setProgress(p);
    });
    return () => {
      alive.current = false;
      offAvailable();
      offProgress();
    };
  }, [present, refresh]);

  if (!present) return null;

  const handleCheck = async () => {
    setStep('checking');
    setError(null);
    setUpToDate(false);
    try {
      const found = await checkForUpdate();
      if (!alive.current) return;
      setAvailable(found);
      setUpToDate(found === null);
      void refresh();
    } catch (err) {
      if (alive.current) setError(updateErrorText(err));
    } finally {
      if (alive.current) setStep('idle');
    }
  };

  const handleInstall = async () => {
    setStep('installing');
    setError(null);
    setProgress(null);
    try {
      const outcome = await installUpdate();
      if (!alive.current) return;
      setInstalledVersion(outcome.version);
      setStep(outcome.restart_needed ? 'restart' : 'idle');
      setAvailable(null);
    } catch (err) {
      if (alive.current) {
        setError(updateErrorText(err));
        setStep('idle');
      }
    }
  };

  const handleRestart = async () => {
    setError(null);
    try {
      await restartApp();
    } catch (err) {
      if (alive.current) setError(updateErrorText(err));
    }
  };

  const configured = status?.configured ?? true;
  const pacman = status?.install_kind === 'pacman';
  const percent =
    progress && progress.total && progress.total > 0
      ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100))
      : null;

  return (
    <section className="studio-section" aria-labelledby="settings-updates-heading">
      <div className="studio-section-bar">
        <h2 id="settings-updates-heading" className="studio-section-title">
          Updates
        </h2>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          <div className="studio-facts">
            <div className="studio-fact">
              <span className="studio-fact-label">Installed version</span>
              <span className="studio-fact-value">{status?.current_version ?? '...'}</span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Installed from</span>
              <span className="studio-fact-value">{pacman ? 'Arch package (pacman)' : 'Installer or app bundle'}</span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Last checked</span>
              <span className="studio-fact-value">{lastCheckedText(status?.last_checked ?? null)}</span>
            </div>
          </div>

          {statusFailed && !status ? (
            <div className="studio-hint">Could not read the update status. You can still check for updates.</div>
          ) : null}

          {!configured ? (
            <div className="studio-hint">
              Updates are not set up in this build yet. Install a newer version from the releases page instead.
            </div>
          ) : null}

          {step === 'restart' ? (
            <div className="studio-stack-sm">
              <div className="studio-hint" role="status">
                Version {installedVersion} is installed. Restart Magnm Studio to start using it. Your data stays where it is.
              </div>
              <div className="studio-btn-group">
                <PillButton size="sm" onClick={() => void handleRestart()}>
                  Restart to finish
                </PillButton>
              </div>
            </div>
          ) : available ? (
            <div className="studio-stack-sm">
              <div className="studio-card-title">Version {available.version} is available</div>
              {available.notes ? (
                <p className="studio-hint" style={{ whiteSpace: 'pre-wrap' }}>
                  {available.notes}
                </p>
              ) : null}
              {pacman ? (
                <div className="studio-hint">
                  This downloads the Arch package, checks its signature, and asks for your administrator password to install it with pacman.
                </div>
              ) : null}
              {step === 'installing' ? (
                <div className="studio-stack-sm" role="status" aria-live="polite">
                  <progress
                    aria-label="Download progress"
                    max={100}
                    {...(percent === null ? {} : { value: percent })}
                  />
                  <span className="studio-hint">
                    {progress && progress.total
                      ? `Downloading ${megabytes(progress.downloaded)} of ${megabytes(progress.total)}`
                      : progress && progress.downloaded > 0
                        ? `Downloading ${megabytes(progress.downloaded)}`
                        : 'Working...'}
                  </span>
                </div>
              ) : (
                <div className="studio-btn-group">
                  <PillButton size="sm" disabled={step !== 'idle'} onClick={() => void handleInstall()}>
                    Download and install
                  </PillButton>
                </div>
              )}
            </div>
          ) : upToDate ? (
            <div className="studio-hint" role="status">
              Magnm Studio is up to date.
            </div>
          ) : null}

          {step !== 'restart' ? (
            <div className="studio-btn-group">
              <PillButton
                variant="outline"
                size="sm"
                disabled={step !== 'idle' || !configured}
                onClick={() => void handleCheck()}
              >
                {step === 'checking' ? 'Checking...' : 'Check for updates'}
              </PillButton>
            </div>
          ) : null}
        </div>
      </Card>
    </section>
  );
}
