import type { AppVersionInfo, NativeBridge } from './native';

/** One release's android.json, after every field was checked. */
export interface AndroidUpdateManifest {
  version: string;
  versionCode: number;
  url: string;
  sha256: string;
  bytes: number;
  notes: string;
}

export interface UpdateCheckResult {
  installed: AppVersionInfo;
  /** The newer release, or null when this phone already has the newest. */
  update: AndroidUpdateManifest | null;
}

/** A refusal with a plain sentence that can be shown as it is. */
export class UpdateCheckError extends Error {}

export const MAX_APK_BYTES = 250 * 1024 * 1024;
const MAX_MANIFEST_CHARS = 64 * 1024;
const MAX_NOTES_CHARS = 8000;
const VERSION = /^(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)$/;
const SHA256 = /^[0-9a-f]{64}$/;

/** The Android versionCode for X.Y.Z: major * 10000 + minor * 100 + patch (the same rule as scripts/set_version.py). */
export function versionCodeOf(version: string): number {
  const match = VERSION.exec(version);
  if (!match) throw new Error('The version is not written like 1.2.3.');
  return Number(match[1]) * 10000 + Number(match[2]) * 100 + Number(match[3]);
}

/** The one address a release's APK may have. Nothing else is accepted from android.json. */
export function apkUrlFor(version: string): string {
  versionCodeOf(version);
  return `https://github.com/NetPranav/Magnm-Marketing/releases/download/v${version}/magnm-studio-${version}.apk`;
}

function incomplete(): UpdateCheckError {
  return new UpdateCheckError('The update information is not valid, so nothing was downloaded.');
}

/** Read and check the text of android.json. Throws UpdateCheckError for anything that is not exactly right. */
export function parseAndroidManifest(text: string): AndroidUpdateManifest {
  if (typeof text !== 'string' || text.length === 0 || text.length > MAX_MANIFEST_CHARS) throw incomplete();
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw incomplete();
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) throw incomplete();
  const obj = raw as Record<string, unknown>;

  const version = obj.version;
  if (typeof version !== 'string' || !VERSION.test(version)) throw incomplete();
  const versionCode = obj.versionCode;
  if (typeof versionCode !== 'number' || !Number.isInteger(versionCode) || versionCode !== versionCodeOf(version)) {
    throw incomplete();
  }
  if (typeof obj.url !== 'string' || obj.url !== apkUrlFor(version)) throw incomplete();
  if (typeof obj.sha256 !== 'string' || !SHA256.test(obj.sha256)) throw incomplete();
  const bytes = obj.bytes;
  if (typeof bytes !== 'number' || !Number.isInteger(bytes) || bytes < 1 || bytes > MAX_APK_BYTES) throw incomplete();
  const notes = obj.notes === undefined ? '' : obj.notes;
  if (typeof notes !== 'string' || notes.length > MAX_NOTES_CHARS) throw incomplete();

  return { version, versionCode, url: obj.url, sha256: obj.sha256, bytes, notes };
}

/** Ask the latest release whether it is newer than the installed app. Never downloads anything. */
export async function checkForAppUpdate(bridge: NativeBridge): Promise<UpdateCheckResult> {
  if (!bridge.getAppVersion || !bridge.fetchUpdateManifest) {
    throw new UpdateCheckError('Updates can only be checked in the Android app.');
  }
  const installed = await bridge.getAppVersion();
  const text = await bridge.fetchUpdateManifest();
  const manifest = parseAndroidManifest(text);
  return { installed, update: manifest.versionCode > installed.versionCode ? manifest : null };
}
