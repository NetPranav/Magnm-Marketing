import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (ev: MessageEvent<string>) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(cb);
    this.listeners.set(type, list);
  }

  emit(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners.get(type) ?? []) {
      cb(ev);
    }
  }

  open() {
    this.onopen?.(new Event('open'));
  }

  close() {
    this.closed = true;
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockEngineFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    if (url === '/api/health') {
      return jsonResponse({ ok: true, service: 'magnm-engine', version: '0.1.0' });
    }
    if (url === '/api/status') {
      return jsonResponse({
        status: 'ready',
        env_status: {
          default_provider: 'nvidia_nim',
          nvidia_nim_global: true,
          nvidia_nim_global_masked: 'nvapi-****1234',
          nvidia_nim_roles_configured: 5,
          openai: false,
          anthropic: false,
          gemini: true,
          groq: false,
        },
        models_matrix: new Array(7).fill({ role: 'writer', has_key: true }),
        current_job: { running: false },
      });
    }
    if (url === '/api/jobs?limit=10') {
      return jsonResponse([
        {
          id: 'job-1',
          name: 'daily_cycle',
          status: 'done',
          progress: 100,
          message: 'Digest built',
        },
        {
          id: 'job-2',
          name: 'market_scan',
          status: 'running',
          progress: 45,
          message: 'Scanning HN and Reddit',
        },
      ]);
    }
    return jsonResponse({ error: 'Not found' }, 404);
  });
}

describe('Studio shell and Today screen', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
    window.location.hash = '#/today';
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.location.hash = '';
  });

  it('renders all 9 numbered sections and switches on click and arrow keys', async () => {
    mockEngineFetch();
    render(<App />);

    const expectedLabels = [
      'Today',
      'Team',
      'Research',
      'Plan',
      'Create',
      'Review',
      'Library',
      'Repos',
      'Settings',
    ];

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(9);
    expectedLabels.forEach((label, idx) => {
      expect(tabs[idx].textContent).toContain(`${idx + 1}`);
      expect(tabs[idx].textContent).toContain(label);
    });

    // Today uses reading column (.mg-wrap)
    const main = screen.getByRole('main');
    expect(main.firstElementChild?.className).toBe('mg-wrap');

    // Click Review -> wide layout + placeholder header
    fireEvent.click(tabs[5]);
    expect(screen.getByRole('heading', { name: 'Review' })).toBeTruthy();
    expect(main.firstElementChild?.className).toBe('mg-wide');

    // ArrowDown from Review (index 5) moves to Library (index 6)
    fireEvent.keyDown(tabs[5], { key: 'ArrowDown' });
    expect(screen.getByRole('heading', { name: 'Library' })).toBeTruthy();
    expect(main.firstElementChild?.className).toBe('mg-wrap');

    // ArrowUp from Library returns to Review
    fireEvent.keyDown(tabs[6], { key: 'ArrowUp' });
    expect(screen.getByRole('heading', { name: 'Review' })).toBeTruthy();
  });

  it('renders safe engine status and live jobs on Today without exposing keys', async () => {
    mockEngineFetch();
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('daily_cycle')).toBeTruthy();
    });

    expect(screen.getByText('nvidia_nim')).toBeTruthy();
    expect(screen.getByText('5 of 7')).toBeTruthy();
    expect(screen.getByText('NVIDIA NIM: yes')).toBeTruthy();
    expect(screen.getByText('market_scan')).toBeTruthy();
    expect(screen.getByText('Scanning HN and Reddit')).toBeTruthy();
    expect(screen.getByText('Running 45%')).toBeTruthy();

    // Masked key from /api/status must never be rendered in the DOM
    expect(document.body.textContent).not.toContain('nvapi-****1234');

    // Open SSE and push a live job update
    const es = MockEventSource.instances[0];
    expect(es).toBeDefined();
    act(() => {
      es.open();
      es.emit('job', {
        id: 'job-2',
        name: 'market_scan',
        status: 'done',
        progress: 100,
        message: 'Scan finished',
      });
    });

    expect(screen.getByText('Scan finished')).toBeTruthy();
  });
});
