import { describe, expect, it } from 'vitest';
import {
  UpdateCheckError,
  apkUrlFor,
  checkForAppUpdate,
  parseAndroidManifest,
  versionCodeOf,
} from './updates';
import { createPreviewNativeBridge } from './native';

const SHA = 'ab'.repeat(32);

function manifest(over: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: '0.2.0',
    versionCode: 200,
    url: 'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
    sha256: SHA,
    bytes: 12_345_678,
    notes: 'Faster Library.',
    ...over,
  });
}

describe('versionCodeOf', () => {
  it('is major * 10000 + minor * 100 + patch', () => {
    expect(versionCodeOf('0.1.0')).toBe(100);
    expect(versionCodeOf('1.2.3')).toBe(10203);
    expect(versionCodeOf('12.34.56')).toBe(123456);
  });

  it('refuses anything that is not X.Y.Z with small parts', () => {
    for (const bad of ['', '1.2', 'v1.2.3', '1.2.3-beta', '1.100.0', '1.0.100', '1.2.3.4', ' 1.2.3', '1.2.x', '01.2.3']) {
      expect(() => versionCodeOf(bad), bad).toThrow();
    }
  });
});

describe('parseAndroidManifest', () => {
  it('reads a good manifest', () => {
    const m = parseAndroidManifest(manifest());
    expect(m.version).toBe('0.2.0');
    expect(m.versionCode).toBe(200);
    expect(m.sha256).toBe(SHA);
    expect(m.bytes).toBe(12_345_678);
    expect(m.notes).toBe('Faster Library.');
    expect(m.url).toBe(apkUrlFor('0.2.0'));
  });

  it('keeps notes optional', () => {
    expect(parseAndroidManifest(manifest({ notes: undefined })).notes).toBe('');
  });

  it('refuses text that is not a JSON object', () => {
    for (const bad of ['', 'not json', '[]', 'null', '"x"', '123', '{']) {
      expect(() => parseAndroidManifest(bad), bad).toThrow(UpdateCheckError);
    }
  });

  it('refuses a version code that does not match the version', () => {
    expect(() => parseAndroidManifest(manifest({ versionCode: 201 }))).toThrow(UpdateCheckError);
    expect(() => parseAndroidManifest(manifest({ versionCode: '200' }))).toThrow(UpdateCheckError);
    expect(() => parseAndroidManifest(manifest({ versionCode: 200.5 }))).toThrow(UpdateCheckError);
  });

  it('refuses a download address that is not our release asset for that version', () => {
    const bad = [
      'http://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://github.com.evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://user@github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://github.com:444/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk?x=1',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk#x',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.3.0.apk',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.3.0/magnm-studio-0.2.0.apk',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/other.apk',
      'https://github.com/Evil/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk',
      'https://github.com/NetPranav/Magnm-Marketing/archive/v0.2.0.zip',
      'https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/../v0.2.0/magnm-studio-0.2.0.apk',
      'javascript:alert(1)',
      'file:///sdcard/magnm-studio-0.2.0.apk',
      '',
    ];
    for (const url of bad) {
      expect(() => parseAndroidManifest(manifest({ url })), url).toThrow(UpdateCheckError);
    }
    expect(() => parseAndroidManifest(manifest({ url: undefined }))).toThrow(UpdateCheckError);
    expect(() => parseAndroidManifest(manifest({ url: 5 }))).toThrow(UpdateCheckError);
  });

  it('refuses a missing or malformed checksum or size', () => {
    for (const sha256 of [undefined, '', 'abc', 'AB'.repeat(32), 'zz'.repeat(32), SHA + '0', 5]) {
      expect(() => parseAndroidManifest(manifest({ sha256 })), String(sha256)).toThrow(UpdateCheckError);
    }
    for (const bytes of [undefined, 0, -5, 1.5, '100', 251 * 1024 * 1024, Number.NaN]) {
      expect(() => parseAndroidManifest(manifest({ bytes })), String(bytes)).toThrow(UpdateCheckError);
    }
  });

  it('refuses a notes field that is not text or is huge', () => {
    expect(() => parseAndroidManifest(manifest({ notes: 5 }))).toThrow(UpdateCheckError);
    expect(() => parseAndroidManifest(manifest({ notes: 'x'.repeat(20_000) }))).toThrow(UpdateCheckError);
  });

  it('refuses a manifest that is far too big', () => {
    expect(() => parseAndroidManifest(manifest({ notes: 'x'.repeat(100_000) }))).toThrow(UpdateCheckError);
  });
});

describe('checkForAppUpdate', () => {
  const installed = { versionName: '0.1.0', versionCode: 100, canInstall: true };

  it('reports a newer version', async () => {
    const bridge = createPreviewNativeBridge({ appVersion: installed, updateManifest: manifest() });
    const result = await checkForAppUpdate(bridge);
    expect(result.installed.versionName).toBe('0.1.0');
    expect(result.update?.version).toBe('0.2.0');
  });

  it('reports no update when the version code is the same or older', async () => {
    for (const code of [200, 300]) {
      const bridge = createPreviewNativeBridge({
        appVersion: { ...installed, versionCode: code },
        updateManifest: manifest(),
      });
      expect((await checkForAppUpdate(bridge)).update).toBeNull();
    }
  });

  it('turns a native failure into a plain sentence', async () => {
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: () => {
        throw new Error('The update server did not answer in time. Try again.');
      },
    });
    await expect(checkForAppUpdate(bridge)).rejects.toThrow('The update server did not answer in time. Try again.');
  });

  it('refuses a hostile manifest from the native side', async () => {
    const bridge = createPreviewNativeBridge({
      appVersion: installed,
      updateManifest: manifest({ url: 'https://evil.example/magnm-studio-0.2.0.apk' }),
    });
    await expect(checkForAppUpdate(bridge)).rejects.toBeInstanceOf(UpdateCheckError);
  });

  it('says so when this build cannot check at all', async () => {
    const bridge = createPreviewNativeBridge();
    await expect(checkForAppUpdate(bridge)).rejects.toBeInstanceOf(Error);
  });
});
