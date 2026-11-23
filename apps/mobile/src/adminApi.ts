// Admin on the phone, straight from the cloud (no laptop). It only uses what the web admin already uses: the
// existing tables and functions under /rest/v1/, which the database already limits to active admins (row-level
// security and admin-only functions). A member who tried the same calls would get empty answers or a refusal, and the
// Admin screen is not even offered to them. Nothing read here is saved on the phone: the screen keeps it in memory.
//
// Every id, kind, cursor and search text is checked before it goes into a request, and every row that comes back is
// rebuilt from checked fields.

import { getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, netJson, type NetworkCredentials } from './net';

export class AdminError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AdminError';
  }
}

export const ADMINS_ONLY = 'Admins only.';
export const OWN_ROLE_MESSAGE = 'Ask another admin to change your own role.';
export const LAST_ADMIN_MESSAGE = "You can't remove the last admin.";
export const MAX_LIMIT = 200;
export const DEFAULT_LIMIT = 50;
export const STORAGE_LIMIT_BYTES = 1_073_741_824;
export const MAX_AI_DAILY_REQUESTS = 100_000;
export const MAX_AI_DAILY_TOKENS = 100_000_000;
const DEFAULT_AI_REQUESTS = 200;
const DEFAULT_AI_TOKENS = 400_000;
const ONLINE_WINDOW_MS = 3 * 60_000;
const IDLE_WINDOW_MS = 30 * 60_000;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SAFE_KIND_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const SAFE_LOCAL_ID_RE = /^[A-Za-z0-9._:@-]{1,200}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export type Presence = 'working' | 'online' | 'idle' | 'offline';
export type Role = 'admin' | 'member';

export interface AdminDevice {
  device_id: string;
  device_kind: string;
  status: string;
  running_agents: string[];
  app_version: string | null;
  last_seen_at: string | null;
}

export interface AdminMember {
  id: string;
  email: string;
  name: string;
  role: Role;
  active: boolean;
  created_at: string | null;
  last_active_at: string | null;
  presence: Presence;
  running_agents: string[];
  devices: AdminDevice[];
}

export interface AdminInvite {
  email: string;
  role: Role;
  created_at: string | null;
  accepted_at: string | null;
  revoked_at: string | null;
}

function isObj(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function s(value: unknown, max = 500): string | null {
  return typeof value === 'string' ? value.slice(0, max) : null;
}

function rows(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isObj) : [];
}

function q(value: string): string {
  return encodeURIComponent(value);
}

async function cloud(bridge: NativeBridge): Promise<{ url: string; creds: NetworkCredentials }> {
  const session = await getStoredSession(bridge);
  const token = await getValidAccessToken(bridge);
  if (!session || !token) throw new AdminError('Sign in to use Admin.');
  const cfg = getPublicSupabaseConfig();
  return { url: cfg.url, creds: { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token } };
}

/** One call to the cloud. A refusal from the database (401/403) is shown as "Admins only." and nothing else. */
async function call<T>(
  bridge: NativeBridge,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const c = await cloud(bridge);
  try {
    return await netJson<T>(
      { destination: 'supabase', method, url: `${c.url}/rest/v1/${path}`, ...(body !== undefined ? { body } : {}), ...(headers ? { headers } : {}) },
      c.creds,
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError) {
      if (err.status === 401 || err.status === 403) throw new AdminError(ADMINS_ONLY);
      if (/last active admin/i.test(`${err.message} ${err.body}`)) throw new AdminError(LAST_ADMIN_MESSAGE);
      if (err.status === 409 || /duplicate/i.test(err.message)) throw new AdminError('That already exists.');
      throw new AdminError(err.status >= 500 ? 'The team server had a problem. Try again.' : 'The team server refused that.');
    }
    throw new AdminError("Can't reach the team server. Check your connection.");
  }
}

export function validUuid(value: unknown, what = 'member'): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!UUID_RE.test(text)) throw new AdminError(`Invalid ${what}.`);
  return text.toLowerCase();
}

function validKind(value: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text || text.includes('..') || !SAFE_KIND_RE.test(text)) throw new AdminError('Invalid kind.');
  return text;
}

function validLocalId(value: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text || text.includes('..') || text.startsWith('.') || !SAFE_LOCAL_ID_RE.test(text)) throw new AdminError('Invalid id.');
  return text;
}

function validBefore(value: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text || text.includes('..') || /[,()*%\s]/.test(text) || !Number.isFinite(Date.parse(text))) {
    throw new AdminError('Invalid time.');
  }
  return text;
}

function limitOf(value: number | undefined): number {
  if (value === undefined) return DEFAULT_LIMIT;
  if (!Number.isInteger(value) || value <= 0) throw new AdminError('Invalid limit.');
  return Math.min(value, MAX_LIMIT);
}

/** Escape what LIKE and the REST filter syntax treat as special in a search text. */
export function escapeSearch(text: string): string {
  let out = String(text ?? '');
  for (const ch of ['\\', '%', '_', '*', ',', '(', ')']) out = out.split(ch).join(`\\${ch}`);
  return out;
}

function profileLabel(p: Record<string, unknown> | undefined): string {
  if (!p) return '';
  return (s(p.display_name, 200) ?? '').trim() || (s(p.email, 200) ?? '').trim();
}

async function profileMap(bridge: NativeBridge): Promise<Map<string, Record<string, unknown>>> {
  const list = rows(await call<unknown>(bridge, 'GET', 'profiles?select=id,email,display_name,avatar_url'));
  const map = new Map<string, Record<string, unknown>>();
  for (const p of list) if (typeof p.id === 'string') map.set(p.id.toLowerCase(), p);
  return map;
}

// --- Team ---

const RANK: Record<Presence, number> = { offline: 0, idle: 1, online: 2, working: 3 };

/** Presence from device heartbeats: seen in the last 3 minutes counts as live, in the last 30 as idle. */
export function derivePresence(devices: unknown, active = true, now: number = Date.now()): Presence {
  if (!active) return 'offline';
  let best: Presence = 'offline';
  for (const dev of Array.isArray(devices) ? devices : []) {
    if (!isObj(dev)) continue;
    const status = String(dev.status ?? '').trim().toLowerCase();
    if (status === 'offline') continue;
    const seen = typeof dev.last_seen_at === 'string' ? Date.parse(dev.last_seen_at) : NaN;
    if (!Number.isFinite(seen)) continue;
    const age = now - seen;
    const known = status === 'working' || status === 'online' || status === 'idle';
    let cand: Presence = 'offline';
    if (age <= ONLINE_WINDOW_MS && known) cand = status as Presence;
    else if (age <= IDLE_WINDOW_MS && known) cand = 'idle';
    if (RANK[cand] > RANK[best]) best = cand;
  }
  return best;
}

export async function fetchAdminTeam(
  _adminId: string,
  bridge: NativeBridge = getNativeBridge(),
  now: number = Date.now(),
): Promise<{ members: AdminMember[]; invites: AdminInvite[] }> {
  const profiles = rows(
    await call<unknown>(bridge, 'GET', 'profiles?select=id,email,display_name,avatar_url,role,active,created_at,last_seen_at&order=created_at.asc'),
  );
  const [devices, events, invites] = await Promise.all([
    call<unknown>(bridge, 'GET', 'device_status?select=owner_id,device_id,device_kind,status,running_agents,app_version,last_seen_at&order=last_seen_at.desc&limit=500'),
    call<unknown>(bridge, 'GET', 'activity_events?select=owner_id,ts&order=ts.desc&limit=500'),
    call<unknown>(bridge, 'GET', 'invites?select=email,role,created_at,accepted_at,revoked_at&order=created_at.desc&limit=100'),
  ]);
  const devicesByOwner = new Map<string, AdminDevice[]>();
  for (const d of rows(devices)) {
    if (typeof d.owner_id !== 'string' || typeof d.device_id !== 'string') continue;
    const list = devicesByOwner.get(d.owner_id.toLowerCase()) ?? [];
    list.push({
      device_id: d.device_id.slice(0, 128),
      device_kind: s(d.device_kind, 20) ?? 'desktop',
      status: s(d.status, 20) ?? 'offline',
      running_agents: Array.isArray(d.running_agents) ? d.running_agents.filter((a): a is string => typeof a === 'string').slice(0, 20) : [],
      app_version: s(d.app_version, 40),
      last_seen_at: s(d.last_seen_at, 40),
    });
    devicesByOwner.set(d.owner_id.toLowerCase(), list);
  }
  const latestEvent = new Map<string, string>();
  for (const e of rows(events)) {
    if (typeof e.owner_id === 'string' && typeof e.ts === 'string' && !latestEvent.has(e.owner_id.toLowerCase())) {
      latestEvent.set(e.owner_id.toLowerCase(), e.ts);
    }
  }
  const members: AdminMember[] = [];
  for (const p of profiles) {
    if (typeof p.id !== 'string' || !UUID_RE.test(p.id) || (p.role !== 'admin' && p.role !== 'member')) continue;
    const id = p.id.toLowerCase();
    const active = p.active !== false;
    const devs = devicesByOwner.get(id) ?? [];
    const stamps = [s(p.last_seen_at, 40), devs[0]?.last_seen_at ?? null, latestEvent.get(id) ?? null]
      .filter((v): v is string => Boolean(v) && Number.isFinite(Date.parse(v as string)))
      .sort((a, b) => Date.parse(b) - Date.parse(a));
    const presence = derivePresence(devs, active, now);
    members.push({
      id,
      email: s(p.email, 200) ?? '',
      name: profileLabel(p) || id,
      role: p.role,
      active,
      created_at: s(p.created_at, 40),
      last_active_at: stamps[0] ?? null,
      presence,
      running_agents: active && presence !== 'offline' ? [...new Set(devs.flatMap((d) => d.running_agents))] : [],
      devices: devs,
    });
  }
  const inviteList: AdminInvite[] = [];
  for (const i of rows(invites)) {
    if (typeof i.email !== 'string' || (i.role !== 'admin' && i.role !== 'member')) continue;
    inviteList.push({
      email: i.email.slice(0, 200),
      role: i.role,
      created_at: s(i.created_at, 40),
      accepted_at: s(i.accepted_at, 40),
      revoked_at: s(i.revoked_at, 40),
    });
  }
  return { members, invites: inviteList };
}

function cleanEmail(raw: unknown): string {
  const email = String(raw ?? '').trim().toLowerCase();
  if (email.length > 254 || !EMAIL_RE.test(email)) throw new AdminError('Enter a valid email address.');
  return email;
}

function cleanRole(raw: unknown): Role {
  if (raw !== 'admin' && raw !== 'member') throw new AdminError("Role must be 'admin' or 'member'.");
  return raw;
}

export async function inviteMember(email: string, role: Role, bridge: NativeBridge = getNativeBridge()): Promise<void> {
  const clean = cleanEmail(email);
  const cleanR = cleanRole(role);
  const res = await call<unknown>(bridge, 'POST', 'invites', { email: clean, role: cleanR }, { Prefer: 'return=representation' });
  if (!Array.isArray(res) || res.length === 0) throw new AdminError(ADMINS_ONLY);
}

export async function revokeInvite(email: string, bridge: NativeBridge = getNativeBridge()): Promise<void> {
  const clean = cleanEmail(email);
  const res = await call<unknown>(
    bridge,
    'PATCH',
    `invites?email=eq.${q(clean)}&revoked_at=is.null`,
    { revoked_at: new Date().toISOString() },
    { Prefer: 'return=representation' },
  );
  if (!Array.isArray(res) || res.length === 0) throw new AdminError(ADMINS_ONLY);
}

export interface MemberStub {
  id: string;
  role: Role;
  active: boolean;
}

function wouldRemoveLastAdmin(targetId: string, members: MemberStub[]): boolean {
  const target = members.find((m) => m.id.toLowerCase() === targetId);
  if (!target || target.role !== 'admin' || !target.active) return false;
  return !members.some((m) => m.id.toLowerCase() !== targetId && m.role === 'admin' && m.active);
}

async function patchMember(id: string, body: Record<string, unknown>, bridge: NativeBridge): Promise<void> {
  const res = await call<unknown>(bridge, 'PATCH', `profiles?id=eq.${q(id)}`, body, { Prefer: 'return=representation' });
  if (!Array.isArray(res) || res.length === 0) throw new AdminError('Member not found, or admins only.');
}

/** Change another member's role. Your own role, and the last active admin, are refused here and again by the database. */
export async function changeMemberRole(
  memberId: string,
  role: Role,
  selfId: string,
  members: MemberStub[],
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const id = validUuid(memberId);
  const cleanR = cleanRole(role);
  if (id === String(selfId ?? '').toLowerCase()) throw new AdminError(OWN_ROLE_MESSAGE);
  if (cleanR !== 'admin' && wouldRemoveLastAdmin(id, members)) throw new AdminError(LAST_ADMIN_MESSAGE);
  await patchMember(id, { role: cleanR }, bridge);
}

export async function changeMemberActive(
  memberId: string,
  active: boolean,
  selfId: string,
  members: MemberStub[],
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const id = validUuid(memberId);
  if (typeof active !== 'boolean') throw new AdminError('Active must be on or off.');
  if (id === String(selfId ?? '').toLowerCase()) throw new AdminError(OWN_ROLE_MESSAGE);
  if (!active && wouldRemoveLastAdmin(id, members)) throw new AdminError(LAST_ADMIN_MESSAGE);
  await patchMember(id, { active }, bridge);
}

// --- Activity ---

export interface AdminActivityEvent {
  id: string;
  owner_id: string;
  member_name: string;
  ts: string;
  kind: string;
  agent_id: string | null;
  summary: string | null;
}

export async function fetchActivity(
  params: { member?: string; kind?: string; before?: string; limit?: number } = {},
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ events: AdminActivityEvent[]; next_before: string | null }> {
  const limit = limitOf(params.limit);
  const filters = ['select=id,owner_id,ts,kind,agent_id,summary', 'order=ts.desc,id.desc', `limit=${limit}`];
  if (params.member) filters.push(`owner_id=eq.${validUuid(params.member)}`);
  if (params.kind) filters.push(`kind=eq.${q(validKind(params.kind))}`);
  if (params.before) filters.push(`ts=lt.${q(validBefore(params.before))}`);
  const [list, names] = await Promise.all([call<unknown>(bridge, 'GET', `activity_events?${filters.join('&')}`), profileMap(bridge)]);
  const events: AdminActivityEvent[] = [];
  for (const r of rows(list)) {
    if (typeof r.owner_id !== 'string' || typeof r.ts !== 'string') continue;
    events.push({
      id: String(r.id ?? `${r.owner_id}-${r.ts}`),
      owner_id: r.owner_id,
      member_name: profileLabel(names.get(r.owner_id.toLowerCase())) || r.owner_id,
      ts: r.ts,
      kind: s(r.kind, 64) ?? '',
      agent_id: s(r.agent_id, 64),
      summary: s(r.summary, 500),
    });
  }
  return { events, next_before: events.length === limit ? events[events.length - 1].ts : null };
}

// --- Workspace ---

export interface AdminRecord {
  id: string;
  kind: string;
  local_id: string;
  parent_local_id: string | null;
  title: string | null;
  local_updated_at: string;
  synced_at: string | null;
}

function cleanRecord(r: Record<string, unknown>): AdminRecord | null {
  if (typeof r.kind !== 'string' || typeof r.local_id !== 'string' || typeof r.local_updated_at !== 'string') return null;
  return {
    id: String(r.id ?? r.local_id),
    kind: r.kind.slice(0, 64),
    local_id: r.local_id.slice(0, 200),
    parent_local_id: s(r.parent_local_id, 200),
    title: s(r.title, 300),
    local_updated_at: r.local_updated_at,
    synced_at: s(r.synced_at, 40),
  };
}

export async function fetchMemberRecords(
  memberId: string,
  params: { kind?: string; q?: string; before?: string; limit?: number } = {},
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ records: AdminRecord[]; next_before: string | null }> {
  const id = validUuid(memberId);
  const limit = limitOf(params.limit);
  const filters = [
    `owner_id=eq.${id}`,
    'deleted_at=is.null',
    'select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at',
    'order=local_updated_at.desc',
    `limit=${limit}`,
  ];
  if (params.kind) filters.push(`kind=eq.${q(validKind(params.kind))}`);
  if (params.q) filters.push(`title=ilike.*${q(escapeSearch(params.q.slice(0, 100)))}*`);
  if (params.before) filters.push(`local_updated_at=lt.${q(validBefore(params.before))}`);
  const list = rows(await call<unknown>(bridge, 'GET', `mirror_records?${filters.join('&')}`));
  const records = list.map(cleanRecord).filter((r): r is AdminRecord => r !== null);
  return { records, next_before: records.length === limit ? records[records.length - 1].local_updated_at : null };
}

export async function fetchRecordDetail(
  memberId: string,
  kind: string,
  localId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ record: AdminRecord & { data: Record<string, unknown> }; children: AdminRecord[] }> {
  const id = validUuid(memberId);
  const k = validKind(kind);
  const lid = validLocalId(localId);
  const found = rows(
    await call<unknown>(
      bridge,
      'GET',
      `mirror_records?owner_id=eq.${id}&kind=eq.${q(k)}&local_id=eq.${q(lid)}&deleted_at=is.null` +
        '&select=id,kind,local_id,parent_local_id,title,data,local_updated_at,synced_at&limit=1',
    ),
  );
  const record = found.length > 0 ? cleanRecord(found[0]) : null;
  if (!record) throw new AdminError('Record not found.');
  const kids = rows(
    await call<unknown>(
      bridge,
      'GET',
      `mirror_records?owner_id=eq.${id}&parent_local_id=eq.${q(lid)}&deleted_at=is.null` +
        '&select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at&order=local_updated_at.desc&limit=50',
    ),
  );
  return {
    record: { ...record, data: isObj(found[0].data) ? found[0].data : {} },
    children: kids.map(cleanRecord).filter((r): r is AdminRecord => r !== null),
  };
}

export interface AdminPreviewInfo {
  id: string;
  kind: string;
  parent_local_id: string | null;
  content_type: string;
  bytes: number;
  created_at: string | null;
  expires_at: string | null;
}

/** Previews are listed (what exists, how big, until when). Opening the image stays on the desktop app. */
export async function fetchMemberPreviews(
  memberId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ previews: AdminPreviewInfo[] }> {
  const id = validUuid(memberId);
  const list = rows(
    await call<unknown>(
      bridge,
      'GET',
      `media_previews?owner_id=eq.${id}&deleted_at=is.null` +
        `&select=id,owner_id,parent_local_id,kind,storage_path,content_type,bytes,created_at,expires_at&order=created_at.desc&limit=${DEFAULT_LIMIT}`,
    ),
  );
  const previews: AdminPreviewInfo[] = [];
  for (const r of list) {
    const path = String(r.storage_path ?? '');
    if (!path.toLowerCase().startsWith(`${id}/`) || path.split('/').includes('..')) continue;
    if (typeof r.id !== 'string' || typeof r.bytes !== 'number') continue;
    previews.push({
      id: r.id,
      kind: s(r.kind, 40) ?? 'preview',
      parent_local_id: s(r.parent_local_id, 200),
      content_type: s(r.content_type, 80) ?? '',
      bytes: r.bytes,
      created_at: s(r.created_at, 40),
      expires_at: s(r.expires_at, 40),
    });
  }
  return { previews };
}

// --- Pitch ---

export interface AdminPitchLink {
  share_id: string;
  local_proposal_id: string | null;
  kind: string;
  status: string;
  client_name: string;
  business_name: string;
  updated_at: string | null;
}

export interface AdminPitchRequest {
  id: string;
  share_id: string;
  name: string;
  business: string;
  phone: string;
  notes: string;
  items: string[];
  total_shown: number | null;
  created_at: string | null;
  seen_at: string | null;
}

export async function fetchMemberPitch(
  memberId: string,
  catalogNames: Record<string, string>,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ proposals: AdminPitchLink[]; submissions: AdminPitchRequest[] }> {
  const id = validUuid(memberId);
  const [props, subs] = await Promise.all([
    call<unknown>(
      bridge,
      'GET',
      `published_proposals?owner_id=eq.${id}&select=share_id,local_proposal_id,kind,payload,status,updated_at&order=updated_at.desc&limit=${MAX_LIMIT}`,
    ),
    call<unknown>(
      bridge,
      'GET',
      `quote_submissions?owner_id=eq.${id}&select=id,share_id,choices,contact,created_at,seen_at&order=created_at.desc&limit=${MAX_LIMIT}`,
    ),
  ]);
  const customByShare = new Map<string, Map<string, string>>();
  const proposals: AdminPitchLink[] = [];
  for (const r of rows(props)) {
    const share = String(r.share_id ?? '');
    const payload = isObj(r.payload) ? r.payload : {};
    const contact = isObj(payload.contact) ? payload.contact : {};
    const preset = isObj(payload.preset) ? payload.preset : {};
    const custom = new Map<string, string>();
    for (const c of Array.isArray(payload.custom_items) ? payload.custom_items : []) {
      if (isObj(c) && typeof c.id === 'string') custom.set(c.id, String(c.n ?? c.id).slice(0, 200));
    }
    customByShare.set(share, custom);
    proposals.push({
      share_id: share.slice(0, 6),
      local_proposal_id: s(r.local_proposal_id, 200),
      kind: s(r.kind, 40) ?? '',
      status: s(r.status, 20) ?? '',
      client_name: String(contact.name ?? preset.clientName ?? '').trim().slice(0, 200),
      business_name: String(contact.business ?? preset.businessName ?? '').trim().slice(0, 200),
      updated_at: s(r.updated_at, 40),
    });
  }
  const submissions: AdminPitchRequest[] = [];
  for (const r of rows(subs)) {
    if (typeof r.id !== 'string') continue;
    const share = String(r.share_id ?? '');
    const contact = isObj(r.contact) ? r.contact : {};
    const choices = isObj(r.choices) ? r.choices : {};
    const ids = [
      ...new Set(
        [...(Array.isArray(choices.selected) ? choices.selected : []), ...(Array.isArray(choices.custom_selected) ? choices.custom_selected : [])].filter(
          (x): x is string => typeof x === 'string' && x.trim() !== '',
        ),
      ),
    ].slice(0, 100);
    const custom = customByShare.get(share);
    const total = choices.total_shown;
    submissions.push({
      id: r.id,
      share_id: share.slice(0, 6),
      name: String(contact.name ?? '').slice(0, 200),
      business: String(contact.business ?? '').slice(0, 200),
      phone: String(contact.phone ?? '').slice(0, 200),
      notes: String(contact.notes ?? '').slice(0, 1000),
      items: ids.map((x) => custom?.get(x) ?? catalogNames[x] ?? x),
      total_shown: typeof total === 'number' && Number.isFinite(total) ? total : null,
      created_at: s(r.created_at, 40),
      seen_at: s(r.seen_at, 40),
    });
  }
  return { proposals, submissions };
}

// --- Storage ---

export interface AdminStorage {
  bytes: number;
  files: number;
  limit_bytes: number;
  percent: number;
  warning: boolean;
  by_kind: Array<{ kind: string; bytes: number; files: number }>;
}

export async function fetchStorage(bridge: NativeBridge = getNativeBridge()): Promise<AdminStorage> {
  const raw = await call<unknown>(bridge, 'POST', 'rpc/storage_usage', {});
  const d = isObj(raw) ? raw : {};
  const bytes = Number.isFinite(Number(d.bytes)) ? Math.max(0, Number(d.bytes)) : 0;
  const files = Number.isFinite(Number(d.files)) ? Math.max(0, Number(d.files)) : 0;
  const by_kind = isObj(d.by_kind)
    ? Object.entries(d.by_kind)
        .filter(([, v]) => isObj(v))
        .map(([kind, v]) => ({ kind: kind.slice(0, 40), bytes: Number((v as Record<string, unknown>).bytes) || 0, files: Number((v as Record<string, unknown>).files) || 0 }))
    : [];
  return {
    bytes,
    files,
    limit_bytes: STORAGE_LIMIT_BYTES,
    percent: Math.round((bytes / STORAGE_LIMIT_BYTES) * 1000) / 10,
    warning: bytes * 100 >= STORAGE_LIMIT_BYTES * 80,
    by_kind,
  };
}

// --- Audit log ---

export interface AdminAuditEntry {
  id: string;
  actor_name: string;
  action: string;
  target_label: string;
  created_at: string;
}

export async function fetchAudit(
  params: { before?: string; limit?: number } = {},
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ entries: AdminAuditEntry[]; next_before: string | null }> {
  const limit = limitOf(params.limit);
  const filters = ['select=id,actor_id,action,target,created_at', 'order=created_at.desc,id.desc', `limit=${limit}`];
  if (params.before) filters.push(`created_at=lt.${q(validBefore(params.before))}`);
  const [list, names] = await Promise.all([call<unknown>(bridge, 'GET', `audit_log?${filters.join('&')}`), profileMap(bridge)]);
  const entries: AdminAuditEntry[] = [];
  for (const r of rows(list)) {
    if (typeof r.created_at !== 'string') continue;
    const actor = typeof r.actor_id === 'string' ? r.actor_id.toLowerCase() : '';
    const target = String(r.target ?? '');
    entries.push({
      id: String(r.id ?? r.created_at),
      actor_name: profileLabel(names.get(actor)) || (actor ? actor : 'System'),
      action: s(r.action, 100) ?? '',
      target_label: UUID_RE.test(target) ? profileLabel(names.get(target.toLowerCase())) || target : target.slice(0, 200),
      created_at: r.created_at,
    });
  }
  return { entries, next_before: entries.length === limit ? entries[entries.length - 1].created_at : null };
}

// --- AI keys ---

export interface AdminKeyLabel {
  label: string;
  last_status: number | null;
  last_ok_at: string | null;
  cooldown_until: string | null;
  last_error: string | null;
}

export interface AdminAiUsage {
  owner_id: string;
  name: string;
  active: boolean;
  today: { requests: number; tokens: number; rejected: number };
  seven_days: { requests: number; tokens: number; rejected: number };
  limit: { daily_requests: number; daily_tokens: number; is_override: boolean };
}

export interface AdminAiOverview {
  keys: AdminKeyLabel[];
  models: Array<{ model: string; enabled: boolean; max_tokens_cap: number }>;
  default_limit: { daily_requests: number; daily_tokens: number };
  usage: AdminAiUsage[];
}

function whole(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
}

export async function fetchAiOverview(bridge: NativeBridge = getNativeBridge(), now: number = Date.now()): Promise<AdminAiOverview> {
  const today = new Date(now).toISOString().slice(0, 10);
  const since = new Date(now - 6 * 86_400_000).toISOString().slice(0, 10);
  const [keysRaw, modelsRaw, limitsRaw, usageRaw, profilesRaw] = await Promise.all([
    call<unknown>(bridge, 'POST', 'rpc/admin_ai_key_labels', {}),
    call<unknown>(bridge, 'GET', 'ai_allowed_models?select=model,enabled,max_tokens_cap&order=model.asc'),
    call<unknown>(bridge, 'GET', 'ai_limits?select=id,owner_id,daily_requests,daily_tokens'),
    call<unknown>(bridge, 'GET', `ai_usage_daily?day=gte.${q(since)}&select=owner_id,day,requests,prompt_tokens,completion_tokens,rejected`),
    call<unknown>(bridge, 'GET', 'profiles?select=id,email,display_name,active&order=created_at.asc'),
  ]);
  let defaultLimit = { daily_requests: DEFAULT_AI_REQUESTS, daily_tokens: DEFAULT_AI_TOKENS };
  const overrides = new Map<string, { daily_requests: number; daily_tokens: number }>();
  for (const l of rows(limitsRaw)) {
    const lim = { daily_requests: whole(l.daily_requests, DEFAULT_AI_REQUESTS), daily_tokens: whole(l.daily_tokens, DEFAULT_AI_TOKENS) };
    if (l.owner_id === null || l.owner_id === undefined) defaultLimit = lim;
    else if (typeof l.owner_id === 'string') overrides.set(l.owner_id.toLowerCase(), lim);
  }
  const zero = () => ({ requests: 0, tokens: 0, rejected: 0 });
  const todayBy = new Map<string, ReturnType<typeof zero>>();
  const weekBy = new Map<string, ReturnType<typeof zero>>();
  for (const u of rows(usageRaw)) {
    if (typeof u.owner_id !== 'string') continue;
    const id = u.owner_id.toLowerCase();
    const add = (map: Map<string, ReturnType<typeof zero>>) => {
      const b = map.get(id) ?? zero();
      b.requests += whole(u.requests, 0);
      b.tokens += whole(u.prompt_tokens, 0) + whole(u.completion_tokens, 0);
      b.rejected += whole(u.rejected, 0);
      map.set(id, b);
    };
    add(weekBy);
    if (u.day === today) add(todayBy);
  }
  const usage: AdminAiUsage[] = [];
  for (const p of rows(profilesRaw)) {
    if (typeof p.id !== 'string') continue;
    const id = p.id.toLowerCase();
    const override = overrides.get(id);
    usage.push({
      owner_id: id,
      name: profileLabel(p) || id,
      active: p.active !== false,
      today: todayBy.get(id) ?? zero(),
      seven_days: weekBy.get(id) ?? zero(),
      limit: { ...(override ?? defaultLimit), is_override: override !== undefined },
    });
  }
  return {
    keys: rows(keysRaw).map((k) => ({
      label: (s(k.label, 80) ?? '').replace(/nvapi-[A-Za-z0-9_-]+/g, 'nvapi-...'),
      last_status: typeof k.last_status === 'number' ? k.last_status : null,
      last_ok_at: s(k.last_ok_at, 40),
      cooldown_until: s(k.cooldown_until, 40),
      last_error: s(k.last_error, 200) === null ? null : (k.last_error as string).slice(0, 200).replace(/nvapi-[A-Za-z0-9_-]+/g, 'nvapi-...'),
    })),
    models: rows(modelsRaw)
      .filter((m) => typeof m.model === 'string')
      .map((m) => ({ model: (m.model as string).slice(0, 120), enabled: m.enabled !== false, max_tokens_cap: whole(m.max_tokens_cap, 4096) })),
    default_limit: defaultLimit,
    usage,
  };
}

function limitNumber(value: unknown, max: number, what: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > max) {
    throw new AdminError(`${what} must be a whole number from 1 to ${max.toLocaleString('en-US')}.`);
  }
  return value;
}

/** Set the team default (memberId null) or one member's override. */
export async function saveAiLimits(
  memberId: string | null,
  requests: number,
  tokens: number,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const id = memberId === null ? null : validUuid(memberId);
  const dailyRequests = limitNumber(requests, MAX_AI_DAILY_REQUESTS, 'Requests per day');
  const dailyTokens = limitNumber(tokens, MAX_AI_DAILY_TOKENS, 'Tokens per day');
  if (id === null) {
    const patched = await call<unknown>(bridge, 'PATCH', 'ai_limits?owner_id=is.null', { daily_requests: dailyRequests, daily_tokens: dailyTokens }, { Prefer: 'return=representation' });
    if (Array.isArray(patched) && patched.length > 0) return;
    await call<unknown>(bridge, 'POST', 'ai_limits', { owner_id: null, daily_requests: dailyRequests, daily_tokens: dailyTokens }, { Prefer: 'return=representation' });
    return;
  }
  await call<unknown>(
    bridge,
    'POST',
    'ai_limits?on_conflict=owner_id',
    { owner_id: id, daily_requests: dailyRequests, daily_tokens: dailyTokens },
    { Prefer: 'resolution=merge-duplicates,return=representation' },
  );
}

export async function resetAiLimit(memberId: string, bridge: NativeBridge = getNativeBridge()): Promise<void> {
  await call<unknown>(bridge, 'DELETE', `ai_limits?owner_id=eq.${validUuid(memberId)}`);
}
