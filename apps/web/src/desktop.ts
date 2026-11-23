export interface SecretStatus {
  name: string;
  is_set: boolean;
}

interface TauriCore {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

interface TauriEvent {
  listen?: (
    event: string,
    handler: (event: { payload?: unknown }) => void,
  ) => Promise<() => void>;
}

interface TauriGlobal {
  core?: TauriCore;
  event?: TauriEvent;
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

/** Store or update an allow-listed key in the desktop OS keychain (restarts the engine in place). */
export async function setSecret(name: string, value: string): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  await invoke('set_secret', { name, value });
}

/** Remove an allow-listed key from the desktop OS keychain (restarts the engine in place). */
export async function deleteSecret(name: string): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  await invoke('delete_secret', { name });
}

/**
 * Write several allow-listed keys to (and remove several from) the OS keychain without restarting the engine. Used when
 * the cloud sync brings keys added or removed on the phone; the engine updates itself once this succeeds.
 */
export async function storeSecrets(
  sets: Array<{ name: string; value: string }>,
  deletes: string[],
): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Desktop keychain is unavailable outside Magnm Studio.');
  }
  await invoke('store_secrets', { sets, deletes });
}

/** Restart the local engine on the same port and token; the page stays where it is. */
export async function restartEngine(): Promise<void> {
  const invoke = getInvoke();
  if (!invoke) {
    throw new Error('Restarting the engine is only possible inside Magnm Studio.');
  }
  await invoke('restart_engine');
}

export type DesktopNotificationPermission = 'granted' | 'denied' | 'default';

/** Check desktop notification permission via tauri-plugin-notification. */
export async function getDesktopNotificationPermission(): Promise<DesktopNotificationPermission> {
  const invoke = getInvoke();
  if (!invoke) {
    return 'default';
  }
  const granted = await invoke('plugin:notification|is_permission_granted');
  if (granted === true) return 'granted';
  if (granted === false) return 'denied';
  return 'default';
}

/** Request desktop notification permission via tauri-plugin-notification. */
export async function requestDesktopNotificationPermission(): Promise<DesktopNotificationPermission> {
  const invoke = getInvoke();
  if (!invoke) {
    return 'default';
  }
  const res = await invoke('plugin:notification|request_permission');
  if (res === 'granted') return 'granted';
  if (res === 'denied') return 'denied';
  return 'default';
}

/** Show a system notification through tauri-plugin-notification. */
export async function sendDesktopNotification(options: {
  title: string;
  body?: string;
  route?: string;
}): Promise<boolean> {
  const invoke = getInvoke();
  if (!invoke) {
    return false;
  }
  await invoke('plugin:notification|notify', {
    options: {
      title: options.title,
      ...(options.body ? { body: options.body } : {}),
      extra: options.route ? { route: options.route } : {},
    },
  });
  return true;
}

/** Subscribe to notification tap events emitted by the Tauri shell to navigate to a route. */
export function listenDesktopNotificationClick(handler: (route: string) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const listen = window.__TAURI__?.event?.listen;
  if (typeof listen !== 'function') return () => {};
  let disposed = false;
  let unlisten: (() => void) | null = null;
  void listen('magnm:notification-click', (ev) => {
    if (typeof ev?.payload === 'string' && ev.payload.trim()) {
      handler(ev.payload);
    }
  }).then((fn) => {
    if (disposed) {
      fn();
    } else {
      unlisten = fn;
    }
  });
  return () => {
    disposed = true;
    if (unlisten) {
      unlisten();
      unlisten = null;
    }
  };
}
