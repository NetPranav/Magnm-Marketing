import { Card, PillButton, Wordmark } from '@magnm/design/react';

export interface WelcomeGateProps {
  /** Message from the last sign-in attempt, such as the refusal for an account that is not invited. */
  error: string | null;
  busy: boolean;
  onSignIn: () => void;
}

/** First screen of the phone app: signing in with an invited Google account is required before anything else. */
export function WelcomeGate({ error, busy, onSignIn }: WelcomeGateProps) {
  return (
    <main className="mob-welcome">
      <Wordmark compact />
      <h1 className="mob-welcome-title">Welcome to Magnm Studio</h1>
      <p className="mob-body-text">
        Sign-up is invite-only. Ask your admin to invite your Google account, then sign in with it here.
      </p>
      <Card>
        <div className="mob-stack-sm">
          {error ? (
            <div className="mob-banner-error" role="alert">
              {error}
            </div>
          ) : null}
          <div className="mob-actions">
            <PillButton disabled={busy} onClick={onSignIn}>
              Sign in with Google
            </PillButton>
          </div>
          <span className="mob-mono">After signing in you can pair this phone with your laptop under More.</span>
        </div>
      </Card>
    </main>
  );
}
