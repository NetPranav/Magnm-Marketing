import { beforeEach, describe, expect, it } from 'vitest';
import { NIM_KEY_SECRET_KEY, SESSION_SECRET_KEY, getPersonalNimKey, wipeAllLocalState } from './auth';
import { createPersonalCloud, fp } from './fixtures/personalCloud';
import { createPreviewNativeBridge, type NativeBridge } from './native';
import {
  KEY_SLOTS,
  KEYS_MAP_SECRET_KEY,
  KEYS_SYNC_SECRET_KEY,
  addPersonalKey,
  listPersonalKeys,
  reconcileKeys,
  removePersonalKey,
  syncPersonalKeys,
} from './personalKeys';
import { dumpStorage } from './test-storage';

const key = (n: number) => `nvapi-FAKEKEYPHONE${String(n).padStart(20, '0')}`;

async function setup(uid = 'user-a') {
  const cloud = createPersonalCloud();
  const bridge = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
  await signIn(bridge, uid);
  return { cloud, bridge };
}

async function signIn(bridge: NativeBridge, uid: string) {
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: `jwt-${uid}`, refresh_token: 'r', expires_at: 9_999_999_999, user_id: uid, email: `${uid}@example.com` }),
  );
}

describe('reconcileKeys (same decision as the laptop)', () => {
  const A = 'a'.repeat(64);
  const B = 'b'.repeat(64);
  it('uploads local-only, downloads cloud-only, keeps equal', () => {
    const plan = reconcileKeys({ NVIDIA_NIM_API_KEY: A }, { NVIDIA_NIM_API_KEY1: B }, {});
    expect(plan.upload).toEqual(['NVIDIA_NIM_API_KEY']);
    expect(plan.download).toEqual(['NVIDIA_NIM_API_KEY1']);
    expect(reconcileKeys({ NVIDIA_NIM_API_KEY: A }, { NVIDIA_NIM_API_KEY: A }, {}).keep).toEqual({ NVIDIA_NIM_API_KEY: A });
  });
  it('follows who changed since the last agreement', () => {
    expect(reconcileKeys({ NVIDIA_NIM_API_KEY: A }, { NVIDIA_NIM_API_KEY: B }, { NVIDIA_NIM_API_KEY: B }).upload).toEqual(['NVIDIA_NIM_API_KEY']);
    expect(reconcileKeys({ NVIDIA_NIM_API_KEY: A }, { NVIDIA_NIM_API_KEY: B }, { NVIDIA_NIM_API_KEY: A }).download).toEqual(['NVIDIA_NIM_API_KEY']);
  });
  it('removed elsewhere means removed here only when it was agreed before', () => {
    expect(reconcileKeys({ NVIDIA_NIM_API_KEY: A }, {}, { NVIDIA_NIM_API_KEY: A }).deleteLocal).toEqual(['NVIDIA_NIM_API_KEY']);
    expect(reconcileKeys({ NVIDIA_NIM_API_KEY: A }, {}, {}).deleteLocal).toEqual([]);
  });
  it('two different keys in one slot keep both', () => {
    const plan = reconcileKeys({ NVIDIA_NIM_API_KEY: A }, { NVIDIA_NIM_API_KEY: B }, {});
    expect(plan.download).toEqual(['NVIDIA_NIM_API_KEY']);
    expect(plan.copyLocal).toEqual([['NVIDIA_NIM_API_KEY', 'NVIDIA_NIM_API_KEY1']]);
    expect(plan.upload).toEqual(['NVIDIA_NIM_API_KEY1']);
  });
});

describe('personal keys on the phone', () => {
  beforeEach(() => window.localStorage.clear());

  it('a key added on the phone is stored securely, uploaded, and used as the phone key', async () => {
    const { cloud, bridge } = await setup();
    const res = await addPersonalKey(bridge, key(1));
    expect(res.slot).toBe('NVIDIA_NIM_API_KEY');
    expect(cloud.keysOf('user-a')).toEqual({ NVIDIA_NIM_API_KEY: key(1) });
    expect(await getPersonalNimKey(bridge)).toBe(key(1));
    expect(await listPersonalKeys(bridge)).toEqual([{ name: 'NVIDIA_NIM_API_KEY', label: `...${key(1).slice(-4)}`, synced: true, shareable: true }]);
    expect(dumpStorage()).not.toContain('FAKEKEY');
  });

  it('works offline: saved locally and uploaded on the next sync', async () => {
    const { cloud, bridge } = await setup();
    cloud.state.down = true;
    const res = await addPersonalKey(bridge, key(2));
    expect(res.uploaded).toBe(false);
    expect(await getPersonalNimKey(bridge)).toBe(key(2));
    cloud.state.down = false;
    await syncPersonalKeys(bridge);
    expect(cloud.keysOf('user-a')).toEqual({ NVIDIA_NIM_API_KEY: key(2) });
  });

  it('a key added on the laptop arrives on the phone', async () => {
    const { cloud, bridge } = await setup();
    cloud.putKey('user-a', 'NVIDIA_NIM_API_KEY2', key(3));
    const out = await syncPersonalKeys(bridge);
    expect(out?.downloaded).toEqual(['NVIDIA_NIM_API_KEY2']);
    expect(await getPersonalNimKey(bridge)).toBe(key(3));
  });

  it('removing on the phone removes it in the cloud; removing in the cloud removes it on the phone', async () => {
    const { cloud, bridge } = await setup();
    await addPersonalKey(bridge, key(1));
    await removePersonalKey(bridge, 'NVIDIA_NIM_API_KEY');
    expect(cloud.keysOf('user-a')).toEqual({});
    expect(await getPersonalNimKey(bridge)).toBeNull();

    await addPersonalKey(bridge, key(4));
    cloud.dropKey('user-a', 'NVIDIA_NIM_API_KEY');
    const out = await syncPersonalKeys(bridge);
    expect(out?.removedLocal).toEqual(['NVIDIA_NIM_API_KEY']);
    expect(await getPersonalNimKey(bridge)).toBeNull();
    expect(await listPersonalKeys(bridge)).toEqual([]);
  });

  it('a removal made offline is remembered until the cloud is reachable', async () => {
    const { cloud, bridge } = await setup();
    await addPersonalKey(bridge, key(1));
    cloud.state.down = true;
    const res = await removePersonalKey(bridge, 'NVIDIA_NIM_API_KEY');
    expect(res.removedInCloud).toBe(false);
    cloud.state.down = false;
    await syncPersonalKeys(bridge);
    expect(cloud.keysOf('user-a')).toEqual({});
    expect(await getPersonalNimKey(bridge)).toBeNull();
  });

  it('two keys in one slot are both kept', async () => {
    const { cloud, bridge } = await setup();
    cloud.state.down = true;
    await addPersonalKey(bridge, key(5));
    cloud.state.down = false;
    cloud.putKey('user-a', 'NVIDIA_NIM_API_KEY', key(6));
    await syncPersonalKeys(bridge);
    expect(Object.values(cloud.keysOf('user-a')).sort()).toEqual([key(5), key(6)].sort());
    const labels = (await listPersonalKeys(bridge)).map((k) => k.label).sort();
    expect(labels).toEqual([`...${key(5).slice(-4)}`, `...${key(6).slice(-4)}`].sort());
  });

  it('rejects malformed keys before anything is stored or sent', async () => {
    const { cloud, bridge } = await setup();
    for (const bad of ['', 'nvapi-short', 'sk-FAKEKEYPHONE00000000000000000000', `${key(1)}\n`, ` ${key(1)}`, 'nvapi-' + 'A'.repeat(201)]) {
      await expect(addPersonalKey(bridge, bad)).rejects.toThrow();
    }
    expect(cloud.state.seen.length).toBe(0);
    expect(await getPersonalNimKey(bridge)).toBeNull();
  });

  it('a key of unusual length stays on this phone only: kept, never uploaded, removable', async () => {
    const { cloud, bridge } = await setup();
    const res = await addPersonalKey(bridge, 'nvapi-test-key');
    expect(res.uploaded).toBe(false);
    expect(await getPersonalNimKey(bridge)).toBe('nvapi-test-key');
    await syncPersonalKeys(bridge);
    expect(cloud.keysOf('user-a')).toEqual({});
    expect(await listPersonalKeys(bridge)).toEqual([{ name: 'NVIDIA_NIM_API_KEY', label: '...-key', synced: false, shareable: false }]);
    await removePersonalKey(bridge, 'NVIDIA_NIM_API_KEY');
    expect(await getPersonalNimKey(bridge)).toBeNull();
  });

  it('adding a key that is already saved does not make a second slot', async () => {
    const { bridge } = await setup();
    await addPersonalKey(bridge, key(1));
    const again = await addPersonalKey(bridge, key(1));
    expect(again.slot).toBe('NVIDIA_NIM_API_KEY');
    expect((await listPersonalKeys(bridge)).length).toBe(1);
  });

  it('only eleven keys fit', async () => {
    const { bridge } = await setup();
    for (let i = 0; i < KEY_SLOTS.length; i += 1) await addPersonalKey(bridge, key(100 + i));
    await expect(addPersonalKey(bridge, key(999))).rejects.toThrow(/up to 11/);
  });

  it('hostile cloud answers are not stored', async () => {
    const { cloud } = await setup();
    cloud.putKey('user-a', 'NVIDIA_NIM_API_KEY', key(1));
    const real = cloud.handler;
    cloud.handler = async (req) => {
      const res = await real(req);
      if (res && req.url.endsWith('rpc/personal_key_get')) {
        return { ...res, body: JSON.stringify([{ name: 'NVIDIA_NIM_API_KEY', api_key: key(9) }, { name: 'PATH', api_key: '/evil' }]) };
      }
      return res;
    };
    const wrapped = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
    await signIn(wrapped, 'user-a');
    await syncPersonalKeys(wrapped);
    expect(await getPersonalNimKey(wrapped)).toBeNull();
    expect(await wrapped.secureStore.get(KEYS_MAP_SECRET_KEY)).toBeNull();
  });

  it("never reads another member's keys, and a new account does not inherit the old account's keys", async () => {
    const { cloud, bridge } = await setup('user-a');
    cloud.putKey('user-b', 'NVIDIA_NIM_API_KEY', key(8));
    await addPersonalKey(bridge, key(1));
    await signIn(bridge, 'user-b');
    const out = await syncPersonalKeys(bridge);
    expect(out?.uploaded).toEqual([]);
    expect(cloud.keysOf('user-a')).toEqual({ NVIDIA_NIM_API_KEY: key(1) });
    expect(cloud.keysOf('user-b')).toEqual({ NVIDIA_NIM_API_KEY: key(8) });
    expect(await getPersonalNimKey(bridge)).toBe(key(8));
  });

  it('keeps no key in the sync state, local storage or errors, and sign-out wipes the keys', async () => {
    const { cloud, bridge } = await setup();
    await addPersonalKey(bridge, key(1));
    const state = (await bridge.secureStore.get(KEYS_SYNC_SECRET_KEY)) ?? '';
    expect(state).not.toContain('FAKEKEY');
    expect(state).toContain(await fp(key(1)));
    cloud.state.down = true;
    await expect(syncPersonalKeys(bridge)).resolves.toBeNull();
    expect(dumpStorage()).not.toContain('FAKEKEY');
    await wipeAllLocalState(bridge);
    expect(await bridge.secureStore.get(KEYS_MAP_SECRET_KEY)).toBeNull();
    expect(await bridge.secureStore.get(KEYS_SYNC_SECRET_KEY)).toBeNull();
    expect(await bridge.secureStore.get(NIM_KEY_SECRET_KEY)).toBeNull();
  });

  it('a key stored by an older app version is picked up and shared', async () => {
    const { cloud, bridge } = await setup();
    await bridge.secureStore.set(NIM_KEY_SECRET_KEY, key(7));
    await syncPersonalKeys(bridge);
    expect(cloud.keysOf('user-a')).toEqual({ NVIDIA_NIM_API_KEY: key(7) });
  });

  it('does nothing when signed out', async () => {
    const cloud = createPersonalCloud();
    const bridge = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
    expect(await syncPersonalKeys(bridge)).toBeNull();
    const res = await addPersonalKey(bridge, key(1));
    expect(res.uploaded).toBe(false);
    expect(cloud.state.seen.length).toBe(0);
    expect(await getPersonalNimKey(bridge)).toBe(key(1));
  });
});
