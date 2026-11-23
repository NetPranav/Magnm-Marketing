import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { SESSION_SECRET_KEY } from './auth';
import { ensureCacheOwner, loadCache, saveLaptopSnapshot } from './cache';
import { PitchTab, MAX_PITCH_PDF_BYTES } from './components/PitchTab';
import { extractShareableDraftFiles } from './components/DraftsTab';
import phoneContract from './fixtures/phone_contract.json';
import { ensureSession } from './test-session';
import { createPreviewNativeBridge } from './native';
import { netShareLaptopFile } from './net';
import { LAPTOP_TOKEN_SECRET_KEY, PAIRING_META_KEY } from './pairing';
import { writeNonSecretJson } from './storage';
import type { PitchProposalItem } from './types';

function readBundledPitchAsset(relPath: string): string {
  return readFileSync(path.resolve(__dirname, '../public/pitch', relPath), 'utf-8');
}

describe('Bundled Pitch, Present Mode, Today Routing, and Share-Out', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('builds public/pitch/ with strict CSP, inline data fonts, boot.js, and zero network refs', () => {
    const html = readBundledPitchAsset('index.html');
    expect(html).toContain('http-equiv="Content-Security-Policy"');
    expect(html).toContain("default-src 'none'");
    expect(html).toContain("connect-src 'none'");
    expect(html).toContain("font-src data:");
    expect(html).toContain("style-src 'self' https://localhost 'unsafe-inline'");
    expect(html).toContain('<script src="boot.js"></script>');
    expect(html).not.toContain('fonts.googleapis.com');
    expect(html).not.toContain('fonts.gstatic.com');
    expect(html).not.toContain('src="app.js"');

    const fontsCss = readBundledPitchAsset('fonts/fonts.css');
    expect(fontsCss).toContain('data:font/woff2;base64,');
    expect(fontsCss).not.toContain('http://');
    expect(fontsCss).not.toContain('https://');

    const bootJs = readBundledPitchAsset('boot.js');
    expect(bootJs).toContain('magnm:pitch-ready');
    expect(bootJs).toContain('magnm:pitch-payload');
    expect(bootJs).toContain('magnm:pitch-pdf-bytes');
    expect(bootJs).toContain('magnm:pitch-exit-present');
    expect(bootJs).toContain('jsPDF');
  });

  it('enforces iframe sandbox="allow-scripts", rejects spoofed messages, and validates PDF bytes', async () => {
    const prop: PitchProposalItem = {
      id: 'prop_pub',
      name: 'Cero Web Launch',
      kind: 'proposal',
      updated_at: '2026-11-22T09:15:00+00:00',
      payload: phoneContract.pitch.proposals[1].payload as Record<string, unknown>,
    };
    const onExit = vi.fn();
    const onSharePdf = vi.fn(async () => {});

    const { container } = render(
      <PitchTab
        proposals={[prop]}
        presentingProposal={prop}
        laptopReachable={false}
        pricesAsOfLabel="Prices as of Nov 22, 09:15"
        errorMessage={null}
        onSelectProposal={vi.fn()}
        onExitPresent={onExit}
        onSharePdf={onSharePdf}
      />,
    );

    const iframe = container.querySelector('iframe') as HTMLIFrameElement;
    expect(iframe).toBeTruthy();
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(iframe.getAttribute('src')).toBe('/pitch/index.html');

    const frameWin = iframe.contentWindow as unknown as {
      postMessage: (msg: unknown, targetOrigin: string) => void;
    };
    const postedToFrame: unknown[] = [];
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: {
        ...frameWin,
        postMessage: (msg: unknown, origin: string) => {
          postedToFrame.push({ msg, origin });
        },
      },
    });
    const fakeFrameWin = iframe.contentWindow as unknown as MessageEventSource;
    const validPdf = new TextEncoder().encode('%PDF-1.4\n%%EOF');

    // Spoofed source (e.g., parent window or unrelated window) must be ignored
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: window,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'quote.pdf', bytes: validPdf },
        }),
      );
      window.dispatchEvent(
        new MessageEvent('message', {
          source: window,
          data: { type: 'magnm:pitch-ready' },
        }),
      );
    });
    expect(onSharePdf).not.toHaveBeenCalled();
    expect(postedToFrame).toHaveLength(0);

    // Valid source but invalid message types, bad PDF magic, oversized filename, >10 MB bytes must be ignored
    const overLimit = new Uint8Array(MAX_PITCH_PDF_BYTES + 1);
    overLimit.set([0x25, 0x50, 0x44, 0x46, 0x2d]);
    const badMagic = new TextEncoder().encode('<html>not a pdf</html>');

    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', { source: fakeFrameWin, data: { type: 'magnm:unknown' } }),
      );
      window.dispatchEvent(
        new MessageEvent('message', {
          source: fakeFrameWin,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'bad.pdf', bytes: badMagic },
        }),
      );
      window.dispatchEvent(
        new MessageEvent('message', {
          source: fakeFrameWin,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'x'.repeat(205), bytes: validPdf },
        }),
      );
      window.dispatchEvent(
        new MessageEvent('message', {
          source: fakeFrameWin,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'huge.pdf', bytes: overLimit },
        }),
      );
      window.dispatchEvent(
        new MessageEvent('message', {
          source: fakeFrameWin,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'empty.pdf', bytes: new Uint8Array(0) },
        }),
      );
    });
    expect(onSharePdf).not.toHaveBeenCalled();

    // Valid magnm:pitch-ready receives payload via postMessage
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { source: fakeFrameWin, data: { type: 'magnm:pitch-ready' } }));
    });
    expect(postedToFrame).toHaveLength(1);
    expect(postedToFrame[0]).toEqual({
      msg: { type: 'magnm:pitch-payload', payload: prop.payload },
      origin: '*',
    });

    // Valid magnm:pitch-pdf-bytes invokes native PDF share
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: fakeFrameWin,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'cero.pdf', bytes: validPdf },
        }),
      );
    });
    await waitFor(() => {
      expect(onSharePdf).toHaveBeenCalledWith(btoa('%PDF-1.4\n%%EOF'), 'Cero Web Launch');
    });

    // Escape and Exit message return to list
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onExit).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Exit' }));
    expect(onExit).toHaveBeenCalledTimes(2);
  });

  it('syncs Pitch proposals, supports offline present mode with "Prices as of", hides chrome, handles Today tap & wipes cleanly', async () => {
    let laptopOnline = true;
    const pdfShares: Array<{ base64: string; title?: string }> = [];
    const fileShares: Array<{ url: string; headers?: Record<string, string>; title?: string }> = [];

    const draftWithFiles = {
      ...phoneContract.draft_detail,
      item: {
        ...phoneContract.draft_detail.item,
        files: [
          { kind: 'video', path: 'outputs/reels/cero-launch.mp4' },
          { kind: 'card', path: 'outputs/cards/cero-card.png' },
          { kind: 'script', path: 'outputs/posts/script.md' },
        ],
      },
    };
    const notifsWithSend = {
      notifications: [
        {
          id: 'notif_send_pitch',
          kind: 'sent_to_phone',
          title: 'Pitch: Cero Web Launch',
          body: 'Tap to open present mode on your phone.',
          route: '#/pitch/prop_pub/present',
          created_at: '2026-11-22T09:20:00+00:00',
          read_at: null,
        },
        {
          id: 'notif_send_draft',
          kind: 'sent_to_phone',
          title: 'Draft: Public Cero Launch',
          body: 'Tap to open in Drafts on your phone.',
          route: '#/review/cnt_pub',
          created_at: '2026-11-22T09:21:00+00:00',
          read_at: null,
        },
      ],
      unread_count: 2,
    };

    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_contract_1',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-alpha',
      pairedAt: '2026-11-22T09:00:00Z',
    });

    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        if (!laptopOnline && req.url.includes('100.64.5.10')) {
          throw new Error('Network unreachable');
        }
        if (req.url.endsWith('/phone/v1/status')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.status) };
        }
        if (req.url.includes('/phone/v1/drafts?')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.drafts_list) };
        }
        if (req.url.endsWith('/phone/v1/drafts/cnt_pub')) {
          return { status: 200, headers: {}, body: JSON.stringify(draftWithFiles) };
        }
        if (req.url.includes('/phone/v1/reminders')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.reminders) };
        }
        if (req.url.includes('/phone/v1/notifications')) {
          return { status: 200, headers: {}, body: JSON.stringify(notifsWithSend) };
        }
        if (req.url.endsWith('/phone/v1/summary')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.summary) };
        }
        if (req.url.endsWith('/phone/v1/results/pending')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.results_pending) };
        }
        if (req.url.endsWith('/phone/v1/agents')) {
          return { status: 200, headers: {}, body: JSON.stringify({ agents: [] }) };
        }
        if (req.url.endsWith('/phone/v1/pitch')) {
          return { status: 200, headers: {}, body: JSON.stringify(phoneContract.pitch) };
        }
        return { status: 404, headers: {}, body: '{}' };
      },
      onShareLaptopFile: async (opts) => {
        fileShares.push(opts);
      },
      onSharePdf: async (opts) => {
        pdfShares.push(opts);
      },
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-alpha');
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({
        access_token: 'jwt-alpha',
        refresh_token: 'ref-alpha',
        expires_at: 9_999_999_999,
        user_id: 'uid-alpha',
        email: 'alpha@example.com',
      }),
    );

    await ensureSession(bridge);
    const { container } = render(<App bridge={bridge} initialTab="today" />);

    // Today notification sent_to_phone opens Pitch present mode on tap and hides chrome
    await waitFor(() => expect(screen.getByText('Pitch: Cero Web Launch')).toBeTruthy());
    fireEvent.click(screen.getByText('Pitch: Cero Web Launch'));

    await waitFor(() => expect(screen.getByTestId('pitch-present-screen')).toBeTruthy());
    expect(container.querySelector('.mob-header')).toBeNull();
    expect(container.querySelector('.mob-tabs-wrap')).toBeNull();

    // Android back button exits Pitch present mode and restores chrome
    act(() => {
      bridge.emitBackButton();
    });
    await waitFor(() => expect(screen.queryByTestId('pitch-present-screen')).toBeNull());
    expect(container.querySelector('.mob-header')).toBeTruthy();
    expect(container.querySelector('.mob-tabs-wrap')).toBeTruthy();

    // Pitch tab shows "Prices as of <time>" and non-clickable confidential stub
    expect(screen.getByTestId('pitch-prices-as-of').textContent).toContain('Prices as of ');
    expect(screen.getByText('Confidential proposal')).toBeTruthy();

    // Cached snapshot persisted for paired device
    expect(loadCache().laptop?.deviceId).toBe('pdev_contract_1');
    expect(loadCache().laptop?.pitchProposals).toHaveLength(3);
    expect(typeof loadCache().laptop?.pitchPricesAsOf).toBe('number');

    // Today sent_to_phone draft notification opens Drafts detail with Share buttons only for shareable files
    fireEvent.click(screen.getByRole('tab', { name: /Today/i }));
    await waitFor(() => expect(screen.getByText('Draft: Public Cero Launch')).toBeTruthy());
    fireEvent.click(screen.getByText('Draft: Public Cero Launch'));

    await waitFor(() => expect(screen.getByText('cero-launch.mp4')).toBeTruthy());
    expect(screen.getByText('cero-card.png')).toBeTruthy();
    expect(screen.queryByText('script.md')).toBeNull();

    const shareButtons = screen.getAllByRole('button', { name: 'Get on phone' });
    expect(shareButtons).toHaveLength(2);
    fireEvent.click(shareButtons[0]);
    await waitFor(() => expect(fileShares).toHaveLength(1));
    expect(fileShares[0].url).toBe('http://100.64.5.10:8788/phone/v1/files?content_id=cnt_pub&path=outputs%2Freels%2Fcero-launch.mp4');
    expect(fileShares[0].headers).toEqual({ Authorization: 'Bearer tok-alpha' });

    // Offline reload from saved copy still presents offline with "Prices as of"
    cleanup();
    laptopOnline = false;
    await ensureSession(bridge);
    render(<App bridge={bridge} initialTab="pitch" />);
    await waitFor(() => expect(screen.getByTestId('pitch-prices-as-of').textContent).toContain('Prices as of '));
    expect(screen.getByText(/Offline present mode ready/)).toBeTruthy();
    fireEvent.click(screen.getByText('Cero Web Launch'));
    await waitFor(() => expect(screen.getByTestId('pitch-present-screen')).toBeTruthy());

    // Simulate Pitch PDF share from inside iframe
    const presentIframe = document.querySelector('iframe') as HTMLIFrameElement;
    const validPdf = new TextEncoder().encode('%PDF-1.4\n%%EOF');
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: presentIframe.contentWindow as unknown as MessageEventSource,
          data: { type: 'magnm:pitch-pdf-bytes', filename: 'cero.pdf', bytes: validPdf },
        }),
      );
    });
    await waitFor(() => expect(pdfShares).toHaveLength(1));
    expect(pdfShares[0].base64).toBe(btoa('%PDF-1.4\n%%EOF'));

    fireEvent.click(screen.getByRole('button', { name: 'Exit' }));
    await waitFor(() => expect(screen.queryByTestId('pitch-present-screen')).toBeNull());

    // Sign out wipes offline pitch copy and snapshot
    fireEvent.click(screen.getByRole('tab', { name: /More/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    const confirmDlg = await screen.findByRole('dialog');
    fireEvent.click(within(confirmDlg).getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(loadCache().laptop).toBeNull());
    // Signing in is required, so signing out goes back to the welcome screen with nothing of the old copy left.
    expect(await screen.findByRole('heading', { name: /welcome to magnm studio/i })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: /Pitch/i })).toBeNull();
    expect(loadCache().laptop?.pitchProposals ?? []).toHaveLength(0);

    // Unpair wipe check on cache & app state
    cleanup();
    laptopOnline = true;
    saveLaptopSnapshot('pdev_contract_1', {
      pitchProposals: phoneContract.pitch.proposals as PitchProposalItem[],
      pitchPricesAsOf: Date.now(),
    });
    writeNonSecretJson(PAIRING_META_KEY, {
      host: '100.64.5.10',
      port: 8788,
      deviceId: 'pdev_contract_1',
      deviceName: 'Pixel 9',
      ownerUid: 'uid-alpha',
      pairedAt: '2026-11-22T09:00:00Z',
    });
    await bridge.secureStore.set(LAPTOP_TOKEN_SECRET_KEY, 'tok-alpha');
    await ensureSession(bridge);
    render(<App bridge={bridge} initialTab="more" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unpair laptop' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Unpair laptop' }));
    await waitFor(() => expect(loadCache().laptop).toBeNull());

    // Account change wipes previous user's pitch snapshot
    ensureCacheOwner('uid-alpha');
    saveLaptopSnapshot('pdev_alpha', {
      pitchProposals: phoneContract.pitch.proposals as PitchProposalItem[],
      pitchPricesAsOf: Date.now(),
    });
    expect(loadCache().laptop?.pitchProposals).toHaveLength(3);
    ensureCacheOwner('uid-beta');
    expect(loadCache().ownerUid).toBe('uid-beta');
    expect(loadCache().laptop).toBeNull();
  });

  it('rejects non-laptop or non-file URLs in netShareLaptopFile and filters extractShareableDraftFiles', async () => {
    const shareCalls: unknown[] = [];
    const bridge = createPreviewNativeBridge({
      onShareLaptopFile: async (opts) => {
        shareCalls.push(opts);
      },
    });
    const creds = {
      laptop: {
        host: '100.64.5.10',
        port: 8788,
      },
      laptopToken: 'tok-1',
    };

    await expect(
      netShareLaptopFile('http://100.64.5.10:8788/phone/v1/status', 'Bad', creds, bridge),
    ).rejects.toThrow();
    await expect(
      netShareLaptopFile('https://100.64.5.10:8788/phone/v1/files?path=a.mp4', 'Bad', creds, bridge),
    ).rejects.toThrow();
    await expect(
      netShareLaptopFile('http://192.168.1.2:8788/phone/v1/files?path=a.mp4', 'Bad', creds, bridge),
    ).rejects.toThrow();
    expect(shareCalls).toHaveLength(0);

    await netShareLaptopFile(
      'http://100.64.5.10:8788/phone/v1/files?path=outputs%2Freel.mp4',
      'Reel',
      creds,
      bridge,
    );
    expect(shareCalls).toHaveLength(1);

    const extracted = extractShareableDraftFiles([
      { kind: 'video', path: 'outputs/a.MP4' },
      { kind: 'video', path: 'outputs/a.MP4' },
      { kind: 'doc', path: 'outputs/b.pdf' },
      { kind: 'img', path: 'outputs/c.webp' },
      { kind: 'text', path: 'outputs/d.txt' },
      'outputs/e.jpeg',
    ]);
    expect(extracted.map((f) => f.label)).toEqual(['a.MP4', 'b.pdf', 'c.webp', 'e.jpeg']);
  });
});
