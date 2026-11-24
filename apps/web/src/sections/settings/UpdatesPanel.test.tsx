import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UpdatesPanel } from './UpdatesPanel';
import { UpdateBadge } from '../../components/UpdateBadge';
import type { UpdateStatus } from '../../desktopUpdates';

type Handler = (event: { payload?: unknown }) => void;

const baseStatus: UpdateStatus = {
  current_version: '0.1.0',
  install_kind: 'standard',
  configured: true,
  available: null,
  last_checked: null,
};

interface Fake {
  invoke: ReturnType<typeof vi.fn>;
  emit: (event: string, payload: unknown) => void;
  calls: () => string[];
}

function installTauri(handlers: Record<string, () => unknown>): Fake {
  const listeners = new Map<string, Handler[]>();
  const invoke = vi.fn(async (cmd: string) => {
    const fn = handlers[cmd];
    if (!fn) throw new Error(`unexpected command ${cmd}`);
    return fn();
  });
  const listen = vi.fn(async (event: string, handler: Handler) => {
    listeners.set(event, [...(listeners.get(event) ?? []), handler]);
    return () => {
      listeners.set(event, (listeners.get(event) ?? []).filter((h) => h !== handler));
    };
  });
  (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke }, event: { listen } };
  return {
    invoke,
    emit: (event, payload) => {
      for (const h of listeners.get(event) ?? []) h({ payload });
    },
    calls: () => invoke.mock.calls.map((c) => c[0] as string),
  };
}

describe('UpdatesPanel', () => {
  beforeEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  });
  afterEach(() => {
    cleanup();
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  });

  it('shows nothing outside the desktop app', () => {
    const { container } = render(<UpdatesPanel />);
    expect(container.textContent).toBe('');
  });

  it('shows the installed version and does nothing else until a click', async () => {
    const fake = installTauri({
      update_status: () => ({ ...baseStatus, available: { version: '0.2.0', notes: 'Fixes', date: null } }),
    });
    render(<UpdatesPanel />);
    expect(await screen.findByText('Version 0.2.0 is available')).toBeTruthy();
    expect(screen.getByText('0.1.0')).toBeTruthy();
    // Opening the screen only reads the saved status: it never checks, downloads or installs by itself.
    expect(fake.calls()).toEqual(['update_status']);
  });

  it('survives a shell that answers with something unexpected', async () => {
    for (const answer of [undefined, null, [], 'x', { nope: true }]) {
      cleanup();
      installTauri({ update_status: () => answer });
      render(<UpdatesPanel />);
      expect(await screen.findByText(/Could not read the update status/)).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Check for updates' })).toBeTruthy();
    }
  });

  it('says plainly when this build has no update key', async () => {
    installTauri({ update_status: () => ({ ...baseStatus, configured: false }) });
    render(<UpdatesPanel />);
    expect(await screen.findByText(/Updates are not set up in this build yet/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Check for updates' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('reports that the app is up to date', async () => {
    const fake = installTauri({ update_status: () => baseStatus, check_update: () => null });
    render(<UpdatesPanel />);
    await screen.findByText('0.1.0');
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByText('Magnm Studio is up to date.')).toBeTruthy();
    expect(fake.calls()).toContain('check_update');
    expect(fake.calls()).not.toContain('install_update');
  });

  it('shows the new version and notes, downloads with progress, then offers a restart', async () => {
    let finish: (v: unknown) => void = () => {};
    const fake = installTauri({
      update_status: () => baseStatus,
      check_update: () => ({ version: '0.2.0', notes: 'Faster Library.\nFewer crashes.', date: null }),
      install_update: () => new Promise((resolve) => (finish = resolve)),
      restart_app: () => undefined,
    });
    render(<UpdatesPanel />);
    await screen.findByText('0.1.0');
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByText('Version 0.2.0 is available')).toBeTruthy();
    expect(screen.getByText(/Faster Library\./)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Download and install' }));
    await waitFor(() => expect(fake.calls()).toContain('install_update'));
    act(() => fake.emit('magnm:update-progress', { phase: 'downloading', downloaded: 5 * 1024 * 1024, total: 20 * 1024 * 1024 }));
    expect(await screen.findByText('Downloading 5.0 MB of 20.0 MB')).toBeTruthy();
    expect((screen.getByLabelText('Download progress') as HTMLProgressElement).value).toBe(25);

    await act(async () => finish({ version: '0.2.0', restart_needed: true }));
    expect(await screen.findByText(/Version 0.2.0 is installed/)).toBeTruthy();
    // Installed, but not restarted until the button is pressed.
    expect(fake.calls()).not.toContain('restart_app');
    fireEvent.click(screen.getByRole('button', { name: 'Restart to finish' }));
    await waitFor(() => expect(fake.calls()).toContain('restart_app'));
  });

  it('shows a failed check as a plain sentence', async () => {
    installTauri({
      update_status: () => baseStatus,
      check_update: () => {
        throw 'Could not reach the update server. Check your internet connection and try again.';
      },
    });
    render(<UpdatesPanel />);
    await screen.findByText('0.1.0');
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Could not reach the update server. Check your internet connection and try again.');
  });

  it('shows a failed install and lets the person try again', async () => {
    let fail = true;
    installTauri({
      update_status: () => ({ ...baseStatus, available: { version: '0.2.0', notes: '', date: null } }),
      install_update: () => {
        if (fail) throw 'The update\'s signature did not match, so it was not installed.';
        return { version: '0.2.0', restart_needed: true };
      },
    });
    render(<UpdatesPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Download and install' }));
    expect((await screen.findByRole('alert')).textContent).toContain('signature did not match');
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Download and install' }));
    expect(await screen.findByText(/Version 0.2.0 is installed/)).toBeTruthy();
  });

  it('explains the password prompt on an Arch install', async () => {
    installTauri({
      update_status: () => ({
        ...baseStatus,
        install_kind: 'pacman',
        available: { version: '0.2.0', notes: '', date: null },
      }),
    });
    render(<UpdatesPanel />);
    expect(await screen.findByText(/administrator password to install it with pacman/)).toBeTruthy();
    expect(screen.getByText('Arch package (pacman)')).toBeTruthy();
  });

  it('shows an update found by the quiet check while the screen is open', async () => {
    const fake = installTauri({ update_status: () => baseStatus });
    render(<UpdatesPanel />);
    await screen.findByText('0.1.0');
    act(() => fake.emit('magnm:update-available', { version: '0.3.0', notes: '', date: null }));
    expect(await screen.findByText('Version 0.3.0 is available')).toBeTruthy();
  });
});

describe('UpdateBadge', () => {
  beforeEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  });
  afterEach(() => {
    cleanup();
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  });

  it('is absent outside the desktop app', () => {
    const { container } = render(<UpdateBadge />);
    expect(container.textContent).toBe('');
  });

  it('is absent when no update is waiting or updates are not set up', async () => {
    const fake = installTauri({ update_status: () => baseStatus });
    const { container } = render(<UpdateBadge />);
    await waitFor(() => expect(fake.calls()).toContain('update_status'));
    expect(container.textContent).toBe('');
    cleanup();
    installTauri({
      update_status: () => ({ ...baseStatus, configured: false, available: { version: '0.2.0', notes: '', date: null } }),
    });
    const second = render(<UpdateBadge />);
    await new Promise((r) => setTimeout(r, 20));
    expect(second.container.textContent).toBe('');
  });

  it('shows a link to Settings when an update is waiting, and never installs', async () => {
    const fake = installTauri({
      update_status: () => ({ ...baseStatus, available: { version: '0.2.0', notes: '', date: null } }),
    });
    render(<UpdateBadge />);
    const link = await screen.findByText('Update available');
    expect(link.getAttribute('href')).toBe('#/settings');
    expect(fake.calls()).toEqual(['update_status']);
  });

  it('appears when the quiet check reports a new version', async () => {
    const fake = installTauri({ update_status: () => baseStatus });
    render(<UpdateBadge />);
    await waitFor(() => expect(fake.calls()).toContain('update_status'));
    act(() => fake.emit('magnm:update-available', { version: '0.4.0', notes: '', date: null }));
    expect(await screen.findByText('Update available')).toBeTruthy();
  });
});
