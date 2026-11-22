import { useState } from 'react';
import { Card, PillButton } from '@magnm/design/react';
import { socialChallengeLater, socialChallengeSkip } from '../api/social';
import type { SocialRunRecord } from '../types';

export interface SocialChallengeBannerProps {
  run: SocialRunRecord;
  onSolveNow: () => void;
  onResolved: (run?: SocialRunRecord) => void;
}

/** Global alert shown across all sections when a social run is paused in waiting_user. */
export function SocialChallengeBanner({
  run,
  onSolveNow,
  onResolved,
}: SocialChallengeBannerProps) {
  const [busy, setBusy] = useState<'later' | 'skip' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAction = async (kind: 'later' | 'skip') => {
    setBusy(kind);
    setError(null);
    try {
      const res =
        kind === 'later'
          ? await socialChallengeLater(run.id)
          : await socialChallengeSkip(run.id);
      onResolved(res?.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card selected className="studio-social-alert" role="region" aria-label="Social security check alert">
      <div className="studio-section-bar">
        <div className="studio-stack-xs">
          <div className="mg-label">
            Social check needed · {run.platform} · {run.challenge_kind || 'checkpoint'}
          </div>
          <div className="studio-hint">
            Researcher paused on {run.platform} ({run.recipe}: {run.argument}).
          </div>
          {error ? (
            <div className="mg-notice" role="alert">
              {error}
            </div>
          ) : null}
        </div>
        <div className="studio-row-actions">
          <PillButton size="sm" disabled={busy !== null} onClick={onSolveNow}>
            Solve now
          </PillButton>
          <PillButton
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void handleAction('later')}
          >
            {busy === 'later' ? 'Pausing...' : 'Later'}
          </PillButton>
          <PillButton
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void handleAction('skip')}
          >
            {busy === 'skip' ? 'Skipping...' : 'Skip'}
          </PillButton>
        </div>
      </div>
    </Card>
  );
}
