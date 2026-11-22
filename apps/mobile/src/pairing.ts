import { wipeLaptopSnapshot } from './cache';
import { getNativeBridge, type NativeBridge } from './native';
import { netJson, parseStrictPort, parseStrictTailscaleIp } from './net';
import { wipeQueue } from './queue';
import {
  bumpStateGeneration,
  getStateGeneration,
  readNonSecretJson,
  removeNonSecretKey,
  writeNonSecretJson,
} from './storage';
import type { PairingConfig, ParsedQrPayload } from './types';

export const QR_PREFIX = 'magnm-pair:';
export const PAIRING_META_KEY = 'magnm.mobile.pairingMeta.v1';
export const LAPTOP_TOKEN_SECRET_KEY = 'magnm.laptop.deviceToken';

const CONTROL_CHARS_RE = /[\x00-\x1f\x7f]+/g;

function decodeBase64UrlUtf8(encoded: string): string {
  const padded = encoded + '='.repeat((4 - (encoded.length % 4)) % 4);
  const standard = padded.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(standard);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder().decode(bytes);
}

/** Parse and strictly validate a magnm-pair:<base64url JSON> string. */
export function parsePairingPayload(rawText: string): ParsedQrPayload {
  const text = String(rawText ?? '').trim();
  if (!text.startsWith(QR_PREFIX)) {
    throw new Error('Pairing payload must start with "magnm-pair:".');
  }
  const encoded = text.slice(QR_PREFIX.length).trim();
  if (!encoded || !/^[A-Za-z0-9_-]+={0,2}$/.test(encoded)) {
    throw new Error('Invalid pairing code encoding.');
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(decodeBase64UrlUtf8(encoded));
  } catch {
    throw new Error('Could not decode pairing payload.');
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Invalid pairing payload format.');
  }
  const obj = parsed as Record<string, unknown>;
  if (obj.v !== 1) {
    throw new Error('Unsupported pairing payload version.');
  }
  const host = parseStrictTailscaleIp(String(obj.host ?? ''));
  const port = parseStrictPort(Number(obj.port));
  const code = typeof obj.code === 'string' ? obj.code.trim() : '';
  if (!code) {
    throw new Error('Pairing payload is missing a one-time code.');
  }

  return { v: 1, host, port, code };
}

export function sanitizeDeviceName(rawName: string): string {
  const cleaned = String(rawName ?? '')
    .replace(CONTROL_CHARS_RE, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.slice(0, 80).trim();
}

export function getPairingConfig(): PairingConfig | null {
  const raw = readNonSecretJson<PairingConfig>(PAIRING_META_KEY);
  if (!raw || typeof raw !== 'object') return null;
  try {
    const host = parseStrictTailscaleIp(raw.host);
    const port = parseStrictPort(Number(raw.port));
    if (!raw.deviceId || !raw.deviceName) return null;
    return {
      ...raw,
      host,
      port,
    };
  } catch {
    return null;
  }
}

export async function getLaptopToken(bridge: NativeBridge = getNativeBridge()): Promise<string | null> {
  return bridge.secureStore.get(LAPTOP_TOKEN_SECRET_KEY);
}

export interface PairRequestResult {
  request_id: string;
  poll_secret: string;
  expires_at: string;
}

export interface PairPollResult {
  status: 'pending' | 'denied' | 'approved' | 'cancelled';
  device_id?: string;
  device_name?: string;
  device_token?: string;
}

/** Submit a pairing request to POST /phone/v1/pair on the target laptop. */
export async function submitPairRequest(
  payload: ParsedQrPayload,
  deviceName: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<PairRequestResult> {
  const cleanName = sanitizeDeviceName(deviceName);
  if (!cleanName) {
    throw new Error('Please enter a name for this phone.');
  }
  return netJson<PairRequestResult>(
    {
      destination: 'laptop',
      method: 'POST',
      url: `http://${payload.host}:${payload.port}/phone/v1/pair`,
      requireAuth: false,
      body: {
        code: payload.code,
        device_name: cleanName,
      },
    },
    { laptop: { host: payload.host, port: payload.port } },
    bridge,
  );
}

/** Poll POST /phone/v1/pair/poll; when approved (and not cancelled), store credentials. */
export async function pollPairRequest(
  payload: ParsedQrPayload,
  requestId: string,
  pollSecret: string,
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
  isCancelled?: () => boolean,
): Promise<PairPollResult & { pairing?: PairingConfig }> {
  const gen = getStateGeneration();
  const cancelledNow = () => Boolean(isCancelled?.()) || gen !== getStateGeneration();
  if (cancelledNow()) {
    return { status: 'cancelled' };
  }

  const res = await netJson<PairPollResult>(
    {
      destination: 'laptop',
      method: 'POST',
      url: `http://${payload.host}:${payload.port}/phone/v1/pair/poll`,
      requireAuth: false,
      body: {
        request_id: requestId,
        poll_secret: pollSecret,
      },
    },
    { laptop: { host: payload.host, port: payload.port } },
    bridge,
  );

  if (cancelledNow()) {
    return { status: 'cancelled' };
  }

  if (res.status === 'approved') {
    if (!res.device_token || !res.device_id) {
      throw new Error('Laptop approval response was missing device credentials.');
    }
    const config: PairingConfig = {
      host: payload.host,
      port: payload.port,
      deviceId: res.device_id,
      deviceName: res.device_name || 'Phone',
      ownerUid: ownerUid.trim(),
      pairedAt: new Date().toISOString(),
    };
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, res.device_token);
    if (cancelledNow()) {
      await bridge.secureStore.remove(LAPTOP_TOKEN_SECRET_KEY);
      return { status: 'cancelled' };
    }
    writeNonSecretJson(PAIRING_META_KEY, config);
    return { ...res, pairing: config };
  }

  return res;
}

/** Unpair laptop and wipe device token, laptop snapshot, and offline queue. */
export async function unpairLaptop(bridge: NativeBridge = getNativeBridge()): Promise<void> {
  bumpStateGeneration();
  await bridge.secureStore.remove(LAPTOP_TOKEN_SECRET_KEY);
  removeNonSecretKey(PAIRING_META_KEY);
  wipeLaptopSnapshot();
  wipeQueue();
}
