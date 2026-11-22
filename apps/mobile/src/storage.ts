// Non-secret web storage helpers and async generation counter.
// Secrets (tokens, PKCE verifiers, API keys) MUST NEVER be stored here;
// they belong exclusively in native.secureStore.

const SECRET_KEY_PATTERN = /(token|secret|verifier|pkce|auth\.session|api_?key|nim_?key|password|credential)/i;

let stateGeneration = 0;

/** Return the current state generation counter used to discard stale async completions. */
export function getStateGeneration(): number {
  return stateGeneration;
}

/** Advance the state generation counter on wipe, unpair, sign-out, or account change. */
export function bumpStateGeneration(): number {
  stateGeneration += 1;
  return stateGeneration;
}

function assertNonSecretKey(key: string): void {
  if (SECRET_KEY_PATTERN.test(key)) {
    throw new Error(`Refused to store potential secret under web storage key "${key}".`);
  }
}

export function readNonSecretJson<T>(key: string): T | null {
  assertNonSecretKey(key);
  if (typeof window === 'undefined' || !window.localStorage) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/** Write non-secret JSON to localStorage, throwing on storage write errors. */
export function writeNonSecretJson(key: string, value: unknown): void {
  assertNonSecretKey(key);
  if (typeof window === 'undefined' || !window.localStorage) {
    throw new Error('Local storage is unavailable.');
  }
  window.localStorage.setItem(key, JSON.stringify(value));
}

export function removeNonSecretKey(key: string): void {
  assertNonSecretKey(key);
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore storage removal errors.
  }
}
