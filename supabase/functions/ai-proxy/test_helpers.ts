function deepEquals(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
      return false;
    }
    return a.every((item, idx) => deepEquals(item, b[idx]));
  }
  if (typeof a === "object" && typeof b === "object") {
    const keysA = Object.keys(a as Record<string, unknown>);
    const keysB = Object.keys(b as Record<string, unknown>);
    if (keysA.length !== keysB.length) return false;
    return keysA.every((k) =>
      Object.prototype.hasOwnProperty.call(b, k) &&
      deepEquals(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
      )
    );
  }
  return false;
}

export const assert = {
  equal<T>(actual: T, expected: T, message?: string): void {
    if (!Object.is(actual, expected)) {
      throw new Error(
        message ??
          `Expected ${JSON.stringify(actual)} to equal ${JSON.stringify(expected)}`,
      );
    }
  },
  deepEqual<T>(actual: T, expected: T, message?: string): void {
    if (!deepEquals(actual, expected)) {
      throw new Error(
        message ??
          `Expected ${JSON.stringify(actual)} to deep-equal ${JSON.stringify(expected)}`,
      );
    }
  },
  ok(cond: unknown, message?: string): void {
    if (!cond) {
      throw new Error(message ?? `Expected truthy value, got ${JSON.stringify(cond)}`);
    }
  },
};
