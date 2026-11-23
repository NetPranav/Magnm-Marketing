// "Get on phone": full-quality copies of finished draft files that the laptop keeps in the cloud for 14 days.
//
// The list comes from the signed-in account's own phone_files rows (/rest/v1/phone_files). Getting a copy asks the
// cloud for a short signed link to exactly one object, checks that the link points at that object and nothing else,
// and hands it to the native share sheet. Nothing here is ever sent anywhere else, and a row that is not this
// account's, not well formed or expired is never used.

import { getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import {
  HttpStatusError,
  getPublicSupabaseConfig,
  netJson,
  netShareCloudFile,
  validateCloudFileUrl,
  validatePhoneCopySignUrl,
} from './net';
import { getStateGeneration } from './storage';

export type PhoneCopyKind = 'image' | 'video' | 'pdf';

export interface PhoneCopy {
  id: string;
  content_id: string;
  file_path: string;
  file_name: string;
  kind: PhoneCopyKind;
  content_type: string;
  bytes: number;
  storage_path: string;
  expires_at: string;
}

export class PhoneCopyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PhoneCopyError';
  }
}

export const MAX_COPY_BYTES = 50 * 1024 * 1024;
export const SIGNED_LINK_SECONDS = 300;
const MAX_COPIES = 500;
const DAY_MS = 86_400_000;
const STORAGE_PATH_RE =
  /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/[0-9a-f]{64}\.(?:png|jpg|jpeg|webp|mp4|webm|pdf)$/;
const CONTENT_TYPES: Record<PhoneCopyKind, readonly string[]> = {
  image: ['image/png', 'image/jpeg', 'image/webp'],
  video: ['video/mp4', 'video/webm'],
  pdf: ['application/pdf'],
};
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/;

function cleanCopy(raw: unknown, ownerUid: string): PhoneCopy | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const { id, owner_id, content_id, file_path, file_name, kind, content_type, bytes, storage_path, expires_at } = r;
  if (typeof id !== 'string' || !id || id.length > 64) return null;
  if (owner_id !== ownerUid) return null;
  if (typeof content_id !== 'string' || !content_id || content_id.length > 200 || CONTROL_RE.test(content_id)) return null;
  if (typeof file_path !== 'string' || !file_path || file_path.length > 500 || CONTROL_RE.test(file_path)) return null;
  if (typeof file_name !== 'string' || !file_name || file_name.length > 200 || CONTROL_RE.test(file_name) || /[\\/]/.test(file_name)) {
    return null;
  }
  if (kind !== 'image' && kind !== 'video' && kind !== 'pdf') return null;
  if (typeof content_type !== 'string' || !CONTENT_TYPES[kind].includes(content_type)) return null;
  if (typeof bytes !== 'number' || !Number.isInteger(bytes) || bytes < 1 || bytes > MAX_COPY_BYTES) return null;
  if (typeof storage_path !== 'string') return null;
  const folder = STORAGE_PATH_RE.exec(storage_path)?.[1];
  if (folder !== ownerUid) return null;
  if (typeof expires_at !== 'string' || !Number.isFinite(Date.parse(expires_at))) return null;
  return { id, content_id, file_path, file_name, kind, content_type, bytes, storage_path, expires_at };
}

/** This account's copies (never another account's), newest first. An older cloud without the table gives none. */
export async function fetchPhoneCopies(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<PhoneCopy[]> {
  const uid = (ownerUid ?? '').trim();
  if (!uid) return [];
  const gen = getStateGeneration();
  const session = await getStoredSession(bridge);
  if (!session || session.user_id !== uid) return [];
  const token = await getValidAccessToken(bridge);
  if (!token) return [];
  const cfg = getPublicSupabaseConfig();
  let rows: unknown;
  try {
    rows = await netJson<unknown>(
      {
        destination: 'supabase',
        method: 'GET',
        url:
          `${cfg.url}/rest/v1/phone_files?owner_id=eq.${encodeURIComponent(uid)}` +
          `&select=id,owner_id,content_id,file_path,file_name,kind,content_type,bytes,storage_path,expires_at` +
          `&order=created_at.desc&limit=${MAX_COPIES}`,
      },
      { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token },
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && err.status === 404) return [];
    throw err;
  }
  if (gen !== getStateGeneration() || !Array.isArray(rows)) return [];
  return rows.map((row) => cleanCopy(row, uid)).filter((c): c is PhoneCopy => c !== null);
}

export function copiesForDraft(copies: PhoneCopy[], contentId: string): PhoneCopy[] {
  return copies.filter((c) => c.content_id === contentId);
}

/** True if the copy is this account's, well formed and not expired. */
export function isCopyUsable(copy: PhoneCopy, ownerUid: string, now: number = Date.now()): boolean {
  const folder = STORAGE_PATH_RE.exec(copy?.storage_path ?? '')?.[1];
  if (!folder || folder !== (ownerUid ?? '').trim().toLowerCase()) return false;
  const expires = Date.parse(copy.expires_at);
  return Number.isFinite(expires) && expires > now;
}

export function formatCopySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function copyExpiryLabel(copy: PhoneCopy, now: number = Date.now()): string {
  const left = Date.parse(copy.expires_at) - now;
  if (!Number.isFinite(left) || left <= 0) return 'Copy expired';
  if (left < DAY_MS / 2) return 'Copy expires today';
  const days = Math.ceil(left / DAY_MS);
  return `Copy kept for ${days} more ${days === 1 ? 'day' : 'days'}`;
}

/**
 * Download a cloud copy and offer it to save or share. Signs a short link for exactly this object, checks the link
 * points at it (same origin, same path, one token) and gives it to the native share sheet.
 */
export async function getCopyOnPhone(
  ownerUid: string,
  copy: PhoneCopy,
  title: string | undefined,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const uid = (ownerUid ?? '').trim();
  if (!uid || !isCopyUsable(copy, uid)) {
    throw new PhoneCopyError(
      Date.parse(copy?.expires_at ?? '') <= Date.now() ? 'This copy has expired.' : 'This copy is not available.',
    );
  }
  const gen = getStateGeneration();
  const session = await getStoredSession(bridge);
  if (!session || session.user_id !== uid) {
    throw new PhoneCopyError('Sign in to get this file from the cloud.');
  }
  const token = await getValidAccessToken(bridge);
  if (!token) throw new PhoneCopyError('Sign in to get this file from the cloud.');
  const cfg = getPublicSupabaseConfig();
  const signUrl = validatePhoneCopySignUrl(`${cfg.url}/storage/v1/object/sign/phone-files/${copy.storage_path}`, uid);
  const remainingS = Math.floor((Date.parse(copy.expires_at) - Date.now()) / 1000);
  let answer: unknown;
  try {
    answer = await netJson<unknown>(
      {
        destination: 'supabase',
        method: 'POST',
        url: signUrl,
        body: { expiresIn: Math.max(1, Math.min(SIGNED_LINK_SECONDS, remainingS)) },
      },
      { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token },
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && (err.status === 400 || err.status === 404)) {
      throw new PhoneCopyError('This copy is no longer in the cloud. Open the file from your laptop instead.');
    }
    throw err;
  }
  if (gen !== getStateGeneration()) throw new PhoneCopyError('The account changed. Try again.');
  const signed =
    answer && typeof answer === 'object'
      ? (answer as { signedURL?: unknown; signedUrl?: unknown }).signedURL ?? (answer as { signedUrl?: unknown }).signedUrl
      : null;
  if (typeof signed !== 'string' || !signed.startsWith('/object/sign/phone-files/')) {
    throw new PhoneCopyError('The cloud gave an unusable link.');
  }
  const link = validateCloudFileUrl(`${cfg.url}/storage/v1${signed}`, uid);
  // The link must be the very object that was asked for, nothing else.
  if (!link.startsWith(`${cfg.url}/storage/v1/object/sign/phone-files/${copy.storage_path}?token=`)) {
    throw new PhoneCopyError('The cloud gave a link to a different file.');
  }
  await netShareCloudFile(link, title, uid, bridge);
}
