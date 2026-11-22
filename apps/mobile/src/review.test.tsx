import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendPhoneLiteMessage } from './api';
import { App } from './App';
import { SESSION_SECRET_KEY } from './auth';
import { saveCachedProductRules } from './cache';
import contract from './fixtures/phone_contract.json';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { writeNonSecretJson } from './storage';

const ok = (body: unknown): NativeHttpResponse => ({ status: 200, headers: {}, body: JSON.stringify(body) });

function laptop(req: NativeHttpRequest): NativeHttpResponse {
  if (req.url.endsWith('/status')) return ok(contract.status);
  if (req.url.includes('/drafts?')) return ok(contract.drafts_list);
  if (req.url.includes('/reminders')) return ok(contract.reminders);
  if (req.url.includes('/notifications')) return ok(contract.notifications);
  if (req.url.endsWith('/summary')) return ok(contract.summary);
  if (req.url.endsWith('/results/pending')) return ok(contract.results_pending);
  if (req.url.endsWith('/agents')) return ok(contract.agents);
  return { status: 404, headers: {}, body: '{}' };
}

async function pairedBridge(handler: (req: NativeHttpRequest) => Promise<NativeHttpResponse>) {
  writeNonSecretJson(PAIRING_META_KEY, {
    host: '100.64.5.10',
    port: 8788,
    deviceId: 'pdev_review',
    deviceName: 'Phone',
    ownerUid: '',
    pairedAt: '2026-11-22T09:00:00Z',
  });
  const bridge = createPreviewNativeBridge({ httpHandler: handler });
  await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok');
  return bridge;
}

describe('review regressions', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders laptop text as text, never as HTML', async () => {
    const title = 'Cero <script>alert(1)</script>';
    const body = 'Body <b>not html</b> <img src=x onerror="alert(2)">';
    const reply = 'Reply <iframe src="https://example.com"></iframe>';
    const detail = structuredClone(contract.draft_detail);
    detail.item.title = title;
    detail.body = body;
    for (const version of detail.item.versions) version.body = body;
    detail.comments[0].replies[0].body = reply;
    const list = structuredClone(contract.drafts_list);
    (list.drafts[0] as { title: string }).title = title;

    const bridge = await pairedBridge(async (req) => {
      if (req.url.includes('/drafts?')) return ok(list);
      if (req.url.endsWith('/drafts/cnt_pub')) return ok(detail);
      return laptop(req);
    });
    render(<App bridge={bridge} initialTab="drafts" />);
    fireEvent.click(await screen.findByText(title));
    expect(await screen.findByText(body)).toBeTruthy();
    expect(screen.getByText(reply)).toBeTruthy();
    expect(document.querySelector('script, iframe, img, b')).toBeNull();
  });

  it('uses freshly fetched rules even when they cannot be saved', async () => {
    const nova = { id: 'nova', name: 'Nova', status: 'released', show: 'everything', confidential: false } as const;
    const show_levels = { everything: 'All' };
    saveCachedProductRules('uid-1', { show_levels, products: [nova] });
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    const urls: string[] = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        urls.push(req.url);
        if (!req.url.includes('shared_knowledge')) return ok({ choices: [{ message: { content: 'sent to cloud' } }] });
        return ok([{ data: { rules: { show_levels, products: [{ ...nova, confidential: true }] }, version: 1 } }]);
      },
    });
    const session = { access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: 'uid-1', email: '' };
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify(session));

    await expect(sendPhoneLiteMessage({ agentId: 'writer', text: 'Post about Nova', bridge })).rejects.toThrow(
      /confidential/,
    );
    expect(urls.some((url) => /ai-proxy|chat\/completions/.test(url))).toBe(false);
  });

  it('a full phone store does not mark a reachable laptop offline', async () => {
    const bridge = await pairedBridge(async (req) => laptop(req));
    const setItem = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    render(<App bridge={bridge} />);
    await waitFor(() => expect(setItem).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText('Laptop connected')).toBeTruthy();
  });

  it('chat can be used again after unpairing during a reply', async () => {
    let sent = false;
    const bridge = await pairedBridge(async (req) => {
      if (req.url.endsWith('/message')) {
        sent = true;
        return new Promise<NativeHttpResponse>(() => {});
      }
      return laptop(req);
    });
    render(<App bridge={bridge} initialTab="chat" />);
    await screen.findByText('Laptop connected');
    fireEvent.change(screen.getByLabelText('Message or voice brief'), { target: { value: 'Draft a post' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent).toBe(true));

    fireEvent.click(screen.getByRole('tab', { name: /More/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Unpair laptop' }));
    await screen.findByLabelText(/Pairing code/);
    fireEvent.click(screen.getByRole('tab', { name: /Chat/ }));
    expect(screen.queryByRole('button', { name: 'Sending...' })).toBeNull();
  });
});
