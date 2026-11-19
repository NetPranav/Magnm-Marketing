import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ResearchSection } from './ResearchSection';

class MockEventSource {
  static instances: MockEventSource[] = [];
  private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(_url: string) {
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

  close() {}
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ResearchSection', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('starts research by question, filters radar items, manages watch topics, and shows live browser frame', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/research_items')) {
        return jsonResponse([
          {
            id: 'ri-1',
            url: 'https://news.ycombinator.com/item?id=1',
            title: 'Local CLI agents discussion',
            snippet: 'Developers want transparency',
            source: 'hn',
            topic_id: 'top-1',
            found_at: '2026-11-19T07:00:00Z',
          },
          {
            id: 'ri-2',
            url: 'https://github.com/example/repo',
            title: 'GitHub CLI benchmark',
            snippet: 'Fast terminal tool',
            source: 'github',
            topic_id: 'top-1',
            found_at: '2026-11-19T06:00:00Z',
          },
        ]);
      }
      if (url === '/api/watch_topics' && (!init?.method || init.method === 'GET')) {
        return jsonResponse([
          {
            id: 'top-1',
            query: 'local cli',
            sources: ['hn', 'github'],
            cadence_hours: 12,
            days: 7,
            enabled: true,
            created_at: '2026-11-19T05:00:00Z',
          },
        ]);
      }
      if (url === '/api/status') {
        return jsonResponse({ research_findings: { items: [] } });
      }
      if (url.startsWith('/api/sessions')) {
        return jsonResponse([]);
      }
      if (url === '/api/agent_chat' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          session_id: 'sess-res-1',
          response: 'Found discussion on [HN Thread](https://news.ycombinator.com/item?id=99).',
        });
      }
      if (url === '/api/watch_topics/top-1/toggle' && init?.method === 'POST') {
        return jsonResponse({ success: true, id: 'top-1', enabled: false });
      }
      if (url === '/api/watch_topics/top-1' && init?.method === 'DELETE') {
        return jsonResponse({ success: true, id: 'top-1' });
      }
      if (url === '/api/sessions/sess-live/cancel' && init?.method === 'POST') {
        return jsonResponse({ success: true, session: { id: 'sess-live', status: 'cancelled' } });
      }
      return jsonResponse({});
    });

    render(<ResearchSection />);

    await waitFor(() => {
      expect(screen.getByText('Local CLI agents discussion')).toBeTruthy();
    });
    expect(screen.getByText('news.ycombinator.com')).toBeTruthy();

    // Filter by source = github
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by source' }), {
      target: { value: 'github' },
    });
    expect(screen.queryByText('Local CLI agents discussion')).toBeNull();
    expect(screen.getByText('GitHub CLI benchmark')).toBeTruthy();

    // Ask researcher question
    fireEvent.change(screen.getByRole('textbox', { name: 'Research question' }), {
      target: { value: 'What are people saying about terminal AI?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start research' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/agent_chat',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            agent_id: 'researcher',
            message: 'What are people saying about terminal AI?',
          }),
        }),
      );
    });
    const hnLinks = screen.getAllByRole('link', { name: 'HN Thread' });
    expect(hnLinks.length).toBeGreaterThanOrEqual(1);
    expect(hnLinks[0].getAttribute('target')).toBe('_blank');

    // Toggle and delete watch topic
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/watch_topics/top-1/toggle',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/watch_topics/top-1',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });

    // Live browser_frame SSE event
    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('browser_frame', {
        session_id: 'sess-live',
        url: 'https://example.org/article',
        screenshot_path: 'shot_1.jpg',
      });
    });

    expect(screen.getByText('Live')).toBeTruthy();
    const img = screen.getByAltText('Live research browser view') as HTMLImageElement;
    expect(img.src).toContain('/api/browser_frame?path=shot_1.jpg');

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-live/cancel',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/research_items')) {
        return jsonResponse({ error: 'Search cache error' }, 500);
      }
      if (url === '/api/status') {
        return jsonResponse({ research_findings: { items: [] } });
      }
      return jsonResponse([]);
    });

    render(<ResearchSection />);
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Search cache error');
    });
    expect(screen.getByText(/No research items match the current filters/i)).toBeTruthy();
    expect(screen.getByText('No watch topics configured yet.')).toBeTruthy();
  });
});
