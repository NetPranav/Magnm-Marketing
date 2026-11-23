// Plan on the phone from the cloud copy: the weekly plan, briefs, the editorial calendar and the schedules, all read
// from this account's own mirror records. A schedule change is queued for the laptop (schedule_update) and shown as
// waiting until the laptop applies it. Nothing here talks to the laptop.

import type { ActionInput } from './actions';
import type { ContextCacheState } from './cache';
import type { MirrorRecord, PhoneAction, PhoneActionPayload } from './types';

export interface PhoneSchedule {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  intervalHours: number | null;
  cron: string | null;
  /** The weekly summary only runs on one weekday, so "every day" is not offered for it. */
  weekly: boolean;
  lastRun: string | null;
  lastStatus: string | null;
}

export interface CalendarRow {
  slot: string;
  platforms: string;
  topic: string;
  status: string;
}

export interface PlanSlot {
  date: string;
  platform: string;
  topic: string;
  angle: string;
  productId: string;
  reason: string;
}

export interface WeeklyPlan {
  runId: string;
  status: string;
  weekStart: string;
  slots: PlanSlot[];
  updatedAt: string;
}

export interface PlanBrief {
  id: string;
  title: string;
  kind: string;
  status: string;
  updatedAt: string;
  confidential: boolean;
}

export interface PlanView {
  weeklyPlan: WeeklyPlan | null;
  briefs: PlanBrief[];
  calendar: CalendarRow[];
  schedules: PhoneSchedule[];
}

const SCHEDULE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const MAX_BRIEFS = 40;
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/g;

function str(v: unknown, limit: number): string {
  return typeof v === 'string' ? v.replace(CONTROL_RE, ' ').slice(0, limit) : '';
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** Read "30 9 * * 1" as "Every Monday at 09:30 IST". Anything else is shown as it is. */
export function describeCron(cron: string | null | undefined): string | null {
  if (!cron) return null;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5 || !/^\d{1,2}$/.test(parts[0]) || !/^\d{1,2}$/.test(parts[1])) return cron;
  const time = `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`;
  const day = parts[4] === '*' ? 'Every day' : /^[0-6]$/.test(parts[4]) ? `Every ${WEEKDAYS[Number(parts[4])]}` : null;
  return day ? `${day} at ${time} IST` : cron;
}

function mine(cache: ContextCacheState, kind: string): MirrorRecord[] {
  const owner = cache.ownerUid;
  if (!owner) return [];
  return cache.mirrorRecords.filter((r) => r.kind === kind && r.owner_id === owner);
}

function scheduleOf(rec: MirrorRecord): PhoneSchedule | null {
  if (typeof rec.local_id !== 'string' || !SCHEDULE_ID_RE.test(rec.local_id)) return null;
  const d = obj(rec.data);
  const hours = d.interval_hours;
  return {
    id: rec.local_id,
    name: str(d.name, 120) || rec.local_id,
    description: str(d.description, 300),
    enabled: d.enabled !== false,
    intervalHours: typeof hours === 'number' && Number.isInteger(hours) && hours >= 1 && hours <= 720 ? hours : null,
    cron: str(d.cron, 40) || null,
    weekly: d.weekly === true,
    lastRun: str(d.last_run, 40) || null,
    lastStatus: str(d.last_status, 40) || null,
  };
}

function slotOf(raw: unknown): PlanSlot | null {
  const d = obj(raw);
  const slot = { date: str(d.date, 40), platform: str(d.platform, 40), topic: str(d.topic, 300), angle: str(d.angle, 300), productId: str(d.product_id, 80), reason: str(d.reason, 300) };
  return slot.date || slot.topic ? slot : null;
}

/** Everything Plan shows, from this account's cloud mirror only. Junk records are dropped, never trusted. */
export function planFromMirror(cache: ContextCacheState): PlanView {
  const schedules = mine(cache, 'schedule').map(scheduleOf).filter((s): s is PhoneSchedule => s !== null);
  const calendar: CalendarRow[] = [];
  for (const rec of mine(cache, 'editorial_plan')) {
    const rows = obj(rec.data).rows;
    if (!Array.isArray(rows)) continue;
    for (const raw of rows.slice(0, 200)) {
      const d = obj(raw);
      calendar.push({ slot: str(d.slot, 80), platforms: str(d.platforms, 80), topic: str(d.topic, 300), status: str(d.status, 80) });
    }
    break;
  }
  const briefRecs = mine(cache, 'brief')
    .filter((r) => {
      const d = obj(r.data);
      return d.confidential === true || (typeof d.kind === 'string' && d.kind !== 'weekly_summary' && typeof d.status === 'string');
    })
    .sort((a, b) => String(b.local_updated_at).localeCompare(String(a.local_updated_at)));
  const briefs: PlanBrief[] = briefRecs.slice(0, MAX_BRIEFS).map((r) => {
    const d = obj(r.data);
    const conf = d.confidential === true;
    return {
      id: r.local_id,
      title: conf ? 'Confidential item' : str(r.title, 300) || str(d.kind, 40) || 'Brief',
      kind: conf ? '' : str(d.kind, 40),
      status: str(d.status, 40),
      updatedAt: str(r.local_updated_at, 40),
      confidential: conf,
    };
  });
  let weeklyPlan: WeeklyPlan | null = null;
  for (const r of briefRecs) {
    const d = obj(r.data);
    if (d.kind !== 'weekly_plan' || d.confidential === true) continue;
    const slots = Array.isArray(d.proposed_slots) ? d.proposed_slots.map(slotOf).filter((s): s is PlanSlot => s !== null).slice(0, 30) : [];
    weeklyPlan = { runId: r.local_id, status: str(d.status, 40), weekStart: str(obj(d.inputs).week_start, 20), slots, updatedAt: str(r.local_updated_at, 40) };
    break;
  }
  return { weeklyPlan, briefs, calendar, schedules };
}

/** A schedule change as a phone action (the laptop applies it through the same rules as the web app). */
export function buildScheduleChange(scheduleId: string, change: PhoneActionPayload & { label?: string }, label?: string): ActionInput {
  const { label: inlineLabel, ...payload } = change;
  return { kind: 'schedule_update', target_id: scheduleId, payload, label: label ?? inlineLabel ?? null };
}

/** The change still waiting for the laptop on one schedule, or null. */
export function pendingScheduleChange(actions: PhoneAction[], scheduleId: string): PhoneAction | null {
  const waiting = actions.filter((a) => a.kind === 'schedule_update' && a.state === 'waiting' && a.target_id === scheduleId);
  return waiting.length > 0 ? waiting[waiting.length - 1] : null;
}
