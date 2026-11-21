import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';
import type { NotificationRecord } from './api/notifications';
import { NotificationsPanel } from './sections/settings/NotificationsPanel';

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();

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

  close() {}
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Notifications UI, SSE live updates, Settings, and system notifications', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
    window.location.hash = '#/today';
  });

  afterEach(() => {
    cleanup();
    delete window.__TAURI__;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.location.hash = '';
  });

  it('renders bell badge and panel, marks read on click and mark-all, navigates, and updates live from SSE', async () => {
    const initialNotifs: NotificationRecord[] = [
      {
        id: 'n-1',
        kind: 'quote_request',
        title: 'New quote request',
        body: 'Priya (Northstar) for Aero Proposal',
        route: '#/pitch',
        created_at: new Date().toISOString(),
        read_at: null,
      },
      {
        id: 'n-2',
        kind: 'ai_limit',
        title: 'AI limit almost reached',
        body: 'Used 90% today',
        route: '#/settings',
        created_at: new Date().toISOString(),
        read_at: null,
      },
    ];

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, service: 'magnm-engine', version: '0.1.0' });
      }
      if (url === '/api/status') {
        return jsonResponse({ status: 'ready', env_status: {}, models_matrix: [], current_job: { running: false } });
      }
      if (url === '/api/jobs?limit=10') {
        return jsonResponse([]);
      }
      if (url === '/api/account') {
        return jsonResponse({ configured: false, signed_in: false, user: null });
      }
      if (url.startsWith('/api/notifications?limit=')) {
        const unread = initialNotifs.filter((n) => !n.read_at).length;
        return jsonResponse({ notifications: initialNotifs, unread_count: unread });
      }
      if (url === '/api/notifications/read' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { ids?: string[]; all?: boolean };
        const nowIso = new Date().toISOString();
        if (body.all) {
          for (const item of initialNotifs) item.read_at = nowIso;
        } else if (Array.isArray(body.ids)) {
          for (const item of initialNotifs) {
            if (body.ids.includes(item.id)) item.read_at = nowIso;
          }
        }
        const unread = initialNotifs.filter((n) => !n.read_at).length;
        return jsonResponse({ ok: true, unread_count: unread });
      }
      if (url.startsWith('/api/pitch/')) {
        return jsonResponse({ proposals: [], requests: [], unread_count: 0 });
      }
      return jsonResponse({});
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('notification-badge').textContent).toBe('2');
    });

    const es = MockEventSource.instances[0];
    expect(es).toBeDefined();
    act(() => {
      es.open();
    });

    // Open bell panel
    fireEvent.click(screen.getByRole('button', { name: 'Notifications (2 unread)' }));
    expect(screen.getByRole('dialog', { name: 'Notifications' })).toBeTruthy();
    expect(screen.getByText('New quote request')).toBeTruthy();
    expect(screen.getByText('Priya (Northstar) for Aero Proposal')).toBeTruthy();

    // Click first notification -> marks read, decrements badge, navigates to #/pitch
    fireEvent.click(screen.getByText('New quote request'));
    await waitFor(() => {
      expect(window.location.hash).toBe('#/pitch');
      expect(screen.getByTestId('notification-badge').textContent).toBe('1');
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/notifications/read',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ ids: ['n-1'] }),
      }),
    );

    // Live SSE notification event updates list and badge
    act(() => {
      es.emit('notification', {
        id: 'n-3',
        kind: 'team_knowledge',
        title: 'Brand kit updated by Priya',
        body: '',
        route: '#/settings',
        created_at: new Date().toISOString(),
        read_at: null,
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('notification-badge').textContent).toBe('2');
    });

    // Open panel and click Mark all as read
    fireEvent.click(screen.getByRole('button', { name: 'Notifications (2 unread)' }));
    expect(screen.getByText('Brand kit updated by Priya')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Mark all as read' }));

    await waitFor(() => {
      expect(screen.queryByTestId('notification-badge')).toBeNull();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/notifications/read',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ all: true }),
      }),
    );
  });

  it('uses Web Notification API when unfocused and granted, and focuses + navigates on click', async () => {
    const createdInstances: Array<{
      title: string;
      options?: NotificationOptions;
      onclick: (() => void) | null;
      close: ReturnType<typeof vi.fn>;
    }> = [];
    const requestPermissionSpy = vi.fn(async () => 'granted' as NotificationPermission);
    class MockNotification {
      static permission: NotificationPermission = 'granted';
      static requestPermission = requestPermissionSpy;
      title: string;
      options?: NotificationOptions;
      onclick: (() => void) | null = null;
      close = vi.fn();

      constructor(title: string, options?: NotificationOptions) {
        this.title = title;
        this.options = options;
        createdInstances.push(this);
      }
    }
    vi.stubGlobal('Notification', MockNotification);
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    const focusSpy = vi.spyOn(window, 'focus').mockImplementation(() => {});

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') return jsonResponse({ ok: true });
      if (url === '/api/status') return jsonResponse({ status: 'ready', env_status: {}, models_matrix: [] });
      if (url === '/api/jobs?limit=10') return jsonResponse([]);
      if (url === '/api/account') return jsonResponse({ configured: false, signed_in: false, user: null });
      if (url.startsWith('/api/notifications?')) return jsonResponse({ notifications: [], unread_count: 0 });
      return jsonResponse({});
    });

    render(<App />);
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    expect(requestPermissionSpy).not.toHaveBeenCalled();

    act(() => {
      MockEventSource.instances[0].open();
      MockEventSource.instances[0].emit('notification', {
        id: 'n-web-1',
        kind: 'sync_paused',
        title: 'Team sync is paused',
        body: 'Waiting for connection',
        route: '#/settings',
        created_at: new Date().toISOString(),
        read_at: null,
      });
    });

    await waitFor(() => {
      expect(createdInstances).toHaveLength(1);
    });
    expect(createdInstances[0].title).toBe('Team sync is paused');

    act(() => {
      createdInstances[0].onclick?.();
    });
    expect(focusSpy).toHaveBeenCalled();
    expect(window.location.hash).toBe('#/settings');
  });

  it('uses Tauri notification plugin helper when running in desktop shell and navigates on tap event', async () => {
    const invokeMock = vi.fn(async (cmd: string) => {
      if (cmd === 'plugin:notification|is_permission_granted') return true;
      return null;
    });
    let tapListener: ((ev: { payload?: unknown }) => void) | null = null;
    window.__TAURI__ = {
      core: { invoke: invokeMock },
      event: {
        listen: vi.fn(async (eventName, cb) => {
          if (eventName === 'magnm:notification-click') {
            tapListener = cb;
          }
          return () => {
            tapListener = null;
          };
        }),
      },
    };
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') return jsonResponse({ ok: true });
      if (url === '/api/status') return jsonResponse({ status: 'ready', env_status: {}, models_matrix: [] });
      if (url === '/api/jobs?limit=10') return jsonResponse([]);
      if (url === '/api/account') return jsonResponse({ configured: false, signed_in: false, user: null });
      if (url.startsWith('/api/notifications?')) return jsonResponse({ notifications: [], unread_count: 0 });
      return jsonResponse({});
    });

    render(<App />);
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });

    act(() => {
      MockEventSource.instances[0].open();
      MockEventSource.instances[0].emit('notification', {
        id: 'n-desk-1',
        kind: 'quote_request',
        title: 'New quote request',
        body: 'Priya for Aero',
        route: '#/pitch',
        created_at: new Date().toISOString(),
        read_at: null,
      });
    });

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('plugin:notification|notify', {
        options: {
          title: 'New quote request',
          body: 'Priya for Aero',
          extra: { route: '#/pitch' },
        },
      });
    });

    act(() => {
      tapListener?.({ payload: '#/pitch' });
    });
    expect(window.location.hash).toBe('#/pitch');
  });

  it('renders Settings toggles (admin kinds hidden for members) and saves changes', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/notifications/settings' && (!init || init.method !== 'POST')) {
        return jsonResponse({
          is_admin: false,
          settings: [
            {
              kind: 'quote_request',
              label: 'New quote requests',
              description: 'Client quote submissions',
              admin_only: false,
              default: true,
              enabled: true,
            },
            {
              kind: 'ai_limit',
              label: 'Team AI limit alerts',
              description: 'Usage alerts',
              admin_only: false,
              default: true,
              enabled: true,
            },
          ],
        });
      }
      if (url === '/api/notifications/settings' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { kind: string; enabled: boolean };
        return jsonResponse({ ok: true, kind: body.kind, enabled: body.enabled });
      }
      return jsonResponse({});
    });

    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn(async () => 'granted'),
    });

    render(<NotificationsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'New quote requests' })).toBeTruthy();
    });
    expect(screen.queryByRole('checkbox', { name: 'Member drafts' })).toBeNull();

    const checkbox = screen.getByRole('checkbox', { name: 'New quote requests' }) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/notifications/settings',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ kind: 'quote_request', enabled: false }),
        }),
      );
    });
  });

  it('handles permission button flows: granted, denied, and unsupported', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/notifications/settings') {
        return jsonResponse({ is_admin: false, settings: [] });
      }
      return jsonResponse({});
    });

    // 1. default -> click button -> granted
    const reqPermGranted = vi.fn(async () => 'granted' as NotificationPermission);
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: reqPermGranted,
    });
    const { unmount: unmount1 } = render(<NotificationsPanel />);
    const btn1 = await screen.findByRole('button', { name: 'Turn on desktop notifications' });
    fireEvent.click(btn1);
    await waitFor(() => {
      expect(reqPermGranted).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Allowed')).toBeTruthy();
    });
    unmount1();

    // 2. default -> click button -> denied
    const reqPermDenied = vi.fn(async () => 'denied' as NotificationPermission);
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: reqPermDenied,
    });
    const { unmount: unmount2 } = render(<NotificationsPanel />);
    const btn2 = await screen.findByRole('button', { name: 'Turn on desktop notifications' });
    fireEvent.click(btn2);
    await waitFor(() => {
      expect(reqPermDenied).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/Showing in-app notifications only/i)).toBeTruthy();
      expect(screen.getByText('Denied')).toBeTruthy();
    });
    unmount2();

    // 3. unsupported
    vi.stubGlobal('Notification', undefined);
    render(<NotificationsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Unavailable')).toBeTruthy();
      expect(screen.getByText(/Showing in-app notifications only/i)).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Turn on desktop notifications' })).toBeNull();
  });
});
