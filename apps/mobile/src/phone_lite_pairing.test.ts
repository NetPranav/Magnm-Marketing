import { beforeEach, describe, expect, it } from 'vitest';
import {
  ConfidentialRefusalError,
  ProductRulesUnavailableError,
  sendPhoneLiteMessage,
} from './api';
import { NIM_KEY_SECRET_KEY, SESSION_SECRET_KEY } from './auth';
import { saveCachedProductRules, wipeCache } from './cache';
import { createPreviewNativeBridge } from './native';
import { getPublicSupabaseConfig, NIM_BASE_URL } from './net';
import {
  getLaptopToken,
  getPairingConfig,
  parsePairingPayload,
  pollPairRequest,
  submitPairRequest,
  unpairLaptop,
} from './pairing';
import { enqueueInboxItem, loadQueue } from './queue';
import { dumpStorage } from './test-storage';

const SUPA = getPublicSupabaseConfig();

function encodeQr(obj: unknown): string {
  const json = JSON.stringify(obj);
  const b64 = btoa(json).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return `magnm-pair:${b64}`;
}

describe('pairing and Phone Lite guards', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('validates pairing payload, pairs via laptop API, stores token in secureStore, and wipes on unpair', async () => {
    expect(() => parsePairingPayload('https://evil.com')).toThrow();
    expect(() => parsePairingPayload(encodeQr({ v: 1, host: '192.168.1.5', port: 8788, code: 'abc' }))).toThrow();
    expect(() => parsePairingPayload(encodeQr({ v: 1, host: '0144.0100.0.1', port: 8788, code: 'abc' }))).toThrow();

    const qr = encodeQr({ v: 1, host: '100.80.10.5', port: 8788, code: 'secret-code-123' });
    const parsed = parsePairingPayload(qr);
    expect(parsed.host).toBe('100.80.10.5');
    expect(parsed.port).toBe(8788);

    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (req.url.endsWith('/phone/v1/pair')) {
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              request_id: 'preq_1',
              poll_secret: 'psec_1',
              expires_at: '2026-11-22T12:00:00Z',
            }),
          };
        }
        if (req.url.endsWith('/phone/v1/pair/poll')) {
          return {
            status: 200,
            headers: {},
            body: JSON.stringify({
              status: 'approved',
              device_id: 'pdev_1',
              device_name: 'Pixel 9',
              device_token: 'device-bearer-xyz',
            }),
          };
        }
        return { status: 404, headers: {}, body: '{}' };
      },
    });

    const pairReq = await submitPairRequest(parsed, 'Pixel 9', bridge);
    const pollRes = await pollPairRequest(parsed, pairReq.request_id, pairReq.poll_secret, 'uid-1', bridge);
    expect(pollRes.status).toBe('approved');
    expect(await getLaptopToken(bridge)).toBe('device-bearer-xyz');
    expect(getPairingConfig()?.deviceId).toBe('pdev_1');
    // Device token must NEVER be in localStorage
    expect(dumpStorage()).not.toContain('device-bearer-xyz');

    enqueueInboxItem({
      kind: 'note',
      title: 'Offline note',
      body: 'Test',
      ownerUid: 'uid-1',
      laptopDeviceId: 'pdev_1',
    });
    expect(loadQueue()).toHaveLength(1);

    await unpairLaptop(bridge);
    expect(await getLaptopToken(bridge)).toBeNull();
    expect(getPairingConfig()).toBeNull();
    expect(loadQueue()).toHaveLength(0);
  });

  it('refuses Phone Lite if product_rules are uncached and unfetchable, and refuses confidential products', async () => {
    let networkCalls = 0;
    const bridge = createPreviewNativeBridge({
      httpHandler: async () => {
        networkCalls += 1;
        return { status: 503, headers: {}, body: '{}' };
      },
    });

    // No cached rules and signed out -> refuses to run
    await expect(
      sendPhoneLiteMessage({ agentId: 'writer', text: 'Write a post about Cero', bridge }),
    ).rejects.toThrow(ProductRulesUnavailableError);
    expect(networkCalls).toBe(0);

    // Cache product rules with confidential ProjectNightfall
    saveCachedProductRules('uid-1', {
      version: 1,
      show_levels: {
        everything: 'All features and visuals',
        teasers_only: 'Teasers only',
      },
      products: [
        {
          id: 'cero',
          name: 'Cero',
          status: 'released',
          show: 'everything',
          confidential: false,
        },
        {
          id: 'projectnightfall',
          name: 'ProjectNightfall',
          status: 'in_development',
          show: 'teasers_only',
          blocked_terms: ['keyframe_engine'],
          confidential: true,
        },
      ],
    });

    // Confidential product name or blocked term is refused before any cloud call
    await expect(
      sendPhoneLiteMessage({
        agentId: 'writer',
        text: 'Write a launch thread for ProjectNightfall',
        bridge,
      }),
    ).rejects.toThrow(ConfidentialRefusalError);
    expect(networkCalls).toBe(0);

    await expect(
      sendPhoneLiteMessage({
        agentId: 'cmo',
        text: 'Mention keyframe_engine in our roadmap post',
        bridge,
      }),
    ).rejects.toThrow(ConfidentialRefusalError);
    expect(networkCalls).toBe(0);

    wipeCache();
  });

  it('routes Phone Lite to NIM when personal key exists, else Supabase ai-proxy with no tools', async () => {
    const requests: Array<{ url: string; headers?: Record<string, string>; body?: string }> = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        // Rules can't be refreshed here, so the saved copy is used.
        if (req.url.includes('/rest/v1/shared_knowledge')) return { status: 503, headers: {}, body: '' };
        requests.push({ url: req.url, headers: req.headers, body: req.body });
        return {
          status: 200,
          headers: {},
          body: JSON.stringify({ choices: [{ message: { content: 'Draft reply' } }] }),
        };
      },
    });

    saveCachedProductRules('uid-1', {
      version: 1,
      show_levels: { everything: 'All', teasers_only: 'Teasers' },
      products: [
        { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
      ],
    });

    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'supa-jwt-123',
        refresh_token: 'supa-ref-123',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'u1@example.com',
      }),
    );

    // Without personal key -> uses ai-proxy
    const proxyRes = await sendPhoneLiteMessage({
      agentId: 'writer',
      text: 'Write a short post about Cero',
      bridge,
    });
    expect(proxyRes.provider).toBe('ai-proxy');
    expect(requests[0].url).toBe(`${SUPA.url}/functions/v1/ai-proxy`);
    expect(requests[0].headers?.Authorization).toBe('Bearer supa-jwt-123');
    const proxyBody = JSON.parse(requests[0].body || '{}') as Record<string, unknown>;
    expect(proxyBody.tools).toBeUndefined();
    expect(String((proxyBody.messages as Array<{ content: string }>)[0].content)).toContain('Product rules');

    // With personal key -> uses NVIDIA NIM directly
    await bridge.secureStore.set(NIM_KEY_SECRET_KEY, 'nvapi-personal-999');
    const nimRes = await sendPhoneLiteMessage({
      agentId: 'cmo',
      text: 'Outline a Cero week plan',
      bridge,
    });
    expect(nimRes.provider).toBe('personal-nim');
    expect(requests[1].url).toBe(`${NIM_BASE_URL}/chat/completions`);
    expect(requests[1].headers?.Authorization).toBe('Bearer nvapi-personal-999');
    expect(requests[1].headers?.apikey).toBeUndefined();
  });

  it('prefers fresh rules: a product that became confidential is refused despite a stale saved copy', async () => {
    const product = { id: 'nova', name: 'Nova', status: 'released', show: 'everything', confidential: false } as const;
    const showLevels = { everything: 'All' };
    saveCachedProductRules('uid-1', { show_levels: showLevels, products: [product] });
    const urls: string[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        urls.push(req.url);
        const rules = { show_levels: showLevels, products: [{ ...product, confidential: true }] };
        return { status: 200, headers: {}, body: JSON.stringify([{ data: { rules, version: 1 } }]) };
      },
    });
    const session = { access_token: 'jwt', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: 'uid-1', email: '' };
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify(session));

    await expect(sendPhoneLiteMessage({ agentId: 'writer', text: 'Post about Nova', bridge })).rejects.toThrow(
      /confidential/,
    );
    expect(urls.some((url) => url.includes('/rest/v1/shared_knowledge'))).toBe(true);
    expect(urls.some((url) => /ai-proxy|chat\/completions/.test(url))).toBe(false);
  });
});
