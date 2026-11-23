import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';
import { SESSION_SECRET_KEY } from './auth';
import { saveCachedProductRules, saveLaptopSnapshot } from './cache';
import phoneContract from './fixtures/phone_contract.json';
import { ensureSession } from './test-session';
import { createPreviewNativeBridge, PREVIEW_BANNER_LABEL } from './native';
import { PAIRING_META_KEY, LAPTOP_TOKEN_SECRET_KEY } from './pairing';
import { writeNonSecretJson } from './storage';

function encodeQr(obj: unknown): string {
  const json = JSON.stringify(obj);
  const b64 = btoa(json).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return `magnm-pair:${b64}`;
}

describe('Magnm Mobile App UI', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows Preview label, renders contract-shaped Today/Drafts/Chat/More states with nested replies, and handles 401 cleanly', async () => {
    let return401 = false;
    const postedUrls: string[] = [];
    const postedCalls: unknown[] = [];
    const commentUrls: string[] = [];
    const commentCalls: unknown[] = [];

    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_test',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const bridge = createPreviewNativeBridge({
      dictateResult: 'Draft a short launch note for Cero.',
      httpHandler: async (req) => {
        if (return401) {
          return { status: 401, headers: {}, body: JSON.stringify({ error: 'Unauthorized' }) };
        }
        if (req.url.endsWith('/phone/v1/status')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
        }
        if (req.url.includes('/phone/v1/drafts?')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.drafts_list) };
        }
        if (req.url.endsWith('/phone/v1/drafts/cnt_pub')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.draft_detail) };
        }
        if (req.url.endsWith('/phone/v1/drafts/cnt_conf')) {
          return {
            status: 200,
            headers: {},
            body: JSON.stringify(phoneContract.draft_detail_confidential),
          };
        }
        if (req.url.includes('/phone/v1/drafts/') && req.url.endsWith('/comments')) {
          commentUrls.push(req.url);
          commentCalls.push(JSON.parse(req.body || '{}'));
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.draft_comment) };
        }
        if (req.url.includes('/phone/v1/drafts/') && req.url.endsWith('/posted')) {
          postedUrls.push(req.url);
          postedCalls.push(JSON.parse(req.body || '{}'));
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.draft_posted) };
        }
        if (req.url.includes('/phone/v1/reminders')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.reminders) };
        }
        if (req.url.includes('/phone/v1/notifications')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.notifications) };
        }
        if (req.url.endsWith('/phone/v1/summary')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.summary) };
        }
        if (req.url.endsWith('/phone/v1/results/pending')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.results_pending) };
        }
        if (req.url.endsWith('/phone/v1/agents')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.agents) };
        }
        return { status: 404, headers: {}, body: '{}' };
      },
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');

    await ensureSession(bridge);
    render(<App bridge={bridge} />);

    // Preview label visible
    expect(screen.getByText(PREVIEW_BANNER_LABEL)).toBeTruthy();

    // Today tab shows connected laptop, reminders, notifications, summary, pending results
    await waitFor(() => expect(screen.getByText('Laptop connected')).toBeTruthy());
    expect(screen.getByText('Add post metrics for Cero')).toBeTruthy();
    expect(screen.getByText('Weekly summary ready')).toBeTruthy();
    expect(screen.getByText('Published 2 Cero posts this week.')).toBeTruthy();

    // Switch to Drafts tab
    fireEvent.click(screen.getByRole('tab', { name: /Drafts/i }));
    await waitFor(() => expect(screen.getByText('Public Cero Launch')).toBeTruthy());
    expect(screen.getByText('Confidential item')).toBeTruthy();

    // Open draft detail -> shows v2 body, warnings, root comment AND nested reply; posts to /cnt_pub/ (never /undefined/)
    fireEvent.click(screen.getByText('Public Cero Launch'));
    await waitFor(() =>
      expect(
        screen.getByText(/Cero v2 release is live for developers today/),
      ).toBeTruthy(),
    );
    expect(screen.getByText('Strong opening line.')).toBeTruthy();
    expect(screen.getByText('Agreed, trim one hashtag.')).toBeTruthy();
    expect(screen.getByText(/Too many hashtags for linkedin/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Add comment'), { target: { value: 'Looks good from phone' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    await waitFor(() => expect(commentCalls).toHaveLength(1));
    expect(commentUrls[0]).toBe('http://100.64.5.10:8788/phone/v1/drafts/cnt_pub/comments');

    fireEvent.change(screen.getByLabelText('Posted link (optional)'), {
      target: { value: 'https://linkedin.com/posts/123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mark as posted' }));
    await waitFor(() => expect(postedCalls).toHaveLength(1));
    expect(postedUrls[0]).toBe('http://100.64.5.10:8788/phone/v1/drafts/cnt_pub/posted');
    await waitFor(() =>
      expect(screen.getByText('Posted URL: https://linkedin.com/posts/123')).toBeTruthy(),
    );

    // Confidential item opens flat stub cleanly
    fireEvent.click(screen.getByRole('button', { name: 'Back to drafts' }));
    fireEvent.click(screen.getByText('Confidential item'));
    await waitFor(() =>
      expect(screen.getByText(/Confidential drafts stay stubs on mobile/)).toBeTruthy(),
    );

    // Dictate in Chat tab fills editable input and never auto-sends
    fireEvent.click(screen.getByRole('tab', { name: /Chat/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
    await waitFor(() => {
      const box = screen.getByLabelText('Message or voice brief') as HTMLTextAreaElement;
      expect(box.value).toBe('Draft a short launch note for Cero.');
    });
    expect(screen.queryByText('You · laptop')).toBeNull();

    // Simulate 401 from laptop on refresh
    return401 = true;
    fireEvent.click(screen.getByRole('tab', { name: /Today/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('401'));
  });

  it('shows Saved copy label when offline, Phone Lite label, and share-in never auto-sends', async () => {
    const sentRequests: string[] = [];

    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_test',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    saveLaptopSnapshot('pdev_test', {
      drafts: [{ id: 'd-cached', title: 'Cached Draft', platform: 'x', status: 'draft' }],
      reminders: [],
      notifications: [],
      unreadCount: 0,
      summary: null,
      pendingResults: [],
    });

    saveCachedProductRules('uid-1', {
      version: 1,
      show_levels: { everything: 'All', teasers_only: 'Teasers' },
      products: [
        { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
        { id: 'projectnightfall', name: 'ProjectNightfall', status: 'in_development', show: 'teasers_only', confidential: true },
      ],
    });

    const bridge = createPreviewNativeBridge({
      initialSharedItems: [
        {
          id: 'sh-1',
          text: 'Shared analytics note',
          url: 'https://example.com/post',
          imageBase64: 'iVBORw0KGgo=',
          mimeType: 'image/png',
          receivedAt: '2026-11-22T10:00:00Z',
        },
      ],
      httpHandler: async (req) => {
        sentRequests.push(req.url);
        throw new Error('Laptop unreachable');
      },
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'supa-jwt',
        refresh_token: 'supa-ref',
        expires_at: 9_999_999_999,
        user_id: 'uid-1',
        email: 'member@example.com',
      }),
    );

    await ensureSession(bridge);
    render(<App bridge={bridge} />);

    // Share-in modal opens with preview & choices; NOTHING sent before tap
    await waitFor(() => expect(screen.getByText('Shared into Magnm')).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Idea' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Note' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Read results screenshot' })).toBeTruthy();
    expect(sentRequests.filter((u) => u.includes('/inbox') || u.includes('/results'))).toHaveLength(0);

    // Tap Idea while offline -> queued locally (not sent)
    fireEvent.click(screen.getByRole('button', { name: 'Idea' }));
    await waitFor(() => expect(screen.queryByText('Shared into Magnm')).toBeNull());

    // Offline Today shows Saved copy from <time>
    await waitFor(() => expect(screen.getByTestId('offline-saved-label').textContent).toContain('Saved copy from '));

    // Chat tab clearly shows Phone Lite (offline) and refuses confidential product
    fireEvent.click(screen.getByRole('tab', { name: /Chat/i }));
    expect(screen.getByText('Phone Lite (offline)')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Message or voice brief'), {
      target: { value: 'Tell me about ProjectNightfall' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('ProjectNightfall'));

    // More tab shows queued item, privacy note, and pairing/unpair
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    expect(screen.getByText('[idea] Shared analytics note')).toBeTruthy();
    expect(screen.getByText(/Privacy note/i)).toBeTruthy();
  });

  it('requires explicit Save results tap after screenshot extraction and supports pairing UI confirmation', async () => {
    const confirmedPayloads: unknown[] = [];
    const bridge = createPreviewNativeBridge({
      initialSharedItems: [
        {
          id: 'sh-2',
          text: 'LinkedIn screenshot',
          imageBase64: 'iVBORw0KGgo=',
          mimeType: 'image/png',
          receivedAt: '2026-11-22T10:00:00Z',
        },
      ],
      httpHandler: async (req) => {
        if (req.url.endsWith('/phone/v1/status')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
        }
        if (req.url.includes('/phone/v1/drafts')) {
          return { status: 200, headers: {}, body: JSON.stringify({ drafts: [] }) };
        }
        if (req.url.includes('/phone/v1/reminders')) {
          return { status: 200, headers: {}, body: JSON.stringify({ reminders: [] }) };
        }
        if (req.url.includes('/phone/v1/notifications')) {
          return { status: 200, headers: {}, body: JSON.stringify({ notifications: [], unread_count: 0 }) };
        }
        if (req.url.endsWith('/phone/v1/summary')) {
          return { status: 200, headers: {}, body: JSON.stringify({ summary: null }) };
        }
        if (req.url.endsWith('/phone/v1/results/pending')) {
          return { status: 200, headers: {}, body: JSON.stringify({ proposals: [] }) };
        }
        if (req.url.endsWith('/phone/v1/agents')) {
          return { status: 200, headers: {}, body: JSON.stringify({ agents: [] }) };
        }
        if (req.url.endsWith('/phone/v1/results/extract')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.results_extract) };
        }
        if (req.url.includes('/phone/v1/results/') && req.url.endsWith('/confirm')) {
          confirmedPayloads.push(JSON.parse(req.body || '{}'));
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.results_confirm) };
        }
        return { status: 200, headers: {}, body: '{}' };
      },
    });

    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_test',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-1',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-valid');

    await ensureSession(bridge);
    render(<App bridge={bridge} />);

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Read results screenshot' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Read results screenshot' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Save results' })).toBeTruthy());
    expect(confirmedPayloads).toHaveLength(0);

    // Edit metric before saving
    const viewsInput = screen.getByLabelText('views') as HTMLInputElement;
    fireEvent.change(viewsInput, { target: { value: '1250' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await waitFor(() => expect(confirmedPayloads).toHaveLength(1));
    expect((confirmedPayloads[0] as { metrics: Record<string, number> }).metrics.views).toBe(1250);

    // Also test pairing confirmation prompt when unpaired
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Unpair laptop' }));
    await waitFor(() => expect(screen.getByLabelText('Pairing code (magnm-pair:...)')).toBeTruthy());

    const qr = encodeQr({ v: 1, host: '100.70.1.2', port: 8788, code: 'abc123' });
    fireEvent.change(screen.getByLabelText('Pairing code (magnm-pair:...)'), { target: { value: qr } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByText(/Pair with this laptop\? 100\.70\.1\.2:8788/)).toBeTruthy();
  });
});
