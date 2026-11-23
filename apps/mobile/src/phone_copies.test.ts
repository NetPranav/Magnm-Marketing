import { beforeEach, describe, expect, it } from 'vitest';
import { SESSION_SECRET_KEY } from './auth';
import {
  PhoneCopyError,
  copyExpiryLabel,
  copiesForDraft,
  fetchPhoneCopies,
  formatCopySize,
  getCopyOnPhone,
  isCopyUsable,
  type PhoneCopy,
} from './phoneCopies';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse, type ShareCloudFileOptions } from './native';
import {
  NetworkPolicyError,
  getPublicSupabaseConfig,
  netShareCloudFile,
  validateCloudFileUrl,
  validatePhoneCopySignUrl,
  validateSupabaseUrl,
} from './net';

const SUPA = getPublicSupabaseConfig();
const UID = '11111111-2222-4333-8444-555555555501';
const OTHER = '11111111-2222-4333-8444-555555555502';
const H = 'a'.repeat(64);
const H2 = 'b'.repeat(64);
const SIGN_BASE = `${SUPA.url}/storage/v1/object/sign/phone-files`;
const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ1cmwiOiJ4In0.c2lnbmF0dXJl';

beforeEach(() => {
  window.localStorage.clear();
});

function copy(over: Partial<PhoneCopy> = {}): PhoneCopy {
  return {
    id: 'row-1',
    content_id: 'cnt_1',
    file_path: 'outputs/cards/hero.png',
    file_name: 'hero.png',
    kind: 'image',
    content_type: 'image/png',
    bytes: 1_500_000,
    storage_path: `${UID}/${H}.png`,
    expires_at: new Date(Date.now() + 10 * 86_400_000).toISOString(),
    ...over,
  };
}

describe('the exact storage path the phone may use', () => {
  it('lets the signing request and the signed download through for one owner folder and one hashed name', () => {
    expect(validateSupabaseUrl(`${SIGN_BASE}/${UID}/${H}.png`)).toBe(`${SIGN_BASE}/${UID}/${H}.png`);
    expect(validateSupabaseUrl(`${SIGN_BASE}/${UID}/${H}.pdf?token=${TOKEN}`)).toContain('?token=');
    expect(validatePhoneCopySignUrl(`${SIGN_BASE}/${UID}/${H}.mp4`, UID)).toBe(`${SIGN_BASE}/${UID}/${H}.mp4`);
    expect(validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.mp4?token=${TOKEN}`, UID)).toBe(`${SIGN_BASE}/${UID}/${H}.mp4?token=${TOKEN}`);
  });

  it('refuses every other storage path, bucket, folder, name and method of reaching storage', () => {
    const bad = [
      `${SUPA.url}/storage/v1/object/sign/previews/${UID}/${H}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/sub/${H}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${H}.html`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${H}.svg`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/evil.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${'A'.repeat(64)}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${'a'.repeat(63)}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/..%2F${H}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/../${OTHER}/${H}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/not-a-uuid/${H}.png`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/`,
      `${SUPA.url}/storage/v1/object/sign/phone-files`,
      `${SUPA.url}/storage/v1/object/phone-files/${UID}/${H}.png`,
      `${SUPA.url}/storage/v1/object/authenticated/phone-files/${UID}/${H}.png`,
      `${SUPA.url}/storage/v1/object/public/phone-files/${UID}/${H}.png`,
      `${SUPA.url}/storage/v1/object/list/phone-files`,
      `${SUPA.url}/storage/v1/object/phone-files`,
      `${SUPA.url}/storage/v1/bucket`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${H}.png#frag`,
      `${SUPA.url}/storage/v1/object/sign/phone-files/${UID}/${H}.png;x`,
      `http://example.supabase.co/storage/v1/object/sign/phone-files/${UID}/${H}.png`,
      `https://evil.example.com/storage/v1/object/sign/phone-files/${UID}/${H}.png`,
      `https://user@${new URL(SUPA.url).host}/storage/v1/object/sign/phone-files/${UID}/${H}.png`,
    ];
    for (const url of bad) {
      expect(() => validateSupabaseUrl(url), url).toThrow(NetworkPolicyError);
      expect(() => validateCloudFileUrl(url, UID), url).toThrow(NetworkPolicyError);
    }
  });

  it('keeps the signing path to the signed-in owner and the download to a token query', () => {
    expect(() => validatePhoneCopySignUrl(`${SIGN_BASE}/${OTHER}/${H}.png`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${OTHER}/${H}.png?token=${TOKEN}`, UID)).toThrow(NetworkPolicyError);
    expect(() => validatePhoneCopySignUrl(`${SIGN_BASE}/${UID}/${H}.png?token=${TOKEN}`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?token=`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?token=a b`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?token=${TOKEN}&download=x`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?other=${TOKEN}`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?token=${'x'.repeat(5000)}`, UID)).toThrow(NetworkPolicyError);
    expect(() => validateCloudFileUrl(`${SIGN_BASE}/${UID}/${H}.png?token=${TOKEN}`, '')).toThrow(NetworkPolicyError);
  });

  it('leaves every earlier Supabase path exactly as narrow as before', () => {
    expect(validateSupabaseUrl(`${SUPA.url}/rest/v1/phone_files?owner_id=eq.${UID}`)).toContain('/rest/v1/phone_files');
    expect(() => validateSupabaseUrl(`${SUPA.url}/storage/v1/object/previews/${UID}/x.webp`)).toThrow(NetworkPolicyError);
    expect(() => validateSupabaseUrl(`${SUPA.url}/functions/v1/other`)).toThrow(NetworkPolicyError);
  });
});

describe('sharing a signed cloud file natively', () => {
  it('passes only the validated URL and a title, never a credential', async () => {
    const calls: ShareCloudFileOptions[] = [];
    const bridge = createPreviewNativeBridge({ onShareCloudFile: async (o) => void calls.push(o) });
    await netShareCloudFile(`${SIGN_BASE}/${UID}/${H}.png?token=${TOKEN}`, 'Hero', UID, bridge);
    expect(calls).toEqual([{ url: `${SIGN_BASE}/${UID}/${H}.png?token=${TOKEN}`, title: 'Hero' }]);
    expect(JSON.stringify(calls)).not.toMatch(/authorization|bearer|jwt/i);
  });

  it('refuses a bad URL before the native layer is reached', async () => {
    const calls: ShareCloudFileOptions[] = [];
    const bridge = createPreviewNativeBridge({ onShareCloudFile: async (o) => void calls.push(o) });
    await expect(netShareCloudFile(`https://evil.example.com/${H}.png?token=${TOKEN}`, 'x', UID, bridge)).rejects.toThrow(NetworkPolicyError);
    await expect(netShareCloudFile(`${SIGN_BASE}/${OTHER}/${H}.png?token=${TOKEN}`, 'x', UID, bridge)).rejects.toThrow(NetworkPolicyError);
    expect(calls).toEqual([]);
  });
});

describe('the list of copies', () => {
  async function bridgeWith(http: (req: NativeHttpRequest) => Promise<NativeHttpResponse>, uid = UID) {
    const bridge = createPreviewNativeBridge({ httpHandler: http, onShareCloudFile: async () => undefined });
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: `jwt-${uid}`, refresh_token: 'ref', expires_at: 9_999_999_999, user_id: uid, email: 'a@example.com' }),
    );
    return bridge;
  }

  it('asks only for this owner, and keeps only well-formed rows of this owner', async () => {
    const urls: string[] = [];
    const rows = [
      { ...copy(), owner_id: UID },
      { ...copy({ id: 'other-owner', storage_path: `${OTHER}/${H2}.png` }), owner_id: OTHER },
      { ...copy({ id: 'wrong-folder', storage_path: `${OTHER}/${H2}.png` }), owner_id: UID },
      { ...copy({ id: 'traversal', storage_path: `${UID}/../${OTHER}/${H}.png` }), owner_id: UID },
      { ...copy({ id: 'html', storage_path: `${UID}/${H}.html`, content_type: 'text/html' }), owner_id: UID },
      { ...copy({ id: 'huge', bytes: 60 * 1024 * 1024, storage_path: `${UID}/${H2}.png` }), owner_id: UID },
      { ...copy({ id: 'noexpiry', expires_at: 'soon', storage_path: `${UID}/${H2}.png` }), owner_id: UID },
      { id: 5, owner_id: UID },
      'junk',
      null,
    ];
    const bridge = await bridgeWith(async (req) => {
      urls.push(req.url);
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    });
    const got = await fetchPhoneCopies(UID, bridge);
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain('/rest/v1/phone_files?');
    expect(urls[0]).toContain(`owner_id=eq.${UID}`);
    expect(got.map((c) => c.id)).toEqual(['row-1']);
  });

  it('returns nothing (not an error) when the table is not there yet, and never for another account', async () => {
    const bridge = await bridgeWith(async () => ({ status: 404, headers: {}, body: JSON.stringify({ message: 'relation does not exist' }) }));
    expect(await fetchPhoneCopies(UID, bridge)).toEqual([]);
    const other = await bridgeWith(async () => ({ status: 200, headers: {}, body: '[]' }), OTHER);
    await expect(fetchPhoneCopies(UID, other)).resolves.toEqual([]);
  });

  it('works out size, expiry and which copies belong to a draft', () => {
    expect(formatCopySize(1_500_000)).toBe('1.4 MB');
    expect(formatCopySize(512)).toBe('512 B');
    expect(formatCopySize(48 * 1024 * 1024)).toBe('48.0 MB');
    const now = Date.parse('2026-11-23T10:00:00Z');
    expect(copyExpiryLabel(copy({ expires_at: '2026-11-30T10:00:00Z' }), now)).toBe('Copy kept for 7 more days');
    expect(copyExpiryLabel(copy({ expires_at: '2026-11-24T09:00:00Z' }), now)).toBe('Copy kept for 1 more day');
    expect(copyExpiryLabel(copy({ expires_at: '2026-11-23T14:00:00Z' }), now)).toBe('Copy expires today');
    expect(copyExpiryLabel(copy({ expires_at: '2026-11-23T09:59:59Z' }), now)).toBe('Copy expired');
    expect(isCopyUsable(copy({ expires_at: '2026-11-30T10:00:00Z' }), UID, now)).toBe(true);
    expect(isCopyUsable(copy({ expires_at: '2026-11-23T09:59:59Z' }), UID, now)).toBe(false);
    expect(isCopyUsable(copy({ storage_path: `${OTHER}/${H}.png` }), UID, now)).toBe(false);
    expect(copiesForDraft([copy(), copy({ id: 'b', content_id: 'cnt_2' })], 'cnt_2').map((c) => c.id)).toEqual(['b']);
  });
});

describe('getting a copy on the phone', () => {
  interface Seen {
    method: string;
    url: string;
    headers: Record<string, string>;
    body: unknown;
  }

  async function setup(answer: (req: NativeHttpRequest) => NativeHttpResponse | Promise<NativeHttpResponse>) {
    const seen: Seen[] = [];
    const shared: ShareCloudFileOptions[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        seen.push({ method: req.method, url: req.url, headers: req.headers ?? {}, body: req.body ? JSON.parse(req.body) : null });
        return answer(req);
      },
      onShareCloudFile: async (o) => void shared.push(o),
    });
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: `jwt-${UID}`, refresh_token: 'ref', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
    );
    return { bridge, seen, shared };
  }

  const signed = (copyRow: PhoneCopy, token = TOKEN) => ({
    status: 200,
    headers: {},
    body: JSON.stringify({ signedURL: `/object/sign/phone-files/${copyRow.storage_path}?token=${token}` }),
  });

  it('signs a short link for exactly that object and hands the signed URL to the native share', async () => {
    const c = copy();
    const { bridge, seen, shared } = await setup(() => signed(c));
    await getCopyOnPhone(UID, c, 'Cero launch', bridge);
    expect(seen).toHaveLength(1);
    expect(seen[0].method).toBe('POST');
    expect(seen[0].url).toBe(`${SIGN_BASE}/${c.storage_path}`);
    expect(seen[0].headers['Authorization']).toBe(`Bearer jwt-${UID}`);
    const expiresIn = (seen[0].body as { expiresIn: number }).expiresIn;
    expect(expiresIn).toBeGreaterThan(0);
    expect(expiresIn).toBeLessThanOrEqual(300);
    expect(shared).toEqual([{ url: `${SIGN_BASE}/${c.storage_path}?token=${TOKEN}`, title: 'Cero launch' }]);
  });

  it('never asks for a link that outlives the copy', async () => {
    const c = copy({ expires_at: new Date(Date.now() + 90_000).toISOString() });
    const { bridge, seen } = await setup(() => signed(c));
    await getCopyOnPhone(UID, c, 'x', bridge);
    expect((seen[0].body as { expiresIn: number }).expiresIn).toBeLessThanOrEqual(91);
  });

  it('refuses an expired copy, another owner\'s copy and a forged path without any request', async () => {
    const { bridge, seen, shared } = await setup(() => signed(copy()));
    const bad = [
      copy({ expires_at: new Date(Date.now() - 1000).toISOString() }),
      copy({ storage_path: `${OTHER}/${H}.png` }),
      copy({ storage_path: `${UID}/../${OTHER}/${H}.png` }),
      copy({ storage_path: `${UID}/${H}.html` }),
      copy({ storage_path: `previews/${UID}/${H}.png` }),
      copy({ storage_path: '' }),
    ];
    for (const c of bad) {
      await expect(getCopyOnPhone(UID, c, 'x', bridge)).rejects.toThrow(PhoneCopyError);
    }
    await expect(getCopyOnPhone(OTHER, copy(), 'x', bridge)).rejects.toThrow(PhoneCopyError);
    expect(seen).toEqual([]);
    expect(shared).toEqual([]);
  });

  it('says so when the cloud no longer has the copy, and when it is expired on the server', async () => {
    const c = copy();
    const { bridge, shared } = await setup(() => ({ status: 404, headers: {}, body: JSON.stringify({ message: 'Object not found' }) }));
    await expect(getCopyOnPhone(UID, c, 'x', bridge)).rejects.toThrow(/no longer|not available|expired/i);
    expect(shared).toEqual([]);
  });

  it('refuses a signed link that points anywhere else than the object that was asked for', async () => {
    const c = copy();
    const answers = [
      `/object/sign/phone-files/${UID}/${H2}.png?token=${TOKEN}`,
      `/object/sign/phone-files/${OTHER}/${H}.png?token=${TOKEN}`,
      `/object/sign/previews/${UID}/${H}.png?token=${TOKEN}`,
      `https://evil.example.com/object/sign/phone-files/${c.storage_path}?token=${TOKEN}`,
      `//evil.example.com/object/sign/phone-files/${c.storage_path}?token=${TOKEN}`,
      `/object/sign/phone-files/${c.storage_path}`,
      `/object/sign/phone-files/${c.storage_path}?token=${TOKEN}&x=1`,
      `/object/sign/phone-files/${c.storage_path}?token=a b`,
      `../object/sign/phone-files/${c.storage_path}?token=${TOKEN}`,
      '',
    ];
    for (const signedURL of answers) {
      const { bridge, shared } = await setup(() => ({ status: 200, headers: {}, body: JSON.stringify({ signedURL }) }));
      await expect(getCopyOnPhone(UID, c, 'x', bridge), signedURL).rejects.toThrow();
      expect(shared, signedURL).toEqual([]);
    }
    const { bridge, shared } = await setup(() => ({ status: 200, headers: {}, body: JSON.stringify({ nope: true }) }));
    await expect(getCopyOnPhone(UID, c, 'x', bridge)).rejects.toThrow();
    expect(shared).toEqual([]);
  });

  it('refuses redirects and sends no request when signed out', async () => {
    const c = copy();
    const redirect = await setup(() => ({ status: 302, headers: { location: 'https://evil.example.com' }, body: '' }));
    await expect(getCopyOnPhone(UID, c, 'x', redirect.bridge)).rejects.toThrow();
    expect(redirect.shared).toEqual([]);
    const seen: string[] = [];
    const bridge = createPreviewNativeBridge({ httpHandler: async (req) => (seen.push(req.url), signed(c)), onShareCloudFile: async () => undefined });
    await expect(getCopyOnPhone(UID, c, 'x', bridge)).rejects.toThrow();
    expect(seen).toEqual([]);
  });
});
