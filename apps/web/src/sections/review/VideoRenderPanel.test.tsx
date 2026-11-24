import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { VideoRenderPanel } from './VideoRenderPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

interface Routes {
  [key: string]: (body?: unknown) => Response | Promise<Response>;
}

function mockFetch(routes: Routes) {
  const calls: Array<{ method: string; url: string; body?: unknown }> = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const handler = routes[`${method} ${url}`];
    if (!handler) return jsonResponse({ error: `no route ${method} ${url}` }, 404);
    return handler(body);
  });
  return calls;
}

describe('VideoRenderPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('offers Render video, queues it, and shows progress with Cancel', async () => {
    const calls = mockFetch({
      'GET /api/video/render/cnt_1': () => jsonResponse({ content_id: 'cnt_1', state: 'idle', files: [] }),
      'POST /api/video/render': () =>
        jsonResponse({
          success: true,
          job: { id: 'j1' },
          status: { content_id: 'cnt_1', state: 'running', percent: 42, message: 'Encoding 9:16 video' },
        }),
    });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} />);
    const button = await screen.findByRole('button', { name: 'Render video' });
    fireEvent.click(button);
    await screen.findByRole('button', { name: 'Cancel render' });
    expect(screen.getByText(/Encoding 9:16 video/)).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Render progress' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Render video' })).toBeNull();
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toEqual({ content_id: 'cnt_1', voice: true });
  });

  it('says Render again when a video exists', async () => {
    mockFetch({ 'GET /api/video/render/cnt_1': () => jsonResponse({ content_id: 'cnt_1', state: 'done' }) });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo />);
    await screen.findByRole('button', { name: 'Render again' });
  });

  it('shows the plain no-voice error with Download voice and Render without a voice', async () => {
    const calls = mockFetch({
      'GET /api/video/render/cnt_1': () => jsonResponse({ content_id: 'cnt_1', state: 'idle' }),
      'POST /api/video/render': (body) => {
        const voice = (body as { voice: boolean }).voice;
        if (voice) {
          return jsonResponse(
            { error: 'The voice is not installed yet. Open Settings > Video and press Download voice.', code: 'no_voice' },
            409,
          );
        }
        return jsonResponse({ success: true, job: { id: 'j2' }, status: { content_id: 'cnt_1', state: 'queued' } });
      },
    });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Render video' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('The voice is not installed yet');
    expect(screen.getByRole('button', { name: 'Download voice' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Render without a voice' }));
    await screen.findByText(/Waiting for another video to finish/);
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts.map((p) => (p.body as { voice: boolean }).voice)).toEqual([true, false]);
  });

  it('downloads the voice from the error and then asks to render again', async () => {
    let downloaded = false;
    mockFetch({
      'GET /api/video/render/cnt_1': () => jsonResponse({ content_id: 'cnt_1', state: 'idle' }),
      'POST /api/video/render': () => jsonResponse({ error: 'No voice. Download voice.', code: 'no_voice' }, 409),
      'POST /api/video/voice/download': () => {
        downloaded = true;
        return jsonResponse({ success: true, installed: false, job: { id: 'jd' } });
      },
      'GET /api/jobs?limit=20': () =>
        jsonResponse([{ id: 'jd', name: 'download_voice', status: 'done', progress: 100, message: 'The voice is ready' }]),
    });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Render video' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Download voice' }));
    await screen.findByText(/The voice is installed/);
    expect(downloaded).toBe(true);
  });

  it('shows a failed render in plain words', async () => {
    mockFetch({
      'GET /api/video/render/cnt_1': () =>
        jsonResponse({ content_id: 'cnt_1', state: 'failed', error: 'The 9:16 video has a blank picture at 3.0 s.', error_code: 'check_failed' }),
    });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('blank picture');
    expect(screen.queryByRole('button', { name: 'Download voice' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Render video' })).toBeTruthy();
  });

  it('cancels a running render', async () => {
    const calls = mockFetch({
      'GET /api/video/render/cnt_1': () => jsonResponse({ content_id: 'cnt_1', state: 'running', percent: 10, message: 'Mixing the audio' }),
      'POST /api/video/render/cancel': () =>
        jsonResponse({ success: true, status: { content_id: 'cnt_1', state: 'cancelled' } }),
    });
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel render' }));
    await screen.findByText('The render was cancelled.');
    expect(calls.some((c) => c.url === '/api/video/render/cancel' && (c.body as { content_id: string }).content_id === 'cnt_1')).toBe(true);
  });

  it('polls while rendering and tells the page when the video is ready', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    mockFetch({
      'GET /api/video/render/cnt_1': () => {
        n += 1;
        return jsonResponse(
          n < 3
            ? { content_id: 'cnt_1', state: 'running', percent: n * 30, message: 'Encoding 16:9 video' }
            : { content_id: 'cnt_1', state: 'done', percent: 100 },
        );
      },
    });
    const onChanged = vi.fn();
    render(<VideoRenderPanel contentId="cnt_1" hasVideo={false} onChanged={onChanged} />);
    await screen.findByRole('button', { name: 'Cancel render' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Cancel render' })).toBeNull();
  });
});
