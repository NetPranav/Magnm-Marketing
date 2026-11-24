import { useCallback, useEffect, useState } from 'react';
import { Checkbox, Field, PillButton, Row, RowList, StatusDot, TextInput } from '@magnm/design/react';
import {
  actOnMissed,
  getAutomation,
  runAutomationNow,
  saveAutomationSettings,
  setAutomationPaused,
  type AutomationState,
  type PipelineRun,
} from '../../api/automation';
import { toggleSchedule } from '../../api/plan';
import { useStudioEvents } from '../../events';
import { formatTimestamp } from '../../ui/format';

const KIND_LABELS: Record<string, string> = { post: 'Post', blog: 'Blog article', video_script: 'Video script' };
const PLATFORM_LABELS: Record<string, string> = { x: 'X', linkedin: 'LinkedIn', instagram: 'Instagram', youtube: 'YouTube' };
const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  working: 'Content in progress',
  ready: 'Ready to review',
  failed: 'Stopped',
  skipped: 'Skipped (paused)',
  cancelled: 'Cancelled',
};

function dotState(status: string): 'online' | 'working' | 'idle' | 'off' | 'error' {
  if (status === 'ready') return 'online';
  if (status === 'running' || status === 'working') return 'working';
  if (status === 'failed') return 'error';
  return 'off';
}

function RunRow({ run }: { run: PipelineRun }) {
  const repo = run.repo ? `${run.repo.name} (${run.repo.scope})${run.repo.confidential ? ' · confidential' : ''}` : 'No repository picked';
  return (
    <Row
      title={repo}
      description={
        <div className="studio-stack-xs">
          <span>{run.why || run.error || ''}</span>
          {run.status === 'failed' && run.error ? <span>{run.error}</span> : null}
          <span className="studio-mono">
            {formatTimestamp(run.started_at)} · {run.trigger === 'manual' ? 'Run now' : 'Scheduled'}
            {run.steps.length > 0 ? ` · ${run.steps.map((s) => `${s.name} ${s.status}`).join(', ')}` : ''}
          </span>
        </div>
      }
      meta={<StatusDot state={dotState(run.status)} label={STATUS_LABELS[run.status] ?? run.status} />}
    />
  );
}

/** Plan: the "Pause automatic work" switch, missed runs, and the automatic content pipeline (settings, Run now, history). */
export function AutomationCard() {
  const [state, setState] = useState<AutomationState | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [days, setDays] = useState('7');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const adopt = useCallback((next: AutomationState, keepForm: boolean) => {
    setState(next);
    if (!keepForm) {
      setKinds(next.settings.kinds);
      setPlatforms(next.settings.platforms);
      setDays(String(next.settings.skip_recent_days));
    }
  }, []);

  const load = useCallback(
    async (keepForm = true) => {
      try {
        const next = await getAutomation();
        if (next && typeof next === 'object' && !Array.isArray(next) && next.settings && Array.isArray(next.history)) {
          adopt(next, keepForm);
        }
      } catch {
        // Older engines have no automatic work; the card stays hidden.
      }
    },
    [adopt],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  useStudioEvents(
    useCallback(
      (name) => {
        if (name.startsWith('workflow_') || name === 'job') void load();
      },
      [load],
    ),
  );

  const guard = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(null);
    }
  };

  if (!state) return null;
  const toggle = (list: string[], value: string, set: (v: string[]) => void) =>
    set(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  const dirty =
    kinds.join() !== state.settings.kinds.join() ||
    platforms.join() !== state.settings.platforms.join() ||
    days !== String(state.settings.skip_recent_days);
  const scheduleOn = Boolean(state.schedule?.enabled);

  return (
    <section className="studio-section" aria-labelledby="plan-auto-heading">
      <h2 id="plan-auto-heading" className="studio-section-title">
        Automatic work
      </h2>
      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <RowList aria-label="Automatic work switch">
        <Row
          title="Pause automatic work"
          description={
            state.paused
              ? `Schedules do not fire and background jobs wait${state.paused_since ? ` (since ${formatTimestamp(state.paused_since)})` : ''}. Things you start yourself still work.`
              : 'Schedules fire and background jobs run. Pause to hold everything automatic.'
          }
          meta={
            <div className="studio-row-actions">
              <StatusDot state={state.paused ? 'off' : 'online'} label={state.paused ? 'Paused' : 'On'} />
              <PillButton
                variant="outline"
                size="sm"
                disabled={busy === 'pause'}
                onClick={() => void guard('pause', async () => adopt(await setAutomationPaused(!state.paused), true))}
              >
                {state.paused ? 'Resume' : 'Pause'}
              </PillButton>
            </div>
          }
        />
      </RowList>

      {state.missed.length > 0 ? (
        <RowList aria-label="Missed runs">
          {state.missed.map((m) => (
            <Row
              key={m.id}
              title={`Missed: ${m.name}`}
              description={`Due ${formatTimestamp(m.due_at)} while paused. It is not run unless you choose to.`}
              meta={
                <div className="studio-row-actions">
                  <PillButton
                    size="sm"
                    disabled={busy === m.id}
                    onClick={() => void guard(m.id, async () => { await actOnMissed(m.id, 'run'); await load(); })}
                  >
                    Run now
                  </PillButton>
                  <PillButton
                    variant="quiet"
                    size="sm"
                    disabled={busy === m.id}
                    onClick={() => void guard(m.id, async () => { await actOnMissed(m.id, 'dismiss'); await load(); })}
                  >
                    Dismiss
                  </PillButton>
                </div>
              }
            />
          ))}
        </RowList>
      ) : null}

      <div className="studio-stack-sm" aria-label="Automatic pipeline">
        <div className="studio-section-bar">
          <div className="studio-stack-xs">
            <strong>Automatic content pipeline</strong>
            <span className="studio-hint">
              On its schedule it picks a random repo you added, syncs and researches it, and starts content that stops at your
              approval. Nothing is posted. Change the time under Schedules &amp; timers.
            </span>
          </div>
          <div className="studio-row-actions">
            <StatusDot state={scheduleOn ? 'online' : 'off'} label={scheduleOn ? 'Scheduled' : 'Off'} />
            <PillButton
              variant="outline"
              size="sm"
              disabled={busy === 'toggle'}
              onClick={() => void guard('toggle', async () => { await toggleSchedule('auto_pipeline'); await load(); })}
            >
              {scheduleOn ? 'Turn off' : 'Turn on'}
            </PillButton>
            <PillButton
              size="sm"
              disabled={busy === 'run' || state.running}
              onClick={() => void guard('run', async () => { await runAutomationNow(); await load(); })}
            >
              {state.running ? 'Running' : 'Run now'}
            </PillButton>
          </div>
        </div>

        <fieldset className="studio-stack-xs">
          <legend className="studio-section-title">What to make</legend>
          {state.allowed_kinds.map((k) => (
            <Checkbox key={k} label={KIND_LABELS[k] ?? k} checked={kinds.includes(k)} onChange={() => toggle(kinds, k, setKinds)} />
          ))}
        </fieldset>
        <fieldset className="studio-stack-xs">
          <legend className="studio-section-title">Platforms for posts</legend>
          {state.allowed_platforms.map((p) => (
            <Checkbox key={p} label={PLATFORM_LABELS[p] ?? p} checked={platforms.includes(p)} onChange={() => toggle(platforms, p, setPlatforms)} />
          ))}
        </fieldset>
        <Field label="Skip repos used in the last (days)" htmlFor="auto-skip-days">
          <TextInput id="auto-skip-days" type="number" min={0} max={90} value={days} onChange={(e) => setDays(e.target.value)} />
        </Field>
        <p className="studio-hint">
          Archived repos are skipped. A confidential repo is used only when local AI is ready (
          {state.local_ai ? 'it is' : 'it is not right now'}); its text never goes to cloud AI.
        </p>
        <div className="studio-row-actions">
          <PillButton
            size="sm"
            disabled={!dirty || busy === 'save' || kinds.length === 0 || platforms.length === 0}
            onClick={() =>
              void guard('save', async () => {
                await saveAutomationSettings({ kinds, platforms, skip_recent_days: Number.parseInt(days, 10) });
                await load(false);
              })
            }
          >
            Save settings
          </PillButton>
        </div>
      </div>

      <h3 className="studio-section-title">Run history</h3>
      {state.history.length === 0 ? (
        <div className="mg-empty">No automatic runs yet.</div>
      ) : (
        <RowList aria-label="Pipeline run history">
          {state.history.slice(0, 10).map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </RowList>
      )}
    </section>
  );
}
