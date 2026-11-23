// Library on the phone: add an image, a video or a PDF to the owner's Library without the laptop, and see the
// Library (the phone's own waiting uploads and the laptop's assets).
//
// An upload is untrusted data from the start: the type is decided by the first bytes (never by the name), the size is
// capped at 50 MB, a name or note naming a confidential product is never sent, and the file goes only to the owner's
// own folder of the private phone-uploads bucket (14 days, shared 1 GB budget). The laptop then pulls it into its
// Library through a library_upload phone action and removes the cloud copy. Nothing here ever opens or runs a file.

import { enqueueAction, MAX_WAITING_ACTIONS, loadActions } from './actions';
import { getStoredSession, getValidAccessToken } from './auth';
import type { ContextCacheState } from './cache';
import { getNativeBridge, type NativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, netJson, netShareLaptopFile, netUploadStagedFile, type NetworkCredentials } from './net';
import { getLaptopToken } from './pairing';
import { findConfidentialMention } from './productRules';
import { getStateGeneration } from './storage';
import type { PairingConfig, PhoneAction, ProductRulesConfig } from './types';
import { LIBRARY_CATEGORIES, MAX_DESCRIPTION_CHARS } from './workRules';

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const UPLOAD_CHUNK_BYTES = 384 * 1024;
export const MAX_FILE_NAME_CHARS = 120;
const MAX_UPLOADS_LISTED = 100;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ASSET_ID_RE = /^asset_[A-Za-z0-9_]{1,100}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/g;

export class UploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UploadError';
  }
}

export type UploadKind = 'image' | 'video' | 'pdf';

export interface UploadCheck {
  kind: UploadKind;
  contentType: string;
  /** The extension of the object name (decided by the bytes). */
  ext: 'png' | 'jpg' | 'webp' | 'mp4' | 'webm' | 'pdf';
  /** The name shown in the Library: rebuilt from safe characters, with the extension the bytes proved. */
  fileName: string;
}

/** A file the user picked, as far as this module needs it (a browser File satisfies it). */
export interface UploadableFile {
  name: string;
  size: number;
  type?: string;
  slice(start: number, end: number): Blob | { arrayBuffer(): Promise<ArrayBuffer> };
}

/** The bytes of one slice of a picked file (an older WebView has no Blob.arrayBuffer, so FileReader is the fallback). */
async function sliceBytes(file: UploadableFile, start: number, end: number): Promise<Uint8Array> {
  const part = file.slice(start, end);
  if ('arrayBuffer' in part && typeof part.arrayBuffer === 'function') return new Uint8Array(await part.arrayBuffer());
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new UploadError('The file could not be read.'));
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.readAsArrayBuffer(part as Blob);
  });
}

export interface PhoneUpload {
  id: string;
  file_name: string;
  kind: UploadKind;
  content_type: string;
  bytes: number;
  expires_at: string;
}

export interface LibraryAsset {
  id: string;
  name: string;
  fileGroup: string;
  mimeType: string;
  sizeBytes: number;
  category: string;
  description: string;
  fromPhone: boolean;
  uploadedAt: string | null;
  /** True when the laptop can send this file (an image, a video or a PDF). */
  fetchable: boolean;
  confidential: boolean;
}

function startsWith(head: Uint8Array, bytes: number[], at = 0): boolean {
  return bytes.every((b, i) => head[at + i] === b);
}

/** What the first bytes say the file is, or null. */
function sniff(head: Uint8Array): Omit<UploadCheck, 'fileName'> | null {
  if (startsWith(head, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return { kind: 'image', contentType: 'image/png', ext: 'png' };
  if (startsWith(head, [0xff, 0xd8, 0xff])) return { kind: 'image', contentType: 'image/jpeg', ext: 'jpg' };
  if (startsWith(head, [0x52, 0x49, 0x46, 0x46]) && startsWith(head, [0x57, 0x45, 0x42, 0x50], 8)) {
    return { kind: 'image', contentType: 'image/webp', ext: 'webp' };
  }
  if (startsWith(head, [0x66, 0x74, 0x79, 0x70], 4)) return { kind: 'video', contentType: 'video/mp4', ext: 'mp4' };
  if (startsWith(head, [0x1a, 0x45, 0xdf, 0xa3])) return { kind: 'video', contentType: 'video/webm', ext: 'webm' };
  const first = new TextDecoder('latin1').decode(head.slice(0, 1024));
  if (first.includes('%PDF-')) return { kind: 'pdf', contentType: 'application/pdf', ext: 'pdf' };
  return null;
}

const NAME_EXTS: Record<string, UploadCheck['ext']> = {
  png: 'png', jpg: 'jpg', jpeg: 'jpg', webp: 'webp', mp4: 'mp4', m4v: 'mp4', webm: 'webm', pdf: 'pdf',
};

/** Decide what a picked file is. The name's extension and the first bytes must agree; the size must be 1 byte to 50 MB. */
export function checkUploadFile(name: string, size: number, head: Uint8Array): UploadCheck {
  if (!Number.isFinite(size) || size < 1) throw new UploadError('That file is empty.');
  if (size > MAX_UPLOAD_BYTES) throw new UploadError('Files can be at most 50 MB.');
  const base = String(name ?? '').split(/[\\/]/).pop() ?? '';
  const dot = base.lastIndexOf('.');
  const nameExt = dot > 0 ? NAME_EXTS[base.slice(dot + 1).toLowerCase()] : undefined;
  const found = sniff(head);
  if (!found || !nameExt || nameExt !== found.ext) {
    throw new UploadError('Only images (PNG, JPEG, WebP), videos (MP4, WebM) and PDFs can be added.');
  }
  const stem = (dot > 0 ? base.slice(0, dot) : base)
    .replace(CONTROL_RE, '')
    .replace(/[^A-Za-z0-9 ._()-]/g, '_')
    .replace(/^[ ._]+|[ ._]+$/g, '')
    .slice(0, MAX_FILE_NAME_CHARS - 5)
    .replace(/[ ._]+$/g, '');
  return { ...found, fileName: `${stem || 'upload'}.${found.ext}` };
}

function newUuid(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID().toLowerCase();
  const b = new Uint8Array(16);
  globalThis.crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function randomHex64(): string {
  const b = new Uint8Array(32);
  globalThis.crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function cloudSession(
  ownerUid: string,
  bridge: NativeBridge,
): Promise<{ creds: NetworkCredentials; url: string }> {
  const uid = ownerUid.trim();
  const session = uid ? await getStoredSession(bridge) : null;
  if (!session || session.user_id !== uid) throw new UploadError('Sign in to use your Library.');
  const token = await getValidAccessToken(bridge);
  const again = await getStoredSession(bridge);
  if (!token || !again || again.user_id !== uid) throw new UploadError('Sign in to use your Library.');
  const cfg = getPublicSupabaseConfig();
  return { url: cfg.url, creds: { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token } };
}

function cleanUpload(raw: unknown, ownerUid: string): PhoneUpload | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== 'string' || !UUID_RE.test(r.id) || r.owner_id !== ownerUid) return null;
  if (typeof r.file_name !== 'string' || r.file_name.length < 1 || r.file_name.length > 200 || /[\x00-\x1f\x7f/\\]/.test(r.file_name)) return null;
  if (r.kind !== 'image' && r.kind !== 'video' && r.kind !== 'pdf') return null;
  if (typeof r.content_type !== 'string' || typeof r.bytes !== 'number' || !Number.isInteger(r.bytes) || r.bytes < 1 || r.bytes > MAX_UPLOAD_BYTES) return null;
  const folder = typeof r.storage_path === 'string' ? r.storage_path.split('/')[0] : '';
  if (folder !== ownerUid || typeof r.expires_at !== 'string' || !Number.isFinite(Date.parse(r.expires_at))) return null;
  return { id: r.id, file_name: r.file_name, kind: r.kind, content_type: r.content_type, bytes: r.bytes, expires_at: r.expires_at };
}

/** This account's own waiting uploads (never another account's), newest first. An older cloud without the table gives none. */
export async function fetchPhoneUploads(ownerUid: string, bridge: NativeBridge = getNativeBridge()): Promise<PhoneUpload[]> {
  const uid = ownerUid.trim();
  if (!uid) return [];
  const gen = getStateGeneration();
  const cloud = await cloudSession(uid, bridge);
  let rows: unknown;
  try {
    rows = await netJson<unknown>(
      {
        destination: 'supabase',
        method: 'GET',
        url: `${cloud.url}/rest/v1/phone_uploads?owner_id=eq.${encodeURIComponent(uid)}&select=id,owner_id,file_name,kind,content_type,bytes,storage_path,expires_at&order=created_at.desc&limit=${MAX_UPLOADS_LISTED}`,
      },
      cloud.creds,
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && err.status === 404) return [];
    throw err;
  }
  if (gen !== getStateGeneration() || !Array.isArray(rows)) return [];
  return rows.map((r) => cleanUpload(r, uid)).filter((u): u is PhoneUpload => u !== null);
}

/** Remove one of this account's own upload rows (only used to undo an upload that did not finish). */
export async function deletePhoneUpload(ownerUid: string, id: string, bridge: NativeBridge = getNativeBridge()): Promise<void> {
  if (!UUID_RE.test(id)) throw new UploadError('That upload is not valid.');
  const uid = ownerUid.trim();
  const cloud = await cloudSession(uid, bridge);
  await netJson<unknown>(
    {
      destination: 'supabase',
      method: 'DELETE',
      url: `${cloud.url}/rest/v1/phone_uploads?id=eq.${id}&owner_id=eq.${encodeURIComponent(uid)}`,
      headers: { Prefer: 'return=minimal' },
    },
    cloud.creds,
    bridge,
  );
}

export interface UploadRequest {
  ownerUid: string;
  file: UploadableFile;
  category: string;
  description?: string;
  rules: ProductRulesConfig | null;
  bridge?: NativeBridge;
}

function mentionsConfidential(rules: ProductRulesConfig | null, ...texts: string[]): void {
  const present = texts.filter((t) => t.trim());
  if (present.length === 0) return;
  if (!rules) throw new UploadError('Product rules are not available on this phone yet, so this cannot be uploaded safely. Connect and try again.');
  for (const t of present) {
    if (findConfidentialMention(t, rules)) throw new UploadError('This names a confidential product, so it stays on the laptop.');
  }
}

/**
 * Upload one file and queue the Library action. Everything is checked before anything leaves the phone. If a later
 * step fails, the cloud row is removed again and nothing is queued; the message says what happened.
 */
export async function uploadFromPhone(req: UploadRequest): Promise<PhoneAction> {
  const bridge = req.bridge ?? getNativeBridge();
  const uid = req.ownerUid.trim();
  if (!uid) throw new UploadError('Sign in to use your Library.');
  if (!LIBRARY_CATEGORIES.includes(req.category as never)) throw new UploadError('Choose a Library category.');
  const description = (req.description ?? '').trim();
  if (description.length > MAX_DESCRIPTION_CHARS || /[\x00-\x1f\x7f]/.test(description)) {
    throw new UploadError(`A note can have at most ${MAX_DESCRIPTION_CHARS} characters on one line.`);
  }
  if (loadActions(uid).filter((a) => a.state === 'waiting').length >= MAX_WAITING_ACTIONS) {
    throw new UploadError('Too many actions are waiting. Wait for the laptop to catch up.');
  }
  const head = await sliceBytes(req.file, 0, 1024);
  const check = checkUploadFile(req.file.name, req.file.size, head);
  mentionsConfidential(req.rules, check.fileName, req.file.name, description);

  const gen = getStateGeneration();
  const cloud = await cloudSession(uid, bridge);
  const id = newUuid();
  const storagePath = `${uid}/${randomHex64()}.${check.ext}`;
  const sizeBytes = req.file.size;
  try {
    await netJson<unknown>(
      {
        destination: 'supabase',
        method: 'POST',
        url: `${cloud.url}/rest/v1/phone_uploads`,
        headers: { Prefer: 'return=minimal' },
        body: { id, file_name: check.fileName, kind: check.kind, content_type: check.contentType, bytes: sizeBytes, storage_path: storagePath },
      },
      cloud.creds,
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && /budget|storage/i.test(err.message)) {
      throw new UploadError('The cloud storage is full, so this file cannot be uploaded. Free some space or try later.');
    }
    if (err instanceof HttpStatusError && /too many/i.test(err.message)) throw new UploadError(err.message);
    throw err;
  }

  const abortAndForget = async (cause: unknown): Promise<never> => {
    let tail = '';
    try {
      await bridge.abortUpload?.({ uploadId: id });
      await deletePhoneUpload(uid, id, bridge);
    } catch {
      tail = ' The unfinished upload will be removed by itself after 14 days.';
    }
    const base = cause instanceof Error && cause.message ? cause.message : 'The upload failed.';
    throw new UploadError(`${base}${tail}`);
  };

  try {
    if (typeof bridge.stageUploadChunk !== 'function') throw new UploadError('Uploading files is only available in the Android app.');
    for (let offset = 0, index = 0; offset < sizeBytes; offset += UPLOAD_CHUNK_BYTES, index += 1) {
      if (gen !== getStateGeneration()) throw new UploadError('The account changed, so the upload was stopped.');
      const bytes = await sliceBytes(req.file, offset, Math.min(offset + UPLOAD_CHUNK_BYTES, sizeBytes));
      await bridge.stageUploadChunk({ uploadId: id, index, base64: toBase64(bytes) });
    }
    const resp = await netUploadStagedFile(
      { url: `${cloud.url}/storage/v1/object/phone-uploads/${storagePath}`, uploadId: id, contentType: check.contentType, bytes: sizeBytes },
      cloud.creds,
      uid,
      bridge,
    );
    if (resp.status < 200 || resp.status >= 300) throw new UploadError(`The cloud refused the file (${resp.status}).`);
  } catch (err) {
    return abortAndForget(err);
  }

  try {
    return enqueueAction(
      { kind: 'library_upload', target_id: id, payload: description ? { category: req.category, description } : { category: req.category }, label: check.fileName },
      uid,
      req.rules,
    );
  } catch (err) {
    return abortAndForget(err);
  }
}

// --- The Library as the phone shows it -------------------------------------------------------------------------------

function str(v: unknown, limit: number): string {
  return typeof v === 'string' ? v.replace(CONTROL_RE, ' ').slice(0, limit) : '';
}

function assetOf(id: string, data: Record<string, unknown>): LibraryAsset {
  if (data.confidential === true) {
    return { id, name: 'Confidential item', fileGroup: 'document', mimeType: '', sizeBytes: 0, category: '', description: '', fromPhone: false, uploadedAt: null, fetchable: false, confidential: true };
  }
  const size = data.size_bytes;
  return {
    id,
    name: str(data.name, 200) || 'Untitled',
    fileGroup: str(data.file_group, 20) || 'document',
    mimeType: str(data.mime_type, 100),
    sizeBytes: typeof size === 'number' && Number.isFinite(size) && size >= 0 ? size : 0,
    category: str(data.category, 40),
    description: str(data.description, MAX_DESCRIPTION_CHARS),
    fromPhone: data.from_phone === true,
    uploadedAt: str(data.uploaded_at, 40) || null,
    fetchable: data.fetchable === true,
    confidential: false,
  };
}

/** The laptop's assets from this account's cloud mirror (names and sizes, never a path). */
export function assetsFromMirror(cache: ContextCacheState): LibraryAsset[] {
  const owner = cache.ownerUid;
  if (!owner) return [];
  return cache.mirrorRecords
    .filter((r) => r.kind === 'asset' && r.owner_id === owner && typeof r.local_id === 'string' && ASSET_ID_RE.test(r.local_id))
    .map((r) => assetOf(r.local_id, r.data && typeof r.data === 'object' ? r.data : {}))
    .sort((a, b) => String(b.uploadedAt ?? '').localeCompare(String(a.uploadedAt ?? '')));
}

async function laptopCreds(pairing: PairingConfig, bridge: NativeBridge): Promise<NetworkCredentials> {
  return { laptop: { host: pairing.host, port: pairing.port }, laptopToken: await getLaptopToken(bridge) };
}

/** The live Library from the paired laptop. */
export async function fetchLaptopLibrary(pairing: PairingConfig, bridge: NativeBridge = getNativeBridge()): Promise<LibraryAsset[]> {
  const res = await netJson<{ assets?: unknown }>(
    { destination: 'laptop', method: 'GET', url: `http://${pairing.host}:${pairing.port}/phone/v1/library`, timeoutMs: 8000 },
    await laptopCreds(pairing, bridge),
    bridge,
  );
  const list = Array.isArray(res?.assets) ? res.assets : [];
  const out: LibraryAsset[] = [];
  for (const raw of list) {
    if (!raw || typeof raw !== 'object') continue;
    const r = raw as Record<string, unknown>;
    if (typeof r.id !== 'string' || !ASSET_ID_RE.test(r.id)) continue;
    out.push(assetOf(r.id, r));
  }
  return out;
}

/** Ask the paired laptop for one Library file, by its asset id only, and hand it to the share sheet. */
export async function shareLaptopAsset(
  pairing: PairingConfig,
  assetId: string,
  title: string | undefined,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  if (!ASSET_ID_RE.test(assetId)) throw new UploadError('That file is not valid.');
  const url = `http://${pairing.host}:${pairing.port}/phone/v1/files?asset_id=${encodeURIComponent(assetId)}`;
  await netShareLaptopFile(url, title, await laptopCreds(pairing, bridge), bridge);
}
