import { useCallback, useEffect, useState } from 'react';
import { Card, Checkbox, Dialog, PillButton, StatusDot } from '@magnm/design/react';
import {
  cancelSocialLogin,
  disableSocialPlatform,
  doneSocialLogin,
  enableSocialPlatform,
  getSocialStatus,
  removeSocialPlatform,
  startSocialLogin,
} from '../../api/social';
import { useStudioEvents } from '../../events';
import type { SocialPlatform, SocialStatusInfo } from '../../types';

const SOCIAL_PLATFORMS: Array<{ id: SocialPlatform; label: string }> = [
  { id: 'instagram', label: 'Instagram' },
  { id: 'x', label: 'X' },
  { id: 'linkedin', label: 'LinkedIn' },
];

const PLATFORM_RISKS: string[] = [
  'Automated access breaks the platform terms of service.',
  'The account may face temporary locks or a permanent ban.',
  'Use a separate research account, never your main Magnm brand account.',
];

/** Settings panel for opt-in social research: risk acknowledgement, login window, vault removal, pause & page counters. */
export function SocialResearchSettingsPanel() {
  const [status, setStatus] = useState<SocialStatusInfo | null>(null);
  const [loading, setLoading] = useState(true);
  // Checkbox is NEVER pre-ticked.
  const [ackChecked, setAckChecked] = useState<Record<SocialPlatform, boolean>>({
    instagram: false,
    x: false,
    linkedin: false,
  });
  const [confirmRemovePlat, setConfirmRemovePlat] = useState<SocialPlatform | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await getSocialStatus();
      setStatus(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load social research settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'social_run.status' || eventName === 'social_challenge_cleared') {
          void loadStatus();
        }
      },
      [loadStatus],
    ),
  );

  const handleEnable = async (plat: SocialPlatform) => {
    if (!ackChecked[plat]) return;
    setBusyId(`enable-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await enableSocialPlatform(plat, true, status?.acknowledgement_summary);
      setAckChecked((prev) => ({ ...prev, [plat]: false }));
      await loadStatus();
      setNotice(`Enabled social research for ${plat}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not enable ${plat}.`);
    } finally {
      setBusyId(null);
    }
  };

  const handleDisable = async (plat: SocialPlatform) => {
    setBusyId(`disable-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await disableSocialPlatform(plat);
      setAckChecked((prev) => ({ ...prev, [plat]: false }));
      await loadStatus();
      setNotice(`Disabled social research for ${plat}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disable ${plat}.`);
    } finally {
      setBusyId(null);
    }
  };

  const handleLoginStart = async (plat: SocialPlatform) => {
    setBusyId(`login-start-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await startSocialLogin(plat);
      await loadStatus();
      setNotice(`Opened visible sign-in window for ${plat}. Sign in there, then click Done.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not start sign-in for ${plat}.`);
    } finally {
      setBusyId(null);
    }
  };

  const handleLoginDone = async (plat: SocialPlatform) => {
    setBusyId(`login-done-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await doneSocialLogin(plat);
      await loadStatus();
      setNotice(`Saved encrypted session for ${plat}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not save session for ${plat}.`);
    } finally {
      setBusyId(null);
    }
  };

  const handleLoginCancel = async () => {
    setBusyId('login-cancel');
    setError(null);
    setNotice(null);
    try {
      await cancelSocialLogin();
      await loadStatus();
      setNotice('Closed sign-in window.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel sign-in.');
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmRemove = async () => {
    const plat = confirmRemovePlat;
    if (!plat) return;
    setConfirmRemovePlat(null);
    setBusyId(`remove-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await removeSocialPlatform(plat);
      await loadStatus();
      setNotice(`Removed saved session for ${plat}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not remove ${plat} session.`);
    } finally {
      setBusyId(null);
    }
  };

  const loginActive = Boolean(status?.login?.active);
  const loginPlat = status?.login?.platform ?? null;
  const hasActiveRun = Boolean(status?.active_run);

  return (
    <section className="studio-section" aria-labelledby="settings-social-heading">
      <div className="studio-settings-head">
        <span className="studio-settings-num">11</span>
        <h2 id="settings-social-heading" className="studio-section-title">
          Social research
        </h2>
      </div>

      <Card>
        <div className="studio-stack-xs">
          <div className="studio-hint">
            Opt-in read-only research using a separate research account stored encrypted on this laptop.
          </div>
          <div className="studio-hint">
            Note: Platform selectors are untested on the live sites (Instagram, X, LinkedIn), and FCM push notifications are not set up.
          </div>
        </div>
      </Card>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="mg-notice" role="status">
          {notice}
        </div>
      ) : null}

      {loading && !status ? (
        <div className="mg-empty">Loading social research settings.</div>
      ) : (
        <div className="studio-stack-sm">
          {SOCIAL_PLATFORMS.map(({ id, label }) => {
            const platInfo = status?.platforms?.[id];
            const enabled = Boolean(platInfo?.enabled);
            const hasSession = Boolean(platInfo?.has_session);
            const paused = Boolean(platInfo?.paused);
            const isLoggingInHere = loginActive && loginPlat === id;

            return (
              <Card key={id} outline>
                <div className="studio-stack-sm">
                  <div className="studio-card-head">
                    <div>
                      <div className="studio-card-title">{label}</div>
                      <div className="studio-mono">
                        {platInfo?.pages_today ?? 0} / {platInfo?.daily_limit ?? 20} pages used today
                        {hasSession ? ' · Session saved' : ' · Not signed in'}
                      </div>
                    </div>
                    <StatusDot
                      state={paused ? 'error' : enabled ? 'online' : 'off'}
                      label={paused ? 'Paused' : enabled ? 'Enabled' : 'Disabled'}
                    />
                  </div>

                  {paused ? (
                    <div className="mg-notice" role="status">
                      {platInfo?.pause_reason || `Paused until ${platInfo?.paused_until || 'cooldown ends'}.`}
                    </div>
                  ) : null}

                  <ul className="studio-social-risks">
                    {PLATFORM_RISKS.map((risk) => (
                      <li key={risk} className="studio-hint">
                        {risk}
                      </li>
                    ))}
                  </ul>

                  {!enabled ? (
                    <Checkbox
                      label={`I understand the three risks for ${label} and am using a separate research account.`}
                      checked={Boolean(ackChecked[id])}
                      onChange={(e) =>
                        setAckChecked((prev) => ({ ...prev, [id]: e.target.checked }))
                      }
                    />
                  ) : null}

                  <div className="studio-row-actions">
                    {!enabled ? (
                      <PillButton
                        size="sm"
                        disabled={!ackChecked[id] || busyId !== null}
                        onClick={() => void handleEnable(id)}
                      >
                        {busyId === `enable-${id}` ? 'Enabling...' : 'Enable'}
                      </PillButton>
                    ) : (
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => void handleDisable(id)}
                      >
                        {busyId === `disable-${id}` ? 'Disabling...' : 'Disable'}
                      </PillButton>
                    )}

                    {isLoggingInHere ? (
                      <>
                        <PillButton
                          size="sm"
                          disabled={busyId !== null}
                          onClick={() => void handleLoginDone(id)}
                        >
                          {busyId === `login-done-${id}` ? 'Saving...' : 'Done'}
                        </PillButton>
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={busyId !== null}
                          onClick={() => void handleLoginCancel()}
                        >
                          Cancel
                        </PillButton>
                      </>
                    ) : (
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={!enabled || paused || loginActive || hasActiveRun || busyId !== null}
                        onClick={() => void handleLoginStart(id)}
                      >
                        {busyId === `login-start-${id}` ? 'Opening...' : 'Sign in'}
                      </PillButton>
                    )}

                    {hasSession ? (
                      <PillButton
                        variant="danger"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => setConfirmRemovePlat(id)}
                      >
                        Remove
                      </PillButton>
                    ) : null}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog
        open={Boolean(confirmRemovePlat)}
        onClose={() => setConfirmRemovePlat(null)}
        title="Remove social session"
        subtitle={confirmRemovePlat ? confirmRemovePlat.toUpperCase() : undefined}
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmRemovePlat(null)}>
              Cancel
            </PillButton>
            <PillButton variant="danger" size="sm" onClick={() => void handleConfirmRemove()}>
              Confirm remove
            </PillButton>
          </>
        }
      >
        <p className="mg-sub">
          Remove the saved encrypted session and keychain key for{' '}
          <span className="studio-mono">{confirmRemovePlat}</span>?
        </p>
      </Dialog>
    </section>
  );
}
