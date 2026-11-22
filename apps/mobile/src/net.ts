import supabaseConfig from '@magnm/config/supabase.json';
import { getNativeBridge, type NativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';

export const NIM_BASE_URL = 'https://integrate.api.nvidia.com/v1';

export type NetworkDestination = 'laptop' | 'supabase' | 'nim';

export type NetworkErrorCode =
  | 'invalid_url'
  | 'forbidden_destination'
  | 'forbidden_header'
  | 'redirect_refused'
  | 'missing_credential'
  | 'unpaired_laptop';

export class NetworkPolicyError extends Error {
  readonly code: NetworkErrorCode;

  constructor(message: string, code: NetworkErrorCode) {
    super(message);
    this.name = 'NetworkPolicyError';
    this.code = code;
  }
}

export class HttpStatusError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(message: string, status: number, body = '') {
    super(message);
    this.name = 'HttpStatusError';
    this.status = status;
    this.body = body;
  }
}

export interface PairedLaptopEndpoint {
  host: string;
  port: number;
}

export interface NetworkCredentials {
  laptop?: PairedLaptopEndpoint | null;
  laptopToken?: string | null;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
  supabaseAccessToken?: string | null;
  nimPersonalKey?: string | null;
}

const STRICT_OCTET_RE = /^(0|[1-9]\d{0,2})$/;
const STRICT_PORT_RE = /^[1-9]\d{0,4}$/;
const CONTROL_OR_SPACE_RE = /[\s\x00-\x1f\x7f\\]/;
const ENCODED_TRAVERSAL_RE = /%(?:2e|2f|5c)/i;
const FORBIDDEN_CALLER_HEADERS = new Set([
  'origin',
  'cookie',
  'referer',
  'host',
  'authorization',
  'apikey',
  'x-forwarded-host',
  'x-forwarded-for',
]);

interface RawUrlParts {
  scheme: string;
  authority: string;
  host: string;
  portStr: string | null;
  path: string;
  query: string;
}

function splitRawUrl(rawUrl: string): RawUrlParts {
  if (typeof rawUrl !== 'string' || !rawUrl) {
    throw new NetworkPolicyError('URL must be a non-empty string.', 'invalid_url');
  }
  if (CONTROL_OR_SPACE_RE.test(rawUrl)) {
    throw new NetworkPolicyError('URL contains whitespace, backslash, or control characters.', 'invalid_url');
  }
  if (rawUrl.includes('#')) {
    throw new NetworkPolicyError('URL fragments are not permitted.', 'invalid_url');
  }

  const schemeIdx = rawUrl.indexOf('://');
  if (schemeIdx <= 0) {
    throw new NetworkPolicyError('URL must include an explicit scheme.', 'invalid_url');
  }
  const scheme = rawUrl.slice(0, schemeIdx).toLowerCase();
  const rest = rawUrl.slice(schemeIdx + 3);
  if (!rest) {
    throw new NetworkPolicyError('URL authority is missing.', 'invalid_url');
  }

  const slashIdx = rest.indexOf('/');
  const queryIdx = rest.indexOf('?');
  let authEnd = rest.length;
  if (slashIdx !== -1) authEnd = Math.min(authEnd, slashIdx);
  if (queryIdx !== -1) authEnd = Math.min(authEnd, queryIdx);

  const authority = rest.slice(0, authEnd);
  if (!authority) {
    throw new NetworkPolicyError('URL authority is empty.', 'invalid_url');
  }
  if (authority.includes('@')) {
    throw new NetworkPolicyError('Userinfo (user@host) is forbidden in URLs.', 'forbidden_destination');
  }
  if (authority.includes('[') || authority.includes(']')) {
    throw new NetworkPolicyError('IPv6 bracketed hosts are not allowed.', 'forbidden_destination');
  }

  const remainder = rest.slice(authEnd);
  const remQueryIdx = remainder.indexOf('?');
  const path = remQueryIdx === -1 ? remainder : remainder.slice(0, remQueryIdx);
  const query = remQueryIdx === -1 ? '' : remainder.slice(remQueryIdx);

  if (ENCODED_TRAVERSAL_RE.test(path)) {
    throw new NetworkPolicyError('Encoded dot/slash sequences are forbidden in paths.', 'forbidden_destination');
  }
  const segments = path.split('/');
  if (segments.some((seg) => seg === '.' || seg === '..')) {
    throw new NetworkPolicyError('Path traversal segments are forbidden.', 'forbidden_destination');
  }
  if (path.includes('//')) {
    throw new NetworkPolicyError('Empty path segments are forbidden.', 'forbidden_destination');
  }

  const colonIdx = authority.indexOf(':');
  if (colonIdx !== -1 && authority.indexOf(':', colonIdx + 1) !== -1) {
    throw new NetworkPolicyError('Malformed authority.', 'invalid_url');
  }
  const host = colonIdx === -1 ? authority : authority.slice(0, colonIdx);
  const portStr = colonIdx === -1 ? null : authority.slice(colonIdx + 1);
  if (!host) {
    throw new NetworkPolicyError('Host is empty.', 'invalid_url');
  }
  if (portStr !== null && !STRICT_PORT_RE.test(portStr)) {
    throw new NetworkPolicyError('Invalid port syntax.', 'invalid_url');
  }

  return { scheme, authority, host, portStr, path: path || '/', query };
}

/** Strictly parse an IPv4 address in 100.64.0.0/10 (rejects hex, octal, leading zeros, short IPs). */
export function parseStrictTailscaleIp(rawHost: string): string {
  const parts = String(rawHost || '').split('.');
  if (parts.length !== 4) {
    throw new NetworkPolicyError('Laptop host must be a dotted-quad IPv4 address.', 'forbidden_destination');
  }
  const octets: number[] = [];
  for (const part of parts) {
    if (!STRICT_OCTET_RE.test(part)) {
      throw new NetworkPolicyError(
        `Invalid IPv4 octet "${part}": leading zeros and non-decimal encodings are refused.`,
        'forbidden_destination',
      );
    }
    const num = Number(part);
    if (!Number.isInteger(num) || num < 0 || num > 255) {
      throw new NetworkPolicyError('IPv4 octet out of range.', 'forbidden_destination');
    }
    octets.push(num);
  }
  // 100.64.0.0/10 => 100.64.0.0 through 100.127.255.255
  if (octets[0] !== 100 || octets[1] < 64 || octets[1] > 127) {
    throw new NetworkPolicyError(
      'Laptop IP must be strictly inside Tailscale 100.64.0.0/10.',
      'forbidden_destination',
    );
  }
  return octets.join('.');
}

/** Validate strict port number 1..65535. */
export function parseStrictPort(port: number): number {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new NetworkPolicyError('Invalid laptop port.', 'forbidden_destination');
  }
  return port;
}

/** Validate a paired laptop URL against strict Tailscale IP, paired port, http scheme, and /phone/v1/ path. */
export function validateLaptopUrl(rawUrl: string, paired: PairedLaptopEndpoint): string {
  const expectedHost = parseStrictTailscaleIp(paired.host);
  const expectedPort = parseStrictPort(paired.port);
  const parts = splitRawUrl(rawUrl);

  if (parts.scheme !== 'http') {
    throw new NetworkPolicyError('Laptop requests must use http.', 'forbidden_destination');
  }
  const parsedHost = parseStrictTailscaleIp(parts.host);
  if (parsedHost !== expectedHost) {
    throw new NetworkPolicyError('URL host does not match the paired laptop.', 'forbidden_destination');
  }
  if (!parts.portStr) {
    throw new NetworkPolicyError('Laptop URL must specify the paired port.', 'forbidden_destination');
  }
  const parsedPort = parseStrictPort(Number(parts.portStr));
  if (parsedPort !== expectedPort) {
    throw new NetworkPolicyError('URL port does not match the paired laptop port.', 'forbidden_destination');
  }
  if (!parts.path.startsWith('/phone/v1/') || parts.path.length <= '/phone/v1/'.length) {
    throw new NetworkPolicyError('Laptop URL path must be under /phone/v1/.', 'forbidden_destination');
  }
  return `http://${expectedHost}:${expectedPort}${parts.path}${parts.query}`;
}

/** Validate a paired laptop /phone/v1/files?... download URL strictly. */
export function validateLaptopFileUrl(rawUrl: string, paired: PairedLaptopEndpoint): string {
  const normalized = validateLaptopUrl(rawUrl, paired);
  const parts = splitRawUrl(normalized);
  if (parts.path !== '/phone/v1/files' || !parts.query.startsWith('?') || parts.query.length <= 1) {
    throw new NetworkPolicyError('Laptop file share URL must target /phone/v1/files?...', 'forbidden_destination');
  }
  return normalized;
}

/** Return the configured Supabase https origin. */
export function getConfiguredSupabaseOrigin(overrideUrl?: string): string {
  const raw = (overrideUrl ?? supabaseConfig.url ?? '').trim().replace(/\/+$/, '');
  const parts = splitRawUrl(raw);
  if (parts.scheme !== 'https') {
    throw new NetworkPolicyError('Supabase origin must use https.', 'forbidden_destination');
  }
  if (parts.portStr !== null && parts.portStr !== '443') {
    throw new NetworkPolicyError('Supabase origin cannot use a custom port.', 'forbidden_destination');
  }
  if (parts.path !== '/' || parts.query !== '') {
    throw new NetworkPolicyError('Supabase origin must not include path or query.', 'forbidden_destination');
  }
  return `https://${parts.host.toLowerCase()}`;
}

/** Validate a URL targeting the configured Supabase https origin. */
export function validateSupabaseUrl(rawUrl: string, overrideSupabaseUrl?: string): string {
  const origin = getConfiguredSupabaseOrigin(overrideSupabaseUrl);
  const parts = splitRawUrl(rawUrl);
  if (parts.scheme !== 'https') {
    throw new NetworkPolicyError('Supabase requests must use https.', 'forbidden_destination');
  }
  if (parts.portStr !== null && parts.portStr !== '443') {
    throw new NetworkPolicyError('Supabase custom ports are refused.', 'forbidden_destination');
  }
  const targetOrigin = `https://${parts.host.toLowerCase()}`;
  if (targetOrigin !== origin) {
    throw new NetworkPolicyError('URL does not match configured Supabase origin.', 'forbidden_destination');
  }
  if (
    !parts.path.startsWith('/auth/v1/') &&
    !parts.path.startsWith('/rest/v1/') &&
    parts.path !== '/functions/v1/ai-proxy'
  ) {
    throw new NetworkPolicyError('Disallowed Supabase endpoint path.', 'forbidden_destination');
  }
  return `${origin}${parts.path}${parts.query}`;
}

/** Validate a URL targeting NVIDIA NIM (https://integrate.api.nvidia.com/v1/...), requiring a personal key. */
export function validateNimUrl(rawUrl: string, personalKey: string | null | undefined): string {
  const cleanKey = (personalKey ?? '').trim();
  if (!cleanKey) {
    throw new NetworkPolicyError(
      'Direct NVIDIA NIM requests are only allowed when a personal key is configured.',
      'missing_credential',
    );
  }
  const parts = splitRawUrl(rawUrl);
  if (parts.scheme !== 'https') {
    throw new NetworkPolicyError('NVIDIA NIM requests must use https.', 'forbidden_destination');
  }
  if (parts.portStr !== null && parts.portStr !== '443') {
    throw new NetworkPolicyError('NVIDIA NIM custom ports are refused.', 'forbidden_destination');
  }
  if (parts.host.toLowerCase() !== 'integrate.api.nvidia.com') {
    throw new NetworkPolicyError('Host is not NVIDIA NIM (integrate.api.nvidia.com).', 'forbidden_destination');
  }
  if (!parts.path.startsWith('/v1/') || parts.path.length <= '/v1/'.length) {
    throw new NetworkPolicyError('NVIDIA NIM URL path must be under /v1/.', 'forbidden_destination');
  }
  return `https://integrate.api.nvidia.com${parts.path}${parts.query}`;
}

function sanitizeCallerHeaders(headers?: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  if (!headers) return out;
  for (const [k, v] of Object.entries(headers)) {
    const low = k.trim().toLowerCase();
    if (!low) continue;
    if (FORBIDDEN_CALLER_HEADERS.has(low)) {
      throw new NetworkPolicyError(
        `Header "${k}" cannot be set directly; credentials and browser headers are controlled by net.ts.`,
        'forbidden_header',
      );
    }
    out[k] = String(v);
  }
  return out;
}

function checkNoRedirect(resp: NativeHttpResponse): void {
  if (resp.redirected) {
    throw new NetworkPolicyError('HTTP redirects are refused.', 'redirect_refused');
  }
  if (resp.status >= 300 && resp.status < 400) {
    throw new NetworkPolicyError(`HTTP redirect status ${resp.status} is refused.`, 'redirect_refused');
  }
  const headerKeys = Object.keys(resp.headers ?? {});
  for (const key of headerKeys) {
    if (key.toLowerCase() === 'location' && resp.status >= 300 && resp.status < 400) {
      throw new NetworkPolicyError('HTTP redirect Location header is refused.', 'redirect_refused');
    }
  }
}

export interface DestinationHttpRequest {
  destination: NetworkDestination;
  method: NativeHttpRequest['method'];
  url: string;
  headers?: Record<string, string>;
  body?: unknown;
  requireAuth?: boolean;
  timeoutMs?: number;
}

/**
 * Single HTTP gatekeeper for the entire mobile app.
 * Enforces the 3-destination allowlist, strict credential isolation, and redirect refusal.
 */
export async function netRequest(
  req: DestinationHttpRequest,
  creds: NetworkCredentials,
  bridge: NativeBridge = getNativeBridge(),
): Promise<NativeHttpResponse> {
  const safeHeaders = sanitizeCallerHeaders(req.headers);
  let validatedUrl: string;

  if (req.destination === 'laptop') {
    if (!creds.laptop) {
      throw new NetworkPolicyError('No paired laptop is configured.', 'unpaired_laptop');
    }
    validatedUrl = validateLaptopUrl(req.url, creds.laptop);
    const requireAuth = req.requireAuth !== false;
    if (requireAuth) {
      const token = (creds.laptopToken ?? '').trim();
      if (!token) {
        throw new NetworkPolicyError('Laptop device token is missing.', 'missing_credential');
      }
      safeHeaders['Authorization'] = `Bearer ${token}`;
    }
  } else if (req.destination === 'supabase') {
    validatedUrl = validateSupabaseUrl(req.url, creds.supabaseUrl);
    const anonKey = (creds.supabaseAnonKey ?? supabaseConfig.publishable_key ?? '').trim();
    if (!anonKey) {
      throw new NetworkPolicyError('Supabase publishable key is missing.', 'missing_credential');
    }
    safeHeaders['apikey'] = anonKey;
    if (req.requireAuth !== false) {
      const accessToken = (creds.supabaseAccessToken ?? '').trim();
      if (!accessToken) {
        throw new NetworkPolicyError('Supabase access token is missing.', 'missing_credential');
      }
      safeHeaders['Authorization'] = `Bearer ${accessToken}`;
    }
  } else if (req.destination === 'nim') {
    const nimKey = (creds.nimPersonalKey ?? '').trim();
    validatedUrl = validateNimUrl(req.url, nimKey);
    safeHeaders['Authorization'] = `Bearer ${nimKey}`;
  } else {
    throw new NetworkPolicyError('Unknown network destination.', 'forbidden_destination');
  }

  let bodyStr: string | undefined;
  if (req.body !== undefined) {
    safeHeaders['Content-Type'] = safeHeaders['Content-Type'] ?? 'application/json';
    bodyStr = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
  }
  safeHeaders['Accept'] = safeHeaders['Accept'] ?? 'application/json';

  const resp = await bridge.http({
    method: req.method,
    url: validatedUrl,
    headers: safeHeaders,
    body: bodyStr,
    timeoutMs: req.timeoutMs,
  });

  checkNoRedirect(resp);
  return resp;
}

/** Convenience JSON wrapper around netRequest that raises HttpStatusError on non-2xx. */
export async function netJson<T>(
  req: DestinationHttpRequest,
  creds: NetworkCredentials,
  bridge: NativeBridge = getNativeBridge(),
): Promise<T> {
  const resp = await netRequest(req, creds, bridge);
  if (resp.status < 200 || resp.status >= 300) {
    let message = `Request failed (${resp.status})`;
    try {
      const parsed = JSON.parse(resp.body) as Record<string, unknown>;
      for (const field of ['error', 'message', 'msg', 'error_description']) {
        const val = parsed?.[field];
        if (typeof val === 'string' && val.trim()) {
          message = val.trim();
          break;
        }
      }
    } catch {
      // Keep status message
    }
    throw new HttpStatusError(message, resp.status, resp.body);
  }
  if (!resp.body || !resp.body.trim()) {
    return null as T;
  }
  return JSON.parse(resp.body) as T;
}

/** Download and share a laptop media/PDF file via native shareLaptopFile with strict URL and credential isolation. */
export async function netShareLaptopFile(
  rawUrl: string,
  title: string | undefined,
  creds: NetworkCredentials,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  if (!creds.laptop) {
    throw new NetworkPolicyError('No paired laptop is configured.', 'unpaired_laptop');
  }
  const validatedUrl = validateLaptopFileUrl(rawUrl, creds.laptop);
  const token = (creds.laptopToken ?? '').trim();
  if (!token) {
    throw new NetworkPolicyError('Laptop device token is missing.', 'missing_credential');
  }
  if (typeof bridge.shareLaptopFile !== 'function') {
    throw new Error('File sharing is unavailable on this platform.');
  }
  await bridge.shareLaptopFile({
    url: validatedUrl,
    headers: { Authorization: `Bearer ${token}` },
    title,
  });
}

export function getPublicSupabaseConfig(): {
  url: string;
  publishableKey: string;
  pitchSiteUrl: string;
} {
  return {
    url: getConfiguredSupabaseOrigin(),
    publishableKey: String(supabaseConfig.publishable_key ?? '').trim(),
    pitchSiteUrl: String(supabaseConfig.pitch_site_url ?? '').trim(),
  };
}
