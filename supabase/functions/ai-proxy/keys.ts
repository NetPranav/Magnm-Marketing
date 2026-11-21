export interface KeyRecord {
  label: string;
  api_key: string;
  cooldown_until: string | null;
}

export interface CachedKey {
  label: string;
  apiKey: string;
  cooldownUntilMs: number | null;
}

export interface UpstreamErrorAction {
  retryNextKey: boolean;
  cooldownSeconds: number;
  reportStatus: number | null;
  reportError: string | null;
}

export const KEY_CACHE_TTL_MS = 60_000;
export const MAX_KEY_ATTEMPTS = 3;

export function parseRetryAfterSeconds(
  headerValue: string | null,
  defaultSeconds = 60,
  nowMs = Date.now(),
): number {
  if (!headerValue) return defaultSeconds;
  const trimmed = headerValue.trim();
  if (!trimmed) return defaultSeconds;
  if (/^\d+(\.\d+)?$/.test(trimmed)) {
    const secs = Number(trimmed);
    if (Number.isFinite(secs) && secs >= 0) {
      return Math.max(1, Math.ceil(secs));
    }
  }
  const dateMs = Date.parse(trimmed);
  if (Number.isFinite(dateMs)) {
    const diff = Math.ceil((dateMs - nowMs) / 1000);
    return diff > 0 ? diff : 1;
  }
  return defaultSeconds;
}

export function classifyUpstreamError(
  status: number,
  retryAfterHeader: string | null,
  errorText: string,
  nowMs = Date.now(),
): UpstreamErrorAction {
  if (status === 401 || status === 403) {
    return {
      retryNextKey: true,
      cooldownSeconds: 3600,
      reportStatus: status,
      reportError: "auth failed",
    };
  }
  if (status === 429) {
    return {
      retryNextKey: true,
      cooldownSeconds: parseRetryAfterSeconds(retryAfterHeader, 60, nowMs),
      reportStatus: 429,
      reportError: "rate limited",
    };
  }
  if (status >= 500) {
    const cleaned = errorText.trim().slice(0, 300) || `HTTP ${status}`;
    return {
      retryNextKey: true,
      cooldownSeconds: 30,
      reportStatus: status,
      reportError: cleaned,
    };
  }
  return {
    retryNextKey: false,
    cooldownSeconds: 0,
    reportStatus: null,
    reportError: null,
  };
}

export class KeyPool {
  private loader: () => Promise<KeyRecord[]>;
  private ttlMs: number;
  private nowFn: () => number;
  private cache: CachedKey[] = [];
  private loadedAtMs = -Infinity;
  private rotatingIndex = 0;

  constructor(
    loader: () => Promise<KeyRecord[]>,
    opts: { ttlMs?: number; nowFn?: () => number } = {},
  ) {
    this.loader = loader;
    this.ttlMs = opts.ttlMs ?? KEY_CACHE_TTL_MS;
    this.nowFn = opts.nowFn ?? (() => Date.now());
  }

  async getKeys(forceRefresh = false): Promise<CachedKey[]> {
    const now = this.nowFn();
    if (!forceRefresh && now - this.loadedAtMs < this.ttlMs) {
      return this.cache;
    }

    const rows = await this.loader();
    this.cache = rows.map((row) => {
      const parsedMs = row.cooldown_until ? Date.parse(row.cooldown_until) : NaN;
      return {
        label: row.label,
        apiKey: row.api_key,
        cooldownUntilMs: Number.isFinite(parsedMs) ? parsedMs : null,
      };
    });
    this.loadedAtMs = now;
    if (this.cache.length > 0) {
      this.rotatingIndex = this.rotatingIndex % this.cache.length;
    } else {
      this.rotatingIndex = 0;
    }
    return this.cache;
  }

  pickCandidates(keys: CachedKey[], maxAttempts = MAX_KEY_ATTEMPTS): CachedKey[] {
    if (keys.length === 0) return [];
    const now = this.nowFn();
    const start = this.rotatingIndex % keys.length;
    this.rotatingIndex = (start + 1) % keys.length;

    const candidates: CachedKey[] = [];
    for (let offset = 0; offset < keys.length; offset++) {
      const key = keys[(start + offset) % keys.length];
      if (key.cooldownUntilMs !== null && key.cooldownUntilMs > now) {
        continue;
      }
      candidates.push(key);
      if (candidates.length >= maxAttempts) {
        break;
      }
    }
    return candidates;
  }

  markCooldown(label: string, cooldownSeconds: number): void {
    const until = this.nowFn() + Math.max(0, cooldownSeconds) * 1000;
    for (const key of this.cache) {
      if (key.label === label) {
        key.cooldownUntilMs = until;
      }
    }
  }

  clearCooldown(label: string): void {
    for (const key of this.cache) {
      if (key.label === label) {
        key.cooldownUntilMs = null;
      }
    }
  }
}
