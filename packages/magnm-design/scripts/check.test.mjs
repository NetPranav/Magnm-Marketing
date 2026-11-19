import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.resolve(__dirname, '..');

function readText(relPath) {
  return fs.readFileSync(path.join(pkgRoot, relPath), 'utf8');
}

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

function extractRuleBlock(css, selectorStart) {
  const clean = stripComments(css);
  const idx = clean.indexOf(selectorStart);
  assert.notEqual(idx, -1, `Expected rule starting with "${selectorStart}"`);
  const open = clean.indexOf('{', idx);
  assert.notEqual(open, -1, `Expected "{" for "${selectorStart}"`);
  let depth = 1;
  let i = open + 1;
  while (i < clean.length && depth > 0) {
    if (clean[i] === '{') depth += 1;
    else if (clean[i] === '}') depth -= 1;
    i += 1;
  }
  return clean.slice(open + 1, i - 1);
}

function parseVars(block) {
  const vars = {};
  for (const rawLine of block.split(';')) {
    const line = rawLine.trim();
    if (!line.startsWith('--')) continue;
    const colon = line.indexOf(':');
    if (colon === -1) continue;
    const name = line.slice(0, colon).trim();
    const value = line.slice(colon + 1).trim();
    vars[name] = value;
  }
  return vars;
}

function isColourVar(value) {
  return /#[0-9a-fA-F]{3,8}\b|rgba?\(/i.test(value);
}

function relativeLuminance(hex) {
  assert.match(hex, /^#[0-9a-fA-F]{6}$/, `Expected 6-digit hex colour, got ${hex}`);
  const channels = [1, 3, 5].map((offset) => {
    const c = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(hexA, hexB) {
  const lA = relativeLuminance(hexA);
  const lB = relativeLuminance(hexB);
  return (Math.max(lA, lB) + 0.05) / (Math.min(lA, lB) + 0.05);
}

function listFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const results = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...listFiles(full));
    } else if (entry.isFile()) {
      results.push(full);
    }
  }
  return results;
}

const tokensCss = readText('tokens.css');
const lightVars = parseVars(extractRuleBlock(tokensCss, ':root,'));
const darkVars = parseVars(extractRuleBlock(tokensCss, '[data-theme="dark"]'));

test('1. tokens.css light values match Magnm-Pitch exact values', () => {
  const expected = {
    '--bg': '#ffffff',
    '--fg': '#0a0a0a',
    '--body': '#3b3b3b',
    '--tint': '#f5f5f3',
    '--tint-2': '#ebebe8',
    '--line': '#e3e3df',
    '--line-2': '#cfcfca',
    '--f-display': "'Instrument Serif', 'Times New Roman', serif",
    '--f-body': "'Hanken Grotesk', system-ui, Arial, sans-serif",
    '--f-mono': "'DM Mono', ui-monospace, Menlo, Consolas, monospace",
    '--gutter': '20px',
  };
  for (const [key, val] of Object.entries(expected)) {
    assert.equal(lightVars[key], val, `Light token ${key} mismatch`);
  }
});

test('2. dark theme defines the exact same set of colour variables as light', () => {
  const lightColourKeys = Object.entries(lightVars)
    .filter(([, val]) => isColourVar(val))
    .map(([key]) => key)
    .sort();
  const darkKeys = Object.keys(darkVars).sort();
  assert.deepEqual(darkKeys, lightColourKeys);
});

test('3. WCAG AA (>= 4.5) contrast ratio for text pairs in both themes', () => {
  const pairs = [
    ['--fg', '--bg'],
    ['--body', '--bg'],
    ['--muted', '--bg'],
    ['--muted', '--tint'],
  ];
  for (const [themeName, vars] of [
    ['light', lightVars],
    ['dark', darkVars],
  ]) {
    for (const [fgKey, bgKey] of pairs) {
      const ratio = contrastRatio(vars[fgKey], vars[bgKey]);
      assert.ok(
        ratio >= 4.5,
        `${themeName} contrast ${fgKey}/${bgKey} (${vars[fgKey]}/${vars[bgKey]}) = ${ratio.toFixed(2)} < 4.5`,
      );
    }
  }
});

test('4. fonts.css urls exist under fonts/ and start with wOF2', () => {
  const fontsCss = readText('fonts.css');
  const urls = [...fontsCss.matchAll(/url\(([^)]+)\)/g)].map((m) =>
    m[1].trim().replace(/^['"]|['"]$/g, ''),
  );
  assert.ok(urls.length > 0, 'Expected at least one font url() in fonts.css');

  let totalBytes = 0;
  for (const rel of urls) {
    assert.ok(rel.startsWith('./fonts/'), `Font url must be relative ./fonts/: ${rel}`);
    const full = path.resolve(pkgRoot, rel);
    assert.ok(fs.existsSync(full), `Missing font file: ${rel}`);
    const buf = fs.readFileSync(full);
    assert.equal(buf.subarray(0, 4).toString('ascii'), 'wOF2', `${rel} is not valid wOF2`);
    totalBytes += buf.length;
  }
  assert.ok(totalBytes < 1024 * 1024, `Font bundle too large: ${totalBytes} bytes`);
});

test('5. no http/https urls in CSS files and no raw hex colours in components.css', () => {
  const cssFiles = listFiles(pkgRoot).filter((f) => f.endsWith('.css'));
  assert.ok(cssFiles.length >= 4);
  for (const file of cssFiles) {
    const text = fs.readFileSync(file, 'utf8');
    assert.equal(
      /https?:\/\//i.test(text),
      false,
      `Found remote URL in ${path.relative(pkgRoot, file)}`,
    );
  }
  const componentsCss = stripComments(readText('components.css'));
  assert.equal(
    /#[0-9a-fA-F]{3,8}\b/.test(componentsCss),
    false,
    'components.css must not contain raw hex colours',
  );
});

test('6. every mg- class used in gallery.html is defined in components.css', () => {
  const galleryHtml = readText('gallery.html');
  const componentsCss = stripComments(readText('components.css'));

  const defined = new Set(
    [...componentsCss.matchAll(/\.(mg-[a-z0-9_-]+)/gi)].map((m) => m[1]),
  );

  const used = new Set();
  for (const match of galleryHtml.matchAll(/class="([^"]+)"/g)) {
    for (const token of match[1].split(/\s+/)) {
      if (token.startsWith('mg-')) {
        used.add(token);
      }
    }
  }

  assert.ok(used.size > 0, 'Expected mg- classes in gallery.html');
  for (const cls of used) {
    assert.ok(defined.has(cls), `gallery.html uses .${cls} which is missing from components.css`);
  }
});

test('7. no emoji characters in any text file in packages/magnm-design', () => {
  const emojiRe = /\p{Extended_Pictographic}/u;
  const files = listFiles(pkgRoot).filter((f) => !f.endsWith('.woff2'));
  for (const file of files) {
    const content = fs.readFileSync(file, 'utf8');
    const match = content.match(emojiRe);
    assert.equal(
      match,
      null,
      `Found emoji "${match?.[0]}" in ${path.relative(pkgRoot, file)}`,
    );
  }
});

test('8. compact wordmark variant is defined in CSS and React Typography', () => {
  const componentsCss = stripComments(readText('components.css'));
  const typographyTsx = readText('react/Typography.tsx');
  assert.match(componentsCss, /\.mg-wordmark--compact\s*\{/);
  assert.match(typographyTsx, /compact\?:\s*boolean/);
  assert.match(typographyTsx, /mg-wordmark--compact/);
});
