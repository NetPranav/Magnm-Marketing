import { api } from '../api';
import { isDesktop, storeSecrets } from '../desktop';

/** The eleven personal NVIDIA key slots (the engine's key pool names). */
export const PERSONAL_KEY_SLOTS: readonly string[] = [
  'NVIDIA_NIM_API_KEY',
  ...Array.from({ length: 10 }, (_, i) => `NVIDIA_NIM_API_KEY${i + 1}`),
];

export interface PersonalKeyStatus {
  name: string;
  /** Last four characters, like "...ABCD". Never the key. */
  label: string | null;
  present: boolean;
  /** True when the cloud copy (shared with the phone) matches this one. */
  synced: boolean;
}

export interface KeychainOp {
  op: 'set' | 'delete';
  name: string;
  value?: string;
}

/** True for one of the personal key slot names. */
export function isPersonalKeySlot(name: string): boolean {
  return PERSONAL_KEY_SLOTS.includes(name);
}

/** The key slots this laptop knows, with labels only. */
export function getPersonalKeys(): Promise<{ keys: PersonalKeyStatus[]; keychain: boolean }> {
  return api.get<{ keys: PersonalKeyStatus[]; keychain: boolean }>('/api/account/keys');
}

/** Ask the engine to sync now (after a key was saved). Best effort: a failure never blocks the save. */
export async function requestKeySync(): Promise<void> {
  try {
    await api.post<{ ok: boolean }>('/api/account/keys/sync', {});
  } catch {
    // The next regular sync cycle picks it up.
  }
}

/** A key was removed on this laptop: tell the engine so the cloud copy (and the phone's) goes too. */
export async function forgetPersonalKey(name: string): Promise<void> {
  if (!isPersonalKeySlot(name)) return;
  try {
    await api.post<{ ok: boolean }>('/api/account/keys/forget', { name });
  } catch {
    // Remembered nowhere else: the key stays in the cloud until the user removes it again.
  }
}

/**
 * Apply what the cloud sync left for the keychain (keys added or removed on the phone). Only the desktop shell can
 * write the keychain; the engine already has the change in memory only after the shell confirms it. Returns how many
 * slots were written. A failure leaves everything pending for the next try.
 */
export async function applyPendingKeys(): Promise<number> {
  if (!isDesktop()) return 0;
  const { ops } = await api.post<{ ops: KeychainOp[] }>('/api/account/keys/pending', {});
  const list = Array.isArray(ops) ? ops.filter((o) => o && isPersonalKeySlot(o.name)) : [];
  if (list.length === 0) return 0;
  const sets = list
    .filter((o) => o.op === 'set' && typeof o.value === 'string' && o.value)
    .map((o) => ({ name: o.name, value: o.value as string }));
  const deletes = list.filter((o) => o.op === 'delete').map((o) => o.name);
  await storeSecrets(sets, deletes);
  const names = [...sets.map((s) => s.name), ...deletes];
  await api.post<{ applied: string[] }>('/api/account/keys/applied', { names });
  return names.length;
}
