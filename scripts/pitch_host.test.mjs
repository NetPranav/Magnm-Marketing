#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const vendorDir = path.join(repoRoot, 'vendor', 'magnm-pitch');
const hostJsPath = path.join(repoRoot, 'vendor-host', 'magnm-host.js');

const requireFromWeb = createRequire(path.join(repoRoot, 'apps', 'web', 'package.json'));
const { JSDOM } = requireFromWeb('jsdom');

const indexHtmlRaw = fs.readFileSync(path.join(vendorDir, 'index.html'), 'utf8');
const presetsJs = fs.readFileSync(path.join(vendorDir, 'presets.js'), 'utf8');
const appJs = fs.readFileSync(path.join(vendorDir, 'app.js'), 'utf8');
const hostJs = fs.readFileSync(hostJsPath, 'utf8');

function escapeJsonForScript(payload) {
  return JSON.stringify(payload)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

function inlineScript(code) {
  return '<script>' + code.replace(/<\/script/gi, '<\\/script') + '</script>';
}

async function mountPitchDom({ url = 'http://localhost/pitch/', payload, fetchImpl }) {
  const saveCalls = [];

  let html = indexHtmlRaw.replace(
    /<link\s+rel="preconnect"[\s\S]*?<link\s+rel="stylesheet"\s+href="https:\/\/fonts\.googleapis\.com\/[^"]+"\s*>/i,
    '<link rel="stylesheet" href="fonts/fonts.css">',
  );
  html = html.replace(/<link\s+rel="stylesheet"\s+href="style\.css"\s*>/i, '');

  const injectedScripts = [
    inlineScript(presetsJs),
    inlineScript(appJs),
    `<script type="application/json" id="magnm-pitch-data">${escapeJsonForScript(payload)}</script>`,
    inlineScript(hostJs),
  ].join('\n');

  html = html.replace(
    /<script\s+src="vendor\/jspdf\.umd\.min\.js"\s*><\/script>\s*<script\s+src="presets\.js"\s*><\/script>\s*<script\s+src="app\.js"\s*><\/script>/i,
    () => injectedScripts,
  );

  const dom = new JSDOM(html, {
    url,
    runScripts: 'dangerously',
    beforeParse(win) {
      win.scrollTo = () => {};
      win.HTMLElement.prototype.scrollIntoView = () => {};
      if (fetchImpl) {
        win.fetch = fetchImpl;
      }
      function FakeJsPdf() {}
      FakeJsPdf.prototype.save = function (name) {
        saveCalls.push(name);
      };
      FakeJsPdf.prototype.output = function () {
        return new win.Blob(['%PDF-1.4'], { type: 'application/pdf' });
      };
      win.jspdf = { jsPDF: FakeJsPdf };
    },
  });

  if (dom.window.document.readyState === 'loading') {
    await new Promise((resolve) => {
      dom.window.addEventListener('DOMContentLoaded', resolve, { once: true });
    });
  }

  return { dom, saveCalls };
}

test('overrides prices, adds custom item, opens preset slug, and calculates total', async () => {
  const payload = {
    mode: 'preview',
    proposal_id: 'prop-123',
    prices: {
      w_front: 9500,
      b_db: 3100,
      b_admin: 4200,
    },
    custom_items: [
      {
        id: 'custom_audit',
        g: 'Backend',
        n: 'Security & Load Audit',
        d: 'Penetration testing and load benchmarks.',
        p: 2700,
        r: ['b_db'],
      },
      {
        id: 'bad_id',
        g: 'Backend',
        n: 'Invalid prefix should be ignored',
        d: 'Ignored',
        p: 9999,
        r: [],
      },
    ],
    preset: {
      slug: 'acme-corp',
      name: 'Acme Logistics Platform',
      badge: 'Proposal for Acme Logistics',
      clientName: 'Asha',
      businessName: 'Acme Logistics',
      title: 'magnm Proposal: Acme',
      lead: 'Custom logistics architecture.',
      whatWeDo: 'Web & Backend',
      thisPlan: 'Dispatch & tracking platform',
      initialSelected: ['w_front', 'b_db', 'b_admin', 'custom_audit'],
    },
  };

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;

  assert.equal(win.ITEMS.w_front.p, 9500);
  assert.equal(win.ITEMS.b_db.p, 3100);
  assert.equal(win.ITEMS.b_admin.p, 4200);
  assert.deepEqual(JSON.parse(JSON.stringify(win.ITEMS.custom_audit)), {
    g: 'Backend',
    n: 'Security & Load Audit',
    d: 'Penetration testing and load benchmarks.',
    p: 2700,
    r: ['b_db'],
  });
  assert.equal(win.ITEMS.bad_id, undefined);

  assert.deepEqual(JSON.parse(JSON.stringify(win.parseRoute())), { view: 'planner', client: 'acme-corp' });
  assert.equal(win.activePreset.slug, 'acme-corp');
  assert.ok(win.location.search.includes('client=acme-corp'));
  assert.ok(win.location.search.includes('proposal=prop-123'));
  assert.equal(win.document.getElementById('client-banner').hidden, false);

  const expectedTotal = 9500 + 3100 + 4200 + 2700;
  assert.equal(win.total(), expectedTotal);
  const formatted = '\u20b9' + expectedTotal.toLocaleString('en-IN');
  assert.equal(win.document.getElementById('total').textContent, formatted);
  assert.equal(win.document.getElementById('bartotal').textContent, formatted);
});

test('blocks Pitch admin route inside the host', async () => {
  const { dom } = await mountPitchDom({
    url: 'http://localhost/pitch/?admin=true#admin',
    payload: {
      mode: 'preview',
      prices: {},
      custom_items: [],
      preset: null,
      proposal_id: null,
    },
  });
  const win = dom.window;

  assert.equal(win.parseRoute().view, 'planner');
  assert.equal(win.document.getElementById('admin-view').hidden, true);
  assert.equal(win.document.getElementById('planner-view').hidden, false);
});

test('wraps jsPDF.prototype.save to POST PDF blob when proposal_id is set and ignores 404', async () => {
  const fetchCalls = [];
  const fetchMock = async (url, init) => {
    fetchCalls.push({ url, init });
    return { ok: false, status: 404 };
  };
  const { dom, saveCalls } = await mountPitchDom({
    url: 'http://localhost/pitch/',
    payload: {
      mode: 'preview',
      prices: {},
      custom_items: [],
      preset: null,
      proposal_id: 'prop-99',
    },
    fetchImpl: fetchMock,
  });

  const doc = new dom.window.jspdf.jsPDF();
  doc.save('estimate.pdf');

  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(saveCalls, ['estimate.pdf']);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, '/api/pitch/proposals/prop-99/pdf');
  assert.equal(fetchCalls[0].init.method, 'POST');
});

test('does not POST PDF when proposal_id is null', async () => {
  const fetchCalls = [];
  const fetchMock = async (url, init) => {
    fetchCalls.push({ url, init });
    return { ok: true, status: 200 };
  };
  const { dom, saveCalls } = await mountPitchDom({
    url: 'http://localhost/pitch/',
    payload: {
      mode: 'preview',
      prices: {},
      custom_items: [],
      preset: null,
      proposal_id: null,
    },
    fetchImpl: fetchMock,
  });

  const doc = new dom.window.jspdf.jsPDF();
  doc.save('estimate.pdf');

  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(saveCalls, ['estimate.pdf']);
  assert.equal(fetchCalls.length, 0);
});
