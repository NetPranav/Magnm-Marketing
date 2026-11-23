import { useEffect, useMemo, useState } from 'react';
import { Card, Checkbox, Field, Label, PillButton, Select, TextInput } from '@magnm/design/react';
import type { ActionInput } from '../actions';
import { stateLabel } from '../actions';
import type { ContextCacheState } from '../cache';
import { buildWorkflowInput, fetchTeamRepos, type TeamRepo } from '../createPhone';
import type { NativeBridge } from '../native';
import { buildScheduleChange, describeCron, pendingScheduleChange, planFromMirror, type PhoneSchedule } from '../planPhone';
import type { PhoneAction } from '../types';

export interface PlanTabProps {
  ownerUid: string | null;
  signedIn: boolean;
  cache: ContextCacheState;
  phoneActions: PhoneAction[];
  bridge: NativeBridge;
  savedCopyLabel: string | null;
  onQueueAction: (input: ActionInput, okMessage: string) => Promise<boolean>;
}

const PLATFORMS = ['linkedin', 'x', 'instagram', 'youtube'] as const;
const INTERVALS = [1, 2, 3, 6, 12, 24, 48, 168];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function nextMonday(): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + ((8 - d.getUTCDay()) % 7 || 7));
  return d.toISOString().slice(0, 10);
}

function pendingText(action: PhoneAction): string {
  const p = action.payload;
  const parts: string[] = [];
  if (p.enabled !== undefined) parts.push(p.enabled ? 'on' : 'paused');
  if (p.interval_hours) parts.push(`every ${p.interval_hours} hours`);
  if (p.time) parts.push(`at ${p.time}`);
  if (p.day) parts.push(p.day === '*' ? 'every day' : `on ${WEEKDAYS[Number(p.day)]}`);
  return `runs ${parts.join(', ') || 'as before'}`;
}

function ScheduleRow({ schedule, actions, signedIn, onQueueAction }: { schedule: PhoneSchedule; actions: PhoneAction[]; signedIn: boolean; onQueueAction: PlanTabProps['onQueueAction'] }) {
  const cronParts = (schedule.cron ?? '').split(/\s+/);
  const initialTime = cronParts.length === 5 && /^\d+$/.test(cronParts[0]) && /^\d+$/.test(cronParts[1]) ? `${cronParts[1].padStart(2, '0')}:${cronParts[0].padStart(2, '0')}` : '08:00';
  const [interval, setInterval_] = useState(String(schedule.intervalHours ?? 6));
  const [time, setTime] = useState(initialTime);
  const [day, setDay] = useState(cronParts.length === 5 && /^[*0-6]$/.test(cronParts[4]) ? cronParts[4] : '*');
  const pending = pendingScheduleChange(actions, schedule.id);
  const when = schedule.intervalHours ? `Every ${schedule.intervalHours} hours` : describeCron(schedule.cron);
  return (
    <div className="mob-asset">
      <div className="mob-wrap-text">{schedule.name}</div>
      <div className="mob-asset-meta">
        {schedule.enabled ? 'On' : 'Paused'}
        {when ? ` · ${when}` : ''}
        {schedule.lastStatus ? ` · last: ${schedule.lastStatus}` : ''}
      </div>
      {pending ? <div className="mg-notice">Waiting for the laptop: {pendingText(pending)}.</div> : null}
      <div className="mob-actions">
        <PillButton
          variant="outline"
          size="sm"
          disabled={!signedIn}
          aria-label={`${schedule.enabled ? 'Pause' : 'Resume'} ${schedule.name}`}
          onClick={() =>
            void onQueueAction(
              buildScheduleChange(schedule.id, { enabled: !schedule.enabled }, `${schedule.enabled ? 'Pause' : 'Resume'} ${schedule.name}`),
              'Queued for your laptop. It changes the schedule when it is back.',
            )
          }
        >
          {schedule.enabled ? 'Pause' : 'Resume'}
        </PillButton>
      </div>
      {schedule.intervalHours ? (
        <div className="mob-asset-edit">
          <Select aria-label={`Run ${schedule.name} every`} value={interval} onChange={(e) => setInterval_(e.target.value)}>
            {Array.from(new Set([...INTERVALS, schedule.intervalHours])).sort((a, b) => a - b).map((h) => (
              <option key={h} value={h}>
                {h === 1 ? 'every hour' : `every ${h} hours`}
              </option>
            ))}
          </Select>
          <PillButton
            variant="outline"
            size="sm"
            disabled={!signedIn || Number(interval) === schedule.intervalHours}
            aria-label={`Change interval of ${schedule.name}`}
            onClick={() =>
              void onQueueAction(
                buildScheduleChange(schedule.id, { interval_hours: Number(interval) }, `${schedule.name}: every ${interval} hours`),
                'Queued for your laptop. It changes the schedule when it is back.',
              )
            }
          >
            Change
          </PillButton>
        </div>
      ) : null}
      {schedule.cron ? (
        <div className="mob-asset-edit">
          <TextInput type="time" aria-label={`Time for ${schedule.name}`} value={time} onChange={(e) => setTime(e.target.value)} />
          <Select aria-label={`Day for ${schedule.name}`} value={day} onChange={(e) => setDay(e.target.value)}>
            {schedule.weekly ? null : <option value="*">Every day</option>}
            {WEEKDAYS.map((w, i) => (
              <option key={w} value={String(i)}>
                {w}
              </option>
            ))}
          </Select>
          <PillButton
            variant="outline"
            size="sm"
            disabled={!signedIn || !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(time)}
            aria-label={`Change time of ${schedule.name}`}
            onClick={() =>
              void onQueueAction(
                buildScheduleChange(schedule.id, { time, day }, `${schedule.name}: ${time}`),
                'Queued for your laptop. It changes the schedule when it is back.',
              )
            }
          >
            Change
          </PillButton>
        </div>
      ) : null}
    </div>
  );
}

/** Plan: the weekly plan, briefs, the calendar and the schedules from the cloud copy. Changes are queued for the laptop. */
export function PlanTab({ ownerUid, signedIn, cache, phoneActions, bridge, savedCopyLabel, onQueueAction }: PlanTabProps) {
  const plan = useMemo(() => planFromMirror(cache), [cache]);
  const [platforms, setPlatforms] = useState<string[]>(['linkedin', 'x']);
  const [perWeek, setPerWeek] = useState('3');
  const [weekStart, setWeekStart] = useState(nextMonday());
  const [repoId, setRepoId] = useState('');
  const [repos, setRepos] = useState<TeamRepo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const empty = !plan.weeklyPlan && plan.briefs.length === 0 && plan.calendar.length === 0 && plan.schedules.length === 0;
  const waitingPlan = phoneActions.filter((a) => a.kind === 'workflow_request' && a.target_id === 'weekly_plan' && a.state === 'waiting').length;

  useEffect(() => {
    if (!ownerUid) return;
    let cancelled = false;
    fetchTeamRepos(ownerUid, bridge)
      .then((list) => {
        if (!cancelled) setRepos(list);
      })
      .catch(() => {
        // The picker is optional: without it the plan is not tied to one repository.
      });
    return () => {
      cancelled = true;
    };
  }, [ownerUid, bridge]);

  async function queuePlan() {
    setError(null);
    const input: ActionInput = buildWorkflowInput('weekly_plan', {
      platforms,
      posts_per_week: Number(perWeek),
      week_start: weekStart,
      repo_id: repoId || undefined,
    });
    const ok = await onQueueAction(input, 'Queued for your laptop. It starts the plan when it is back and asks for your approval.');
    if (!ok) setError('The weekly plan could not be queued. See the message above.');
  }

  return (
    <div className="mob-stack">
      {empty ? (
        <Card>
          <div className="mob-body-text">No saved plan yet. Your laptop sends its plan, briefs and schedules to your cloud copy when it is online. {savedCopyLabel ?? ''}</div>
        </Card>
      ) : null}

      <Card>
        <div className="mob-stack-sm">
          <Label>Plan my week</Label>
          <div className="mob-body-text">
            Your laptop writes the plan when it is back and asks for your approval at the usual steps. Nothing is approved or posted from here.
          </div>
          <div className="mob-asset-edit">
            {PLATFORMS.map((p) => (
              <Checkbox
                key={p}
                label={p}
                checked={platforms.includes(p)}
                onChange={(e) => setPlatforms((cur) => (e.target.checked ? [...cur, p] : cur.filter((x) => x !== p)))}
              />
            ))}
          </div>
          <Field label="Posts per week" htmlFor="plan-perweek">
            <Select id="plan-perweek" value={perWeek} onChange={(e) => setPerWeek(e.target.value)}>
              {[1, 2, 3, 4, 5, 6, 7, 10, 14].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Week starts" htmlFor="plan-week">
            <TextInput id="plan-week" type="date" value={weekStart} onChange={(e) => setWeekStart(e.target.value)} />
          </Field>
          <Field label="Repository (optional)" htmlFor="plan-repo">
            <Select id="plan-repo" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
              <option value="">Any (not tied to one repository)</option>
              {repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </Field>
          {error ? (
            <div className="mob-banner-error" role="alert">
              {error}
            </div>
          ) : null}
          {waitingPlan > 0 ? <div className="mg-notice">{waitingPlan} weekly plan request{waitingPlan === 1 ? ' is' : 's are'} waiting for the laptop.</div> : null}
          <div className="mob-actions">
            <PillButton size="sm" disabled={!signedIn || platforms.length === 0 || !/^20\d{2}-\d{2}-\d{2}$/.test(weekStart)} onClick={() => void queuePlan()}>
              Queue the weekly plan for the laptop
            </PillButton>
          </div>
        </div>
      </Card>

      {plan.weeklyPlan ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Weekly plan{plan.weeklyPlan.weekStart ? ` from ${plan.weeklyPlan.weekStart}` : ''}</Label>
            <div className="mob-asset-meta">Status: {plan.weeklyPlan.status || 'unknown'}</div>
            {plan.weeklyPlan.slots.length === 0 ? <div className="mob-body-text">No slots proposed yet.</div> : null}
            {plan.weeklyPlan.slots.map((s, i) => (
              <div key={`${s.date}-${i}`} className="mob-asset">
                <div className="mob-wrap-text">{s.topic}</div>
                <div className="mob-asset-meta">{[s.date, s.platform, s.angle].filter(Boolean).join(' · ')}</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {plan.calendar.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Editorial calendar</Label>
            {plan.calendar.map((r, i) => (
              <div key={`${r.slot}-${i}`} className="mob-asset">
                <div className="mob-wrap-text">{r.topic}</div>
                <div className="mob-asset-meta">{[r.slot, r.platforms, r.status].filter(Boolean).join(' · ')}</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {plan.briefs.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Briefs</Label>
            {plan.briefs.slice(0, 15).map((b) => (
              <div key={b.id} className="mob-asset">
                <div className="mob-wrap-text">{b.title}</div>
                <div className="mob-asset-meta">{b.confidential ? 'Only on the laptop' : b.status}</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {plan.schedules.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Schedules</Label>
            <div className="mob-body-text">A change is queued and applied by your laptop. Times are IST.</div>
            {plan.schedules.map((s) => (
              <ScheduleRow key={s.id} schedule={s} actions={phoneActions} signedIn={signedIn} onQueueAction={onQueueAction} />
            ))}
            {phoneActions
              .filter((a) => a.kind === 'schedule_update' && a.state !== 'waiting')
              .slice(-3)
              .reverse()
              .map((a) => (
                <div key={a.id} className="mob-asset-meta">
                  {a.label ?? 'Schedule change'}: {stateLabel(a)}
                  {a.result ? ` · ${a.result}` : ''}
                </div>
              ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
