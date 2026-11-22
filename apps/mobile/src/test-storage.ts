// Test helpers that work with both storages tests meet: jsdom's Storage (Node 22) and our in-memory fallback (Node 25+).
import { vi } from 'vitest';

/** Make every localStorage write throw, like a full phone store. */
export function failStorageWrites() {
  const store = window.localStorage;
  // jsdom keeps setItem on the prototype: assigning it on the instance would just store a key called "setItem".
  const target: Storage = Object.prototype.hasOwnProperty.call(store, 'setItem') ? store : Object.getPrototypeOf(store);
  return vi.spyOn(target, 'setItem').mockImplementation(() => {
    throw new Error('QuotaExceededError');
  });
}

/** Every key and value in localStorage as text. JSON.stringify(localStorage) is "{}" for the fallback. */
export function dumpStorage(): string {
  const store = window.localStorage;
  return Array.from({ length: store.length }, (_, i) => {
    const key = store.key(i) as string;
    return `${key}=${store.getItem(key)}`;
  }).join('\n');
}
