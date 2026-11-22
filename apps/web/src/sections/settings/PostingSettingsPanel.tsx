import { useCallback, useEffect, useState } from 'react';
import { Card, Dialog, Field, PillButton, StatusDot, TextInput } from '@magnm/design/react';
import {
  cancelXOAuth,
  connectInstagram,
  connectLinkedIn,
  disconnectPosting,
  getPostingStatus,
  refreshInstagram,
  startXOAuth,
} from '../../api/posting';
import { useFallbackPoll, useStudioEvents } from '../../events';
import type { PostingAccountStatus, PostingPlatform, PostingStatusInfo } from '../../types';

const POSTING_PLATFORMS: Array<{ id: PostingPlatform; label: string; defaultSteps: string[] }> = [
  {
    id: 'linkedin',
    label: 'LinkedIn',
    defaultSteps: [
      'Open the LinkedIn Developer Portal Token Generator for your app.',
      'Select scopes: openid, profile, w_member_social (personal profile only).',
      'Paste the generated token below (lasts 60 days; Magnm warns 7 days before expiry).',
    ],
  },
  {
    id: 'instagram',
    label: 'Instagram',
    defaultSteps: [
      'Use an Instagram Professional account (Business or Creator).',
      'Generate a User access token in the Meta Developer Dashboard.',
      'Paste the token below (lasts 60 days). Use Refresh token when needed, and provide a public https:// media URL when posting.',
    ],
  },
  {
    id: 'x',
    label: 'X',
    defaultSteps: [
      'Create an OAuth 2.0 App in the X Developer Portal (public client, no client secret).',
      'Add redirect URI: http://127.0.0.1:47831/callback',
      'Enter your Client ID and click Connect with X. Pay-per-use API fees apply per post.',
    ],
  },
];

function formatExpiryLine(info?: PostingAccountStatus): string {
  if (!info || !info.connected) return 'Not connected';
  if (info.expired) return 'Token expired — reconnect or refresh';
  if (info.expires_at) {
    const days = typeof info.days_until_expiry === 'number' ? info.days_until_expiry : null;
    if (info.expiring_soon && days !== null) {
      return `Expires in ${days} day${days === 1 ? '' : 's'} (renew soon)`;
    }
    if (days !== null) {
      return `Expires in ${days} day${days === 1 ? '' : 's'}`;
    }
    return `Expires ${info.expires_at.slice(0, 10)}`;
  }
  return 'Connected';
}

/** Settings panel for official-API posting accounts: setup steps, connect, expiry, refresh, and disconnect. */
export function PostingSettingsPanel() {
  const [status, setStatus] = useState<PostingStatusInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [liToken, setLiToken] = useState('');
  const [igToken, setIgToken] = useState('');
  const [xClientId, setXClientId] = useState('');
  const [xAuthUrl, setXAuthUrl] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState<PostingPlatform | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await getPostingStatus();
      if (res && typeof res === 'object' && 'platforms' in res) {
        setStatus(res);
        const savedCid = res.platforms?.x?.client_id;
        if (savedCid) {
          setXClientId((prev) => (prev ? prev : savedCid));
        }
        if (!res.platforms?.x?.oauth_active) {
          setXAuthUrl(null);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load posting settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const eventsState = useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'posting.updated') {
          void loadStatus();
        }
      },
      [loadStatus],
    ),
  );

  const xOAuthActive = Boolean(status?.platforms?.x?.oauth_active);

  useFallbackPoll(
    useCallback(() => {
      void loadStatus();
    }, [loadStatus]),
    xOAuthActive,
    eventsState,
    3000,
  );

  const handleConnectLinkedIn = async () => {
    const trimmed = liToken.trim();
    if (!trimmed) {
      setError('Paste a LinkedIn token first.');
      return;
    }
    setLiToken('');
    setBusyId('connect-linkedin');
    setError(null);
    setNotice(null);
    try {
      await connectLinkedIn(trimmed);
      await loadStatus();
      setNotice('Connected LinkedIn account.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect LinkedIn.');
    } finally {
      setBusyId(null);
    }
  };

  const handleConnectInstagram = async () => {
    const trimmed = igToken.trim();
    if (!trimmed) {
      setError('Paste an Instagram token first.');
      return;
    }
    setIgToken('');
    setBusyId('connect-instagram');
    setError(null);
    setNotice(null);
    try {
      await connectInstagram(trimmed);
      await loadStatus();
      setNotice('Connected Instagram account.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect Instagram.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRefreshInstagram = async () => {
    setBusyId('refresh-instagram');
    setError(null);
    setNotice(null);
    try {
      await refreshInstagram();
      await loadStatus();
      setNotice('Refreshed Instagram token.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not refresh Instagram token.');
    } finally {
      setBusyId(null);
    }
  };

  const handleStartXOAuth = async () => {
    const trimmed = xClientId.trim();
    if (!trimmed) {
      setError('Enter your X OAuth Client ID first.');
      return;
    }
    setBusyId('connect-x');
    setError(null);
    setNotice(null);
    try {
      const res = await startXOAuth(trimmed);
      setXAuthUrl(res.authorize_url);
      await loadStatus();
      if (typeof window !== 'undefined' && typeof window.open === 'function') {
        window.open(res.authorize_url, '_blank', 'noopener,noreferrer');
      }
      setNotice('Complete the X sign-in within 5 minutes. If no browser opened, copy the address shown under X.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start X sign-in.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelXOAuth = async () => {
    setBusyId('cancel-x');
    setError(null);
    setNotice(null);
    try {
      await cancelXOAuth();
      setXAuthUrl(null);
      await loadStatus();
      setNotice('Cancelled X sign-in.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel X sign-in.');
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmDisconnect = async () => {
    const plat = confirmDisconnect;
    if (!plat) return;
    setConfirmDisconnect(null);
    setBusyId(`disconnect-${plat}`);
    setError(null);
    setNotice(null);
    try {
      await disconnectPosting(plat);
      await loadStatus();
      setNotice(`Disconnected ${plat}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${plat}.`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="studio-section" aria-labelledby="settings-posting-heading">
      <div className="studio-settings-head">
        <span className="studio-settings-num">12</span>
        <h2 id="settings-posting-heading" className="studio-section-title">
          Posting
        </h2>
      </div>

      <Card>
        <div className="studio-hint">
          Optional official-API publishing. Off per platform until connected. Tokens stay encrypted on
          this laptop with your OS keychain and every post requires an explicit human confirmation click.
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
        <div className="mg-empty">Loading posting settings.</div>
      ) : (
        <div className="studio-stack-sm">
          {POSTING_PLATFORMS.map(({ id, label, defaultSteps }) => {
            const info = status?.platforms?.[id];
            const connected = Boolean(info?.connected);
            const steps = info?.setup_steps?.length ? info.setup_steps : defaultSteps;
            const dotState = !connected
              ? 'off'
              : info?.expired
                ? 'error'
                : info?.expiring_soon
                  ? 'idle'
                  : 'online';
            const dotLabel = !connected
              ? 'Off'
              : info?.expired
                ? 'Expired'
                : info?.expiring_soon
                  ? 'Expiring soon'
                  : 'Connected';

            return (
              <Card key={id} outline>
                <div className="studio-stack-sm">
                  <div className="studio-card-head">
                    <div>
                      <div className="studio-card-title">{label}</div>
                      <div className="studio-mono">
                        {connected
                          ? `${info?.display_name || info?.account_id || 'Connected'} · ${formatExpiryLine(info)}`
                          : 'Off until connected'}
                      </div>
                    </div>
                    <StatusDot state={dotState} label={dotLabel} />
                  </div>

                  {connected && (info?.expiring_soon || info?.expired) ? (
                    <div className="mg-notice" role="status">
                      {info.expired
                        ? `${label} token has expired. Reconnect or refresh before posting.`
                        : `${label} token expires in ${info.days_until_expiry} day(s).`}
                    </div>
                  ) : null}

                  <ol className="studio-social-risks">
                    {steps.map((step) => (
                      <li key={step} className="studio-hint">
                        {step}
                      </li>
                    ))}
                  </ol>

                  {id === 'linkedin' ? (
                    <div className="studio-role-controls">
                      <TextInput
                        type="password"
                        autoComplete="off"
                        name="posting-linkedin-token"
                        aria-label="LinkedIn access token"
                        placeholder={connected ? 'Replace LinkedIn token...' : 'Paste LinkedIn token...'}
                        value={liToken}
                        onChange={(e) => setLiToken(e.target.value)}
                      />
                      <PillButton
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => void handleConnectLinkedIn()}
                      >
                        {busyId === 'connect-linkedin' ? 'Connecting...' : connected ? 'Reconnect' : 'Connect'}
                      </PillButton>
                    </div>
                  ) : null}

                  {id === 'instagram' ? (
                    <div className="studio-stack-xs">
                      <div className="studio-role-controls">
                        <TextInput
                          type="password"
                          autoComplete="off"
                          name="posting-instagram-token"
                          aria-label="Instagram access token"
                          placeholder={connected ? 'Replace Instagram token...' : 'Paste Instagram token...'}
                          value={igToken}
                          onChange={(e) => setIgToken(e.target.value)}
                        />
                        <PillButton
                          size="sm"
                          disabled={busyId !== null}
                          onClick={() => void handleConnectInstagram()}
                        >
                          {busyId === 'connect-instagram'
                            ? 'Connecting...'
                            : connected
                              ? 'Reconnect'
                              : 'Connect'}
                        </PillButton>
                        {connected ? (
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busyId !== null}
                            onClick={() => void handleRefreshInstagram()}
                          >
                            {busyId === 'refresh-instagram' ? 'Refreshing...' : 'Refresh token'}
                          </PillButton>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  {id === 'x' ? (
                    <div className="studio-stack-xs">
                      <div className="studio-role-controls">
                        <Field label="Client ID" htmlFor="posting-x-client-id">
                          <TextInput
                            id="posting-x-client-id"
                            autoComplete="off"
                            placeholder="X OAuth 2.0 Client ID"
                            value={xClientId}
                            onChange={(e) => setXClientId(e.target.value)}
                          />
                        </Field>
                        <PillButton
                          size="sm"
                          disabled={busyId !== null || xOAuthActive}
                          onClick={() => void handleStartXOAuth()}
                        >
                          {busyId === 'connect-x'
                            ? 'Starting...'
                            : xOAuthActive
                              ? 'Waiting for X...'
                              : connected
                                ? 'Reconnect with X'
                                : 'Connect with X'}
                        </PillButton>
                        {xOAuthActive ? (
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busyId !== null}
                            onClick={() => void handleCancelXOAuth()}
                          >
                            Cancel
                          </PillButton>
                        ) : null}
                      </div>
                      {xAuthUrl ? (
                        <div className="studio-hint">
                          <a
                            href={xAuthUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="studio-inline-link"
                          >
                            Open X sign-in page
                          </a>{' '}
                          (callback on http://127.0.0.1:47831/callback, expires in 5 minutes).
                          <div className="studio-mono-wrap" style={{ userSelect: 'text' }}>
                            {xAuthUrl}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {connected ? (
                    <div className="studio-row-actions">
                      <PillButton
                        variant="danger"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => setConfirmDisconnect(id)}
                      >
                        Disconnect
                      </PillButton>
                    </div>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog
        open={Boolean(confirmDisconnect)}
        onClose={() => setConfirmDisconnect(null)}
        title="Disconnect posting account"
        subtitle={confirmDisconnect ? confirmDisconnect.toUpperCase() : undefined}
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmDisconnect(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              onClick={() => void handleConfirmDisconnect()}
            >
              Confirm disconnect
            </PillButton>
          </>
        }
      >
        <p className="mg-sub">
          Disconnect <span className="studio-mono">{confirmDisconnect}</span> and delete its saved
          encrypted token and keychain key?
        </p>
      </Dialog>
    </section>
  );
}
