// The task board and the automatic-work state on the phone, read from this account's own cloud mirror (made by the
// laptop). A confidential item arrives as a stub and is shown as one. Pause and resume are queued for the laptop
// (run_control, automation_control) and shown as waiting until the laptop applies them. Nothing here talks to the laptop.

import type { ActionInput } from './actions';
import type { ContextCacheState } from './cache';
import type { MirrorRecord, PhoneAction } from './types';

export const BOARD_COLUMNS = [
  { id: 'queued', label: 'Queued' },
  { id: 'running', label: 'Running' },
  { id: 'waiting', label: 'Waiting for you' },
  { id: 'paused', label: 'Paused' },
  { id: 'done', label: 'Done' },
  { id: 'failed', label: 'Failed' },
] as const;
export type BoardColumnId = (typeof BOARD_COLUMNS)[number]['id'];

export const BOARD_KIND_LABELS: Record<string, string> = {
  agent_task: 'Agent task',
  workflow: 'Workflow',
  approval: 'Approval',
  job: 'Job',
  reminder: 'Reminder',
};

export interface PhoneBoardItem {
  id: string;
  /** The workflow run id, for the two kinds of item a phone may pause or resume. */
  runId: string | null;
  kind: string;
  title: string;
  column: BoardColumnId;
  status: string;
  agents: string[];
  repoName: string;
  scope: string;
  updatedAt: string;
  ageDays: number;
  confidential: boolean;
  automatic: boolean;
  steps: Array<{ name: string; status: string }>;
  error: string;
  /** What the phone may queue for this item: only pause or resume, and never for a confidential item. */
  canPause: boolean;
  canResume: boolean;
}

export interface PhoneAutomation {
  paused: boolean;
  pausedSince: string;
  pausedBy: string;
  missed: Array<{ id: string; name: string; dueAt: string }>;
  pipeline: { enabled: boolean; cron: string; intervalHours: number | null; kinds: string[]; skipRecentDays: number; lastRun: PhoneLastRun | null };
}

export interface PhoneLastRun {
  status: string;
  startedAt: string;
  confidential: boolean;
  repoName: string;
  scope: string;
  why: string;
  error: string;
}

export interface PhoneBoard {
  items: PhoneBoardItem[];
  filters: { agents: string[]; repos: string[]; kinds: string[] };
  automation: PhoneAutomation | null;
}

const ITEM_ID_RE = /^(run|job|session|gate|reminder):[A-Za-z0-9_.-]{1,128}$/;
const RUN_ID_RE = /^wf_[a-z_]{1,32}_[0-9]{1,16}_[0-9a-f]{6}$/;
const SCHED_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const COLUMN_IDS: readonly string[] = BOARD_COLUMNS.map((c) => c.id);
const MAX_ITEMS = 100;
const STUB_TITLE = 'Confidential item';
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/g;

function str(v: unknown, limit: number): string {
  return typeof v === 'string' ? v.replace(CONTROL_RE, ' ').trim().slice(0, limit) : '';
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function mine(cache: ContextCacheState, kind: string): MirrorRecord[] {
  const owner = cache.ownerUid;
  return owner ? cache.mirrorRecords.filter((r) => r.kind === kind && r.owner_id === owner) : [];
}

function itemOf(rec: MirrorRecord): PhoneBoardItem | null {
  if (typeof rec.local_id !== 'string' || !ITEM_ID_RE.test(rec.local_id)) return null;
  const d = obj(rec.data);
  const column = typeof d.column === 'string' && COLUMN_IDS.includes(d.column) ? (d.column as BoardColumnId) : null;
  if (!column) return null;
  const confidential = d.confidential === true;
  const kind = str(d.kind, 20);
  const rawRun = rec.local_id.startsWith('run:') ? rec.local_id.slice(4) : '';
  const runId = !confidential && kind === 'workflow' && RUN_ID_RE.test(rawRun) ? rawRun : null;
  const detail = obj(d.detail);
  const actions = Array.isArray(d.actions) ? d.actions : [];
  const steps = Array.isArray(detail.steps)
    ? detail.steps.slice(0, 12).map((s) => ({ name: str(obj(s).name, 40), status: str(obj(s).status, 20) }))
    : [];
  return {
    id: rec.local_id,
    runId,
    kind,
    title: confidential ? STUB_TITLE : str(rec.title, 200) || 'Untitled',
    column,
    status: str(d.status, 20),
    agents: confidential || !Array.isArray(d.agents) ? [] : d.agents.map((a) => str(a, 40)).filter(Boolean).slice(0, 6),
    repoName: confidential ? '' : str(d.repo_name, 120),
    scope: confidential ? '' : str(d.scope, 10),
    updatedAt: str(d.updated_at, 40) || str(rec.local_updated_at, 40),
    ageDays: typeof d.age_days === 'number' && Number.isFinite(d.age_days) ? Math.max(0, Math.min(Math.trunc(d.age_days), 999)) : 0,
    confidential,
    automatic: d.automatic === true,
    steps: confidential ? [] : steps,
    error: confidential ? '' : str(detail.error, 160),
    canPause: runId !== null && actions.includes('pause'),
    canResume: runId !== null && actions.includes('resume'),
  };
}

function lastRunOf(raw: unknown): PhoneLastRun | null {
  const d = obj(raw);
  if (typeof d.status !== 'string') return null;
  const confidential = d.confidential === true;
  return {
    status: str(d.status, 20),
    startedAt: str(d.started_at, 40),
    confidential,
    repoName: confidential ? '' : str(d.repo_name, 120),
    scope: confidential ? '' : str(d.scope, 10),
    why: confidential ? '' : str(d.why, 300),
    error: confidential ? '' : str(d.error, 200),
  };
}

function automationOf(cache: ContextCacheState): PhoneAutomation | null {
  const rec = mine(cache, 'automation').find((r) => r.local_id === 'state');
  if (!rec) return null;
  const d = obj(rec.data);
  const p = obj(d.pipeline);
  const hours = p.interval_hours;
  return {
    paused: d.paused === true,
    pausedSince: str(d.paused_since, 40),
    pausedBy: str(d.paused_by, 10),
    missed: (Array.isArray(d.missed) ? d.missed : [])
      .map((m) => ({ id: str(obj(m).id, 64), name: str(obj(m).name, 120), dueAt: str(obj(m).due_at, 40) }))
      .filter((m) => SCHED_ID_RE.test(m.id))
      .slice(0, 20),
    pipeline: {
      enabled: p.enabled === true,
      cron: str(p.cron, 40),
      intervalHours: typeof hours === 'number' && Number.isInteger(hours) && hours >= 1 && hours <= 720 ? hours : null,
      kinds: Array.isArray(p.kinds) ? p.kinds.map((k) => str(k, 20)).filter(Boolean).slice(0, 3) : [],
      skipRecentDays: typeof p.skip_recent_days === 'number' && Number.isFinite(p.skip_recent_days) ? Math.max(0, Math.min(Math.trunc(p.skip_recent_days), 90)) : 0,
      lastRun: lastRunOf(p.last_run),
    },
  };
}

/** Everything Board shows, from this account's cloud mirror only. Junk records are dropped, never trusted. */
export function boardFromMirror(cache: ContextCacheState): PhoneBoard {
  const order = new Map(COLUMN_IDS.map((c, i) => [c, i]));
  const items = mine(cache, 'board_item')
    .map(itemOf)
    .filter((i): i is PhoneBoardItem => i !== null)
    .sort((a, b) => (order.get(a.column) ?? 9) - (order.get(b.column) ?? 9) || b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, MAX_ITEMS);
  const shown = items.filter((i) => !i.confidential);
  return {
    items,
    filters: {
      agents: Array.from(new Set(shown.flatMap((i) => i.agents))).sort(),
      repos: Array.from(new Set(shown.map((i) => i.repoName).filter(Boolean))).sort(),
      kinds: Array.from(new Set(shown.map((i) => i.kind))).sort(),
    },
    automation: automationOf(cache),
  };
}

export function buildRunControl(runId: string, action: 'pause' | 'resume', label?: string): ActionInput {
  return { kind: 'run_control', target_id: runId, payload: { action }, label: label ?? null };
}

export function buildAutomationControl(action: 'pause' | 'resume'): ActionInput {
  return { kind: 'automation_control', target_id: 'all', payload: { action }, label: action === 'pause' ? 'Pause automatic work' : 'Resume automatic work' };
}

/** The pause or resume still waiting for the laptop on one run (or the global switch when targetId is "all"), or null. */
export function pendingControl(actions: PhoneAction[], kind: 'run_control' | 'automation_control', targetId: string): PhoneAction | null {
  const waiting = actions.filter((a) => a.kind === kind && a.state === 'waiting' && a.target_id === targetId);
  return waiting.length > 0 ? waiting[waiting.length - 1] : null;
}
