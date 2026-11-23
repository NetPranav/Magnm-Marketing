// Personal NVIDIA NIM keys, kept the same on this phone and the owner's laptop through the cloud (Supabase Vault,
// owner only). Same decision as the laptop (engine/personal_keys.py): per key slot, compare what is here, what the
// cloud has and what both agreed on last time, by SHA-256 fingerprint, never by value.
//
// The keys live only in the phone's secure store. The sync memory (slot names and fingerprints) is in the secure store
// too and never holds a key. Nothing here writes a key to web storage, logs or error text.

import { KEYS_MAP_SECRET_KEY, KEYS_SYNC_SECRET_KEY, NIM_KEY_SECRET_KEY, getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import { HttpStatusError, getPublicSupabaseConfig, netJson } from './net';

export { KEYS_MAP_SECRET_KEY, KEYS_SYNC_SECRET_KEY };
export const KEY_SLOTS: readonly string[] = ['NVIDIA_NIM_API_KEY', ...Array.from({ length: 10 }, (_, i) => `NVIDIA_NIM_API_KEY${i + 1}`)];

const KEY_RE = /^nvapi-[A-Za-z0-9_-]{20,200}$/;
// What this phone accepts and keeps. Only a key of the full shape is shared with the laptop (the cloud checks that).
const LOCAL_KEY_RE = /^nvapi-[A-Za-z0-9_-]{8,200}$/;
const FP_RE = /^[0-9a-f]{64}$/;

export class PersonalKeysError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PersonalKeysError';
  }
}

export function isKeySlot(name: unknown): name is string {
  return typeof name === 'string' && KEY_SLOTS.includes(name);
}

/** A key the cloud accepts, so it can be shared with the laptop. */
export function isValidKey(value: unknown): value is string {
  return typeof value === 'string' && KEY_RE.test(value);
}

/** A key this phone keeps. One of unusual length stays on this phone only. */
export function isLocalKey(value: unknown): value is string {
  return typeof value === 'string' && LOCAL_KEY_RE.test(value);
}

export async function keyFingerprint(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

type FpMap = Record<string, string>;

export interface KeyPlan {
  upload: string[];
  download: string[];
  deleteLocal: string[];
  /** A local key that must move to a free slot so the cloud's different key can take its slot. */
  copyLocal: Array<[string, string]>;
  keep: FpMap;
}

/** Decide what to do per slot. All three maps are slot -> fingerprint. Pure. Mirrors engine/personal_keys.reconcile. */
export function reconcileKeys(local: FpMap, cloud: FpMap, synced: FpMap): KeyPlan {
  const plan: KeyPlan = { upload: [], download: [], deleteLocal: [], copyLocal: [], keep: {} };
  const taken = new Set([...Object.keys(local), ...Object.keys(cloud)]);
  const cloudFps = new Set(Object.values(cloud));
  for (const name of KEY_SLOTS) {
    const lf = local[name];
    const cf = cloud[name];
    const mf = synced[name];
    if (lf && cf) {
      if (lf === cf) plan.keep[name] = lf;
      else if (mf === cf) plan.upload.push(name);
      else if (mf === lf) plan.download.push(name);
      else {
        const free = KEY_SLOTS.find((s) => !taken.has(s));
        if (free === undefined || cloudFps.has(lf)) {
          if (cloudFps.has(lf)) plan.download.push(name);
          continue;
        }
        taken.add(free);
        plan.download.push(name);
        plan.copyLocal.push([name, free]);
        plan.upload.push(free);
      }
    } else if (lf) {
      if (cloudFps.has(lf)) continue;
      if (mf === lf) plan.deleteLocal.push(name);
      else plan.upload.push(name);
    } else if (cf) {
      plan.download.push(name);
    }
  }
  return plan;
}

// ------------------------------------------------------------------------------------------------------ storage

interface SyncState {
  user_id: string | null;
  synced: FpMap;
  forget: string[];
  foreign: FpMap;
}

function cleanFpMap(raw: unknown): FpMap {
  const out: FpMap = {};
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (isKeySlot(k) && typeof v === 'string' && FP_RE.test(v)) out[k] = v;
    }
  }
  return out;
}

async function loadState(bridge: NativeBridge, userId: string | null): Promise<SyncState> {
  let data: Record<string, unknown> | null = null;
  try {
    const raw = await bridge.secureStore.get(KEYS_SYNC_SECRET_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    data = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    data = null;
  }
  if (!data) return { user_id: userId, synced: {}, forget: [], foreign: {} };
  const state: SyncState = {
    user_id: typeof data.user_id === 'string' ? data.user_id : null,
    synced: cleanFpMap(data.synced),
    forget: Array.isArray(data.forget) ? data.forget.filter(isKeySlot) : [],
    foreign: cleanFpMap(data.foreign),
  };
  if (state.user_id === null || state.user_id === userId) {
    state.user_id = userId;
    return state;
  }
  return { user_id: userId, synced: {}, forget: [], foreign: { ...state.foreign, ...state.synced } };
}

async function saveState(bridge: NativeBridge, state: SyncState): Promise<void> {
  await bridge.secureStore.set(
    KEYS_SYNC_SECRET_KEY,
    JSON.stringify({ user_id: state.user_id, synced: state.synced, forget: [...new Set(state.forget)].sort(), foreign: state.foreign }),
  );
}

async function loadMap(bridge: NativeBridge): Promise<Record<string, string>> {
  let raw: string | null = null;
  try {
    raw = await bridge.secureStore.get(KEYS_MAP_SECRET_KEY);
  } catch {
    raw = null;
  }
  const map: Record<string, string> = {};
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        for (const [slot, value] of Object.entries(parsed as Record<string, unknown>)) {
          if (isKeySlot(slot) && isLocalKey(value)) map[slot] = value;
        }
      }
    } catch {
      // Unreadable: treated as empty.
    }
    return map;
  }
  // A key saved by an older version of the app lives under one name only: it becomes the first slot.
  const legacy = (await bridge.secureStore.get(NIM_KEY_SECRET_KEY))?.trim();
  if (isLocalKey(legacy)) map[KEY_SLOTS[0]] = legacy;
  return map;
}

/** Save the keys and keep the single key Phone Lite uses (the first slot that has one) in step. */
async function saveMap(bridge: NativeBridge, map: Record<string, string>): Promise<void> {
  const first = KEY_SLOTS.find((s) => map[s]);
  if (!first) {
    await bridge.secureStore.remove(KEYS_MAP_SECRET_KEY);
    await bridge.secureStore.remove(NIM_KEY_SECRET_KEY);
    return;
  }
  await bridge.secureStore.set(KEYS_MAP_SECRET_KEY, JSON.stringify(map));
  await bridge.secureStore.set(NIM_KEY_SECRET_KEY, map[first]);
}

// ---------------------------------------------------------------------------------------------------------- cloud

class CloudFailure extends Error {
  constructor(readonly status: number) {
    super('The cloud call failed.');
  }
}

async function rpc<T>(bridge: NativeBridge, name: string, body: unknown): Promise<T> {
  const token = await getValidAccessToken(bridge);
  if (!token) throw new CloudFailure(401);
  const cfg = getPublicSupabaseConfig();
  try {
    return await netJson<T>(
      { destination: 'supabase', method: 'POST', url: `${cfg.url}/rest/v1/rpc/${name}`, body },
      { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token },
      bridge,
    );
  } catch (err) {
    // Only the status leaves this function: a server answer could repeat what was sent.
    throw new CloudFailure(err instanceof HttpStatusError ? err.status : 0);
  }
}

async function cloudList(bridge: NativeBridge): Promise<FpMap> {
  const rows = await rpc<unknown>(bridge, 'personal_key_list', {});
  const out: FpMap = {};
  if (!Array.isArray(rows)) throw new CloudFailure(502);
  for (const row of rows) {
    if (row && typeof row === 'object') {
      const r = row as Record<string, unknown>;
      if (isKeySlot(r.name) && typeof r.fingerprint === 'string' && FP_RE.test(r.fingerprint)) out[r.name] = r.fingerprint;
    }
  }
  return out;
}

async function cloudGet(bridge: NativeBridge, names: string[], cloud: FpMap): Promise<Record<string, string>> {
  const rows = await rpc<unknown>(bridge, 'personal_key_get', { p_names: names });
  const out: Record<string, string> = {};
  if (!Array.isArray(rows)) return out;
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;
    if (typeof r.name === 'string' && names.includes(r.name) && isValidKey(r.api_key) && (await keyFingerprint(r.api_key)) === cloud[r.name]) {
      out[r.name] = r.api_key;
    }
  }
  return out;
}

// ----------------------------------------------------------------------------------------------------------- sync

export interface KeySyncSummary {
  uploaded: string[];
  downloaded: string[];
  removedLocal: string[];
  removedCloud: string[];
}

let chain: Promise<unknown> = Promise.resolve();

function serialized<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch(() => undefined);
  return run;
}

async function syncNow(bridge: NativeBridge): Promise<KeySyncSummary | null> {
  const session = await getStoredSession(bridge);
  if (!session?.user_id) return null;
  const userId = session.user_id;
  const summary: KeySyncSummary = { uploaded: [], downloaded: [], removedLocal: [], removedCloud: [] };
  const state = await loadState(bridge, userId);
  let cloud: FpMap;
  try {
    cloud = await cloudList(bridge);
  } catch {
    return null;
  }
  const map = await loadMap(bridge);
  try {
    for (const name of [...state.forget]) {
      if (name in cloud) {
        await rpc<unknown>(bridge, 'personal_key_delete', { p_name: name });
        summary.removedCloud.push(name);
        delete cloud[name];
      }
      delete state.synced[name];
      state.forget = state.forget.filter((n) => n !== name);
    }
    await saveState(bridge, state);

    const values: Record<string, string> = {};
    for (const [name, value] of Object.entries(map)) if (isValidKey(value)) values[name] = value;
    // Keys that belong to a previous account on this phone are neither uploaded nor treated as removed.
    for (const name of Object.keys(state.foreign)) {
      if (values[name] && (await keyFingerprint(values[name])) === state.foreign[name]) delete values[name];
      else delete state.foreign[name];
    }
    const local: FpMap = {};
    for (const [name, value] of Object.entries(values)) local[name] = await keyFingerprint(value);

    const plan = reconcileKeys(local, cloud, state.synced);
    const synced: FpMap = { ...state.synced, ...plan.keep };
    for (const [src, dst] of plan.copyLocal) {
      map[dst] = values[src];
      values[dst] = values[src];
      local[dst] = local[src];
      synced[dst] = local[dst];
    }
    for (const name of plan.upload) {
      try {
        await rpc<unknown>(bridge, 'personal_key_set', { p_name: name, p_key: values[name] });
      } catch (err) {
        if (err instanceof CloudFailure && err.status >= 400 && err.status < 500 && err.status !== 401) continue;
        throw err;
      }
      synced[name] = local[name];
      summary.uploaded.push(name);
    }
    if (plan.download.length > 0) {
      const fetched = await cloudGet(bridge, plan.download, cloud);
      for (const name of plan.download) {
        if (fetched[name]) {
          map[name] = fetched[name];
          synced[name] = cloud[name];
          summary.downloaded.push(name);
        }
      }
    }
    for (const name of plan.deleteLocal) {
      delete map[name];
      delete synced[name];
      summary.removedLocal.push(name);
    }
    for (const name of Object.keys(synced)) if (!(name in local) && !(name in cloud) && !map[name]) delete synced[name];
    for (const name of plan.deleteLocal) delete values[name];
    state.synced = synced;
    state.user_id = userId;
    await saveMap(bridge, map);
    await saveState(bridge, state);
    return summary;
  } catch {
    await saveState(bridge, state);
    return null;
  }
}

/** One key sync for the signed-in account. Null when signed out or the cloud could not be reached (try again later). */
export function syncPersonalKeys(bridge: NativeBridge = getNativeBridge()): Promise<KeySyncSummary | null> {
  return serialized(() => syncNow(bridge));
}

export interface PersonalKeyRow {
  name: string;
  label: string;
  synced: boolean;
  /** False for a key of unusual length: it works on this phone but cannot be shared with the laptop. */
  shareable: boolean;
}

/** The keys on this phone, labelled by their last four characters. Never a key. */
export async function listPersonalKeys(bridge: NativeBridge = getNativeBridge()): Promise<PersonalKeyRow[]> {
  const map = await loadMap(bridge);
  const session = await getStoredSession(bridge);
  const state = await loadState(bridge, session?.user_id ?? null);
  const rows: PersonalKeyRow[] = [];
  for (const slot of KEY_SLOTS) {
    const value = map[slot];
    if (!value) continue;
    rows.push({
      name: slot,
      label: `...${value.slice(-4)}`,
      synced: state.synced[slot] === (await keyFingerprint(value)),
      shareable: isValidKey(value),
    });
  }
  return rows;
}

/** Add a key on this phone (the first free slot) and share it with the laptop. Works offline; syncs when it can. */
export async function addPersonalKey(
  bridge: NativeBridge,
  rawKey: string,
): Promise<{ slot: string; uploaded: boolean }> {
  if (!isLocalKey(rawKey)) throw new PersonalKeysError('That does not look like an NVIDIA key (nvapi-...).');
  return serialized(async () => {
    const map = await loadMap(bridge);
    const existing = KEY_SLOTS.find((s) => map[s] === rawKey);
    const slot = existing ?? KEY_SLOTS.find((s) => !map[s]);
    if (!slot) throw new PersonalKeysError('You can keep up to 11 keys. Remove one first.');
    map[slot] = rawKey;
    await saveMap(bridge, map);
    await syncNow(bridge);
    const session = await getStoredSession(bridge);
    const state = await loadState(bridge, session?.user_id ?? null);
    return { slot, uploaded: state.synced[slot] === (await keyFingerprint(rawKey)) };
  });
}

/** Remove a key on this phone and, through the cloud, on the laptop. Remembered until the cloud was reached. */
export async function removePersonalKey(
  bridge: NativeBridge,
  slot: string,
): Promise<{ removedInCloud: boolean }> {
  if (!isKeySlot(slot)) throw new PersonalKeysError('Unknown key.');
  return serialized(async () => {
    const map = await loadMap(bridge);
    delete map[slot];
    await saveMap(bridge, map);
    const session = await getStoredSession(bridge);
    const state = await loadState(bridge, session?.user_id ?? null);
    delete state.synced[slot];
    if (session?.user_id && !state.forget.includes(slot)) state.forget.push(slot);
    await saveState(bridge, state);
    if (!session?.user_id) return { removedInCloud: false };
    await syncNow(bridge);
    const after = await loadState(bridge, session.user_id);
    return { removedInCloud: !after.forget.includes(slot) };
  });
}
