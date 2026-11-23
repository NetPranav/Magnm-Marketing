import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

// The toggle lives in the shared design kit; the phone and the laptop sidebar both render it.
const css = readFileSync(resolve(process.cwd(), '../../packages/magnm-design/components.css'), 'utf8');

function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf('}', start));
}

it('sizes the theme toggle to its content and keeps it inside its container', () => {
  const toggle = rule('.mg-theme-toggle');
  expect(toggle).toMatch(/width:\s*fit-content/);
  expect(toggle).toMatch(/max-width:\s*100%/);
  expect(toggle).toMatch(/box-sizing:\s*border-box/);
  expect(toggle).toMatch(/justify-self:\s*start/);
  expect(toggle).toMatch(/align-self:\s*flex-start/);
});

it('lets the toggle buttons shrink instead of overflowing the pill', () => {
  const button = rule('.mg-theme-toggle button');
  expect(button).toMatch(/min-width:\s*0/);
  expect(button).toMatch(/white-space:\s*nowrap/);
});
