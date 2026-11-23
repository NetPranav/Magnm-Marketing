import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { KEYCHAIN_EXTERNAL_NOTE, saveEnvConfig } from '../../api/settings';
import { isDesktop, isSecretEnvName } from '../../desktop';
import { SettingsSection } from './SettingsSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('SettingsSection', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    delete window.__TAURI__;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders models, key pool status without masked keys, setup check, bakeoff, and clears key after save', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, service: 'magnm-engine', version: '0.1.0', data_root: '/tmp/magnm-data' });
      }
      if (url === '/api/status') {
        return jsonResponse({
          env_status: {
            default_provider: 'nvidia_nim',
            nvidia_nim_base_url: 'https://integrate.api.nvidia.com/v1',
            nvidia_nim_global: true,
            nvidia_nim_global_masked: 'nvapi-****9999',
            nvidia_nim_fallback_model: 'meta/llama-3.3-70b-instruct',
            openai: false,
            anthropic: false,
            gemini: false,
            groq: false,
          },
          nim_catalog_suggestions: ['minimaxai/minimax-m3.1-flash', 'nvidia/nemotron-3-ultra-550b-a55b'],
          models_matrix: [
            {
              role_id: 'writer',
              name: 'Social Copywriter',
              code: 'WRT',
              model_env: 'NIM_MODEL_WRITER',
              key_env: 'NVIDIA_NIM_API_KEY_WRITER',
              model: 'minimaxai/minimax-m3.1-flash',
              primary: 'minimaxai/minimax-m3.1-flash',
              backup: 'nvidia/nemotron-3-ultra-550b-a55b',
              tier: 'writing',
              has_key: true,
              key_source: 'pool (1 key)',
              masked_key: 'nvapi-****8888',
            },
          ],
        });
      }
      if (url === '/api/key_pool') {
        return jsonResponse([
          {
            label: 'NVIDIA_NIM_API_KEY',
            masked: 'nvapi-****7777',
            healthy: true,
            cooling_down_s: 0,
            requests_last_min: 2,
            ok: 5,
            errors: 0,
          },
        ]);
      }
      if (url === '/api/setup_check') {
        return jsonResponse({
          ok: true,
          checks: [{ id: 'python', label: 'Python Runtime', status: 'ok', detail: 'Python 3.12' }],
        });
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({
          timestamp: '20261119_120000',
          role: 'writer',
          summary: {
            'minimaxai/minimax-m3.1-flash': {
              model: 'minimaxai/minimax-m3.1-flash',
              count: 5,
              median_latency_ms: 1200,
              auto_review_warns: 0,
              errors: 0,
            },
          },
        });
      }
      if (url.startsWith('/api/jobs?')) {
        return jsonResponse([]);
      }
      if (url === '/api/save_env_config' && init?.method === 'POST') {
        return jsonResponse({ success: true, saved_keys: ['NVIDIA_NIM_API_KEY'] });
      }
      if (url === '/api/test_ai' && init?.method === 'POST') {
        return jsonResponse({
          success: true,
          role_id: 'writer',
          latency_ms: 310,
          model_used: 'minimaxai/minimax-m3.1-flash',
        });
      }
      return jsonResponse({});
    });

    render(<SettingsSection />);

    await waitFor(() => {
      expect(screen.getByText('Social Copywriter')).toBeTruthy();
    });

    // Masked keys from API must NEVER appear anywhere
    expect(document.body.textContent).not.toContain('nvapi-****9999');
    expect(document.body.textContent).not.toContain('nvapi-****8888');
    expect(document.body.textContent).not.toContain('nvapi-****7777');

    // About section data folder and /legacy link
    expect(screen.getByText('/tmp/magnm-data')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Old dashboard' }).getAttribute('href')).toBe('/legacy');

    // Bake-off table
    expect(screen.getByText('1200 ms')).toBeTruthy();

    // Test AI button
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    await waitFor(() => {
      expect(screen.getByText(/310ms/)).toBeTruthy();
    });

    // Save key: password field, autocomplete off, cleared after save, key never rendered anywhere
    const keyInput = screen.getByLabelText('Key value') as HTMLInputElement;
    expect(keyInput.type).toBe('password');
    expect(keyInput.getAttribute('autocomplete')).toBe('off');

    const secretKey = 'nvapi-secret-test-key-12345';
    fireEvent.change(keyInput, { target: { value: secretKey } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/save_env_config',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ updates: { NVIDIA_NIM_API_KEY: secretKey } }),
        }),
      );
    });

    await waitFor(() => {
      expect(keyInput.value).toBe('');
    });
    expect(document.body.textContent).not.toContain(secretKey);
    expect(document.body.innerHTML).not.toContain(secretKey);
  });

  it('renders empty and error states including 404 bakeoff', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/empty' });
      }
      if (url === '/api/status') {
        return jsonResponse({ error: 'Engine config error' }, 500);
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({ error: 'No bakeoff results found' }, 404);
      }
      if (url === '/api/setup_check') {
        return jsonResponse({ ok: true, checks: [] });
      }
      return jsonResponse([]);
    });

    render(<SettingsSection />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Engine config error');
    });
    expect(screen.getByText('No NVIDIA NIM keys in pool yet.')).toBeTruthy();
    expect(screen.getByText('No bake-off results yet.')).toBeTruthy();
  });

  it('matches engine _is_secret_env_name rules', () => {
    expect(isSecretEnvName('NVIDIA_NIM_API_KEY')).toBe(true);
    expect(isSecretEnvName('NVIDIA_NIM_API_KEY3')).toBe(true);
    expect(isSecretEnvName('NVIDIA_NIM_API_KEY_CMO')).toBe(true);
    expect(isSecretEnvName('OPENAI_API_KEY')).toBe(true);
    expect(isSecretEnvName('GITHUB_TOKEN')).toBe(true);
    expect(isSecretEnvName('DEFAULT_PROVIDER')).toBe(false);
  });

  it('uses desktop keychain for secrets, keeps non-secrets on /api/save_env_config, and confirms before delete_secret', async () => {
    let secretsState = [
      { name: 'NVIDIA_NIM_API_KEY', is_set: true },
      { name: 'OPENAI_API_KEY', is_set: false },
    ];
    const setSecretGate: { resolve: (() => void) | null } = { resolve: null };

    const invokeMock = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'list_secrets') {
        return secretsState;
      }
      if (cmd === 'set_secret') {
        await new Promise<void>((resolve) => {
          setSecretGate.resolve = () => {
            secretsState = secretsState.map((item) =>
              item.name === args?.name ? { ...item, is_set: true } : item,
            );
            resolve();
          };
        });
        return null;
      }
      if (cmd === 'delete_secret') {
        secretsState = secretsState.map((item) =>
          item.name === args?.name ? { ...item, is_set: false } : item,
        );
        return null;
      }
      return null;
    });

    window.__TAURI__ = { core: { invoke: invokeMock } };
    expect(isDesktop()).toBe(true);

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/magnm-desktop' });
      }
      if (url === '/api/status') {
        // Deliberately opposite of list_secrets to verify status comes from list_secrets, not engine
        return jsonResponse({
          env_status: {
            default_provider: 'nvidia_nim',
            nvidia_nim_base_url: 'https://integrate.api.nvidia.com/v1',
            nvidia_nim_global: false,
            openai: true,
            anthropic: false,
            gemini: false,
            groq: false,
          },
          nim_catalog_suggestions: [],
          models_matrix: [],
        });
      }
      if (url === '/api/key_pool') {
        return jsonResponse([]);
      }
      if (url === '/api/setup_check') {
        return jsonResponse({ ok: true, checks: [] });
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({ error: 'Not found' }, 404);
      }
      if (url.startsWith('/api/jobs?')) {
        return jsonResponse([]);
      }
      if (url === '/api/save_env_config' && init?.method === 'POST') {
        const parsed = JSON.parse(String(init.body || '{}')) as {
          updates?: Record<string, string>;
        };
        return jsonResponse({
          success: true,
          saved_keys: Object.keys(parsed.updates || {}),
        });
      }
      return jsonResponse({});
    });

    render(<SettingsSection />);

    const keychainList = await screen.findByLabelText('Desktop keychain keys');
    const nimRow = within(keychainList).getByText('NVIDIA_NIM_API_KEY').closest('.mg-row') as HTMLElement;
    const openaiRow = within(keychainList).getByText('OPENAI_API_KEY').closest('.mg-row') as HTMLElement;

    expect(within(nimRow).getByText('Configured')).toBeTruthy();
    expect(within(openaiRow).getByText('Not configured')).toBeTruthy();
    expect(screen.getByText('NVIDIA NIM: Configured')).toBeTruthy();
    expect(screen.getByText('OpenAI: Not configured')).toBeTruthy();

    // Save non-secret gateway setting via /api/save_env_config
    fireEvent.click(screen.getByRole('button', { name: 'Save gateway' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/save_env_config',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            updates: {
              AI_DEFAULT_PROVIDER: 'nvidia_nim',
              NVIDIA_NIM_BASE_URL: 'https://integrate.api.nvidia.com/v1',
            },
          }),
        }),
      );
    });
    expect(invokeMock).not.toHaveBeenCalledWith('set_secret', expect.anything());

    // Saving a secret clears input immediately, shows "Saved. Restarting engine..." before resolving,
    // invokes set_secret, and never sends secret to /api/save_env*
    const openaiInput = within(openaiRow).getByLabelText('Key value for OPENAI_API_KEY') as HTMLInputElement;
    expect(openaiInput.type).toBe('password');
    expect(openaiInput.getAttribute('autocomplete')).toBe('off');

    const secretVal = 'sk-desktop-secret-value-999';
    fireEvent.change(openaiInput, { target: { value: secretVal } });
    fireEvent.click(within(openaiRow).getByRole('button', { name: 'Save' }));

    expect(openaiInput.value).toBe('');
    expect(screen.getByText('Saved. Restarting engine...')).toBeTruthy();
    expect(invokeMock).toHaveBeenCalledWith('set_secret', {
      name: 'OPENAI_API_KEY',
      value: secretVal,
    });
    expect(document.body.textContent).not.toContain(secretVal);
    expect(document.body.innerHTML).not.toContain(secretVal);

    setSecretGate.resolve?.();
    await waitFor(() => {
      expect(within(openaiRow).getByText('Configured')).toBeTruthy();
    });

    const saveEnvCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).startsWith('/api/save_env'),
    );
    for (const [, init] of saveEnvCalls) {
      expect(String(init?.body || '')).not.toContain('OPENAI_API_KEY');
      expect(String(init?.body || '')).not.toContain(secretVal);
    }

    // Splitting mixed updates sends non-secrets first, then secrets through set_secret
    const mixedOrder: string[] = [];
    fetchMock.mockImplementationOnce(async (input, init) => {
      mixedOrder.push(`fetch:${String(input)}`);
      const parsed = JSON.parse(String(init?.body || '{}')) as {
        updates?: Record<string, string>;
      };
      expect(parsed.updates).toEqual({ DEFAULT_PROVIDER: 'openai' });
      return jsonResponse({ success: true, saved_keys: ['DEFAULT_PROVIDER'] });
    });
    invokeMock.mockImplementationOnce(async (cmd: string, args?: Record<string, unknown>) => {
      mixedOrder.push(`invoke:${cmd}:${String(args?.name)}`);
      return null;
    });
    await saveEnvConfig({ DEFAULT_PROVIDER: 'openai', GITHUB_TOKEN: 'ghp-mixed-secret' });
    expect(mixedOrder).toEqual(['fetch:/api/save_env_config', 'invoke:set_secret:GITHUB_TOKEN']);

    // Remove calls delete_secret only after confirming in dialog
    fireEvent.click(within(nimRow).getByRole('button', { name: 'Remove' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(invokeMock).not.toHaveBeenCalledWith('delete_secret', expect.anything());

    fireEvent.click(screen.getByRole('button', { name: 'Remove key' }));
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('delete_secret', { name: 'NVIDIA_NIM_API_KEY' });
    });
    await waitFor(() => {
      expect(within(nimRow).getByText('Not configured')).toBeTruthy();
    });
  });

  describe('desktop engine restart', () => {
    function desktopSetup(opts: { healthDownFor?: number } = {}) {
      let secretsState = [
        { name: 'NVIDIA_NIM_API_KEY', is_set: true },
        { name: 'OPENAI_API_KEY', is_set: false },
      ];
      const gate: { release: (() => void) | null } = { release: null };
      const invokeMock = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
        if (cmd === 'list_secrets') return secretsState;
        if (cmd === 'set_secret' || cmd === 'delete_secret' || cmd === 'restart_engine') {
          await new Promise<void>((resolve) => {
            gate.release = () => {
              if (cmd === 'set_secret') {
                secretsState = secretsState.map((i) => (i.name === args?.name ? { ...i, is_set: true } : i));
              }
              if (cmd === 'delete_secret') {
                secretsState = secretsState.map((i) => (i.name === args?.name ? { ...i, is_set: false } : i));
              }
              resolve();
            };
          });
        }
        return null;
      });
      window.__TAURI__ = { core: { invoke: invokeMock } };
      let healthCalls = 0;
      let down = 0;
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/health') {
          healthCalls += 1;
          if (down > 0) {
            down -= 1;
            throw new TypeError('Failed to fetch');
          }
          return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/magnm-desktop' });
        }
        if (url === '/api/status') {
          return jsonResponse({ env_status: { default_provider: 'nvidia_nim' }, nim_catalog_suggestions: [], models_matrix: [] });
        }
        if (url === '/api/key_pool') return jsonResponse([]);
        if (url === '/api/setup_check') return jsonResponse({ ok: true, checks: [] });
        if (url.startsWith('/api/bakeoff/latest')) return jsonResponse({ error: 'Not found' }, 404);
        if (url.startsWith('/api/jobs?')) return jsonResponse([]);
        return jsonResponse({});
      });
      return {
        invokeMock,
        fetchMock,
        gate,
        healthCalls: () => healthCalls,
        setDown: (n: number) => {
          down = n;
        },
        opts,
      };
    }

    it('keeps the page where it is while a saved key restarts the engine, locks the key buttons, then unlocks them', async () => {
      const env = desktopSetup();
      const hrefBefore = window.location.href;
      render(<SettingsSection />);
      const keychainList = await screen.findByLabelText('Desktop keychain keys');
      const openaiRow = within(keychainList).getByText('OPENAI_API_KEY').closest('.mg-row') as HTMLElement;
      const nimRow = within(keychainList).getByText('NVIDIA_NIM_API_KEY').closest('.mg-row') as HTMLElement;

      fireEvent.change(within(openaiRow).getByLabelText('Key value for OPENAI_API_KEY'), { target: { value: 'sk-test-value-1' } });
      fireEvent.click(within(openaiRow).getByRole('button', { name: 'Save' }));

      expect(screen.getByText(/Restarting engine\.\.\./)).toBeTruthy();
      await waitFor(() => expect((within(openaiRow).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true));
      expect((within(nimRow).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
      expect((within(nimRow).getByRole('button', { name: 'Remove' }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByRole('button', { name: 'Restart engine' }) as HTMLButtonElement).disabled).toBe(true);

      // The engine is still down for a while after the command returns: the page waits for /api/health.
      env.setDown(2);
      env.gate.release?.();
      await waitFor(() => expect(within(openaiRow).getByText('Configured')).toBeTruthy(), { timeout: 5000 });
      expect(env.healthCalls()).toBeGreaterThanOrEqual(3);
      expect((within(openaiRow).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
      expect(screen.queryByText(/Restarting engine\.\.\./)).toBeNull();
      expect(window.location.href).toBe(hrefBefore);
      expect(screen.getByRole('heading', { name: 'Settings' })).toBeTruthy();
    });

    it('does the same when a key is removed and when Restart engine is pressed', async () => {
      const env = desktopSetup();
      render(<SettingsSection />);
      const keychainList = await screen.findByLabelText('Desktop keychain keys');
      const nimRow = within(keychainList).getByText('NVIDIA_NIM_API_KEY').closest('.mg-row') as HTMLElement;

      fireEvent.click(within(nimRow).getByRole('button', { name: 'Remove' }));
      fireEvent.click(screen.getByRole('button', { name: 'Remove key' }));
      await waitFor(() => expect(env.invokeMock).toHaveBeenCalledWith('delete_secret', { name: 'NVIDIA_NIM_API_KEY' }));
      expect(screen.getByText(/Restarting engine\.\.\./)).toBeTruthy();
      env.gate.release?.();
      await waitFor(() => expect(within(nimRow).getByText('Not configured')).toBeTruthy(), { timeout: 5000 });
      await waitFor(() => expect((screen.getByRole('button', { name: 'Restart engine' }) as HTMLButtonElement).disabled).toBe(false));

      fireEvent.click(screen.getByRole('button', { name: 'Restart engine' }));
      await waitFor(() => expect(env.invokeMock).toHaveBeenCalledWith('restart_engine'));
      expect(screen.getByText(/Restarting engine\.\.\./)).toBeTruthy();
      expect((within(nimRow).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
      env.gate.release?.();
      await waitFor(() => expect((within(nimRow).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false), { timeout: 5000 });
      expect(screen.queryByText(/Restarting engine\.\.\./)).toBeNull();
    });
  });

  it('shows desktop keychain note when engine returns use_desktop_keychain outside desktop', async () => {
    expect(isDesktop()).toBe(false);

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/magnm-data' });
      }
      if (url === '/api/status') {
        return jsonResponse({ env_status: {}, models_matrix: [] });
      }
      if (url === '/api/setup_check') {
        return jsonResponse({ ok: true, checks: [] });
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({ error: 'Not found' }, 404);
      }
      if (url === '/api/save_env_config' && init?.method === 'POST') {
        return jsonResponse({
          success: false,
          error_code: 'use_desktop_keychain',
          error: "Keys are stored in the desktop app's secure keychain. Use Settings > Keys.",
        });
      }
      return jsonResponse([]);
    });

    render(<SettingsSection />);

    const keyInput = await screen.findByLabelText('Key value') as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'nvapi-external-attempt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));

    await waitFor(() => {
      expect(screen.getByText(KEYCHAIN_EXTERNAL_NOTE)).toBeTruthy();
    });
    expect(keyInput.value).toBe('');
  });

  it('edits and resets Pitch base prices (admin) and shows queued outbox count', async () => {
    let wFrontOverridden = false;
    let wFrontPrice = 6000;
    let pendingCount = 2;

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/magnm-data' });
      }
      if (url === '/api/status') {
        return jsonResponse({ env_status: {}, models_matrix: [] });
      }
      if (url === '/api/setup_check') {
        return jsonResponse({ ok: true, checks: [] });
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({ error: 'Not found' }, 404);
      }
      if (url === '/api/pitch/catalog') {
        return jsonResponse({
          items: {
            w_front: {
              g: 'Web & SaaS',
              n: 'Responsive Web Application / Frontend',
              d: 'Frontend',
              r: [],
              p: wFrontPrice,
              base_price: 6000,
              price: wFrontPrice,
              overridden: wFrontOverridden,
            },
          },
          groups: ['Web & SaaS'],
          standalone_fee: 4000,
          standalone_label: 'App-only setup',
          presets: {},
        });
      }
      if (url.startsWith('/api/pitch/outbox')) {
        return jsonResponse({ events: [], pending_count: pendingCount });
      }
      if (url === '/api/pitch/base_prices' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { item_id: string; price: number };
        wFrontOverridden = true;
        wFrontPrice = body.price;
        pendingCount += 1;
        return jsonResponse({
          admin_only: true,
          item_id: body.item_id,
          old_price: 6000,
          new_price: body.price,
          item: {
            g: 'Web & SaaS',
            n: 'Responsive Web Application / Frontend',
            d: 'Frontend',
            r: [],
            p: wFrontPrice,
            base_price: 6000,
            price: wFrontPrice,
            overridden: true,
          },
        });
      }
      if (url === '/api/pitch/base_prices/reset' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { item_id: string };
        wFrontOverridden = false;
        wFrontPrice = 6000;
        pendingCount += 1;
        return jsonResponse({
          admin_only: true,
          item_id: body.item_id,
          old_price: 7500,
          new_price: 6000,
          reset: true,
        });
      }
      return jsonResponse([]);
    });

    render(<SettingsSection />);

    await waitFor(() => {
      expect(screen.getByText('Responsive Web Application / Frontend')).toBeTruthy();
    });
    expect(screen.getByText('Queued admin notifications: 2')).toBeTruthy();
    expect(screen.getByText(/Only the admin will be able to edit Pitch base prices/)).toBeTruthy();

    const priceInput = screen.getByLabelText(
      'Admin override for Responsive Web Application / Frontend',
    ) as HTMLInputElement;
    fireEvent.change(priceInput, { target: { value: '7500' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Save base price for Responsive Web Application / Frontend' }),
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/base_prices',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ item_id: 'w_front', price: 7500 }),
        }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Queued admin notifications: 3')).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Reset base price for Responsive Web Application / Frontend' }),
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/base_prices/reset',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ item_id: 'w_front' }),
        }),
      );
    });
    await waitFor(() => {
      expect(priceInput.value).toBe('');
      expect(screen.getByText('Queued admin notifications: 4')).toBeTruthy();
    });
  });

  it('shows Pitch base prices read-only with Managed by your admin note for members', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/health') {
        return jsonResponse({ ok: true, version: '0.1.0', data_root: '/tmp/magnm-data' });
      }
      if (url === '/api/status') {
        return jsonResponse({ env_status: {}, models_matrix: [] });
      }
      if (url === '/api/setup_check') {
        return jsonResponse({ ok: true, checks: [] });
      }
      if (url.startsWith('/api/bakeoff/latest')) {
        return jsonResponse({ error: 'Not found' }, 404);
      }
      if (url === '/api/account') {
        return jsonResponse({
          configured: true,
          signed_in: true,
          deactivated: false,
          user: {
            id: 'm-1',
            email: 'member@example.com',
            display_name: 'Maya',
            avatar_url: null,
            role: 'member',
            active: true,
          },
        });
      }
      if (url === '/api/account/sync') {
        return jsonResponse({ enabled: true, signed_in: true, last_sync_at: null, last_error: null, pending: 0, synced_count: 0, running: false });
      }
      if (url === '/api/account/media') {
        return jsonResponse({ ffmpeg: true, uploaded: 0, waiting: 0, failed: 0, last_error: null });
      }
      if (url === '/api/account/ai') {
        return jsonResponse({ mode: 'team', today: { requests: 1, prompt_tokens: 0, completion_tokens: 0 }, limit: { daily_requests: 200, daily_tokens: 400000 } });
      }
      if (url === '/api/team/knowledge') {
        return jsonResponse({ signed_in: true, role: 'member', items: [] });
      }
      if (url === '/api/pitch/catalog') {
        return jsonResponse({
          items: {
            w_front: {
              g: 'Web & SaaS',
              n: 'Responsive Web Application / Frontend',
              d: 'Frontend',
              r: [],
              p: 7500,
              base_price: 6000,
              price: 7500,
              overridden: true,
            },
          },
          groups: ['Web & SaaS'],
          standalone_fee: 4000,
          standalone_label: 'App-only setup',
          presets: {},
        });
      }
      if (url.startsWith('/api/pitch/outbox')) {
        return jsonResponse({ events: [], pending_count: 0 });
      }
      return jsonResponse([]);
    });

    render(<SettingsSection />);

    await waitFor(() => {
      expect(screen.getByText('Responsive Web Application / Frontend')).toBeTruthy();
      expect(screen.getByText('Managed by your admin')).toBeTruthy();
    });
    const priceInput = screen.getByLabelText(
      'Admin override for Responsive Web Application / Frontend',
    ) as HTMLInputElement;
    expect(priceInput.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Save base price for Responsive Web Application / Frontend' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset base price for Responsive Web Application / Frontend' })).toBeNull();
  });
});
