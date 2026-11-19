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

async function mountPitchDom({ url = 'http://localhost/pitch/', payload, fetchImpl, includeHost = true }) {
  const saveCalls = [];

  let html = indexHtmlRaw.replace(
    /<link\s+rel="preconnect"[\s\S]*?<link\s+rel="stylesheet"\s+href="https:\/\/fonts\.googleapis\.com\/[^"]+"\s*>/i,
    '<link rel="stylesheet" href="fonts/fonts.css">',
  );
  html = html.replace(/<link\s+rel="stylesheet"\s+href="style\.css"\s*>/i, '');

  const scriptParts = [inlineScript(presetsJs), inlineScript(appJs)];
  if (includeHost) {
    scriptParts.push(`<script type="application/json" id="magnm-pitch-data">${escapeJsonForScript(payload)}</script>`);
    scriptParts.push(inlineScript(hostJs));
  }
  const injectedScripts = scriptParts.join('\n');

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

function readSheetStructure(doc, sheetId) {
  const container = doc.getElementById(`${sheetId}-groups`);
  return Array.from(container.querySelectorAll('.group')).map((groupEl) => ({
    heading: groupEl.querySelector('.label span')?.textContent || '',
    items: Array.from(groupEl.querySelectorAll('button.item')).map((btn) => btn.getAttribute('data-id')),
  }));
}

test('sanitizes < and > from initial payload and live preview updates to block HTML injection', async () => {
  const xss = 'Safe <img src=x onerror="window.__pwned=1"> Text';
  const payload = {
    mode: 'preview',
    proposal_id: 'prop-xss',
    prices: {},
    custom_items: [
      {
        id: 'custom_xss',
        g: 'Backend',
        n: `Item ${xss}`,
        d: `Desc ${xss}`,
        p: 1500,
        r: [],
      },
    ],
    preset: {
      slug: 'xss-client',
      name: 'XSS Client',
      badge: `Badge ${xss}`,
      clientName: 'Client',
      businessName: 'Biz',
      title: 'Title',
      lead: 'Lead',
      whatWeDo: 'What',
      thisPlan: 'Plan',
      architectureBlock: {
        heading: 'Architecture',
        sub: 'Sub',
        roles: [{ label: 'Role', points: [`Point ${xss}`] }],
      },
      initialSelected: ['custom_xss'],
      sections: { p2: [], p3: [], p4: ['custom_xss'], p5: [] },
    },
  };

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;
  const doc = win.document;

  assert.equal(doc.querySelectorAll('img').length, 0);
  assert.equal(win.__pwned, undefined);
  for (const elId of ['banner-text', 'architecture-block-container', 'sumlines']) {
    const text = doc.getElementById(elId).textContent;
    assert.ok(!text.includes('<'), `${elId} should not contain <`);
    assert.ok(!text.includes('>'), `${elId} should not contain >`);
  }
  assert.ok(doc.getElementById('banner-text').textContent.includes('Badge Safe'));
  assert.ok(doc.getElementById('architecture-block-container').textContent.includes('Point Safe'));
  assert.ok(doc.getElementById('sumlines').textContent.includes('Item Safe'));

  // Untrusted origin message is ignored
  win.dispatchEvent(
    new win.MessageEvent('message', {
      origin: 'https://evil.example',
      data: {
        type: 'magnm:pitch-preview-update',
        payload: { preset: { slug: 'evil', badge: 'Evil' } },
      },
    }),
  );
  assert.equal(win.activePreset.slug, 'xss-client');

  // Same-origin preview update sanitizes badge, architecture point, and custom item name
  const previewXss = 'Preview <img src=x onerror="window.__pwned=1"> Clean';
  win.dispatchEvent(
    new win.MessageEvent('message', {
      origin: win.location.origin,
      data: {
        type: 'magnm:pitch-preview-update',
        payload: {
          prices: {},
          custom_items: [
            {
              id: 'custom_xss2',
              g: 'Web & SaaS',
              n: `LiveItem ${previewXss}`,
              d: 'LiveDesc',
              p: 2000,
              r: [],
            },
          ],
          preset: {
            slug: 'xss-client',
            name: 'XSS Client',
            badge: `LiveBadge ${previewXss}`,
            architectureBlock: {
              heading: 'Architecture',
              sub: 'Sub',
              roles: [{ label: 'Role', points: [`LivePoint ${previewXss}`] }],
            },
            initialSelected: ['custom_xss2'],
            sections: { p2: ['custom_xss2'], p3: [], p4: [], p5: [] },
          },
        },
      },
    }),
  );

  assert.equal(doc.querySelectorAll('img').length, 0);
  assert.equal(win.__pwned, undefined);
  for (const elId of ['banner-text', 'architecture-block-container', 'sumlines']) {
    const text = doc.getElementById(elId).textContent;
    assert.ok(!text.includes('<'), `${elId} after preview-update should not contain <`);
    assert.ok(!text.includes('>'), `${elId} after preview-update should not contain >`);
  }
  assert.ok(doc.getElementById('banner-text').textContent.includes('LiveBadge Preview'));
  assert.ok(doc.getElementById('architecture-block-container').textContent.includes('LivePoint Preview'));
  assert.ok(doc.getElementById('sumlines').textContent.includes('LiveItem Preview'));
});

test('general preset preserves Pitch headings and buttons identically with adapter', async () => {
  const withoutAdapter = await mountPitchDom({
    url: 'http://localhost/pitch/',
    includeHost: false,
  });
  const withAdapter = await mountPitchDom({
    url: 'http://localhost/pitch/',
    includeHost: true,
    payload: {
      mode: 'preview',
      prices: {},
      custom_items: [],
      preset: null,
      proposal_id: null,
    },
  });

  for (const sheetId of ['p2', 'p3', 'p4', 'p5']) {
    assert.deepEqual(
      readSheetStructure(withAdapter.dom.window.document, sheetId),
      readSheetStructure(withoutAdapter.dom.window.document, sheetId),
      `Sheet ${sheetId} should match Pitch's original headings and buttons for general preset`,
    );
  }
});

test('proposal subset keeps Pitch headings, drops empty groups, places extra/custom items in Additional services, and matches engine total', async () => {
  const payload = {
    mode: 'preview',
    proposal_id: 'prop-sections',
    prices: {
      w_front: 7200,
    },
    custom_items: [
      {
        id: 'custom_erp',
        g: 'Web & SaaS',
        n: 'Custom ERP Dashboard',
        d: 'Tailored ERP view.',
        p: 3500,
        r: [],
      },
      {
        id: 'custom_audit',
        g: 'Backend',
        n: 'Load Audit',
        d: 'Benchmarks and hardening.',
        p: 2700,
        r: ['b_db'],
      },
    ],
    preset: {
      slug: 'subset-co',
      name: 'Subset Co',
      badge: 'Proposal for Subset Co',
      initialSelected: ['w_front', 'w_book', 'a_base', 'b_db', 'custom_erp', 'custom_audit'],
      sections: {
        p2: ['w_front', 'w_book', 'custom_erp'],
        p3: ['a_base'],
        p4: ['b_db', 'custom_audit'],
        p5: [],
      },
    },
  };

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;
  const doc = win.document;

  // p2 keeps "Frontend Architecture & UI" (only w_front), drops the other 2 empty groups,
  // and appends "Additional services" with w_book + custom_erp in order.
  assert.deepEqual(readSheetStructure(doc, 'p2'), [
    { heading: 'Frontend Architecture & UI', items: ['w_front'] },
    { heading: 'Additional services', items: ['w_book', 'custom_erp'] },
  ]);

  // p3 keeps "Mobile Apps & Platforms" (only a_base), drops empty groups, no Additional services.
  assert.deepEqual(readSheetStructure(doc, 'p3'), [
    { heading: 'Mobile Apps & Platforms', items: ['a_base'] },
  ]);

  // p4 keeps "Database, API & Authentication" (b_db) and appends "Additional services" (custom_audit).
  assert.deepEqual(readSheetStructure(doc, 'p4'), [
    { heading: 'Database, API & Authentication', items: ['b_db'] },
    { heading: 'Additional services', items: ['custom_audit'] },
  ]);

  // p5 has no selected sections so all groups are dropped.
  assert.deepEqual(readSheetStructure(doc, 'p5'), []);

  // Engine pricing for initialSelected = [w_front (7200), w_book (1200), a_base (6500), b_db (2000), b_admin (3000 via w_book dep), custom_erp (3500), custom_audit (2700)]
  // Because w_front is selected (Web & SaaS), standalone_fee = 0.
  const expectedTotal = 7200 + 1200 + 6500 + 2000 + 3000 + 3500 + 2700;
  assert.equal(win.total(), expectedTotal);
  const formatted = '\u20b9' + expectedTotal.toLocaleString('en-IN');
  assert.equal(doc.getElementById('total').textContent, formatted);
});
