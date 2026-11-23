import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SettingsSection } from './SettingsSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockEngine() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url === '/api/health') return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/x' });
    if (url === '/api/status') return jsonResponse({ env_status: { default_provider: 'nvidia_nim' }, models_matrix: [] });
    if (url === '/api/key_pool') return jsonResponse([]);
    if (url === '/api/setup_check') return jsonResponse({ ok: true, checks: [] });
    if (url === '/api/phone') {
      return jsonResponse({
        enabled: false, running: false, host: null, port: 8788,
        pairing: { active: false, qr_payload: null, expires_at: null, expires_in: 0, pending_requests: [] },
        devices: [],
      });
    }
    if (url.startsWith('/api/bakeoff/latest')) return jsonResponse({ error: 'Not found' }, 404);
    if (url.startsWith('/api/jobs?')) return jsonResponse([]);
    return jsonResponse({});
  });
}

describe('Settings section index and search', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
    Element.prototype.scrollIntoView = vi.fn();
    mockEngine();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lists a jump link for every Settings section in a labelled navigation', async () => {
    const { container } = render(<SettingsSection />);
    const nav = await screen.findByRole('navigation', { name: 'Settings sections' });
    const headings = Array.from(container.querySelectorAll('section.studio-section h2')).map((h) => h.textContent?.trim());
    expect(headings.length).toBeGreaterThanOrEqual(10);
    await waitFor(() => {
      const links = within(nav).getAllByRole('button').map((b) => b.textContent?.trim());
      for (const title of headings) expect(links).toContain(title);
    });
  });

  it('jumps to a section, moves focus to its heading, and works from the keyboard', async () => {
    render(<SettingsSection />);
    const nav = await screen.findByRole('navigation', { name: 'Settings sections' });
    const link = await within(nav).findByRole('button', { name: 'Phone' });
    link.focus();
    fireEvent.click(link);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    const heading = screen.getByRole('heading', { name: 'Phone' });
    expect(document.activeElement).toBe(heading);
  });

  it('filters sections by title and by the labels inside them, and shows a message when nothing matches', async () => {
    const { container } = render(<SettingsSection />);
    const search = (await screen.findByRole('searchbox', { name: 'Search settings' })) as HTMLInputElement;
    const visibleTitles = () =>
      Array.from(container.querySelectorAll<HTMLElement>('section.studio-section'))
        .filter((s) => !s.hidden)
        .map((s) => s.querySelector('h2')?.textContent?.trim());

    // By title
    fireEvent.change(search, { target: { value: 'phone' } });
    await waitFor(() => expect(visibleTitles()).toEqual(['Phone']));

    // By a label inside a section: "Fallback model" lives in Engines and models
    fireEvent.change(search, { target: { value: 'fallback model' } });
    await waitFor(() => expect(visibleTitles()).toEqual(['Engines and models']));

    // The jump list follows the filter
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).queryByRole('button', { name: 'Phone' })).toBeNull();

    fireEvent.change(search, { target: { value: 'zzzz-nothing' } });
    await waitFor(() => expect(visibleTitles()).toEqual([]));
    expect(screen.getByText(/No settings match/i)).toBeTruthy();

    // Escape clears the search and shows everything again
    fireEvent.keyDown(search, { key: 'Escape' });
    await waitFor(() => expect(visibleTitles().length).toBeGreaterThanOrEqual(10));
    expect(search.value).toBe('');
  });
});
