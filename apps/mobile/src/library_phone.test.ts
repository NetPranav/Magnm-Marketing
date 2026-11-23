import { beforeEach, describe, expect, it } from 'vitest';
import { loadActions } from './actions';
import { SESSION_SECRET_KEY } from './auth';
import { LAPTOP_TOKEN_SECRET_KEY } from './pairing';
import type { ContextCacheState } from './cache';
import {
  MAX_UPLOAD_BYTES,
  UploadError,
  assetsFromMirror,
  checkUploadFile,
  deletePhoneUpload,
  fetchLaptopLibrary,
  fetchPhoneUploads,
  shareLaptopAsset,
  uploadFromPhone,
  type UploadableFile,
} from './libraryPhone';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig, validatePhoneUploadUrl } from './net';
import type { MirrorRecord, PairingConfig, ProductRulesConfig } from './types';

const SUPA = getPublicSupabaseConfig();
const UID = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};
const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, ...new Array(40).fill(0)]);
const PDF = new TextEncoder().encode('%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n');
const MP4 = Uint8Array.from([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x6d, 0x70, 0x34, 0x32, ...new Array(40).fill(0)]);
const HTML = new TextEncoder().encode('<html><script>alert(1)</script></html>');

function fileOf(name: string, bytes: Uint8Array, size = bytes.length): UploadableFile {
  return {
    name,
    size,
    type: '',
    slice: (start: number, end: number) => ({ arrayBuffer: async () => bytes.slice(start, end).buffer as ArrayBuffer }),
  };
}

type Http = (req: NativeHttpRequest) => Promise<NativeHttpResponse>;
const ok = (body: unknown, status = 200): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });

async function setup(http: Http, uid = UID) {
  const uploads: Array<{ url: string; headers: Record<string, string>; contentType: string; bytes: number; data: Uint8Array }> = [];
  const bridge = createPreviewNativeBridge({
    httpHandler: http,
    onUploadCloudFile: (info) => {
      uploads.push(info);
      return { status: 200, body: '{}' };
    },
  });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: `jwt-${uid}`, refresh_token: 'r', expires_at: 9_999_999_999, user_id: uid, email: 'a@example.com' }),
  );
  return { bridge, uploads };
}

beforeEach(() => window.localStorage.clear());

describe('checking a file before it is uploaded', () => {
  it('decides the type from the first bytes, never from the name', () => {
    expect(checkUploadFile('logo.png', PNG.length, PNG)).toMatchObject({ kind: 'image', contentType: 'image/png', ext: 'png' });
    expect(checkUploadFile('doc.pdf', PDF.length, PDF)).toMatchObject({ kind: 'pdf', contentType: 'application/pdf', ext: 'pdf' });
    expect(checkUploadFile('clip.mp4', MP4.length, MP4)).toMatchObject({ kind: 'video', contentType: 'video/mp4', ext: 'mp4' });
    // A PNG named .pdf is still refused (the name and the bytes must agree).
    expect(() => checkUploadFile('x.pdf', PNG.length, PNG)).toThrow(UploadError);
    expect(() => checkUploadFile('x.png', HTML.length, HTML)).toThrow(UploadError);
    expect(() => checkUploadFile('x.html', HTML.length, HTML)).toThrow(UploadError);
    expect(() => checkUploadFile('x.svg', HTML.length, HTML)).toThrow(UploadError);
    expect(() => checkUploadFile('x.exe', 20, Uint8Array.from([0x4d, 0x5a, 0, 0]))).toThrow(UploadError);
    expect(() => checkUploadFile('noext', PNG.length, PNG)).toThrow(UploadError);
  });

  it('caps the size and refuses an empty file', () => {
    expect(() => checkUploadFile('big.png', MAX_UPLOAD_BYTES + 1, PNG)).toThrow(/50 MB/);
    expect(() => checkUploadFile('zero.png', 0, PNG)).toThrow(UploadError);
    expect(checkUploadFile('ok.png', MAX_UPLOAD_BYTES, PNG).kind).toBe('image');
  });

  it('rebuilds the shown name from safe characters', () => {
    expect(checkUploadFile('../../etc/passwd.png', PNG.length, PNG).fileName).toBe('passwd.png');
    expect(checkUploadFile('a\\b\\evil.png', PNG.length, PNG).fileName).toBe('evil.png');
    expect(checkUploadFile('line\nbreak.png', PNG.length, PNG).fileName).toBe('linebreak.png');
    expect(checkUploadFile('Launch photo (1).JPG', 5, Uint8Array.from([0xff, 0xd8, 0xff, 0xe0, 0])).fileName).toBe('Launch photo (1).jpg');
    expect(checkUploadFile('x'.repeat(400) + '.png', PNG.length, PNG).fileName.length).toBeLessThanOrEqual(120);
  });

  it('the upload path allows exactly one object of the signed-in account', () => {
    const good = `${SUPA.url}/storage/v1/object/phone-uploads/${UID}/${'a'.repeat(64)}.png`;
    expect(validatePhoneUploadUrl(good, UID)).toBe(good);
    for (const bad of [
      `${SUPA.url}/storage/v1/object/phone-uploads/${OTHER}/${'a'.repeat(64)}.png`,
      `${SUPA.url}/storage/v1/object/phone-uploads/${UID}/${'a'.repeat(64)}.html`,
      `${SUPA.url}/storage/v1/object/phone-uploads/${UID}/short.png`,
      `${SUPA.url}/storage/v1/object/phone-files/${UID}/${'a'.repeat(64)}.png`,
      `${SUPA.url}/storage/v1/object/phone-uploads/${UID}/../${OTHER}/${'a'.repeat(64)}.png`,
      `${good}?token=abcdefgh`,
      `${good}#x`,
      `http://example.com/storage/v1/object/phone-uploads/${UID}/${'a'.repeat(64)}.png`,
      `https://evil.example.com/storage/v1/object/phone-uploads/${UID}/${'a'.repeat(64)}.png`,
    ]) {
      expect(() => validatePhoneUploadUrl(bad, UID), bad).toThrow();
    }
  });
});

describe('uploading from the phone', () => {
  it('adds the row, sends the bytes to the owners own folder, then queues the Library action', async () => {
    const calls: Array<{ method: string; url: string; body?: unknown }> = [];
    const { bridge, uploads } = await setup(async (req) => {
      calls.push({ method: req.method, url: req.url, body: req.body ? JSON.parse(req.body) : undefined });
      return { status: 201, headers: {}, body: '' };
    });
    const action = await uploadFromPhone({ ownerUid: UID, file: fileOf('Launch logo.png', PNG), category: 'brand', description: 'The new mark', rules: RULES, bridge });
    expect(action.kind).toBe('library_upload');
    expect(action.state).toBe('waiting');
    expect(action.payload).toEqual({ category: 'brand', description: 'The new mark' });
    expect(action.target_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const row = calls.find((c) => c.method === 'POST' && c.url.includes('/rest/v1/phone_uploads'));
    expect(row).toBeTruthy();
    expect(row!.body).toMatchObject({ id: action.target_id, file_name: 'Launch logo.png', kind: 'image', content_type: 'image/png', bytes: PNG.length });
    const body = row!.body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['bytes', 'content_type', 'file_name', 'id', 'kind', 'storage_path']);
    expect(String(body.storage_path)).toMatch(new RegExp(`^${UID}/[0-9a-f]{64}\\.png$`));
    expect(uploads).toHaveLength(1);
    expect(uploads[0].url).toBe(`${SUPA.url}/storage/v1/object/phone-uploads/${body.storage_path}`);
    expect(uploads[0].contentType).toBe('image/png');
    expect(Array.from(uploads[0].data)).toEqual(Array.from(PNG));
    expect(uploads[0].headers.Authorization).toBe(`Bearer jwt-${UID}`);
    expect(loadActions(UID)).toHaveLength(1);
  });

  it('sends a big file in chunks and the laptop-bound bytes arrive intact', async () => {
    const big = new Uint8Array(1_000_000).map((_, i) => (i * 7) % 251);
    big.set(PNG, 0);
    const { bridge, uploads } = await setup(async () => ({ status: 201, headers: {}, body: '' }));
    await uploadFromPhone({ ownerUid: UID, file: fileOf('big.png', big), category: 'screenshot', rules: RULES, bridge });
    expect(uploads[0].data.length).toBe(big.length);
    expect(Array.from(uploads[0].data.slice(0, 2000))).toEqual(Array.from(big.slice(0, 2000)));
    expect(Array.from(uploads[0].data.slice(-2000))).toEqual(Array.from(big.slice(-2000)));
  });

  it('refuses a hostile file before anything leaves the phone', async () => {
    const calls: string[] = [];
    const { bridge, uploads } = await setup(async (req) => {
      calls.push(req.url);
      return { status: 201, headers: {}, body: '' };
    });
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('evil.png', HTML), category: 'brand', rules: RULES, bridge })).rejects.toThrow(UploadError);
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('big.png', PNG, MAX_UPLOAD_BYTES + 1), category: 'brand', rules: RULES, bridge })).rejects.toThrow(/50 MB/);
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('x.png', PNG), category: 'weapons' as never, rules: RULES, bridge })).rejects.toThrow();
    expect(calls).toEqual([]);
    expect(uploads).toEqual([]);
    expect(loadActions(UID)).toEqual([]);
  });

  it('keeps confidential work on the laptop: a name or note naming a confidential product is never uploaded', async () => {
    const calls: string[] = [];
    const { bridge, uploads } = await setup(async (req) => {
      calls.push(req.url);
      return { status: 201, headers: {}, body: '' };
    });
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('ProjectNightfall mockup.png', PNG), category: 'brand', rules: RULES, bridge })).rejects.toThrow(/confidential/i);
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('ok.png', PNG), category: 'brand', description: 'ProjectNightfall', rules: RULES, bridge })).rejects.toThrow(/confidential/i);
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('ok.png', PNG), category: 'brand', rules: null, bridge })).rejects.toThrow(/rules/i);
    expect(calls).toEqual([]);
    expect(uploads).toEqual([]);
  });

  it('removes the row again when the bytes could not be sent, and queues nothing', async () => {
    const calls: Array<{ method: string; url: string }> = [];
    const { bridge } = await setup(async (req) => {
      calls.push({ method: req.method, url: req.url });
      return { status: 201, headers: {}, body: '' };
    });
    const failing = createPreviewNativeBridge({
      httpHandler: async (req) => {
        calls.push({ method: req.method, url: req.url });
        return { status: 201, headers: {}, body: '' };
      },
      onUploadCloudFile: () => ({ status: 413, body: '{"message":"too big"}' }),
    });
    await failing.secureStore.set(SESSION_SECRET_KEY, await bridge.secureStore.get(SESSION_SECRET_KEY).then((v) => v ?? ''));
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('a.png', PNG), category: 'brand', rules: RULES, bridge: failing })).rejects.toThrow();
    expect(calls.some((c) => c.method === 'DELETE' && c.url.includes('/rest/v1/phone_uploads'))).toBe(true);
    expect(loadActions(UID)).toEqual([]);
  });

  it('shows the budget message when the cloud is full', async () => {
    const { bridge } = await setup(async (req) => {
      if (req.url.includes('/rest/v1/phone_uploads') && req.method === 'POST') {
        return { status: 400, headers: {}, body: JSON.stringify({ message: 'Cloud storage budget (1 GB) is full' }) };
      }
      return ok([]);
    });
    await expect(uploadFromPhone({ ownerUid: UID, file: fileOf('a.png', PNG), category: 'brand', rules: RULES, bridge })).rejects.toThrow(/storage is full|budget/i);
    expect(loadActions(UID)).toEqual([]);
  });

  it('lists only this accounts own uploads and drops rows that are not well formed', async () => {
    const row = (over: Record<string, unknown> = {}) => ({
      id: '33333333-3333-4333-8333-333333333333', owner_id: UID, file_name: 'a.png', kind: 'image', content_type: 'image/png', bytes: 10,
      storage_path: `${UID}/${'a'.repeat(64)}.png`, expires_at: new Date(Date.now() + 86_400_000).toISOString(), ...over,
    });
    const { bridge } = await setup(async () =>
      ok([
        row(),
        row({ id: '44444444-4444-4444-8444-444444444444', owner_id: OTHER, storage_path: `${OTHER}/${'a'.repeat(64)}.png` }),
        row({ id: '55555555-5555-4555-8555-555555555555', storage_path: `${OTHER}/${'a'.repeat(64)}.png` }),
        row({ id: '66666666-6666-4666-8666-666666666666', kind: 'script' }),
        row({ id: '77777777-7777-4777-8777-777777777777', bytes: -1 }),
        row({ id: 'not-a-uuid' }),
        null,
        'junk',
      ]),
    );
    const list = await fetchPhoneUploads(UID, bridge);
    expect(list.map((u) => u.id)).toEqual(['33333333-3333-4333-8333-333333333333']);
  });

  it('deletes one of its own uploads by id, filtered by owner', async () => {
    const calls: string[] = [];
    const { bridge } = await setup(async (req) => {
      calls.push(`${req.method} ${req.url}`);
      return { status: 204, headers: {}, body: '' };
    });
    await deletePhoneUpload(UID, '33333333-3333-4333-8333-333333333333', bridge);
    expect(calls[0]).toContain('DELETE');
    expect(calls[0]).toContain(`owner_id=eq.${UID}`);
    await expect(deletePhoneUpload(UID, '../x', bridge)).rejects.toThrow();
  });
});

function rec(id: string, data: Record<string, unknown>, extra: Partial<MirrorRecord> = {}): MirrorRecord {
  return { id: `m-${id}`, owner_id: UID, kind: 'asset', local_id: id, title: String(data.name ?? ''), data, local_updated_at: '2026-11-24T08:00:00+00:00', cached_at: Date.now(), ...extra };
}

function cacheOf(records: MirrorRecord[]): ContextCacheState {
  return { ownerUid: UID, mirrorRecords: records, productRules: null, laptop: null } as unknown as ContextCacheState;
}

describe('the Library as the phone shows it', () => {
  it('lists the laptops assets from the mirror and never a path', () => {
    const list = assetsFromMirror(
      cacheOf([
        rec('asset_1', { name: 'logo.png', file_group: 'image', size_bytes: 1200, category: 'brand', description: 'Mark', fetchable: true, uploaded_at: '2026-11-24T08:00:00+00:00', relative_path: '/home/x/y.png' }),
        rec('asset_2', { confidential: true }),
        rec('asset_3', { name: 'notes.txt', file_group: 'document', size_bytes: 3, fetchable: false }, { owner_id: OTHER }),
        rec('c', { name: 'x' }, { kind: 'content_item' }),
      ]),
    );
    expect(list.map((a) => a.id)).toEqual(['asset_1', 'asset_2']);
    expect(list[0]).toMatchObject({ name: 'logo.png', category: 'brand', fetchable: true, confidential: false });
    expect(JSON.stringify(list)).not.toContain('/home/');
    expect(list[1]).toMatchObject({ confidential: true, name: 'Confidential item' });
  });

  it('reads the live list from the paired laptop and fetches one file by asset id only', async () => {
    const pairing: PairingConfig = { host: '100.64.0.7', port: 8787, deviceId: 'dev1', deviceName: 'Pixel', ownerUid: UID, pairedAt: 'x' };
    const urls: string[] = [];
    const shared: string[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        urls.push(req.url);
        return ok({ assets: [{ id: 'asset_1', name: 'logo.png', file_group: 'image', size_bytes: 5, category: 'brand', fetchable: true }, { id: 'asset_2', confidential: true }, { id: '../evil', name: 'x' }] });
      },
      onShareLaptopFile: (o) => void shared.push(o.url),
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok');
    const live = await fetchLaptopLibrary(pairing, bridge);
    expect(live.map((a) => a.id)).toEqual(['asset_1', 'asset_2']);
    await shareLaptopAsset(pairing, 'asset_1', 'logo.png', bridge);
    expect(shared[0]).toBe('http://100.64.0.7:8787/phone/v1/files?asset_id=asset_1');
    await expect(shareLaptopAsset(pairing, '../../state/db', 'x', bridge)).rejects.toThrow();
    expect(shared).toHaveLength(1);
  });
});
