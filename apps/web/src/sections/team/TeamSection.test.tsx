import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TeamSection } from './TeamSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const MOCK_ROSTER = [
  { id: 'cmo', name: 'CMO', code: 'CMO', role: 'Plans week', status: 'idle', model_info: { model: 'nemotron-ultra', has_key: true } },
  { id: 'writer', name: 'Writer', code: 'WRT', role: 'Drafts posts', status: 'working', model_info: { model: 'minimax-m3.1', has_key: true } },
];

describe('TeamSection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders roster and team room messages, sends message, switches to 1-on-1 chat, saves memory and feedback', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url === '/api/assets') return jsonResponse([{ id: 'ast-1', name: 'cero.png', file_group: 'image' }]);
      if (url.startsWith('/api/sessions')) return jsonResponse([]);
      if (url === '/api/messages' && (!init?.method || init.method === 'GET')) {
        return jsonResponse([
          {
            id: 'm1',
            sender: 'writer',
            sender_name: 'Writer',
            sender_code: 'WRT',
            text: 'Draft ready for review.',
            content_ids: ['cnt-10'],
            model_used: 'minimax-m3.1',
            timestamp: '2026-11-19T08:00:00Z',
          },
        ]);
      }
      if (url === '/api/messages' && init?.method === 'POST') {
        return jsonResponse({ success: true, replies: [{ id: 'm2', sender: 'CEO', text: 'Nice' }] });
      }
      if (url === '/api/feedback' && init?.method === 'POST') {
        return jsonResponse({ success: true, memory: { id: 'mem-fb' } });
      }
      if (url === '/api/chat_history/writer') {
        return jsonResponse([
          { sender: 'user', text: 'Draft a post', timestamp: '2026-11-19T07:00:00Z' },
          { sender: 'writer', name: 'Writer', text: 'Here is the draft.', timestamp: '2026-11-19T07:01:00Z' },
        ]);
      }
      if (url === '/api/memory?agent_id=writer') {
        return jsonResponse([
          { id: 'mem-1', agent_id: 'writer', text: 'Keep hooks concise', source: 'manual', pinned: true, created_at: '2026-11-19T06:00:00Z' },
        ]);
      }
      if (url === '/api/memory' && init?.method === 'POST') {
        return jsonResponse({ success: true, id: 'mem-2', agent_id: 'writer', text: 'No hype words', source: 'manual', pinned: false, created_at: '2026-11-19T08:00:00Z' });
      }
      return jsonResponse({});
    });

    render(<TeamSection />);

    await waitFor(() => {
      expect(screen.getByText('Draft ready for review.')).toBeTruthy();
    });
    expect(screen.getByText('nemotron-ultra')).toBeTruthy();

    // Thumbs up feedback on message with content_ids
    fireEvent.click(screen.getByRole('button', { name: 'Thumbs up' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/feedback',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            content_id: 'cnt-10',
            text: 'Thumbs up on WRT · Writer output',
            agent_id: 'writer',
          }),
        }),
      );
    });

    // Send a message in Team room
    const composer = screen.getByRole('textbox', { name: 'Message composer' });
    fireEvent.change(composer, { target: { value: '@cmo check latest draft', selectionStart: 23 } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/messages',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            text: '@cmo check latest draft',
            sender: 'CEO',
            attached_assets: [],
          }),
        }),
      );
    });

    // Switch to Writer 1-on-1 chat + memory panel
    fireEvent.click(screen.getByRole('button', { name: /Writer/i }));
    await waitFor(() => {
      expect(screen.getByText('Here is the draft.')).toBeTruthy();
    });
    expect(screen.getByText('Keep hooks concise')).toBeTruthy();

    const memInput = screen.getByRole('textbox', { name: 'New memory for Writer' });
    fireEvent.change(memInput, { target: { value: 'No hype words' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/memory',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ agent_id: 'writer', text: 'No hype words', pinned: false }),
        }),
      );
    });
  });

  it('renders empty and error states', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/roster') return jsonResponse([]);
      if (url === '/api/messages') return jsonResponse({ error: 'Messages store offline' }, 500);
      return jsonResponse([]);
    });

    render(<TeamSection />);
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Messages store offline');
    });
    expect(screen.getByText(/No messages in the team room yet/i)).toBeTruthy();
  });
});
