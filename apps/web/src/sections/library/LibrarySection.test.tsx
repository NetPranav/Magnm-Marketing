import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LibrarySection } from './LibrarySection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('LibrarySection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders assets and content, opens side panel, uploads asset, and deletes with confirm dialog', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/assets') {
        return jsonResponse([
          {
            id: 'asset_1',
            name: 'cero-prompt.png',
            file_group: 'image',
            mime_type: 'image/png',
            size: '42.0 KB',
            category: 'cero',
            description: 'Approval prompt',
            uploaded_at: '2026-11-19T08:00:00Z',
          },
          {
            id: 'asset_2',
            name: 'demo-clip.mp4',
            file_group: 'video',
            mime_type: 'video/mp4',
            size: '1.2 MB',
            category: 'recording',
            uploaded_at: '2026-11-19T07:00:00Z',
          },
        ]);
      }
      if (url.startsWith('/api/content?')) {
        return jsonResponse([
          {
            id: 'cnt-10',
            type: 'post',
            platform: 'linkedin',
            title: 'Cero safety release',
            status: 'ready',
            created_at: '2026-11-19T06:00:00Z',
            updated_at: '2026-11-19T06:30:00Z',
          },
        ]);
      }
      if (url === '/api/assets/upload' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          asset: {
            id: 'asset_3',
            name: 'note.txt',
            file_group: 'document',
            mime_type: 'text/plain',
            size: '0.1 KB',
            category: 'cero',
          },
        });
      }
      if (url === '/api/assets/delete' && init?.method === 'POST') {
        return jsonResponse({ success: true });
      }
      return jsonResponse({});
    });

    render(<LibrarySection />);

    await waitFor(() => {
      expect(screen.getByText('cero-prompt.png')).toBeTruthy();
    });
    expect(screen.getByText('demo-clip.mp4')).toBeTruthy();
    expect(screen.getByText('IMAGE · 42.0 KB')).toBeTruthy();

    // Content list links to Review
    const reviewLink = screen.getByRole('link', { name: /Cero safety release/i });
    expect(reviewLink.getAttribute('href')).toBe('#/review/cnt-10');

    // Open side panel and check download link
    fireEvent.click(screen.getByText('cero-prompt.png'));
    const panel = screen.getByRole('complementary', { name: 'Asset details' });
    expect(panel.textContent).toContain('Approval prompt');
    const dlLink = screen.getByRole('link', { name: 'Download' });
    expect(dlLink.getAttribute('href')).toBe('/api/asset_file/asset_1');

    // Filter assets by type
    fireEvent.change(screen.getByLabelText('Filter assets by type'), { target: { value: 'video' } });
    expect(screen.queryByRole('button', { name: 'cero-prompt.png' })).toBeNull();
    expect(screen.getByText('demo-clip.mp4')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Filter assets by type'), { target: { value: 'all' } });

    // Upload asset
    const file = new File(['hello'], 'note.txt', { type: 'text/plain' });
    const fileInput = screen.getByLabelText(/File \(drag and drop or pick\)/i);
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload asset' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/assets/upload',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            filename: 'note.txt',
            content_base64: 'aGVsbG8=',
            category: 'cero',
            description: '',
          }),
        }),
      );
    });

    // Delete asset via confirm dialog
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(deleteButtons[0]);
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Delete asset' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/assets/delete',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ id: 'asset_3' }),
        }),
      );
    });
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/assets') {
        return jsonResponse({ error: 'Assets index unreadable' }, 500);
      }
      return jsonResponse([]);
    });

    render(<LibrarySection />);
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Assets index unreadable');
    });
    expect(screen.getByText('No assets uploaded yet.')).toBeTruthy();
    expect(screen.getByText('No content items match this status.')).toBeTruthy();
  });
});
