import { useEffect, useRef, useState } from 'react';
import { Card, Label, PillButton } from '@magnm/design/react';
import type { AppVersionInfo, NativeBridge, UpdateProgressEvent } from '../native';
import { checkForAppUpdate, type AndroidUpdateManifest } from '../updates';

export interface UpdatesPanelProps {
  bridge: NativeBridge;
}

type Step = 'idle' | 'checking' | 'installing';
type Outcome = 'none' | 'up_to_date' | 'installer_opened' | 'needs_permission';

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function messageOf(err: unknown): string {
  if (err instanceof Error && err.message.trim()) return err.message.trim();
  if (typeof err === 'string' && err.trim()) return err.trim();
  return 'Something went wrong with the update. Nothing was changed.';
}

/**
 * More > Updates: check the latest release, show what is new, then download the APK (checked for size and SHA-256 on
 * the phone) and hand it to Android's own installer. Nothing is downloaded or installed without a tap.
 */
export function UpdatesPanel({ bridge }: UpdatesPanelProps) {
  const [installed, setInstalled] = useState<AppVersionInfo | null>(null);
  const [update, setUpdate] = useState<AndroidUpdateManifest | null>(null);
  const [step, setStep] = useState<Step>('idle');
  const [outcome, setOutcome] = useState<Outcome>('none');
  const [progress, setProgress] = useState<UpdateProgressEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    void bridge
      .getAppVersion?.()
      .then((info) => {
        if (alive.current) setInstalled(info);
      })
      .catch(() => undefined);
    const off = bridge.onUpdateProgress?.((event) => {
      if (alive.current) setProgress(event);
    });
    return () => {
      alive.current = false;
      off?.();
    };
  }, [bridge]);

  const handleCheck = async () => {
    setStep('checking');
    setError(null);
    setOutcome('none');
    setUpdate(null);
    try {
      const result = await checkForAppUpdate(bridge);
      if (!alive.current) return;
      setInstalled(result.installed);
      setUpdate(result.update);
      setOutcome(result.update ? 'none' : 'up_to_date');
    } catch (err) {
      if (alive.current) setError(messageOf(err));
    } finally {
      if (alive.current) setStep('idle');
    }
  };

  const handleInstall = async () => {
    if (!update || !bridge.downloadAndInstallUpdate) return;
    setStep('installing');
    setError(null);
    setOutcome('none');
    setProgress(null);
    try {
      const state = await bridge.downloadAndInstallUpdate({
        url: update.url,
        sha256: update.sha256,
        bytes: update.bytes,
        versionCode: update.versionCode,
      });
      if (alive.current) setOutcome(state);
    } catch (err) {
      if (alive.current) setError(messageOf(err));
    } finally {
      if (alive.current) setStep('idle');
    }
  };

  const percent =
    progress && progress.total > 0 ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100)) : null;

  return (
    <Card>
      <div className="mob-stack-sm">
        <Label>Updates</Label>
        {error ? (
          <div className="mob-banner-error" role="alert">
            {error}
          </div>
        ) : null}
        <p className="mob-body-text">
          Installed version: {installed ? `${installed.versionName} (build ${installed.versionCode})` : '...'}
        </p>

        {update ? (
          <div className="mob-stack-sm">
            <p className="mob-body-text">Version {update.version} is available</p>
            {update.notes ? (
              <p className="mob-mono" style={{ whiteSpace: 'pre-wrap' }}>
                {update.notes}
              </p>
            ) : null}
            {step === 'installing' ? (
              <div className="mob-stack-sm" role="status" aria-live="polite">
                <progress aria-label="Download progress" max={100} {...(percent === null ? {} : { value: percent })} />
                <span className="mob-mono">
                  {progress && progress.total > 0
                    ? `Downloading ${megabytes(progress.downloaded)} of ${megabytes(progress.total)}`
                    : 'Working...'}
                </span>
              </div>
            ) : (
              <div className="mob-actions">
                <PillButton size="sm" disabled={step !== 'idle'} onClick={() => void handleInstall()}>
                  Download and install
                </PillButton>
              </div>
            )}
          </div>
        ) : null}

        {outcome === 'up_to_date' ? (
          <p className="mob-body-text" role="status">
            Magnm Studio is up to date.
          </p>
        ) : null}
        {outcome === 'installer_opened' ? (
          <div className="mob-stack-sm" role="status">
            <p className="mob-body-text">The Android installer is open. Confirm there to finish the update.</p>
            <p className="mob-mono">
              If Android says the app was not installed, this version was probably signed with a different key than the one on this phone (for example a test build). Android refuses that on purpose. Uninstall the test build first; that deletes its data on this phone.
            </p>
          </div>
        ) : null}
        {outcome === 'needs_permission' ? (
          <p className="mob-body-text" role="status">
            To install updates, Android needs your permission first. It opened the settings page for Magnm Studio: turn on Allow from this source, go back, and tap Download and install again.
          </p>
        ) : null}

        <div className="mob-actions">
          <PillButton variant="outline" size="sm" disabled={step !== 'idle'} onClick={() => void handleCheck()}>
            {step === 'checking' ? 'Checking...' : 'Check for updates'}
          </PillButton>
        </div>
      </div>
    </Card>
  );
}
