/** Desktop-only bridge to the Tauri updater commands (Settings > Updates and the "Update available" badge). */

export type InstallKind = 'pacman' | 'standard';

export interface UpdateInfo {
  version: string;
  notes: string;
  date: string | null;
}

export interface UpdateStatus {
  current_version: string;
  install_kind: InstallKind;
  /** False while this build has no real update key. */
  configured: boolean;
  available: UpdateInfo | null;
  /** Unix seconds of the last successful check, if any. */
  last_checked: number | null;
}

export interface UpdateProgress {
  phase: string;
  downloaded: number;
  total: number | null;
}

export interface InstallOutcome {
  version: string;
  restart_needed: boolean;
}

interface TauriLike {
  core?: { invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  event?: {
    listen?: (
      event: string,
      handler: (event: { payload?: unknown }) => void,
    ) => Promise<() => void>;
  };
}

function tauri(): TauriLike | null {
  if (typeof window === 'undefined') return null;
  return (window as unknown as { __TAURI__?: TauriLike }).__TAURI__ ?? null;
}

/** True when the Tauri updater commands can be called (the desktop app, not a plain browser). */
export function updatesAvailable(): boolean {
  return typeof tauri()?.core?.invoke === 'function';
}

/** The text of whatever a command rejected with, as a sentence the person can read. */
export function updateErrorText(err: unknown): string {
  if (typeof err === 'string' && err.trim()) return err.trim();
  if (err instanceof Error && err.message.trim()) return err.message.trim();
  return 'Something went wrong with the update. Nothing was changed.';
}

async function call<T>(cmd: string): Promise<T> {
  const invoke = tauri()?.core?.invoke;
  if (typeof invoke !== 'function') {
    throw new Error('Updates are only available inside the Magnm Studio desktop app.');
  }
  return (await invoke(cmd)) as T;
}

/** What the screen needs to draw itself. No network. Resolves with null if the shell answers with something else. */
export async function getUpdateStatus(): Promise<UpdateStatus | null> {
  const status = await call<unknown>('update_status');
  if (status === null || typeof status !== 'object' || Array.isArray(status)) return null;
  const candidate = status as Partial<UpdateStatus>;
  if (typeof candidate.current_version !== 'string') return null;
  return {
    current_version: candidate.current_version,
    install_kind: candidate.install_kind === 'pacman' ? 'pacman' : 'standard',
    configured: candidate.configured !== false,
    available: candidate.available && typeof candidate.available.version === 'string' ? candidate.available : null,
    last_checked: typeof candidate.last_checked === 'number' ? candidate.last_checked : null,
  };
}

/** Ask GitHub now. Resolves with the update found, or null when this is the newest version. */
export function checkForUpdate(): Promise<UpdateInfo | null> {
  return call<UpdateInfo | null>('check_update');
}

/** Download and install the update found by the last check. Never restarts by itself. */
export function installUpdate(): Promise<InstallOutcome> {
  return call<InstallOutcome>('install_update');
}

/** Stop the engine and start the new version. */
export async function restartApp(): Promise<void> {
  await call<void>('restart_app');
}

function listenTo<T>(event: string, handler: (payload: T) => void): () => void {
  const listen = tauri()?.event?.listen;
  if (typeof listen !== 'function') return () => {};
  let disposed = false;
  let unlisten: (() => void) | null = null;
  void listen(event, (ev) => {
    if (ev?.payload !== undefined && ev.payload !== null) handler(ev.payload as T);
  })
    .then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    })
    .catch(() => {});
  return () => {
    disposed = true;
    if (unlisten) {
      unlisten();
      unlisten = null;
    }
  };
}

/** Download progress events from the updater. */
export function listenUpdateProgress(handler: (progress: UpdateProgress) => void): () => void {
  return listenTo<UpdateProgress>('magnm:update-progress', handler);
}

/** Fired when a check (quiet or by click) finds a new version. */
export function listenUpdateAvailable(handler: (info: UpdateInfo) => void): () => void {
  return listenTo<UpdateInfo>('magnm:update-available', handler);
}
