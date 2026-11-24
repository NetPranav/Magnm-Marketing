import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { VideoSettingsPanel } from './VideoSettingsPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const VOICES = [
  { id: 'am_michael', label: 'Michael (US, male)', lang: 'en-us' },
  { id: 'af_heart', label: 'Heart (US, female)', lang: 'en-us' },
];

function status(installed: boolean, settings = {}) {
  return {
    voice: {
      installed, missing: installed ? [] : ['voices-v1.0.bin'], dir: '/x', size_mb: 112, library: true, voices: VOICES,
      default_voice: 'am_michael', default_speed: 1, min_speed: 0.7, max_speed: 1.3,
    },
    settings: { voice: 'am_michael', speed: 1, captions: true, flux: false, ...settings },
    ffmpeg: true, browser: true, rendering: [],
  };
}

describe('VideoSettingsPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the voice state, offers Download voice and shows the download progress', async () => {
    let installed = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method ?? 'GET';
      if (url === '/api/video/status') return jsonResponse(status(installed));
      if (url === '/api/video/voice/download' && method === 'POST') return jsonResponse({ success: true, installed: false, job: { id: 'jd' } });
      if (url === '/api/jobs?limit=20') {
        installed = true;
        return jsonResponse([{ id: 'jd', name: 'download_voice', status: 'done', progress: 100, message: 'The voice is ready' }]);
      }
      return jsonResponse({ error: 'no route' }, 404);
    });
    render(<VideoSettingsPanel />);
    await screen.findByText(/Not installed \(about 112 MB to download\)/);
    fireEvent.click(screen.getByRole('button', { name: 'Download voice' }));
    await waitFor(() => expect(screen.getByText('Installed')).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Download voice' })).toBeNull();
  });

  it('saves the voice, speed and caption choices', async () => {
    const saved: unknown[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/video/status') return jsonResponse(status(true));
      if (url === '/api/video/settings') {
        const body = JSON.parse(String(init?.body));
        saved.push(body);
        return jsonResponse({ settings: { voice: 'am_michael', speed: 1, captions: true, flux: false, ...body } });
      }
      return jsonResponse({ error: 'no route' }, 404);
    });
    render(<VideoSettingsPanel />);
    const select = (await screen.findByLabelText('Voice', { selector: 'select' })) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'af_heart' } });
    await waitFor(() => expect(saved).toContainEqual({ voice: 'af_heart' }));
    fireEvent.click(screen.getByLabelText(/Burn captions into the video/));
    await waitFor(() => expect(saved).toContainEqual({ captions: false }));
    fireEvent.click(screen.getByLabelText(/Draw illustrations with FLUX/));
    await waitFor(() => expect(saved).toContainEqual({ flux: true }));
    const slider = screen.getByLabelText(/Speed/) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '1.2' } });
    fireEvent.keyUp(slider);
    await waitFor(() => expect(saved).toContainEqual({ speed: 1.2 }));
  });

  it('plays a sample and explains a failure in plain words', async () => {
    const played: string[] = [];
    class FakeAudio {
      onended: (() => void) | null = null;
      constructor(public src: string) {}
      play() {
        played.push(this.src);
        return Promise.resolve();
      }
    }
    vi.stubGlobal('Audio', FakeAudio);
    Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:sample', configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: () => undefined, configurable: true });
    let sampleOk = true;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/video/status') return jsonResponse(status(true));
      if (url.startsWith('/api/video/voice/sample')) {
        return sampleOk
          ? new Response(new Blob(['RIFF'], { type: 'audio/wav' }), { status: 200 })
          : jsonResponse({ error: 'The voice is not installed yet. Download voice.', code: 'no_voice' }, 409);
      }
      return jsonResponse({ error: 'no route' }, 404);
    });
    render(<VideoSettingsPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Play sample' }));
    await waitFor(() => expect(played).toEqual(['blob:sample']));
    sampleOk = false;
    fireEvent.click(await screen.findByRole('button', { name: 'Play sample' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('The voice is not installed yet');
  });

  it('disables Play sample until the voice is installed', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse(status(false)));
    render(<VideoSettingsPanel />);
    const button = (await screen.findByRole('button', { name: 'Play sample' })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
