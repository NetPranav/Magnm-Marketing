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
    if (url.startsWith('/api/pitch/')) {
      if (url === '/api/pitch/proposals') return jsonResponse({ proposals: [] });
      if (url === '/api/pitch/version') return jsonResponse({ ref: 'c44b7e2' });
      if (url === '/api/pitch/catalog') {
        return jsonResponse({ items: {}, groups: [], standalone_fee: 4000, standalone_label: '', presets: {} });
      }
      if (url.startsWith('/api/pitch/proposals/prop_present')) {
        return jsonResponse({
          id: 'prop_present',
          slug: 'present-co',
          kind: 'proposal',
          status: 'ready',
          client_name: 'Present Client',
          business_name: 'Present Co',
          contact: {},
          preset: {
            slug: 'present-co',
            name: 'Present Co',
            badge: '',
            clientName: 'Present Client',
            businessName: 'Present Co',
            title: 'Present Plan',
            lead: '',
            whatWeDo: '',
            portfolio: '',
            thisPlan: '',
            architectureBlock: { heading: '', sub: '', roles: [] },
            initialSelected: ['w_front'],
            sections: { p2: ['w_front'], p3: [], p4: [], p5: [] },
          },
          price_overrides: {},
          custom_items: [],
          notes: '',
          created_by: 'user',
          created_at: '2026-11-19T00:00:00Z',
          updated_at: '2026-11-19T00:00:00Z',
          pricing_summary: {
            lines: [],
            custom_items: [],
            subtotal: 6000,
            standalone_fee: 0,
            standalone_label: '',
            total: 6000,
            differs_from_base: [],
          },
        });
      }
      if (url.endsWith('/pdfs')) return jsonResponse({ proposal_id: 'prop_present', slug: 'present-co', pdfs: [] });
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

  it('renders all 10 numbered sections and switches on click and arrow keys', async () => {
    mockEngineFetch();
    render(<App />);

    const expectedLabels = [
      'Today',
      'Team',
      'Research',
      'Plan',
      'Create',
      'Review',
      'Pitch',
      'Library',
      'Repos',
      'Settings',
    ];

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(10);
    expectedLabels.forEach((label, idx) => {
      expect(tabs[idx].textContent).toContain(`${idx + 1}`);
      expect(tabs[idx].textContent).toContain(label);
    });

    // Today uses reading column (.mg-wrap)
    const main = screen.getByRole('main');
    expect(main.firstElementChild?.className).toBe('mg-wrap');

    // Click Review -> wide layout
    fireEvent.click(tabs[5]);
    expect(screen.getByRole('heading', { name: 'Review' })).toBeTruthy();
    expect(main.firstElementChild?.className).toBe('mg-wide');

    // ArrowDown from Review (index 5) moves to Pitch (index 6, wide layout)
    fireEvent.keyDown(tabs[5], { key: 'ArrowDown' });
    expect(screen.getByRole('heading', { name: 'Pitch' })).toBeTruthy();
    expect(main.firstElementChild?.className).toBe('mg-wide');

    // ArrowDown from Pitch (index 6) moves to Library (index 7, reading layout)
    fireEvent.keyDown(tabs[6], { key: 'ArrowDown' });
    expect(screen.getByRole('heading', { name: 'Library' })).toBeTruthy();
    expect(main.firstElementChild?.className).toBe('mg-wrap');

    // ArrowUp from Library returns to Pitch
    fireEvent.keyDown(tabs[7], { key: 'ArrowUp' });
    expect(screen.getByRole('heading', { name: 'Pitch' })).toBeTruthy();
  });

  it('hides the Studio shell in present mode and returns to editor on Escape', async () => {
    mockEngineFetch();
    window.location.hash = '#/pitch/prop_present/present';
    render(<App />);

    expect(screen.getByTestId('pitch-present-screen')).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Studio sections' })).toBeNull();
    const iframe = screen.getByTitle('Present proposal prop_present') as HTMLIFrameElement;
    expect(iframe.getAttribute('src')).toBe('/pitch/?proposal=prop_present&mode=present');

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(window.location.hash).toBe('#/pitch/prop_present');
    });
    expect(screen.getByRole('navigation', { name: 'Studio sections' })).toBeTruthy();
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
