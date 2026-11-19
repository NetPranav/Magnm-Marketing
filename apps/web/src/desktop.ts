export interface SecretStatus {
  name: string;
  is_set: boolean;
}

interface TauriCore {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

interface TauriGlobal {
  core?: TauriCore;
}

declare global {
  interface Window {
    __TAURI__?: TauriGlobal;
  }
}

function getInvoke(): TauriCore['invoke'] | null {
  if (typeof window === 'undefined') return null;
  const invoke = window.__TAURI__?.core?.invoke;
  return typeof invoke === 'function' ? invoke : null;
}

/** Return true when running inside the Tauri desktop shell. */
export function isDesktop(): boolean {
  return getInvoke() !== null;
}

/** Match the engine's `_is_secret_env_name` rule for secret environment variables. */
export function isSecretEnvName(name: string): boolean {
  const upper = String(name || '').trim().toUpperCase();
  return upper.includes('KEY') || upper.includes('TOKEN') || upper.includes('SECRET');
}

/** List configured status (never values) for known keys in the desktop keychain. */
export async function listSecrets(): Promise<SecretStatus[]> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  const result = await invoke('list_secrets');
  return Array.isArray(result) ? (result as SecretStatus[]) : [];
}

/** Store or update an allow-listed key in the desktop OS keychain (restarts the engine). */
export async function setSecret(name: string, value: string): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  await invoke('set_secret', { name, value });
}

/** Remove an allow-listed key from the desktop OS keychain (restarts the engine). */
export async function deleteSecret(name: string): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  await invoke('delete_secret', { name });
}
