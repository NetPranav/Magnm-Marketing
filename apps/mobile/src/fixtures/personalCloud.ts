// A fake of the cloud side of personal keys and repos for phone tests. It follows the SQL functions and row-level
// security: the signed-in user is read from the bearer token ("jwt-<user id>"). Every key here is a fake string.
import type { NativeHttpRequest, NativeHttpResponse } from '../native';

const SLOT_RE = /^NVIDIA_NIM_API_KEY(?:[1-9]|10)?$/;
const KEY_RE = /^nvapi-[A-Za-z0-9_-]{20,200}$/;
const FIELDS = ['id', 'name', 'kind', 'github', 'local_path', 'branch', 'status', 'marketing_angle', 'confidential'];

export async function fp(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export interface CloudRepoRow {
  uid: string;
  owner_id: string;
  id: string;
  name: string;
  kind: string;
  github: string | null;
  local_path: string | null;
  path_status: string;
  path_reason: string | null;
  branch: string | null;
  status: string;
  marketing_angle: string | null;
  confidential: boolean;
  rev: number;
  field_rev: Record<string, number>;
  field_at: Record<string, string>;
  created_at: string;
}

export interface SeenCall {
  method: string;
  url: string;
  body: unknown;
}

export function createPersonalCloud() {
  const state = {
    vault: new Map<string, { key: string; version: number }>(),
    repos: new Map<string, CloudRepoRow>(),
    seen: [] as SeenCall[],
    down: false,
    counter: 0,
    extraRows: [] as unknown[],
  };
  const respond = (status: number, body: unknown): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });
  const fail = (message: string, status = 400) => respond(status, { message });
  const vkey = (owner: string, slot: string) => `${owner}|${slot}`;

  const api = {
    state,
    putKey(owner: string, slot: string, key: string) {
      const cur = state.vault.get(vkey(owner, slot));
      state.vault.set(vkey(owner, slot), { key, version: (cur?.version ?? 0) + 1 });
    },
    dropKey(owner: string, slot: string) {
      state.vault.delete(vkey(owner, slot));
    },
    keysOf(owner: string): Record<string, string> {
      const out: Record<string, string> = {};
      for (const [k, v] of state.vault) if (k.startsWith(`${owner}|`)) out[k.split('|')[1]] = v.key;
      return out;
    },
    rowsOf(owner: string): CloudRepoRow[] {
      return [...state.repos.values()].filter((r) => r.owner_id === owner);
    },
    addRow(owner: string, fields: Partial<CloudRepoRow>): CloudRepoRow {
      const now = new Date().toISOString();
      state.counter += 1;
      const row: CloudRepoRow = {
        uid: `00000000-0000-4000-8000-${String(state.counter).padStart(12, '0')}`,
        owner_id: owner, id: 'x', name: 'X', kind: 'github', github: null, local_path: null, path_status: 'ok',
        path_reason: null, branch: null, status: 'in_development', marketing_angle: null, confidential: false,
        rev: 1, field_rev: {}, field_at: {}, created_at: now, ...fields,
      };
      state.repos.set(row.uid, row);
      return row;
    },
    async handler(req: NativeHttpRequest): Promise<NativeHttpResponse | undefined> {
      const url = new URL(req.url);
      if (!url.pathname.startsWith('/rest/v1/')) return undefined;
      const body = req.body ? JSON.parse(req.body) : null;
      state.seen.push({ method: req.method, url: req.url, body });
      if (state.down) return fail('down', 503);
      const auth = (req.headers?.Authorization ?? req.headers?.authorization ?? '').replace('Bearer ', '');
      const owner = auth.startsWith('jwt-') ? auth.slice(4) : '';
      if (!owner) return fail('not signed in', 401);
      const path = url.pathname.slice('/rest/v1/'.length);
      if (path === 'personal_repos' && req.method === 'GET') {
        const limit = Number(url.searchParams.get('limit') ?? 1000);
        return respond(200, [...api.rowsOf(owner).slice(0, limit), ...state.extraRows]);
      }
      if (!path.startsWith('rpc/')) return undefined;
      const name = path.slice(4);
      if (name === 'personal_key_list') {
        const out = [];
        for (const [slot, v] of Object.entries(api.keysOf(owner))) {
          out.push({ name: slot, last4: v.slice(-4), fingerprint: await fp(v), version: state.vault.get(vkey(owner, slot))!.version });
        }
        return respond(200, out);
      }
      if (name === 'personal_key_get') {
        const names = body?.p_names;
        if (!Array.isArray(names) || names.length > 11) return fail('Too many key slots requested');
        const mine = api.keysOf(owner);
        return respond(200, names.filter((n: string) => n in mine).map((n: string) => ({ name: n, api_key: mine[n] })));
      }
      if (name === 'personal_key_set') {
        const { p_name: slot, p_key: key } = body ?? {};
        if (typeof slot !== 'string' || !SLOT_RE.test(slot)) return fail('Unknown key slot');
        if (typeof key !== 'string' || !KEY_RE.test(key)) return fail('Invalid NVIDIA NIM API key format');
        for (const [s, v] of Object.entries(api.keysOf(owner))) if (s !== slot && v === key) return fail('That key is already saved in another slot');
        api.putKey(owner, slot, key);
        return respond(200, [{ name: slot, last4: key.slice(-4), fingerprint: await fp(key), version: 1 }]);
      }
      if (name === 'personal_key_delete') {
        const slot = body?.p_name;
        if (typeof slot !== 'string' || !SLOT_RE.test(slot)) return fail('Unknown key slot');
        const had = state.vault.delete(vkey(owner, slot));
        return respond(200, had);
      }
      if (name === 'personal_repo_save') {
        const fields = body?.p_fields;
        if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return fail('Repository fields must be an object');
        for (const k of Object.keys(fields)) if (!FIELDS.includes(k)) return fail('Unknown repository field');
        const mine = api.rowsOf(owner);
        const now = new Date().toISOString();
        if (body.p_uid == null) {
          if (mine.length >= 100) return fail('At most 100 repositories are allowed');
          if (mine.some((r) => r.id === fields.id)) return fail('duplicate key', 409);
          if (fields.github && mine.some((r) => (r.github ?? '').toLowerCase() === String(fields.github).toLowerCase())) return fail('duplicate key', 409);
          const row = api.addRow(owner, {
            id: fields.id, name: fields.name, kind: fields.kind, github: fields.github ?? null, local_path: fields.local_path ?? null,
            branch: fields.branch ?? null, status: fields.status ?? 'in_development', marketing_angle: fields.marketing_angle ?? null,
            confidential: Boolean(fields.confidential), path_status: fields.local_path && !body.p_checked ? 'pending' : 'ok',
            field_rev: Object.fromEntries(FIELDS.map((f) => [f, 1])), field_at: Object.fromEntries(FIELDS.map((f) => [f, now])),
          });
          return respond(200, row);
        }
        const row = state.repos.get(body.p_uid);
        if (!row || row.owner_id !== owner) return fail('Repository not found');
        const rec = row as unknown as Record<string, unknown>;
        const changed = Object.keys(fields).filter((f) => fields[f] !== rec[f]);
        if (changed.length === 0) return respond(200, row);
        if (changed.includes('id') && mine.some((r) => r.uid !== row.uid && r.id === fields.id)) return fail('duplicate key', 409);
        row.rev += 1;
        for (const f of changed) {
          rec[f] = fields[f];
          row.field_rev[f] = row.rev;
          row.field_at[f] = now;
          if (f === 'local_path') {
            row.path_status = body.p_checked ? 'ok' : 'pending';
            row.path_reason = null;
          }
        }
        return respond(200, row);
      }
      if (name === 'personal_repo_delete') {
        const row = state.repos.get(body?.p_uid);
        if (row && row.owner_id === owner) {
          state.repos.delete(body.p_uid);
          return respond(200, true);
        }
        return respond(200, false);
      }
      return fail('unknown function', 404);
    },
  };
  return api;
}
