import { useEffect } from 'react';
import { applyPendingKeys } from '../../api/keys';

const POLL_MS = 15_000;

/**
 * Inside the desktop app, write keys that were added or removed on the phone into the OS keychain. It runs while
 * signed in, never navigates and never shows a key; a failed try simply waits for the next one.
 */
export function usePersonalKeysSync(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let busy = false;
    const tick = async () => {
      if (stopped || busy) return;
      busy = true;
      try {
        await applyPendingKeys();
      } catch {
        // Try again next time.
      } finally {
        busy = false;
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [enabled]);
}
