import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, PillButton, Wordmark } from '@magnm/design/react';
import { cancelLogin, getAccount, startLogin } from '../api/account';

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

export interface OnboardingScreenProps {
  /** Called once the engine reports a signed-in account. */
  onSignedIn: () => void;
  /** Called when the person chooses to work locally without signing in. */
  onContinue: () => void;
}

/** First-run welcome: sign in with an invited Google account, or continue with local work. */
export function OnboardingScreen({ onSignedIn, onContinue }: OnboardingScreenProps) {
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    startedAtRef.current = Date.now();
    timerRef.current = setInterval(() => {
      if (Date.now() - startedAtRef.current >= POLL_TIMEOUT_MS) {
        stopPolling();
        setAuthorizeUrl(null);
        setError('Google sign-in timed out. Please try again.');
        return;
      }
      void (async () => {
        try {
          const next = await getAccount();
          if (next.signed_in) {
            stopPolling();
            setAuthorizeUrl(null);
            onSignedIn();
          } else if (next.last_error) {
            // For example: the Google account is not invited. Show the server's own message.
            stopPolling();
            setAuthorizeUrl(null);
            setError(next.last_error);
          }
        } catch (err) {
          stopPolling();
          setAuthorizeUrl(null);
          setError(err instanceof Error ? err.message : 'Could not check the sign-in status.');
        }
      })();
    }, POLL_INTERVAL_MS);
  }, [onSignedIn, stopPolling]);

  const handleSignIn = async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await startLogin(true);
      setAuthorizeUrl(res.authorize_url);
      startPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start Google sign-in.');
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    stopPolling();
    setAuthorizeUrl(null);
    try {
      await cancelLogin();
    } catch {
      // The pending sign-in expires by itself.
    }
  };

  return (
    <div className="mg-root studio-welcome">
      <main className="studio-welcome-main">
        <Wordmark compact />
        <h1 className="studio-welcome-title">Welcome to Magnm Studio</h1>
        <p className="mg-lead">
          Sign in with your Google account to share work with your team. Sign-up is invite-only.
          Ask your admin to invite your Google account.
        </p>

        <Card>
          <div className="studio-stack-sm">
            {error ? (
              <div className="mg-notice" role="alert">
                {error}
              </div>
            ) : null}
            {authorizeUrl ? (
              <div className="studio-stack-sm">
                <div className="studio-card-title">Waiting for Google sign-in in your browser...</div>
                <div className="studio-row-actions">
                  <a className="studio-inline-link" href={authorizeUrl} target="_blank" rel="noreferrer noopener">
                    Open the sign-in page again
                  </a>
                  <PillButton variant="quiet" size="sm" onClick={() => void handleCancel()}>
                    Cancel
                  </PillButton>
                </div>
              </div>
            ) : (
              <div className="studio-row-actions">
                <PillButton disabled={busy} onClick={() => void handleSignIn()}>
                  Sign in with Google
                </PillButton>
                <PillButton variant="outline" onClick={onContinue}>
                  Continue without signing in
                </PillButton>
              </div>
            )}
            <div className="studio-hint">
              Without signing in, your local work on this computer still works. You can sign in later
              from Settings, Account.
            </div>
          </div>
        </Card>
      </main>
    </div>
  );
}
