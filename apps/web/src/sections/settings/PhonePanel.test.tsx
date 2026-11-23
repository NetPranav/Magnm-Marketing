import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PhonePanel } from './PhonePanel';
import type { PhoneStatus } from '../../api/phone';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function baseStatus(overrides: Partial<PhoneStatus> = {}): PhoneStatus {
  return {
    enabled: false,
    running: false,
    host: null,
    port: 8788,
    tailscale_missing: false,
    error: null,
    pairing: {
      active: false,
      qr_payload: null,
      expires_at: null,
      expires_in: 0,
      pending_requests: [],
    },
    devices: [],
    ...overrides,
  };
}

describe('PhonePanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders off-by-default state with explanation and empty devices', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/phone') {
        return jsonResponse(baseStatus());
      }
      return jsonResponse({});
    });

    render(<PhonePanel />);

    const toggle = (await screen.findByRole('switch', {
      name: 'Allow phone connections',
    })) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(screen.getByText('10')).toBeTruthy();
    expect(screen.getByText(/strictly over Tailscale/i)).toBeTruthy();
    expect(screen.getByText('No phones paired yet.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Pair a phone' })).toBeNull();
  });

  it('shows Tailscale missing notice and keeps switch off when Tailscale is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/phone') {
        return jsonResponse(baseStatus());
      }
      if (url === '/api/phone/enable' && init?.method === 'POST') {
        return jsonResponse(
          {
            error: 'Install and connect Tailscale on this laptop first.',
            tailscale_missing: true,
          },
          400,
        );
      }
      return jsonResponse({});
    });

    render(<PhonePanel />);

    const toggle = (await screen.findByRole('switch', {
      name: 'Allow phone connections',
    })) as HTMLInputElement;
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain(
        'Install and connect Tailscale on this laptop first.',
      );
    });
    expect(toggle.checked).toBe(false);
  });

  it('enables connections, renders React SVG QR with countdown, cancels, approves/denies, and revokes devices', async () => {
    let currentState = baseStatus();

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/phone') {
        return jsonResponse(currentState);
      }
      if (url === '/api/phone/enable' && init?.method === 'POST') {
        currentState = baseStatus({
          enabled: true,
          running: true,
          host: '100.64.0.10',
          port: 8788,
        });
        return jsonResponse(currentState);
      }
      if (url === '/api/phone/pair/start' && init?.method === 'POST') {
        const expiresAt = new Date(Date.now() + 295_000).toISOString();
        currentState = baseStatus({
          enabled: true,
          running: true,
          host: '100.64.0.10',
          port: 8788,
          pairing: {
            active: true,
            qr_payload: 'magnm-pair:eyJ2IjoxLCJob3N0IjoiMTAwLjY0LjAuMTAiLCJwb3J0Ijo4Nzg4LCJjb2RlIjoiYWJjMTIzIn0',
            expires_at: expiresAt,
            expires_in: 295,
            pending_requests: [
              {
                request_id: 'preq_1',
                device_name: 'Pixel 9',
                source_ip: '100.64.0.42',
                created_at: new Date().toISOString(),
                expires_at: expiresAt,
              },
              {
                request_id: 'preq_2',
                device_name: 'Unknown Tablet',
                source_ip: '100.64.0.99',
                created_at: new Date().toISOString(),
                expires_at: expiresAt,
              },
            ],
          },
        });
        return jsonResponse({
          active: true,
          qr_payload: currentState.pairing.qr_payload,
          expires_at: expiresAt,
          expires_in: 295,
          status: currentState,
        });
      }
      if (url === '/api/phone/pair/deny' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { request_id: string };
        currentState = {
          ...currentState,
          pairing: {
            ...currentState.pairing,
            pending_requests: currentState.pairing.pending_requests.filter(
              (r: { request_id: string }) => r.request_id !== body.request_id,
            ),
          },
        };
        return jsonResponse({ ok: true, status: currentState });
      }
      if (url === '/api/phone/pair/approve' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { request_id: string };
        const approvedDev = {
          id: 'pdev_1',
          name: 'Pixel 9',
          created_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
          last_ip: '100.64.0.42',
        };
        currentState = {
          ...currentState,
          pairing: {
            active: false,
            qr_payload: null,
            expires_at: null,
            expires_in: 0,
            pending_requests: currentState.pairing.pending_requests.filter(
              (r: { request_id: string }) => r.request_id !== body.request_id,
            ),
          },
          devices: [approvedDev],
        };
        return jsonResponse({ device: approvedDev, status: currentState });
      }
      if (url === '/api/phone/pair/cancel' && init?.method === 'POST') {
        currentState = baseStatus({
          enabled: true,
          running: true,
          host: '100.64.0.10',
          port: 8788,
        });
        return jsonResponse(currentState);
      }
      if (url === '/api/phone/devices/revoke' && init?.method === 'POST') {
        currentState = {
          ...currentState,
          devices: [],
        };
        return jsonResponse({ device: { id: 'pdev_1', revoked_at: new Date().toISOString() }, status: currentState });
      }
      if (url === '/api/phone/disable' && init?.method === 'POST') {
        currentState = baseStatus();
        return jsonResponse(currentState);
      }
      return jsonResponse({});
    });

    render(<PhonePanel />);

    const toggle = (await screen.findByRole('switch', {
      name: 'Allow phone connections',
    })) as HTMLInputElement;
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByText('Listening on 100.64.0.10:8788')).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Pair a phone' })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Pair a phone' }));

    const qrImg = await screen.findByRole('img', { name: 'Pairing QR code' });
    expect(qrImg.tagName.toLowerCase()).toBe('svg');
    expect(qrImg.querySelector('path')).toBeTruthy();
    expect(screen.getByTestId('phone-pair-countdown').textContent).toMatch(/Expires in \d+:\d{2}/);

    // Pending approval list shows device name and source IP
    const pendingList = screen.getByLabelText('Pending phone pairing requests');
    expect(within(pendingList).getByText('Pixel 9')).toBeTruthy();
    expect(within(pendingList).getByText(/100\.64\.0\.42/)).toBeTruthy();
    expect(within(pendingList).getByText('Unknown Tablet')).toBeTruthy();
    expect(within(pendingList).getByText(/100\.64\.0\.99/)).toBeTruthy();

    // Deny Unknown Tablet
    const tabletRow = within(pendingList).getByText('Unknown Tablet').closest('.mg-row') as HTMLElement;
    fireEvent.click(within(tabletRow).getByRole('button', { name: 'Deny' }));
    await waitFor(() => {
      expect(screen.queryByText('Unknown Tablet')).toBeNull();
    });

    // Approve Pixel 9
    const pixelRow = within(screen.getByLabelText('Pending phone pairing requests'))
      .getByText('Pixel 9')
      .closest('.mg-row') as HTMLElement;
    fireEvent.click(within(pixelRow).getByRole('button', { name: 'Approve' }));

    const devicesList = await screen.findByLabelText('Paired phones');
    expect(within(devicesList).getByText('Pixel 9')).toBeTruthy();
    expect(within(devicesList).getByText(/Last seen: just now/i)).toBeTruthy();

    // Revoke Pixel 9
    fireEvent.click(within(devicesList).getByRole('button', { name: 'Revoke Pixel 9' }));
    await waitFor(() => {
      expect(screen.getByText('No phones paired yet.')).toBeTruthy();
    });

    // Disable phone connections
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/phone/disable',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(toggle.checked).toBe(false);
    });
  });

  it('shows the full pairing code under the QR with a Copy button and its expiry', async () => {
    const code = 'magnm-pair:eyJ2IjoxLCJob3N0IjoiMTAwLjY0LjAuMTAiLCJwb3J0Ijo4Nzg4LCJjb2RlIjoieHl6In0';
    const expiresAt = new Date(Date.now() + 240_000).toISOString();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/phone') {
        return jsonResponse(
          baseStatus({
            enabled: true,
            running: true,
            host: '100.64.0.10',
            pairing: { active: true, qr_payload: code, expires_at: expiresAt, expires_in: 240, pending_requests: [] },
          }),
        );
      }
      return jsonResponse({});
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<PhonePanel />);

    const field = (await screen.findByLabelText('Pairing code')) as HTMLTextAreaElement | HTMLInputElement;
    expect(field.value).toBe(code);
    expect(field.readOnly).toBe(true);
    expect(screen.getByText(/Or type or paste this code/i)).toBeTruthy();
    expect(screen.getByTestId('phone-pair-countdown').textContent).toMatch(/Expires in \d:\d\d/);

    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(code));
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeTruthy();
  });

  it('cancels an active pairing QR session', async () => {
    let active = true;
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/phone') {
        return jsonResponse(
          baseStatus({
            enabled: true,
            running: true,
            host: '100.64.0.10',
            port: 8788,
            pairing: {
              active,
              qr_payload: active ? 'magnm-pair:eyJ2IjoxLCJob3N0IjoiMTAwLjY0LjAuMTAiLCJwb3J0Ijo4Nzg4LCJjb2RlIjoiMTIzIn0' : null,
              expires_at: active ? new Date(Date.now() + 200_000).toISOString() : null,
              expires_in: active ? 200 : 0,
              pending_requests: [],
            },
          }),
        );
      }
      if (url === '/api/phone/pair/cancel' && init?.method === 'POST') {
        active = false;
        return jsonResponse(
          baseStatus({
            enabled: true,
            running: true,
            host: '100.64.0.10',
            port: 8788,
          }),
        );
      }
      return jsonResponse({});
    });

    render(<PhonePanel />);

    expect(await screen.findByRole('img', { name: 'Pairing QR code' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/phone/pair/cancel',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(screen.queryByRole('img', { name: 'Pairing QR code' })).toBeNull();
    });
  });
});
