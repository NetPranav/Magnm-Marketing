// Phone actions: comment on a draft, mark it posted, finish a reminder, queued on the phone and sent to the cloud
// by itself so the laptop can apply them when it is back. Everything here is local and bound to one account.
// The same checks run when an action is made, when it is loaded and again just before it is sent.

import { getConfiguredAgent } from './agentTools';
import {
  cleanPitchFields,
  collectStrings,
  MAX_VERSION_CHARS,
  NEW_PROPOSAL_TARGET,
  PitchRuleError,
} from './pitchRules';
import { findConfidentialMention } from './productRules';
import { CONTROL_ACTION_KINDS, WORK_ACTION_KINDS, WorkRuleError, validateWorkInput } from './workRules';
import {
  readNonSecretJson,
  removeNonSecretKey,
  writeNonSecretJson,
  bumpStateGeneration,
} from './storage';
import type {
  PhoneAction,
  PhoneActionKind,
  PhoneActionPayload,
  PhoneActionState,
  ProductRulesConfig,
} from './types';

export const ACTIONS_STORAGE_KEY = 'magnm.mobile.phoneActions.v1';
export const PITCH_ACTION_KINDS: readonly PhoneActionKind[] = ['pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link'];
export const ACTION_KINDS: readonly PhoneActionKind[] = [
  'draft_comment',
  'draft_posted',
  'reminder_done',
  'agent_task',
  ...PITCH_ACTION_KINDS,
  ...WORK_ACTION_KINDS,
];
export const MAX_COMMENT_CHARS = 4000;
export const MAX_AGENT_MESSAGE_CHARS = 4000;
export const MAX_URL_CHARS = 2000;
export const MAX_TARGET_CHARS = 200;
export const MAX_WAITING_ACTIONS = 100;
export const MAX_FINISHED_KEPT = 50;
export const MAX_RESULT_CHARS = 300;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/;
// eslint-disable-next-line no-control-regex
const BODY_BAD_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;
// eslint-disable-next-line no-control-regex
const URL_BAD_RE = /[\s\x00-\x1f\x7f]/;
const STATES: readonly PhoneActionState[] = ['waiting', 'applied', 'rejected'];

export class PhoneActionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PhoneActionError';
  }
}

export interface ActionInput {
  kind: PhoneActionKind;
  target_id: string;
  payload: PhoneActionPayload;
  label?: string | null;
  /** True when the draft or reminder is confidential: nothing may be queued for it. */
  targetConfidential?: boolean;
}

export interface CleanActionInput {
  kind: PhoneActionKind;
  target_id: string;
  payload: PhoneActionPayload;
}

function isHttpUrl(raw: string): boolean {
  try {
    const parsed = new URL(raw);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && Boolean(parsed.host);
  } catch {
    return false;
  }
}

/** Check one action the way the cloud and the laptop will, and return the cleaned fields. Throws PhoneActionError. */
export function validateActionInput(input: ActionInput | CleanActionInput): CleanActionInput {
  const kind = input?.kind;
  if (!ACTION_KINDS.includes(kind)) {
    throw new PhoneActionError('That kind of action is not supported.');
  }
  const target = input.target_id;
  if (typeof target !== 'string' || target.length < 1 || target.length > MAX_TARGET_CHARS || CONTROL_RE.test(target)) {
    throw new PhoneActionError('The draft or reminder is not valid.');
  }
  const payload = input.payload;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new PhoneActionError('The action details are not valid.');
  }
  const keys = Object.keys(payload);

  if (PITCH_ACTION_KINDS.includes(kind)) {
    return validatePitchInput(kind, target, payload, keys);
  }
  if (WORK_ACTION_KINDS.includes(kind)) {
    try {
      return validateWorkInput(kind, target, payload);
    } catch (err) {
      if (err instanceof WorkRuleError) throw new PhoneActionError(err.message);
      throw err;
    }
  }

  if (kind === 'draft_comment') {
    if (keys.some((k) => k !== 'body' && k !== 'version')) {
      throw new PhoneActionError('The comment has unexpected details.');
    }
    const body = payload.body;
    if (typeof body !== 'string' || BODY_BAD_RE.test(body) || !body.trim() || body.trim().length > MAX_COMMENT_CHARS) {
      throw new PhoneActionError(`A comment must have 1 to ${MAX_COMMENT_CHARS} characters.`);
    }
    const clean: PhoneActionPayload = { body: body.trim() };
    if (payload.version !== undefined && payload.version !== null) {
      const v = payload.version;
      if (typeof v !== 'number' || !Number.isInteger(v) || v < 1 || v > 999_999_999) {
        throw new PhoneActionError('The draft version is not valid.');
      }
      clean.version = v;
    }
    return { kind, target_id: target, payload: clean };
  }

  if (kind === 'draft_posted') {
    if (keys.some((k) => k !== 'url')) {
      throw new PhoneActionError('The posted details have unexpected fields.');
    }
    const url = payload.url;
    if (url === undefined || url === null || url === '') {
      return { kind, target_id: target, payload: {} };
    }
    if (typeof url !== 'string' || url.length > MAX_URL_CHARS || URL_BAD_RE.test(url) || !isHttpUrl(url)) {
      throw new PhoneActionError('The posted link must be a valid http or https address.');
    }
    return { kind, target_id: target, payload: { url } };
  }

  if (kind === 'agent_task') {
    if (getConfiguredAgent(target) === null) {
      throw new PhoneActionError('That agent is not on your team.');
    }
    if (keys.length !== 1 || keys[0] !== 'message') {
      throw new PhoneActionError('The request has unexpected details.');
    }
    const message = payload.message;
    if (
      typeof message !== 'string' ||
      BODY_BAD_RE.test(message) ||
      !message.trim() ||
      message.trim().length > MAX_AGENT_MESSAGE_CHARS
    ) {
      throw new PhoneActionError(`A request must have 1 to ${MAX_AGENT_MESSAGE_CHARS} characters.`);
    }
    return { kind, target_id: target, payload: { message: message.trim() } };
  }

  if (keys.length > 0) {
    throw new PhoneActionError('A reminder needs no details.');
  }
  return { kind, target_id: target, payload: {} };
}

function validatePitchInput(
  kind: PhoneActionKind,
  target: string,
  payload: PhoneActionPayload,
  keys: string[],
): CleanActionInput {
  try {
    if (kind === 'pitch_create') {
      if (target !== NEW_PROPOSAL_TARGET) throw new PitchRuleError('A new proposal has no target.');
      return { kind, target_id: target, payload: cleanPitchFields(payload, true) as PhoneActionPayload };
    }
    if (target === NEW_PROPOSAL_TARGET) throw new PitchRuleError('Choose the proposal this is about.');
    if (kind === 'pitch_update') {
      if (keys.length !== 2 || !keys.includes('base_version') || !keys.includes('fields')) {
        throw new PitchRuleError('The edit has unexpected details.');
      }
      const base = payload.base_version;
      if (typeof base !== 'string' || base.length < 1 || base.length > MAX_VERSION_CHARS || CONTROL_RE.test(base)) {
        throw new PitchRuleError('The proposal version is not valid.');
      }
      const fields = cleanPitchFields(payload.fields, false);
      if (Object.keys(fields).length === 0) throw new PitchRuleError('There is nothing to change.');
      return { kind, target_id: target, payload: { base_version: base, fields } };
    }
    if (kind === 'pitch_finalize') {
      if (keys.length > 0) throw new PitchRuleError('A final quotation needs no details.');
      return { kind, target_id: target, payload: {} };
    }
    if (keys.length !== 1 || (payload.action !== 'publish' && payload.action !== 'withdraw')) {
      throw new PitchRuleError('The link change is not valid.');
    }
    return { kind, target_id: target, payload: { action: payload.action } };
  } catch (err) {
    if (err instanceof PitchRuleError) throw new PhoneActionError(err.message);
    throw err;
  }
}

function newActionId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID().toLowerCase();
  }
  const b = new Uint8Array(16);
  globalThis.crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function cleanLabel(label: unknown): string | null {
  if (typeof label !== 'string') return null;
  const one = label.replace(/\s+/g, ' ').trim();
  return one ? one.slice(0, 80) : null;
}

/** An agent's reply as shown on the phone: text only, no control characters, at most 4000 characters. */
export function cleanReply(raw: string): string | null {
  // eslint-disable-next-line no-control-regex
  const text = raw.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ').trim().slice(0, MAX_AGENT_MESSAGE_CHARS);
  return text || null;
}

function asStoredAction(raw: unknown): PhoneAction | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Partial<PhoneAction>;
  if (typeof r.id !== 'string' || !UUID_RE.test(r.id)) return null;
  if (typeof r.owner_uid !== 'string' || !r.owner_uid) return null;
  if (typeof r.sent !== 'boolean' || !STATES.includes(r.state as PhoneActionState)) return null;
  if (typeof r.created_at !== 'string') return null;
  try {
    const clean = validateActionInput({ kind: r.kind as PhoneActionKind, target_id: r.target_id as string, payload: r.payload as PhoneActionPayload });
    return {
      id: r.id,
      ...clean,
      label: cleanLabel(r.label),
      created_at: r.created_at,
      owner_uid: r.owner_uid,
      sent: r.sent,
      state: r.state as PhoneActionState,
      result: typeof r.result === 'string' ? r.result.slice(0, MAX_RESULT_CHARS) : null,
      ...(clean.kind === 'agent_task'
        ? {
            reply: typeof r.reply === 'string' ? cleanReply(r.reply) : null,
            laptop_only: r.laptop_only === true,
          }
        : {}),
    };
  } catch {
    return null;
  }
}

/** Every valid stored action of every account. Junk and tampered items are dropped. */
function readAll(): PhoneAction[] {
  const raw = readNonSecretJson<unknown[]>(ACTIONS_STORAGE_KEY);
  if (!Array.isArray(raw)) return [];
  const valid = raw.map(asStoredAction).filter((a): a is PhoneAction => a !== null);
  if (valid.length !== raw.length) {
    try {
      if (valid.length === 0) removeNonSecretKey(ACTIONS_STORAGE_KEY);
      else writeNonSecretJson(ACTIONS_STORAGE_KEY, valid);
    } catch {
      // Cleanup is best effort.
    }
  }
  return valid;
}

function writeAll(items: PhoneAction[]): void {
  const waiting = items.filter((a) => a.state === 'waiting');
  const finished = items.filter((a) => a.state !== 'waiting').slice(-MAX_FINISHED_KEPT);
  const keep = new Set([...waiting, ...finished].map((a) => a.id));
  const kept = items.filter((a) => keep.has(a.id));
  if (kept.length === 0) removeNonSecretKey(ACTIONS_STORAGE_KEY);
  else writeNonSecretJson(ACTIONS_STORAGE_KEY, kept);
}

/** The actions of this account, oldest first. Another account's actions are never returned. */
export function loadActions(ownerUid: string | null | undefined): PhoneAction[] {
  const uid = (ownerUid ?? '').trim();
  if (!uid) return [];
  return readAll().filter((a) => a.owner_uid === uid);
}

/** Change one stored action of this account (used when the cloud answers). Returns false if it is gone. */
export function patchAction(
  ownerUid: string,
  id: string,
  patch: Partial<Pick<PhoneAction, 'sent' | 'state' | 'result' | 'reply' | 'laptop_only'>>,
): boolean {
  const all = readAll();
  const at = all.findIndex((a) => a.id === id && a.owner_uid === ownerUid);
  if (at === -1) return false;
  const next = { ...all[at], ...patch };
  if (typeof next.result === 'string') next.result = next.result.slice(0, MAX_RESULT_CHARS);
  all[at] = next;
  writeAll(all);
  return true;
}

/** Drop one action of this account from this phone. */
export function removeAction(id: string, ownerUid: string): void {
  writeAll(readAll().filter((a) => !(a.id === id && a.owner_uid === ownerUid)));
}

/** Forget every action on this phone (sign-out and account change). */
export function wipeActions(): void {
  removeNonSecretKey(ACTIONS_STORAGE_KEY);
  bumpStateGeneration();
}

function mentionsConfidential(texts: Array<string | undefined>, rules: ProductRulesConfig | null): void {
  const present = texts.filter((t): t is string => typeof t === 'string' && t.trim().length > 0);
  if (present.length === 0) return;
  if (!rules) {
    throw new PhoneActionError(
      'Product rules are not available on this phone yet, so text cannot be queued safely. Connect and try again.',
    );
  }
  for (const text of present) {
    if (findConfidentialMention(text, rules)) {
      throw new PhoneActionError('This text names a confidential product, so it stays on the laptop.');
    }
  }
}

/** Queue an action on this phone. It shows as waiting at once and is sent to the cloud by itself. */
export function enqueueAction(
  input: ActionInput,
  ownerUid: string,
  rules: ProductRulesConfig | null,
  clientId?: string,
): PhoneAction {
  const uid = (ownerUid ?? '').trim();
  if (!uid) {
    throw new PhoneActionError('Sign in to queue actions while the laptop is away.');
  }
  if (input.targetConfidential) {
    throw new PhoneActionError('Confidential items can only be changed on the laptop.');
  }
  const clean = validateActionInput(input);
  // A pause or resume carries no free text, so there is nothing to check against the confidential rules.
  const structured =
    PITCH_ACTION_KINDS.includes(clean.kind) || (WORK_ACTION_KINDS.includes(clean.kind) && !CONTROL_ACTION_KINDS.includes(clean.kind));
  const pitchTexts = structured ? collectStrings(clean.payload) : [];
  mentionsConfidential([clean.payload.body, clean.payload.url, clean.payload.message, ...pitchTexts], rules);

  // Another account's items are not adopted: they go when a different account queues.
  const mine = readAll().filter((a) => a.owner_uid === uid);
  if (mine.filter((a) => a.state === 'waiting').length >= MAX_WAITING_ACTIONS) {
    throw new PhoneActionError('Too many actions are waiting. Wait for the laptop to catch up.');
  }
  // An edit is based on the version the phone saw. A second edit of the same proposal before the laptop has applied
  // the first would be based on a stale version and always become a copy, so it waits its turn.
  if (
    clean.kind === 'pitch_update' &&
    mine.some((a) => a.kind === 'pitch_update' && a.state === 'waiting' && a.target_id === clean.target_id)
  ) {
    throw new PhoneActionError('An earlier edit of this proposal is still waiting for the laptop. Try again when it is applied.');
  }
  const id = (clientId ?? '').trim().toLowerCase() || newActionId();
  if (!UUID_RE.test(id)) {
    throw new PhoneActionError('The action id is not valid.');
  }
  const existing = mine.find((a) => a.id === id);
  if (existing) return existing;
  const action: PhoneAction = {
    id,
    ...clean,
    label: cleanLabel(input.label),
    created_at: new Date().toISOString(),
    owner_uid: uid,
    sent: false,
    state: 'waiting',
    result: null,
  };
  writeAll([...mine, action]);
  return action;
}

export function stateLabel(action: PhoneAction): string {
  if (action.state === 'applied') return 'Applied';
  if (action.state === 'rejected') return 'Not applied';
  return 'Waiting for laptop';
}

export function kindLabel(kind: PhoneActionKind): string {
  if (kind === 'pitch_create') return 'New proposal';
  if (kind === 'pitch_update') return 'Proposal edit';
  if (kind === 'pitch_finalize') return 'Final quotation';
  if (kind === 'pitch_link') return 'Client link';
  if (kind === 'draft_comment') return 'Comment';
  if (kind === 'draft_posted') return 'Mark as posted';
  if (kind === 'agent_task') return 'Agent request';
  if (kind === 'library_upload') return 'Library upload';
  if (kind === 'schedule_update') return 'Schedule change';
  if (kind === 'draft_create') return 'Draft from the phone';
  if (kind === 'workflow_request') return 'Work for the laptop';
  if (kind === 'run_control') return 'Pause or resume a run';
  if (kind === 'automation_control') return 'Automatic work switch';
  return 'Reminder done';
}
