import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AddResultsDialog } from './AddResultsDialog';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('AddResultsDialog', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('reads screenshot into editable fields with evidence, never auto-saves, and supports save and discard', async () => {
    const onSaved = vi.fn();
    const onDiscarded = vi.fn();
    const onClose = vi.fn();

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/results/extract' && init?.method === 'POST') {
        return jsonResponse({
          proposal: {
            id: 'mprop-99',
            content_id: 'cnt-2',
            platform: 'x',
            metrics: { views: 3400, likes: 80 },
            evidence: { views: '3,400', likes: '80' },
            image_path: 'assets/results/res_99.png',
            status: 'pending',
            created_at: '2026-11-22T00:00:00Z',
            decided_at: null,
          },
        });
      }
      if (url === '/api/results/proposals/mprop-99/confirm' && init?.method === 'POST') {
        return jsonResponse({
          proposal: { id: 'mprop-99', status: 'saved' },
          metric: { id: 'met-99', source: 'screenshot' },
        });
      }
      if (url === '/api/results/proposals/mprop-99/discard' && init?.method === 'POST') {
        return jsonResponse({ proposal: { id: 'mprop-99', status: 'discarded' } });
      }
      return jsonResponse({});
    });

    const posts = [
      {
        id: 'cnt-2',
        type: 'post',
        platform: 'x',
        title: 'X Launch Thread',
        status: 'posted',
        created_at: '2026-11-20T00:00:00Z',
        updated_at: '2026-11-20T00:00:00Z',
      },
    ];

    const { rerender } = render(
      <AddResultsDialog
        open
        onClose={onClose}
        initialContentId="cnt-2"
        initialPlatform="x"
        contentItems={posts}
        onSaved={onSaved}
        onDiscarded={onDiscarded}
      />,
    );

    // Post is prefilled
    const postSelect = screen.getByLabelText('Post') as HTMLSelectElement;
    expect(postSelect.value).toBe('cnt-2');

    // Pick screenshot
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'stats.png', {
      type: 'image/png',
    });
    const fileInput = screen.getByLabelText(/Analytics screenshot \(PNG/i);
    fireEvent.change(fileInput, { target: { files: [file] } });

    // Click Read screenshot
    fireEvent.click(screen.getByRole('button', { name: 'Read screenshot' }));

    await waitFor(() => {
      expect(screen.getByText('Seen: 3,400')).toBeTruthy();
    });

    // Evidence, thumbnail, and empty fields for missing metrics are visible
    expect(screen.getByText('Seen: 80')).toBeTruthy();
    expect(screen.getAllByText('Not found').length).toBeGreaterThan(0);
    expect((screen.getByLabelText('Views') as HTMLInputElement).value).toBe('3400');
    expect((screen.getByLabelText('Comments') as HTMLInputElement).value).toBe('');
    expect(screen.getByAltText('Analytics screenshot')).toBeTruthy();

    // Never saves without clicking Save results
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/results/proposals/mprop-99/confirm',
      expect.anything(),
    );

    // Edit field and click Save results
    fireEvent.change(screen.getByLabelText('Comments'), { target: { value: '14' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/results/proposals/mprop-99/confirm',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            metrics: { views: 3400, likes: 80, comments: 14 },
            content_id: 'cnt-2',
            platform: 'x',
          }),
        }),
      );
    });
    expect(onSaved).toHaveBeenCalledTimes(1);

    // Reopen with pending proposal and test Discard
    rerender(
      <AddResultsDialog
        open
        onClose={onClose}
        initialProposal={{
          id: 'mprop-99',
          content_id: 'cnt-2',
          platform: 'x',
          metrics: { views: 3400 },
          evidence: { views: '3,400' },
          image_path: 'assets/results/res_99.png',
          status: 'pending',
          created_at: '2026-11-22T00:00:00Z',
          decided_at: null,
        }}
        contentItems={posts}
        onSaved={onSaved}
        onDiscarded={onDiscarded}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/results/proposals/mprop-99/discard',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(onDiscarded).toHaveBeenCalledWith('mprop-99');
  });
});
