// Personal repositories on the phone, straight from the cloud (owner only; no laptop needed). The phone lists, adds
// (GitHub only), edits and removes the signed-in user's own repos. A folder path edited here is only a request: it is
// stored as "pending" and the laptop checks it on its next sync before it uses it.
//
// Every answer from the cloud is rebuilt field by field from checked values, every value is checked before it is
// sent, and text that names a confidential product is refused here so it never reaches the cloud. Error messages are
// short and never repeat what was sent.

import { getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import { HttpStatusError, getPublicSupabaseConfig, netJson } from './net';
import { findConfidentialMention } from './productRules';
import type { ProductRulesConfig } from './types';

export class PersonalReposError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PersonalReposError';
  }
}

/** The text names a confidential product (or the rules are not loaded, so it cannot be checked). Do it on the laptop. */
export class RepoRefused extends PersonalReposError {
  constructor(message = 'That names a confidential product. Add or edit it on your laptop.') {
    super(message);
    this.name = 'RepoRefused';
  }
}

export const REPO_STATUSES = ['released', 'in_development', 'secret'] as const;
export type RepoStatus = (typeof REPO_STATUSES)[number];

export interface PersonalRepo {
  uid: string;
  id: string;
  name: string;
  kind: 'github' | 'local';
  github: string | null;
  local_path: string | null;
  path_status: 'ok' | 'pending' | 'rejected';
  path_reason: string | null;
  branch: string | null;
  status: RepoStatus;
  marketing_angle: string | null;
  confidential: boolean;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const OWNER_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;
const REPO_NAME_RE = /^[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}$/;
const BRANCH_RE = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/;
// eslint-disable-next-line no-control-regex
const ANGLE_BAD_RE = /[\x00-\x08\x0b-\x1f\x7f]/;
const MAX_REPOS = 100;
const SELECT = 'uid,owner_id,id,name,kind,github,local_path,path_status,path_reason,branch,status,marketing_angle,confidential';

/** 'owner/repo' from a slug or a github.com address, or null. Never any other host or shape. */
export function normalizeGithub(input: unknown): string | null {
  if (typeof input !== 'string') return null;
  let text = input.trim();
  if (!text || CONTROL_RE.test(text)) return null;
  const ssh = /^git@github\.com:([^/]+)\/([^/]+?)(?:\.git)?\/?$/i.exec(text);
  if (ssh) {
    text = `${ssh[1]}/${ssh[2]}`;
  } else if (text.includes('://')) {
    const match = /^https:\/\/(?:www\.)?github\.com\/([^/?#@\s]+)\/([^/?#@\s]+?)(?:\.git)?\/?$/i.exec(text);
    if (!match) return null;
    text = `${match[1]}/${match[2]}`;
  }
  const parts = text.split('/');
  if (parts.length !== 2) return null;
  const [owner, repo] = parts;
  if (!OWNER_RE.test(owner) || !REPO_NAME_RE.test(repo) || repo === '.' || repo === '..') return null;
  return `${owner}/${repo}`;
}

/** A safe repo id made from a name: lowercase letters, digits and dashes. */
export function slugifyId(text: string): string {
  const slug = String(text ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    .replace(/-+$/g, '');
  return slug || 'repo';
}

function validBranch(value: string): boolean {
  return BRANCH_RE.test(value) && !value.includes('..') && !value.includes('//') && !value.endsWith('/') && !value.endsWith('.lock');
}

function validPath(value: unknown): string {
  if (typeof value !== 'string') throw new PersonalReposError('The folder path must be text.');
  const text = value.trim();
  if (!text) throw new PersonalReposError('A folder path is required.');
  if (text.length > 500) throw new PersonalReposError('The folder path is too long.');
  if (CONTROL_RE.test(text)) throw new PersonalReposError('The folder path has characters that are not allowed.');
  if (text.includes('://') || text.startsWith('\\\\')) throw new PersonalReposError('Use a folder on your laptop.');
  if (text.split(/[\\/]+/).includes('..')) throw new PersonalReposError("The folder path cannot contain '..'.");
  return text;
}

export type RepoFieldChanges = Partial<{
  id: string;
  name: string;
  branch: string | null;
  status: string;
  marketing_angle: string | null;
  confidential: boolean;
  local_path: string;
}>;

const EDITABLE = ['id', 'name', 'branch', 'status', 'marketing_angle', 'confidential', 'local_path'];

/** Check the fields of an edit and return them cleaned (empty branch and angle become null). Throws on anything odd. */
export function validateRepoFields(fields: Record<string, unknown>, kind: 'github' | 'local'): Record<string, unknown> {
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) throw new PersonalReposError('Nothing to change.');
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(fields)) {
    if (!EDITABLE.includes(key)) throw new PersonalReposError('That field cannot be edited.');
  }
  if ('name' in fields) {
    const name = typeof fields.name === 'string' ? fields.name.trim() : '';
    if (!name || name.length > 80 || CONTROL_RE.test(name)) throw new PersonalReposError('The name must be 1 to 80 characters.');
    out.name = name;
  }
  if ('id' in fields) {
    if (typeof fields.id !== 'string' || !ID_RE.test(fields.id)) {
      throw new PersonalReposError('The id may use lowercase letters, digits and dashes, up to 63 characters.');
    }
    out.id = fields.id;
  }
  if ('branch' in fields) {
    const branch = typeof fields.branch === 'string' ? fields.branch.trim() : fields.branch === null ? '' : undefined;
    if (branch === undefined) throw new PersonalReposError('The branch must be text.');
    if (branch && !validBranch(branch)) throw new PersonalReposError('That is not a valid branch name.');
    out.branch = branch || null;
  }
  if ('status' in fields) {
    if (!(REPO_STATUSES as readonly unknown[]).includes(fields.status)) throw new PersonalReposError('Pick a status from the list.');
    out.status = fields.status;
  }
  if ('marketing_angle' in fields) {
    const angle = typeof fields.marketing_angle === 'string' ? fields.marketing_angle.trim() : fields.marketing_angle === null ? '' : undefined;
    if (angle === undefined) throw new PersonalReposError('The marketing angle must be text.');
    if (angle.length > 500 || ANGLE_BAD_RE.test(angle)) throw new PersonalReposError('The marketing angle is too long or has characters that are not allowed.');
    out.marketing_angle = angle || null;
  }
  if ('confidential' in fields) {
    if (typeof fields.confidential !== 'boolean') throw new PersonalReposError('Confidential must be on or off.');
    out.confidential = fields.confidential;
  }
  if ('local_path' in fields) {
    if (kind !== 'local') throw new PersonalReposError('Only a local repository has a folder to edit.');
    out.local_path = validPath(fields.local_path);
  }
  return out;
}

function checkConfidential(texts: Array<string | null | undefined>, rules: ProductRulesConfig | null): void {
  const joined = texts.filter((t): t is string => typeof t === 'string' && t.length > 0).join('\n');
  if (!joined) return;
  if (!rules) throw new RepoRefused('The product rules are not loaded yet, so this cannot be checked. Try again when online, or use your laptop.');
  if (findConfidentialMention(joined, rules)) throw new RepoRefused();
}

// ------------------------------------------------------------------------------------------------------- cloud

async function cloud(bridge: NativeBridge): Promise<{ url: string; userId: string; creds: { supabaseUrl: string; supabaseAnonKey: string; supabaseAccessToken: string } }> {
  const session = await getStoredSession(bridge);
  const token = await getValidAccessToken(bridge);
  if (!session?.user_id || !token) throw new PersonalReposError('Sign in to use Repos.');
  const cfg = getPublicSupabaseConfig();
  return { url: cfg.url, userId: session.user_id, creds: { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token } };
}

async function call<T>(bridge: NativeBridge, method: 'GET' | 'POST', path: string, body?: unknown): Promise<{ data: T; userId: string }> {
  const c = await cloud(bridge);
  try {
    const data = await netJson<T>(
      { destination: 'supabase', method, url: `${c.url}/rest/v1/${path}`, ...(body !== undefined ? { body } : {}) },
      c.creds,
      bridge,
    );
    return { data, userId: c.userId };
  } catch (err) {
    if (err instanceof HttpStatusError) {
      if (err.status === 401) throw new PersonalReposError('Sign in again to use Repos.');
      if (err.status === 409) throw new PersonalReposError('That repository or id is already in your list.');
      if (err.status >= 500) throw new PersonalReposError('The team server had a problem. Try again.');
      throw new PersonalReposError('The team server refused that.');
    }
    throw new PersonalReposError("Can't reach the team server. Check your connection.");
  }
}

function isObj(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/** Rebuild one row from checked fields. Null for anything that is not a usable, non-confidential row of this user. */
function toRepo(raw: unknown, userId: string): PersonalRepo | null {
  if (!isObj(raw)) return null;
  const { uid, id, name, kind } = raw;
  if (typeof uid !== 'string' || !UUID_RE.test(uid) || raw.owner_id !== userId) return null;
  if (typeof id !== 'string' || !ID_RE.test(id)) return null;
  if (typeof name !== 'string' || !name || name.length > 80 || CONTROL_RE.test(name)) return null;
  if (kind !== 'github' && kind !== 'local') return null;
  if (raw.confidential !== false) return null;
  let github: string | null = null;
  if (raw.github !== null && raw.github !== undefined) {
    github = typeof raw.github === 'string' && !raw.github.includes('://') ? normalizeGithub(raw.github) : null;
    if (!github) return null;
  }
  let path: string | null = null;
  if (raw.local_path !== null && raw.local_path !== undefined) {
    try {
      path = validPath(raw.local_path);
    } catch {
      return null;
    }
  }
  if ((kind === 'github' && !github) || (kind === 'local' && !path)) return null;
  const branch = raw.branch === null || raw.branch === undefined || raw.branch === '' ? null : raw.branch;
  if (branch !== null && !(typeof branch === 'string' && validBranch(branch))) return null;
  if (!(REPO_STATUSES as readonly unknown[]).includes(raw.status)) return null;
  const angle = raw.marketing_angle === null || raw.marketing_angle === undefined || raw.marketing_angle === '' ? null : raw.marketing_angle;
  if (angle !== null && !(typeof angle === 'string' && angle.length <= 500 && !ANGLE_BAD_RE.test(angle))) return null;
  const pathStatus = raw.path_status === 'ok' || raw.path_status === 'pending' || raw.path_status === 'rejected' ? raw.path_status : 'pending';
  const reason = typeof raw.path_reason === 'string' && raw.path_reason ? raw.path_reason.slice(0, 200).replace(CONTROL_RE, ' ') : null;
  return {
    uid, id, name, kind, github, local_path: path, path_status: pathStatus, path_reason: reason, branch,
    status: raw.status as RepoStatus, marketing_angle: angle as string | null, confidential: false,
  };
}

/** The signed-in user's own repos, oldest first. */
export async function listPersonalRepos(bridge: NativeBridge = getNativeBridge()): Promise<PersonalRepo[]> {
  const { data, userId } = await call<unknown>(bridge, 'GET', `personal_repos?select=${SELECT}&order=created_at.asc,id.asc&limit=${MAX_REPOS + 1}`);
  const rows = Array.isArray(data) ? data : [];
  const out: PersonalRepo[] = [];
  const seen = new Set<string>();
  for (const raw of rows) {
    const repo = toRepo(raw, userId);
    if (repo && !seen.has(repo.uid)) {
      seen.add(repo.uid);
      out.push(repo);
    }
  }
  return out.slice(0, MAX_REPOS);
}

async function save(bridge: NativeBridge, uid: string | null, fields: Record<string, unknown>): Promise<PersonalRepo> {
  const { data, userId } = await call<unknown>(bridge, 'POST', 'rpc/personal_repo_save', { p_uid: uid, p_fields: fields, p_checked: false });
  const repo = toRepo(data, userId);
  if (!repo) throw new PersonalReposError('The team server sent something unexpected.');
  return repo;
}

export interface GithubRepoInput {
  github: string;
  name?: string;
  branch?: string;
  status?: string;
  marketing_angle?: string;
}

/** Add a GitHub repo from the phone. No laptop needed: the laptop picks it up on its next sync. */
export async function addGithubRepo(
  bridge: NativeBridge,
  input: GithubRepoInput,
  rules: ProductRulesConfig | null,
  existingIds: string[],
): Promise<PersonalRepo> {
  const github = normalizeGithub(input.github);
  if (!github) throw new PersonalReposError('Use a GitHub repository: owner/repo or its github.com address. Folders are added on your laptop.');
  const name = (input.name ?? '').trim() || github.split('/')[1];
  const fields: Record<string, unknown> = { name };
  if (input.branch !== undefined && input.branch.trim()) fields.branch = input.branch;
  if (input.status !== undefined) fields.status = input.status;
  if (input.marketing_angle !== undefined && input.marketing_angle.trim()) fields.marketing_angle = input.marketing_angle;
  const clean = validateRepoFields(fields, 'github');
  const taken = new Set(existingIds.map((i) => i.toLowerCase()));
  const base = slugifyId(String(clean.name));
  let id = base;
  for (let n = 2; taken.has(id); n += 1) {
    const suffix = `-${n}`;
    id = `${base.slice(0, 63 - suffix.length)}${suffix}`;
  }
  checkConfidential([github, String(clean.name), id, clean.branch as string | null, clean.marketing_angle as string | null], rules);
  const payload: Record<string, unknown> = { id, kind: 'github', github, name: clean.name, status: clean.status ?? 'in_development' };
  if (clean.branch) payload.branch = clean.branch;
  if (clean.marketing_angle) payload.marketing_angle = clean.marketing_angle;
  return save(bridge, null, payload);
}

/** Edit one of my repos. Only changed fields are sent; a folder path becomes "pending" until the laptop checks it. */
export async function editPersonalRepo(
  bridge: NativeBridge,
  repo: PersonalRepo,
  changes: RepoFieldChanges,
  rules: ProductRulesConfig | null,
): Promise<PersonalRepo> {
  const clean = validateRepoFields(changes as Record<string, unknown>, repo.kind);
  const current = repo as unknown as Record<string, unknown>;
  const fields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(clean)) {
    if (value !== current[key]) fields[key] = value;
  }
  if (Object.keys(fields).length === 0) return repo;
  checkConfidential(
    [fields.name, fields.id, fields.marketing_angle, fields.branch, fields.local_path].map((v) => (typeof v === 'string' ? v : null)),
    rules,
  );
  return save(bridge, repo.uid, fields);
}

/** Remove one of my repos (the laptop drops it on its next sync). */
export async function removePersonalRepo(bridge: NativeBridge, uid: string): Promise<void> {
  if (typeof uid !== 'string' || !UUID_RE.test(uid)) throw new PersonalReposError('Unknown repository.');
  await call<unknown>(bridge, 'POST', 'rpc/personal_repo_delete', { p_uid: uid });
}
