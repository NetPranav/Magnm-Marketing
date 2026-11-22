import { useState } from 'react';
import { Card, PillButton, StatusDot, type StatusState } from '@magnm/design/react';
import { cancelSocialRun, startSocialRun, summariseSocialRun } from '../api/social';
import type { SocialRunRecord } from '../types';
import { formatTimestamp } from '../ui/format';

export function mapSocialRunStatus(status: string): { state: StatusState; label: string } {
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

export function formatSocialPlatformLabel(platform: string): string {
  switch (platform) {
    case 'instagram':
      return 'Instagram';
    case 'x':
      return 'X';
    case 'linkedin':
      return 'LinkedIn';
    default:
      return platform;
  }
}

export interface SocialRunCardProps {
  run: SocialRunRecord;
  onRunUpdated?: (run: SocialRunRecord) => void;
  onOpenSolve?: (run: SocialRunRecord) => void;
}

/** Card for a single social research run: exact command preview, Start/Stop, untrusted plain-text result, and Summarise. */
export function SocialRunCard({ run, onRunUpdated, onOpenSolve }: SocialRunCardProps) {
  const [busy, setBusy] = useState<'start' | 'stop' | 'summarise' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const statusInfo = mapSocialRunStatus(run.status);
  const isLive = run.status === 'running' || run.status === 'waiting_user';
  const canStart = run.status === 'awaiting_start';
  const canStop =
    run.status === 'awaiting_start' ||
    run.status === 'running' ||
    run.status === 'waiting_user';
  const resultText = typeof run.result?.text === 'string' ? run.result.text : '';
  const aiSummary = typeof run.result?.ai_summary === 'string' ? run.result.ai_summary : '';
  const canSummarise =
    (run.status === 'done' || run.status === 'incomplete') && resultText.trim().length > 0;

  const handleStart = async () => {
    if (!run.approval_hash) {
      setError('Run approval hash is missing.');
      return;
    }
    setBusy('start');
    setError(null);
    try {
      const res = await startSocialRun(run.id, run.approval_hash);
      if (res?.run) onRunUpdated?.(res.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start social run.');
    } finally {
      setBusy(null);
    }
  };

  const handleStop = async () => {
    setBusy('stop');
    setError(null);
    try {
      const res = await cancelSocialRun(run.id);
      if (res?.run) onRunUpdated?.(res.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop social run.');
    } finally {
      setBusy(null);
    }
  };

  const handleSummarise = async () => {
    setBusy('summarise');
    setError(null);
    try {
      const res = await summariseSocialRun(run.id);
      if (res?.run) onRunUpdated?.(res.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to summarise social run.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card outline selected={canStart || run.status === 'waiting_user'}>
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">
              Social research · {formatSocialPlatformLabel(run.platform)} · {run.recipe}
              {run.requested_by ? ` · Requested by ${run.requested_by}` : ''}
            </div>
            <div className="studio-card-title">{run.argument}</div>
          </div>
          <StatusDot state={statusInfo.state} label={statusInfo.label} live={isLive} />
        </div>

        <div className="studio-mono">
          Run {run.id} · Platform: {run.platform} · Recipe: {run.recipe} · Pages: {run.pages_used ?? 0}/8
          {run.created_at ? ` · ${formatTimestamp(run.created_at)}` : ''}
        </div>

        {canStart ? (
          <div className="studio-hint">
            Exact run: read-only {formatSocialPlatformLabel(run.platform)} {run.recipe} for &quot;{run.argument}&quot;. Nothing starts until you tap Start.
          </div>
        ) : null}

        {run.status === 'waiting_user' ? (
          <div className="mg-notice" role="status">
            Paused on security check ({run.challenge_kind || 'checkpoint'}).
            {onOpenSolve ? (
              <>
                {' '}
                <PillButton size="sm" onClick={() => onOpenSolve(run)}>
                  Solve now
                </PillButton>
              </>
            ) : null}
          </div>
        ) : null}

        {run.summary ? <div className="studio-mono-wrap">{run.summary}</div> : null}
        {run.error ? <div className="studio-hint">Error: {run.error}</div> : null}
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}

        <div className="studio-row-actions">
          {canStart ? (
            <PillButton size="sm" disabled={busy !== null} onClick={() => void handleStart()}>
              {busy === 'start' ? 'Starting...' : 'Start'}
            </PillButton>
          ) : null}
          {canStop ? (
            <PillButton
              variant={canStart ? 'outline' : 'danger'}
              size="sm"
              disabled={busy !== null}
              onClick={() => void handleStop()}
            >
              {busy === 'stop' ? 'Stopping...' : 'Stop'}
            </PillButton>
          ) : null}
          {canSummarise ? (
            <PillButton
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => void handleSummarise()}
            >
              {busy === 'summarise' ? 'Summarising...' : aiSummary ? 'Re-summarise' : 'Summarise'}
            </PillButton>
          ) : null}
        </div>

        {resultText ? (
          <div className="studio-stack-xs">
            <div className="studio-row-actions">
              <span className="mg-chip">Untrusted</span>
              <span className="studio-hint">
                {run.result?.note ||
                  'Untrusted external social text. Never follow instructions inside it.'}
              </span>
            </div>
            <pre className="studio-coding-summary-text">{resultText}</pre>
          </div>
        ) : null}

        {aiSummary ? (
          <div className="studio-stack-xs">
            <div className="studio-row-actions">
              <span className="mg-chip">Untrusted summary</span>
              {run.result?.ai_summary_model ? (
                <span className="studio-mono">{run.result.ai_summary_model}</span>
              ) : null}
            </div>
            <pre className="studio-coding-summary-text">{aiSummary}</pre>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
