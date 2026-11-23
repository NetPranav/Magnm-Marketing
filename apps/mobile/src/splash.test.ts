import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const RES = resolve(process.cwd(), 'android/app/src/main/res');
const read = (rel: string) => readFileSync(join(RES, rel), 'utf8');

/** Width and height from a PNG header. */
function pngSize(path: string): { w: number; h: number; colorType: number } {
  const buf = readFileSync(path);
  expect(buf.subarray(1, 4).toString('ascii')).toBe('PNG');
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20), colorType: buf[25] };
}

describe('mobile splash screen', () => {
  it('uses the app dark background, the same colour as the launcher icon background', () => {
    const colors = read('values/colors.xml');
    expect(colors).toMatch(/<color name="magnm_splash_background">#14151A<\/color>/i);
    expect(read('values/ic_launcher_background.xml')).toMatch(/#14151A/i);
  });

  it('shows only the Magnm logo on that background, with no spinner or leftover default image', () => {
    const styles = read('values/styles.xml');
    const launch = styles.slice(styles.indexOf('AppTheme.NoActionBarLaunch'));
    expect(launch).toMatch(/parent="Theme\.SplashScreen"/);
    expect(launch).toMatch(/name="windowSplashScreenBackground">@color\/magnm_splash_background</);
    expect(launch).toMatch(/name="windowSplashScreenAnimatedIcon">@drawable\/splash_icon</);
    expect(launch).toMatch(/name="postSplashScreenTheme">@style\/AppTheme\.NoActionBar</);
    expect(launch).not.toMatch(/@drawable\/splash</);
    expect(launch).not.toMatch(/progress|spinner/i);
  });

  it('ships the logo at every density and removes the default Capacitor splash images', () => {
    const scale = { mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 } as const;
    for (const [density, factor] of Object.entries(scale)) {
      const file = join(RES, `drawable-${density}`, 'splash_icon.png');
      expect(existsSync(file), file).toBe(true);
      const { w, h, colorType } = pngSize(file);
      expect(w).toBe(288 * factor);
      expect(h).toBe(288 * factor);
      expect(colorType).toBe(6); // RGBA, so the logo sits on the splash background colour
    }
    const defaults = readdirSync(RES).filter((dir) => dir.startsWith('drawable') && existsSync(join(RES, dir, 'splash.png')));
    expect(defaults).toEqual([]);
  });
});
