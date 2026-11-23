import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyPendingKeys, forgetPersonalKey, isPersonalKeySlot, requestKeySync } from './keys';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const FAKE = 'nvapi-FAKEKEYWEBTEST00000000000000001';

describe('personal key helpers', () => {
  const invoke = vi.fn();
  beforeEach(() => {
    invoke.mockReset();
    invoke.mockResolvedValue(undefined);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  });

  it('knows exactly eleven slots', () => {
    expect(isPersonalKeySlot('NVIDIA_NIM_API_KEY')).toBe(true);
    expect(isPersonalKeySlot('NVIDIA_NIM_API_KEY10')).toBe(true);
    for (const bad of ['NVIDIA_NIM_API_KEY11', 'OPENAI_API_KEY', 'PATH', '', 'nvidia_nim_api_key']) {
      expect(isPersonalKeySlot(bad)).toBe(false);
    }
  });

  it('writes pending keys through the desktop shell, then confirms them, once', async () => {
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke } };
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push(`${init?.method || 'GET'} ${String(input)} ${init?.body ?? ''}`);
      if (String(input).endsWith('/pending')) {
        return jsonResponse({ ops: [{ op: 'set', name: 'NVIDIA_NIM_API_KEY', value: FAKE }, { op: 'delete', name: 'NVIDIA_NIM_API_KEY2' }] });
      }
      return jsonResponse({ applied: [] });
    });
    expect(await applyPendingKeys()).toBe(2);
    expect(invoke).toHaveBeenCalledWith('store_secrets', {
      sets: [{ name: 'NVIDIA_NIM_API_KEY', value: FAKE }],
      deletes: ['NVIDIA_NIM_API_KEY2'],
    });
    expect(calls[1]).toContain('/api/account/keys/applied');
    expect(calls[1]).toContain('NVIDIA_NIM_API_KEY2');
    expect(calls[1]).not.toContain('FAKEKEY');
  });

  it('does not confirm when the keychain write fails, and ignores names that are not slots', async () => {
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke } };
    invoke.mockRejectedValue(new Error('keychain locked'));
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/pending')) {
        return jsonResponse({ ops: [{ op: 'set', name: 'NVIDIA_NIM_API_KEY', value: FAKE }, { op: 'set', name: 'PATH', value: 'x' }] });
      }
      return jsonResponse({});
    });
    await expect(applyPendingKeys()).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith('store_secrets', { sets: [{ name: 'NVIDIA_NIM_API_KEY', value: FAKE }], deletes: [] });
  });

  it('does nothing outside the desktop app', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    expect(await applyPendingKeys()).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forget and sync requests are best effort and only for real slots', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    await expect(forgetPersonalKey('NVIDIA_NIM_API_KEY3')).resolves.toBeUndefined();
    await expect(requestKeySync()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await forgetPersonalKey('OPENAI_API_KEY');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
