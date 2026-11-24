import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UpdatesPanel } from './components/UpdatesPanel';
import { createPreviewNativeBridge, type UpdateInstallRequest, type UpdateInstallState } from './native';

const SHA = 'cd'.repeat(32);
const URL_OK = 'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk';

function manifest(over: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: '0.2.0',
    versionCode: 200,
    url: URL_OK,
    sha256: SHA,
    bytes: 20 * 1024 * 1024,
    notes: 'Faster Library.\nFewer crashes.',
    ...over,
  });
}

const installed = { versionName: '0.1.0', versionCode: 100, canInstall: true };

describe('Updates panel (More tab)', () => {
  afterEach(cleanup);

  it('shows the installed version and does nothing until a click', async () => {
    const requests: UpdateInstallRequest[] = [];
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: manifest(),
      onDownloadAndInstallUpdate: (r) => {
        requests.push(r);
        return 'installer_opened';
      },
    });
    render(<UpdatesPanel bridge={bridge} />);
    expect(await screen.findByText(/0\.1\.0/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Download and install' })).toBeNull();
    expect(requests).toEqual([]);
  });

  it('says the app is up to date', async () => {
    const bridge = createPreviewNativeBridge({ appVersion: { ...installed, versionCode: 200 }, updateManifest: manifest() });
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByText('Magnm Studio is up to date.')).toBeTruthy();
  });

  it('shows the new version with notes, downloads with progress and opens the installer', async () => {
    const requests: UpdateInstallRequest[] = [];
    let finish: (s: UpdateInstallState) => void = () => {};
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: manifest(),
      onDownloadAndInstallUpdate: (r) => {
        requests.push(r);
        return new Promise((resolve) => (finish = resolve));
      },
    });
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByText('Version 0.2.0 is available')).toBeTruthy();
    expect(screen.getByText(/Fewer crashes\./)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Download and install' }));
    await waitFor(() => expect(requests.length).toBe(1));
    expect(requests[0]).toEqual({ url: URL_OK, sha256: SHA, bytes: 20 * 1024 * 1024, versionCode: 200 });

    act(() => bridge.emitUpdateProgress({ downloaded: 5 * 1024 * 1024, total: 20 * 1024 * 1024 }));
    expect(await screen.findByText('Downloading 5.0 MB of 20.0 MB')).toBeTruthy();
    expect((screen.getByLabelText('Download progress') as HTMLProgressElement).value).toBe(25);

    await act(async () => finish('installer_opened'));
    expect(await screen.findByText(/The Android installer is open/)).toBeTruthy();
    expect(screen.getByText(/signed with a different key/)).toBeTruthy();
  });

  it('explains how to allow installs when Android has not allowed them yet', async () => {
    const bridge = createPreviewNativeBridge({
      appVersion: { ...installed, canInstall: false },
      updateManifest: manifest(),
      onDownloadAndInstallUpdate: () => 'needs_permission',
    });
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Download and install' }));
    expect(await screen.findByText(/Allow from this source/)).toBeTruthy();
    // The button stays, so the person can try again after allowing it.
    expect(screen.getByRole('button', { name: 'Download and install' })).toBeTruthy();
  });

  it('shows a failed check or download as a plain sentence', async () => {
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: () => {
        throw new Error('The update server did not answer in time. Try again.');
      },
    });
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect((await screen.findByRole('alert')).textContent).toBe('The update server did not answer in time. Try again.');

    cleanup();
    const second = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: manifest(),
      onDownloadAndInstallUpdate: () => {
        throw new Error('The downloaded update\'s checksum does not match, so it was not kept.');
      },
    });
    render(<UpdatesPanel bridge={second} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Download and install' }));
    expect((await screen.findByRole('alert')).textContent).toContain('checksum does not match');
  });

  it('refuses a hostile manifest and offers no install', async () => {
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: manifest({ url: 'https://evil.example/magnm-studio-0.2.0.apk' }),
      onDownloadAndInstallUpdate: () => 'installer_opened',
    });
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Download and install' })).toBeNull();
  });

  it('says plainly when updates are only in the Android app', async () => {
    const bridge = createPreviewNativeBridge();
    render(<UpdatesPanel bridge={bridge} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
  });
});
