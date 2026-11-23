// Test helper: the phone app needs a signed-in account before it shows anything but the welcome screen.
import { SESSION_SECRET_KEY } from './auth';
import type { NativeBridge } from './native';

/** Sign the test bridge in as a default account unless a test already stored its own session. */
export async function ensureSession(bridge: NativeBridge, userId = 'uid-1'): Promise<void> {
  if (await bridge.secureStore.get(SESSION_SECRET_KEY)) return;
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({
      access_token: 'jwt-test',
      refresh_token: 'ref-test',
      expires_at: 9_999_999_999,
      user_id: userId,
      email: `${userId}@example.com`,
    }),
  );
}
