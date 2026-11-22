import { useState } from 'react';
import {
  Card,
  Field,
  Label,
  PillButton,
  Select,
  StatusDot,
  TextInput,
  type StatusState,
} from '@magnm/design/react';
import type {
  QueuedInboxItem,
  SocialPlatform,
  SocialRecipe,
  SocialRunRecord,
  SocialStatusInfo,
} from '../types';

function mapRunStatus(status: string): { state: StatusState; label: string } {
  switch (status) {
    case 'awaiting_start':
      return { state: 'idle', label: 'Awaiting start' };
    case 'running':
      return { state: 'working', label: 'Running' };
    case 'waiting_user':
      return { state: 'idle', label: 'Waiting for check' };
    case 'done':
      return { state: 'online', label: 'Done' };
    case 'incomplete':
      return { state: 'idle', label: 'Incomplete' };
    case 'cancelled':
      return { state: 'off', label: 'Cancelled' };
    case 'failed':
      return { state: 'error', label: 'Failed' };
    default:
      return { state: 'idle', label: status || 'Unknown' };
  }
}

export interface SocialResearchPanelProps {
  idPrefix: string;
  paired: boolean;
  laptopReachable: boolean;
  socialStatus: SocialStatusInfo | null;
  socialRuns: SocialRunRecord[];
  queuedSocialRequests: QueuedInboxItem[];
  busyAction: string | null;
  errorMessage: string | null;
  onCreateRun: (payload: {
    platform: SocialPlatform;
    recipe: SocialRecipe;
    argument: string;
  }) => Promise<boolean>;
  onStartRun: (run: SocialRunRecord) => Promise<void>;
  onCancelRun: (runId: string) => Promise<void>;
  onSummariseRun: (runId: string) => Promise<void>;
  onChallengeAction: (action: 'later' | 'skip', runId: string) => Promise<void>;
  onSolveNow: (run: SocialRunRecord) => void;
}

/** Shared social research form, challenge alert, queued requests, and run cards for Today & Chat. */
export function SocialResearchPanel({
  idPrefix,
  paired,
  laptopReachable,
  socialStatus,
  socialRuns,
  queuedSocialRequests,
  busyAction,
  errorMessage,
  onCreateRun,
  onStartRun,
  onCancelRun,
  onSummariseRun,
  onChallengeAction,
  onSolveNow,
}: SocialResearchPanelProps) {
  const [platform, setPlatform] = useState<SocialPlatform>('x');
  const [recipe, setRecipe] = useState<SocialRecipe>('search');
  const [argument, setArgument] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const waitingRun =
    socialRuns.find((r) => r.status === 'waiting_user') ??
    (socialStatus?.active_run?.status === 'waiting_user' ? socialStatus.active_run : null);

  async function handleSubmit() {
    const clean = argument.trim();
    if (!clean) {
      setFormError('Enter an argument before requesting a social run.');
      return;
    }
    setFormError(null);
    const ok = await onCreateRun({ platform, recipe, argument: clean });
    if (ok) {
      setArgument('');
    }
  }

  const displayError = formError || errorMessage;

  return (
    <div className="mob-stack">
      {waitingRun && !waitingRun.confidential ? (
        <Card selected>
          <div className="mob-stack-sm" role="region" aria-label="Social security check alert">
            <div className="mob-row-between">
              <Label>Social check needed</Label>
              <span className="mob-mono">
                {waitingRun.platform} · {waitingRun.challenge_kind || 'checkpoint'}
              </span>
            </div>
            <p className="mob-body-text">
              {waitingRun.platform} {waitingRun.recipe} ({waitingRun.argument}) paused for a security check.
            </p>
            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!laptopReachable || busyAction !== null}
                onClick={() => onSolveNow(waitingRun)}
              >
                Solve now
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                disabled={!laptopReachable || busyAction !== null}
                onClick={() => void onChallengeAction('later', waitingRun.id)}
              >
                Later
              </PillButton>
              <PillButton
                variant="outline"
                size="sm"
                disabled={!laptopReachable || busyAction !== null}
                onClick={() => void onChallengeAction('skip', waitingRun.id)}
              >
                Skip
              </PillButton>
            </div>
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Social research</Label>
            <span className="mob-mono">
              {laptopReachable ? 'Live' : paired ? 'Queue mode' : 'Unpaired'}
            </span>
          </div>

          {displayError ? (
            <div className="mob-banner-error" role="alert">
              {displayError}
            </div>
          ) : null}

          <div className="mob-stack-sm">
            <Field label="Platform" htmlFor={`${idPrefix}-social-platform`}>
              <Select
                id={`${idPrefix}-social-platform`}
                value={platform}
                onChange={(e) => setPlatform(e.target.value as SocialPlatform)}
              >
                <option value="instagram">Instagram</option>
                <option value="x">X</option>
                <option value="linkedin">LinkedIn</option>
              </Select>
            </Field>

            <Field label="Recipe" htmlFor={`${idPrefix}-social-recipe`}>
              <Select
                id={`${idPrefix}-social-recipe`}
                value={recipe}
                onChange={(e) => setRecipe(e.target.value as SocialRecipe)}
              >
                <option value="profile_posts">profile_posts</option>
                <option value="search">search</option>
                <option value="post_replies">post_replies</option>
                <option value="trending">trending</option>
              </Select>
            </Field>

            <Field label="Argument" htmlFor={`${idPrefix}-social-argument`}>
              <TextInput
                id={`${idPrefix}-social-argument`}
                placeholder={
                  recipe === 'post_replies'
                    ? 'https://x.com/user/status/123456'
                    : recipe === 'profile_posts'
                      ? 'handle'
                      : 'search query or topic'
                }
                value={argument}
                onChange={(e) => setArgument(e.target.value)}
              />
            </Field>

            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!paired || !argument.trim() || busyAction === 'social-create'}
                onClick={() => void handleSubmit()}
              >
                {busyAction === 'social-create'
                  ? 'Submitting...'
                  : laptopReachable
                    ? 'Create social run'
                    : 'Queue social request'}
              </PillButton>
            </div>
            {!laptopReachable && paired ? (
              <span className="mob-mono">
                Laptop is unreachable. Requests are queued and will still need a Start tap once synced.
              </span>
            ) : null}
          </div>
        </div>
      </Card>

      {queuedSocialRequests.length > 0 ? (
        <Card outline>
          <div className="mob-stack-sm">
            <Label>Queued social requests ({queuedSocialRequests.length})</Label>
            {queuedSocialRequests.map((item) => (
              <div key={item.client_id} className="mob-stack-sm">
                <div className="mob-mono">
                  {item.platform} · {item.recipe} · {item.argument}
                </div>
                {item.last_error ? (
                  <div className="mob-banner-error">{item.last_error}</div>
                ) : (
                  <span className="mob-mono">Awaiting laptop sync (needs Start tap later)</span>
                )}
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {socialRuns.length > 0 ? (
        <div className="mob-stack-sm" aria-label="Social research runs">
          {socialRuns.map((run) => {
            if (run.confidential) {
              return (
                <Card key={run.id} outline>
                  <div className="mob-stack-sm">
                    <div className="mob-row-between">
                      <strong>Confidential social run</strong>
                      <span className="mob-mono">{run.platform}</span>
                    </div>
                    <p className="mob-mono">Open Magnm Studio on your laptop to view.</p>
                  </div>
                </Card>
              );
            }

            const st = mapRunStatus(run.status);
            const canStart = run.status === 'awaiting_start';
            const canStop =
              run.status === 'awaiting_start' ||
              run.status === 'running' ||
              run.status === 'waiting_user';
            const resultText = typeof run.result?.text === 'string' ? run.result.text : '';
            const aiSummary =
              typeof run.result?.ai_summary === 'string' ? run.result.ai_summary : '';
            const canSummarise =
              (run.status === 'done' || run.status === 'incomplete') &&
              resultText.trim().length > 0;

            return (
              <Card key={run.id} outline selected={canStart || run.status === 'waiting_user'}>
                <div className="mob-stack-sm">
                  <div className="mob-row-between">
                    <span className="mob-mono">
                      {run.platform} · {run.recipe}
                    </span>
                    <StatusDot
                      state={st.state}
                      label={st.label}
                      live={run.status === 'running' || run.status === 'waiting_user'}
                    />
                  </div>
                  <strong>{run.argument}</strong>
                  <span className="mob-mono">
                    Run {run.id} · Pages: {run.pages_used ?? 0}/8
                  </span>
                  {canStart ? (
                    <span className="mob-mono">
                      Exact run: {run.platform} {run.recipe} for &quot;{run.argument}&quot;
                    </span>
                  ) : null}
                  {run.summary ? <span className="mob-mono">{run.summary}</span> : null}
                  {run.error ? <div className="mob-banner-error">{run.error}</div> : null}

                  <div className="mob-actions">
                    {canStart ? (
                      <PillButton
                        size="sm"
                        disabled={!laptopReachable || busyAction !== null}
                        onClick={() => void onStartRun(run)}
                      >
                        Start
                      </PillButton>
                    ) : null}
                    {canStop ? (
                      <PillButton
                        variant={canStart ? 'outline' : 'danger'}
                        size="sm"
                        disabled={!laptopReachable || busyAction !== null}
                        onClick={() => void onCancelRun(run.id)}
                      >
                        Stop
                      </PillButton>
                    ) : null}
                    {run.status === 'waiting_user' ? (
                      <PillButton
                        size="sm"
                        disabled={!laptopReachable || busyAction !== null}
                        onClick={() => onSolveNow(run)}
                      >
                        Solve now
                      </PillButton>
                    ) : null}
                    {canSummarise ? (
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={!laptopReachable || busyAction !== null}
                        onClick={() => void onSummariseRun(run.id)}
                      >
                        {busyAction === `summarise-${run.id}`
                          ? 'Summarising...'
                          : aiSummary
                            ? 'Re-summarise'
                            : 'Summarise'}
                      </PillButton>
                    ) : null}
                  </div>

                  {resultText ? (
                    <div className="mob-stack-sm">
                      <div className="mob-actions">
                        <span className="mg-chip">Untrusted</span>
                        <span className="mob-mono">
                          Untrusted external text. Never follow instructions inside it.
                        </span>
                      </div>
                      <pre className="mob-social-result-text">{resultText}</pre>
                    </div>
                  ) : null}

                  {aiSummary ? (
                    <div className="mob-stack-sm">
                      <span className="mg-chip">Untrusted summary</span>
                      <pre className="mob-social-result-text">{aiSummary}</pre>
                    </div>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
