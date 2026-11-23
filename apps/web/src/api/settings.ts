import { ApiError, api } from '../api';
import { isDesktop, isSecretEnvName, setSecret } from '../desktop';
import { isPersonalKeySlot, requestKeySync } from './keys';
import type {
  BakeoffResult,
  EngineStatusInfo,
  HealthInfo,
  JobRecord,
  KeyPoolEntry,
  SetupCheckResult,
} from '../types';

export const KEYCHAIN_EXTERNAL_NOTE =
  "Keys are stored in the desktop app's keychain. Open Magnm Studio to change them.";

export interface SaveEnvResponse {
  success: boolean;
  saved_keys?: string[];
  error?: string;
  error_code?: string;
}

export interface TestAiResult {
  success: boolean;
  role_id: string;
  provider?: string;
  primary?: string;
  backup?: string | null;
  warnings?: string[];
  model_requested?: string | null;
  model_used?: string | null;
  fallback_used?: boolean;
  fallback_reason?: string | null;
  latency_ms?: number;
  error?: string;
  error_code?: string;
}

export interface ImportLegacyResult {
  success: boolean;
  files_copied?: number;
  db?: Record<string, number>;
  error?: string;
}

/** Fetch engine health info including version and data_root path. */
export function getHealth(): Promise<HealthInfo> {
  return api.get<HealthInfo>('/api/health');
}

/**
 * Wait until /api/health answers again after the engine was restarted. Resolves true when it answers,
 * false if it never does within the timeout.
 */
export async function waitForEngine(options: { intervalMs?: number; timeoutMs?: number } = {}): Promise<boolean> {
  const intervalMs = options.intervalMs ?? 300;
  const deadline = Date.now() + (options.timeoutMs ?? 60_000);
  for (;;) {
    try {
      const health = await getHealth();
      if (health?.ok !== false) return true;
    } catch {
      // The engine is still starting.
    }
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/** Fetch gateway, role model matrix, and safe provider status. */
export function getStatus(): Promise<EngineStatusInfo> {
  return api.get<EngineStatusInfo>('/api/status');
}

/** Split env updates into non-secret settings and secret key/token entries. */
export function splitEnvUpdates(updates: Record<string, string>): {
  nonSecrets: Record<string, string>;
  secrets: Record<string, string>;
} {
  const nonSecrets: Record<string, string> = {};
  const secrets: Record<string, string> = {};
  for (const [key, value] of Object.entries(updates)) {
    if (isSecretEnvName(key)) {
      secrets[key] = value;
    } else {
      nonSecrets[key] = value;
    }
  }
  return { nonSecrets, secrets };
}

/**
 * Persist non-secret settings via /api/save_env_config and, inside the desktop app,
 * route secret names through the Tauri keychain instead of the engine.
 */
export async function saveEnvConfig(updates: Record<string, string>): Promise<SaveEnvResponse> {
  if (!isDesktop()) {
    return api.post<SaveEnvResponse>('/api/save_env_config', { updates });
  }

  const { nonSecrets, secrets } = splitEnvUpdates(updates);
  const nonSecretEntries = Object.entries(nonSecrets);
  const secretEntries = Object.entries(secrets);

  if (nonSecretEntries.length > 0) {
    const res = await api.post<SaveEnvResponse>('/api/save_env_config', { updates: nonSecrets });
    if (!res.success) {
      return res;
    }
  }

  for (const [name, value] of secretEntries) {
    await setSecret(name, value);
  }
  if (secretEntries.some(([name]) => isPersonalKeySlot(name))) {
    // The engine restarted in place; tell it to share the new key with the phone now.
    await requestKeySync();
  }

  return {
    success: true,
    saved_keys: Object.keys(updates),
  };
}

/** Run a live endpoint diagnostic for a single agent role. */
export function testRoleModel(roleId: string): Promise<TestAiResult> {
  return api.post<TestAiResult>('/api/test_ai', { role_id: roleId });
}

/** Fetch safe NVIDIA NIM key pool health metadata (never renders masked values). */
export function getKeyPool(): Promise<KeyPoolEntry[]> {
  return api.get<KeyPoolEntry[]>('/api/key_pool');
}

/** Run environment and dependency diagnostics. */
export function runSetupCheck(): Promise<SetupCheckResult> {
  return api.get<SetupCheckResult>('/api/setup_check');
}

/** Queue Playwright Chromium installation job. */
export function installBrowser(): Promise<{ success: boolean; job: JobRecord; message?: string }> {
  return api.post<{ success: boolean; job: JobRecord; message?: string }>(
    '/api/setup/install_browser',
    {},
  );
}

/** List recent engine background jobs (used to track install_browser progress). */
export function listJobs(limit = 10): Promise<JobRecord[]> {
  return api.get<JobRecord[]>(`/api/jobs?limit=${limit}`);
}

/** Import data and SQLite state from an existing Magnm-Marketing folder. */
export function importLegacyFolder(path: string): Promise<ImportLegacyResult> {
  return api.post<ImportLegacyResult>('/api/import_legacy', { path });
}

/** Fetch the latest bake-off results, returning null when none exist (HTTP 404). */
export async function getLatestBakeoff(): Promise<BakeoffResult | null> {
  try {
    return await api.get<BakeoffResult>('/api/bakeoff/latest?reveal=1');
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}
