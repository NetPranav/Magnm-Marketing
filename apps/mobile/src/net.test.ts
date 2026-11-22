import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { createPreviewNativeBridge } from './native';
import {
  getPublicSupabaseConfig,
  netRequest,
  NetworkPolicyError,
  NIM_BASE_URL,
  parseStrictTailscaleIp,
  validateLaptopUrl,
  validateNimUrl,
  validateSupabaseUrl,
} from './net';

const PAIRED = { host: '100.64.12.34', port: 8788 };
const SUPA_CFG = getPublicSupabaseConfig();

describe('network policy (src/net.ts)', () => {
  it('accepts valid Tailscale 100.64.0.0/10 addresses and paired /phone/v1/ paths', () => {
    expect(parseStrictTailscaleIp('100.64.0.0')).toBe('100.64.0.0');
    expect(parseStrictTailscaleIp('100.127.255.255')).toBe('100.127.255.255');
    expect(validateLaptopUrl('http://100.64.12.34:8788/phone/v1/status', PAIRED)).toBe(
      'http://100.64.12.34:8788/phone/v1/status',
    );
    expect(
      validateLaptopUrl('http://100.64.12.34:8788/phone/v1/drafts?limit=20', PAIRED),
    ).toBe('http://100.64.12.34:8788/phone/v1/drafts?limit=20');
  });

  it('refuses laptop bypass attempts: odd IP spellings, wrong ranges, user@host, paths, schemes', () => {
    const badUrls = [
      // Wrong scheme
      'https://100.64.12.34:8788/phone/v1/status',
      // Outside 100.64.0.0/10
      'http://100.63.255.255:8788/phone/v1/status',
      'http://100.128.0.0:8788/phone/v1/status',
      'http://127.0.0.1:8788/phone/v1/status',
      'http://192.168.1.10:8788/phone/v1/status',
      'http://10.0.0.1:8788/phone/v1/status',
      // Wrong IP inside 100.64.0.0/10 (not paired host)
      'http://100.64.12.35:8788/phone/v1/status',
      // Wrong port / missing port / leading zero port
      'http://100.64.12.34:8787/phone/v1/status',
      'http://100.64.12.34/phone/v1/status',
      'http://100.64.12.34:08788/phone/v1/status',
      // Odd IP spellings (octal, hex, decimal int, leading zeros, short IP, IPv6)
      'http://0144.0100.0014.0042:8788/phone/v1/status',
      'http://0x64.0x40.0x0c.0x22:8788/phone/v1/status',
      'http://1681919010:8788/phone/v1/status',
      'http://100.064.12.34:8788/phone/v1/status',
      'http://100.64.3106:8788/phone/v1/status',
      'http://[::ffff:100.64.12.34]:8788/phone/v1/status',
      'http://100.64.12.34.nip.io:8788/phone/v1/status',
      // user@host
      'http://user@100.64.12.34:8788/phone/v1/status',
      'http://100.64.12.34:8788@evil.com/phone/v1/status',
      // Path escapes and non-/phone/v1/ paths
      'http://100.64.12.34:8788/api/status',
      'http://100.64.12.34:8788/phone/v1',
      'http://100.64.12.34:8788/phone/v1/',
      'http://100.64.12.34:8788/phone/v1/../api/status',
      'http://100.64.12.34:8788/phone/v1/%2e%2e/api/status',
      'http://100.64.12.34:8788/phone/v2/status',
      'http://100.64.12.34:8788/phone/v1/status#frag',
      'http://100.64.12.34:8788/phone\\v1/status',
    ];
    for (const url of badUrls) {
      expect(() => validateLaptopUrl(url, PAIRED), `expected reject for ${url}`).toThrow(
        NetworkPolicyError,
      );
    }
  });

  it('enforces Supabase origin and path allowlist strictly', () => {
    expect(validateSupabaseUrl(`${SUPA_CFG.url}/rest/v1/profiles?id=eq.1`)).toBe(
      `${SUPA_CFG.url}/rest/v1/profiles?id=eq.1`,
    );
    expect(validateSupabaseUrl(`${SUPA_CFG.url}/functions/v1/ai-proxy`)).toBe(
      `${SUPA_CFG.url}/functions/v1/ai-proxy`,
    );

    const badSupa = [
      SUPA_CFG.url.replace('https://', 'http://') + '/rest/v1/profiles',
      `${SUPA_CFG.url}:8443/rest/v1/profiles`,
      'https://evil.example.com/rest/v1/profiles',
      `https://attacker@${new URL(SUPA_CFG.url).host}/rest/v1/profiles`,
      `${SUPA_CFG.url}/rest/v1/../admin`,
      `${SUPA_CFG.url}/storage/v1/object/public/x`,
    ];
    for (const url of badSupa) {
      expect(() => validateSupabaseUrl(url), `expected reject for ${url}`).toThrow(
        NetworkPolicyError,
      );
    }
  });

  it('allows NVIDIA NIM only when personal key exists and only under /v1/', () => {
    expect(() => validateNimUrl(`${NIM_BASE_URL}/chat/completions`, '')).toThrow(
      NetworkPolicyError,
    );
    expect(() => validateNimUrl(`${NIM_BASE_URL}/chat/completions`, null)).toThrow(
      NetworkPolicyError,
    );
    expect(validateNimUrl(`${NIM_BASE_URL}/chat/completions`, 'nvapi-test-123')).toBe(
      'https://integrate.api.nvidia.com/v1/chat/completions',
    );
    expect(() =>
      validateNimUrl('http://integrate.api.nvidia.com/v1/chat/completions', 'nvapi-test-123'),
    ).toThrow(NetworkPolicyError);
    expect(() =>
      validateNimUrl('https://integrate.api.nvidia.com/v2/chat/completions', 'nvapi-test-123'),
    ).toThrow(NetworkPolicyError);
    expect(() =>
      validateNimUrl('https://evil.com/v1/chat/completions', 'nvapi-test-123'),
    ).toThrow(NetworkPolicyError);
  });

  it('isolates credentials per destination and blocks forbidden headers & redirects', async () => {
    const calls: Array<{ url: string; headers?: Record<string, string> }> = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        calls.push({ url: req.url, headers: req.headers });
        return { status: 200, headers: {}, body: '{"ok":true}' };
      },
    });

    const creds = {
      laptop: PAIRED,
      laptopToken: 'laptop-secret-token',
      supabaseUrl: SUPA_CFG.url,
      supabaseAnonKey: SUPA_CFG.publishableKey,
      supabaseAccessToken: 'supa-user-jwt',
      nimPersonalKey: 'nvapi-personal-key',
    };

    await netRequest(
      {
        destination: 'laptop',
        method: 'GET',
        url: `http://${PAIRED.host}:${PAIRED.port}/phone/v1/status`,
      },
      creds,
      bridge,
    );
    expect(calls[0].headers?.Authorization).toBe('Bearer laptop-secret-token');
    expect(calls[0].headers?.apikey).toBeUndefined();

    await netRequest(
      {
        destination: 'supabase',
        method: 'GET',
        url: `${SUPA_CFG.url}/rest/v1/profiles`,
      },
      creds,
      bridge,
    );
    expect(calls[1].headers?.Authorization).toBe('Bearer supa-user-jwt');
    expect(calls[1].headers?.apikey).toBe(SUPA_CFG.publishableKey);
    expect(JSON.stringify(calls[1])).not.toContain('laptop-secret-token');
    expect(JSON.stringify(calls[1])).not.toContain('nvapi-personal-key');

    await netRequest(
      {
        destination: 'nim',
        method: 'POST',
        url: `${NIM_BASE_URL}/chat/completions`,
        body: { model: 'x', messages: [] },
      },
      creds,
      bridge,
    );
    expect(calls[2].headers?.Authorization).toBe('Bearer nvapi-personal-key');
    expect(calls[2].headers?.apikey).toBeUndefined();
    expect(JSON.stringify(calls[2])).not.toContain('supa-user-jwt');
    expect(JSON.stringify(calls[2])).not.toContain('laptop-secret-token');

    // Caller cannot inject Origin, Cookie, or Authorization
    await expect(
      netRequest(
        {
          destination: 'laptop',
          method: 'GET',
          url: `http://${PAIRED.host}:${PAIRED.port}/phone/v1/status`,
          headers: { Origin: 'https://evil.com' },
        },
        creds,
        bridge,
      ),
    ).rejects.toThrow(NetworkPolicyError);

    // Redirect status or redirected flag is refused
    const redirectBridge = createPreviewNativeBridge({
      httpHandler: vi.fn(async () => ({
        status: 302,
        headers: { Location: 'https://evil.com' },
        body: '',
      })),
    });
    await expect(
      netRequest(
        {
          destination: 'laptop',
          method: 'GET',
          url: `http://${PAIRED.host}:${PAIRED.port}/phone/v1/status`,
        },
        creds,
        redirectBridge,
      ),
    ).rejects.toThrow(/redirect/i);
  });

  it('verifies no other source file calls fetch, XMLHttpRequest, or native.http directly', () => {
    const srcRoot = __dirname;
    const files: string[] = [];
    function walk(dir: string) {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (
          (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx')) &&
          !entry.name.endsWith('.test.ts') &&
          !entry.name.endsWith('.test.tsx')
        ) {
          files.push(full);
        }
      }
    }
    walk(srcRoot);

    for (const file of files) {
      if (file.endsWith('/net.ts') || file.endsWith('/native.ts')) continue;
      const content = readFileSync(file, 'utf8');
      expect(content, `${file} must not call fetch()`).not.toMatch(/\bfetch\s*\(/);
      expect(content, `${file} must not use XMLHttpRequest`).not.toMatch(/XMLHttpRequest/);
      expect(content, `${file} must not call bridge.http directly`).not.toMatch(/bridge\.http\s*\(/);
    }
  });
});
