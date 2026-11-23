// The rules for what a phone may queue for Library, Plan and Create. The cloud (phone_actions_payload_shape and
// phone_workflow_inputs_ok) and the laptop (engine/phone_library.py and engine/phone_plan.py) check the same things
// again: this copy only makes the phone say no early, with a clear reason, instead of queueing something refused.

import type { PhoneActionKind, PhoneActionPayload } from './types';

export const WORK_ACTION_KINDS: readonly PhoneActionKind[] = ['library_upload', 'schedule_update', 'draft_create', 'workflow_request'];
export const LIBRARY_CATEGORIES = ['cero', 'brand', 'screenshot', 'recording', 'document'] as const;
export const WORKFLOW_KINDS = ['post', 'blog', 'video_script', 'repurpose', 'weekly_plan'] as const;
export const DRAFT_PLATFORMS = ['x', 'linkedin', 'instagram', 'youtube'] as const;
export const DRAFT_MODES = ['post', 'thread', 'rewrite'] as const;
export const MAX_DESCRIPTION_CHARS = 300;
export const MAX_DRAFT_TITLE_CHARS = 200;
export const MAX_DRAFT_BODY_CHARS = 6000;
export const MAX_BRIEF_CHARS = 2000;
export const MAX_TOPIC_CHARS = 1000;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SCHEDULE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const REPO_ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const TARGET_RE = /^[a-z][a-z0-9_]{0,31}$/;
const TIME_RE = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
// eslint-disable-next-line no-control-regex
const BODY_BAD_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;
// eslint-disable-next-line no-control-regex
const LINE_BAD_RE = /[\x00-\x1f\x7f]/;

export class WorkRuleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkRuleError';
  }
}

export interface CleanWorkInput {
  kind: PhoneActionKind;
  target_id: string;
  payload: PhoneActionPayload;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function onlyKeys(payload: Record<string, unknown>, allowed: readonly string[], what: string): void {
  if (Object.keys(payload).some((k) => !allowed.includes(k))) {
    throw new WorkRuleError(`The ${what} has details a phone may not send.`);
  }
}

function text(value: unknown, what: string, limit: number, multiline: boolean): string {
  if (typeof value !== 'string' || (multiline ? BODY_BAD_RE : LINE_BAD_RE).test(value) || !value.trim()) {
    throw new WorkRuleError(`The ${what} must be text.`);
  }
  const out = value.trim();
  if (out.length > limit) throw new WorkRuleError(`The ${what} can have at most ${limit} characters.`);
  return out;
}

function platforms(value: unknown): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4 || value.some((p) => !DRAFT_PLATFORMS.includes(p as never))) {
    throw new WorkRuleError('Choose one to four platforms.');
  }
  return Array.from(new Set(value as string[]));
}

function validateLibrary(target: string, payload: Record<string, unknown>): CleanWorkInput {
  if (!UUID_RE.test(target)) throw new WorkRuleError('The upload is not valid.');
  onlyKeys(payload, ['category', 'description'], 'upload');
  const category = payload.category;
  if (typeof category !== 'string' || !LIBRARY_CATEGORIES.includes(category as never)) {
    throw new WorkRuleError('Choose a Library category.');
  }
  const clean: PhoneActionPayload = { category };
  if (payload.description !== undefined && payload.description !== null && payload.description !== '') {
    const d = payload.description;
    if (typeof d !== 'string' || LINE_BAD_RE.test(d) || d.length > MAX_DESCRIPTION_CHARS) {
      throw new WorkRuleError(`A note can have at most ${MAX_DESCRIPTION_CHARS} characters on one line.`);
    }
    if (d.trim()) clean.description = d.trim();
  }
  return { kind: 'library_upload', target_id: target, payload: clean };
}

function validateSchedule(target: string, payload: Record<string, unknown>): CleanWorkInput {
  if (!SCHEDULE_ID_RE.test(target)) throw new WorkRuleError('The schedule is not valid.');
  onlyKeys(payload, ['enabled', 'interval_hours', 'time', 'day'], 'schedule change');
  const clean: PhoneActionPayload = {};
  if ('enabled' in payload) {
    if (typeof payload.enabled !== 'boolean') throw new WorkRuleError('On or off is not valid.');
    clean.enabled = payload.enabled;
  }
  if ('interval_hours' in payload) {
    const h = payload.interval_hours;
    if (typeof h !== 'number' || !Number.isInteger(h) || h < 1 || h > 720) throw new WorkRuleError('The interval must be 1 to 720 whole hours.');
    clean.interval_hours = h;
  }
  if ('time' in payload) {
    if (typeof payload.time !== 'string' || !TIME_RE.test(payload.time)) throw new WorkRuleError('The time must look like 08:30.');
    clean.time = payload.time;
  }
  if ('day' in payload) {
    if (typeof payload.day !== 'string' || !/^[*0-6]$/.test(payload.day)) throw new WorkRuleError('Choose every day or a weekday.');
    clean.day = payload.day;
  }
  if (Object.keys(clean).length === 0) throw new WorkRuleError('There is nothing to change.');
  return { kind: 'schedule_update', target_id: target, payload: clean };
}

function validateDraft(target: string, payload: Record<string, unknown>): CleanWorkInput {
  if (target !== 'new') throw new WorkRuleError('A new draft has no target.');
  onlyKeys(payload, ['platform', 'title', 'body', 'mode'], 'draft');
  const platform = payload.platform;
  if (typeof platform !== 'string' || !DRAFT_PLATFORMS.includes(platform as never)) throw new WorkRuleError('Choose a platform.');
  const clean: PhoneActionPayload = {
    platform,
    title: text(payload.title, 'title', MAX_DRAFT_TITLE_CHARS, false),
    body: text(payload.body, 'text', MAX_DRAFT_BODY_CHARS, true),
  };
  if (payload.mode !== undefined) {
    if (!DRAFT_MODES.includes(payload.mode as never)) throw new WorkRuleError('The kind of draft is not valid.');
    clean.mode = payload.mode as (typeof DRAFT_MODES)[number];
  }
  return { kind: 'draft_create', target_id: 'new', payload: clean };
}

function validateWorkflow(target: string, payload: Record<string, unknown>): CleanWorkInput {
  const keys: Record<string, string[]> = {
    post: ['brief', 'platforms', 'with_image', 'repo_id'],
    blog: ['topic', 'repo_id'],
    video_script: ['topic', 'duration_s', 'aspect', 'repo_id'],
    repurpose: ['content_id', 'targets'],
    weekly_plan: ['platforms', 'posts_per_week', 'week_start', 'repo_id'],
  };
  if (!WORKFLOW_KINDS.includes(target as never)) throw new WorkRuleError('That kind of work cannot be started from the phone.');
  onlyKeys(payload, keys[target], 'request');
  const clean: PhoneActionPayload = {};
  if (target === 'post') clean.brief = text(payload.brief, 'brief', MAX_BRIEF_CHARS, true);
  if (target === 'blog' || target === 'video_script') clean.topic = text(payload.topic, 'topic', MAX_TOPIC_CHARS, true);
  if (target === 'repurpose') {
    const cid = payload.content_id;
    if (typeof cid !== 'string' || cid.length < 1 || cid.length > 200 || LINE_BAD_RE.test(cid)) throw new WorkRuleError('Choose a draft to repurpose.');
    clean.content_id = cid;
  }
  if ('platforms' in payload) clean.platforms = platforms(payload.platforms);
  if ('with_image' in payload) {
    if (typeof payload.with_image !== 'boolean') throw new WorkRuleError('The image setting is not valid.');
    clean.with_image = payload.with_image;
  }
  if ('repo_id' in payload) {
    if (typeof payload.repo_id !== 'string' || !REPO_ID_RE.test(payload.repo_id)) throw new WorkRuleError('The repository is not valid.');
    clean.repo_id = payload.repo_id;
  }
  if ('duration_s' in payload) {
    if (payload.duration_s !== 15 && payload.duration_s !== 30 && payload.duration_s !== 60) throw new WorkRuleError('Choose 15, 30 or 60 seconds.');
    clean.duration_s = payload.duration_s;
  }
  if ('aspect' in payload) {
    if (payload.aspect !== '9:16' && payload.aspect !== '16:9') throw new WorkRuleError('Choose a video shape.');
    clean.aspect = payload.aspect;
  }
  if ('targets' in payload) {
    const t = payload.targets;
    if (!Array.isArray(t) || t.length < 1 || t.length > 6 || t.some((x) => typeof x !== 'string' || !TARGET_RE.test(x))) {
      throw new WorkRuleError('The repurpose targets are not valid.');
    }
    clean.targets = Array.from(new Set(t as string[]));
  }
  if ('posts_per_week' in payload) {
    const n = payload.posts_per_week;
    if (typeof n !== 'number' || !Number.isInteger(n) || n < 1 || n > 14) throw new WorkRuleError('Posts per week must be 1 to 14.');
    clean.posts_per_week = n;
  }
  if ('week_start' in payload) {
    const w = payload.week_start;
    const d = typeof w === 'string' && /^20\d{2}-\d{2}-\d{2}$/.test(w) ? new Date(`${w}T00:00:00Z`) : null;
    if (!d || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== w) throw new WorkRuleError('The week start must be a date.');
    clean.week_start = w as string;
  }
  return { kind: 'workflow_request', target_id: target, payload: clean };
}

/** Check one Library, Plan or Create action and return the cleaned fields. Throws WorkRuleError. */
export function validateWorkInput(kind: PhoneActionKind, target: string, payload: unknown): CleanWorkInput {
  if (!isPlainObject(payload)) throw new WorkRuleError('The action details are not valid.');
  if (kind === 'library_upload') return validateLibrary(target, payload);
  if (kind === 'schedule_update') return validateSchedule(target, payload);
  if (kind === 'draft_create') return validateDraft(target, payload);
  return validateWorkflow(target, payload);
}
