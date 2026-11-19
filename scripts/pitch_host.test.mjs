#!/usr/bin/env node
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
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
  const pdfTextCalls = [];

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
      FakeJsPdf.prototype.setTextColor = function () {};
      FakeJsPdf.prototype.setFont = function () {};
      FakeJsPdf.prototype.setFontSize = function () {};
      FakeJsPdf.prototype.setDrawColor = function () {};
      FakeJsPdf.prototype.setLineWidth = function () {};
      FakeJsPdf.prototype.line = function () {};
      FakeJsPdf.prototype.addPage = function () {};
      FakeJsPdf.prototype.splitTextToSize = function (txt) {
        return String(txt || '').split('\n');
      };
      FakeJsPdf.prototype.text = function (txt, x, y, opts) {
        pdfTextCalls.push({ text: String(txt), x, y, opts: opts || null });
      };
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

  return { dom, saveCalls, pdfTextCalls };
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

function buildFinalPayload(overrides = {}) {
  return {
    mode: 'form',
    kind: 'final',
    locked: true,
    proposal_id: 'prop-final-1',
    prices: {
      w_front: 7500,
      b_db: 2000,
      b_admin: 3000,
    },
    custom_items: [],
    contact: {
      name: 'Priya',
      business: 'Priya Tech',
      phone: '+91 9876543210',
      email: 'priya@example.com',
      notes: 'Deliver staging by Friday.',
    },
    payment_plan: [
      { percent: 50, label: 'to start', amount: 7750 },
      { percent: 50, label: 'at launch', amount: 7750 },
    ],
    payment_note: 'Custom payment note for Priya Tech.',
    timeline: [
      { title: 'Kickoff', when: 'Day 1', detail: 'Confirm scope and access.' },
      { title: 'Staging build', when: 'Week 2', detail: 'Demo core workflows.' },
      { title: 'Go live', when: 'Week 4', detail: 'Launch and handover.' },
    ],
    service_plans: [
      {
        id: 'plan_setup',
        name: 'Cloud Setup',
        description: 'Initial cloud provisioning.',
        price: 3000,
        period: 'one_time',
      },
      {
        id: 'plan_care',
        name: 'Monthly Care',
        description: 'SLA monitoring and updates.',
        price: 2000,
        period: 'monthly',
      },
      {
        id: 'plan_qtr',
        name: 'Quarterly Audit',
        description: 'Security and perf check.',
        price: 4500,
        period: 'quarterly',
      },
      {
        id: 'plan_yr',
        name: 'Annual Domain & SSL',
        description: 'Yearly renewal.',
        price: 1800,
        period: 'yearly',
      },
    ],
    preset: {
      slug: 'priya-final',
      name: 'Priya Final',
      badge: 'Final Quotation for Priya Tech',
      clientName: '',
      businessName: '',
      title: 'magnm Final Quotation',
      lead: 'Agreed scope.',
      whatWeDo: 'Web & Backend',
      thisPlan: 'Custom platform build',
      initialSelected: ['w_front', 'b_db', 'b_admin'],
      sections: {
        p2: ['w_front'],
        p3: [],
        p4: ['b_db', 'b_admin'],
        p5: [],
      },
    },
    ...overrides,
  };
}

test('final quotation locks items against click and keyboard, shows only agreed items, and keeps focusability', async () => {
  const payload = buildFinalPayload();
  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload });
  const win = dom.window;
  const doc = win.document;

  // Only agreed items exist across p2..p4, plus Plans and support in p5
  assert.deepEqual(readSheetStructure(doc, 'p2'), [
    { heading: 'Frontend Architecture & UI', items: ['w_front'] },
  ]);
  assert.deepEqual(readSheetStructure(doc, 'p3'), []);
  assert.deepEqual(readSheetStructure(doc, 'p4'), [
    { heading: 'Database, API & Authentication', items: ['b_db'] },
    { heading: 'Operations & Admin Dashboard', items: ['b_admin'] },
  ]);
  assert.deepEqual(readSheetStructure(doc, 'p5'), [
    { heading: 'Plans and support', items: ['plan_setup', 'plan_care', 'plan_qtr', 'plan_yr'] },
  ]);

  const allItems = Array.from(doc.querySelectorAll('button.item'));
  assert.ok(allItems.length > 0);
  for (const btn of allItems) {
    assert.equal(btn.getAttribute('data-locked'), 'true');
    assert.equal(btn.getAttribute('aria-disabled'), 'true');
    assert.equal(btn.disabled, false);
    assert.notEqual(btn.getAttribute('tabindex'), '-1');
  }

  // Bottom bar and summary remain visible
  assert.equal(doc.getElementById('bottom-bar').hidden, false);
  assert.equal(doc.getElementById('summary').hidden, false);

  const beforeTotal = win.total();
  const wFrontBtn = doc.querySelector('button.item[data-id="w_front"]');
  wFrontBtn.click();
  wFrontBtn.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  wFrontBtn.dispatchEvent(new win.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  win.toggle('w_front');
  win.toggle('w_seo');

  assert.equal(win.total(), beforeTotal);
  assert.equal(wFrontBtn.getAttribute('aria-checked'), 'true');
  assert.equal(wFrontBtn.getAttribute('data-locked'), 'true');
});

test('final quotation includes one-time plans in total() and excludes recurring plans with period suffix', async () => {
  const payload = buildFinalPayload();
  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload });
  const win = dom.window;
  const doc = win.document;

  const setupBtn = doc.querySelector('button.item[data-id="plan_setup"]');
  const careBtn = doc.querySelector('button.item[data-id="plan_care"]');
  const qtrBtn = doc.querySelector('button.item[data-id="plan_qtr"]');
  const yrBtn = doc.querySelector('button.item[data-id="plan_yr"]');

  assert.equal(setupBtn.querySelector('.price').textContent, '\u20b93,000');
  assert.equal(careBtn.querySelector('.price').textContent, '\u20b92,000 / month');
  assert.equal(qtrBtn.querySelector('.price').textContent, '\u20b94,500 / quarter');
  assert.equal(yrBtn.querySelector('.price').textContent, '\u20b91,800 / year');

  // w_front (7500) + b_db (2000) + b_admin (3000) + plan_setup (3000) = 15500; recurring plans excluded
  assert.equal(win.total(), 15500);
  assert.equal(win.sel.plan_setup, true);
  assert.equal(win.sel.plan_care, undefined);
  assert.equal(win.sel.plan_qtr, undefined);
  assert.equal(win.sel.plan_yr, undefined);
  assert.equal(doc.getElementById('total').textContent, '\u20b915,500');
});

test('final quotation renders payment block rows, amounts, grid style for 2 and 4 rows, and timeline rows', async () => {
  // 2 rows
  const payload2 = buildFinalPayload({
    payment_plan: [
      { percent: 50, label: 'to start', amount: 7750 },
      { percent: 50, label: 'at launch', amount: 7750 },
    ],
    payment_note: 'Two-part schedule note.',
  });
  const res2 = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload: payload2 });
  const doc2 = res2.dom.window.document;
  const payEl2 = doc2.querySelector('#p1 .pay');
  assert.equal(payEl2.style.gridTemplateColumns, 'repeat(2, 1fr)');
  const cells2 = Array.from(payEl2.children);
  assert.equal(cells2.length, 2);
  assert.equal(cells2[0].querySelector('b').textContent, '50%');
  assert.deepEqual(
    Array.from(cells2[0].querySelectorAll('span')).map((s) => s.textContent),
    ['to start', '\u20b97,750'],
  );
  assert.equal(payEl2.parentNode.querySelector('p.hint').textContent, 'Two-part schedule note.');

  // Timeline rows with two-digit numbers
  const steps = Array.from(doc2.querySelectorAll('#p1 ol.steps li'));
  assert.equal(steps.length, 3);
  assert.equal(steps[0].querySelector('b').textContent, '01');
  assert.equal(steps[0].querySelector('span:not(.t)').textContent, 'Kickoff');
  assert.equal(steps[0].querySelector('span.t').textContent, 'Day 1');
  assert.equal(steps[0].querySelector('small').textContent, 'Confirm scope and access.');
  assert.equal(steps[2].querySelector('b').textContent, '03');

  // Timeline block's own hint should remain intact
  const stepsHint = doc2.querySelector('#p1 ol.steps').parentNode.querySelector('p.hint');
  assert.ok(stepsHint.textContent.includes('Delivery timelines depend on scope'));

  // 4 rows
  const payload4 = buildFinalPayload({
    payment_plan: [
      { percent: 25, label: 'kickoff', amount: 3875 },
      { percent: 25, label: 'design', amount: 3875 },
      { percent: 25, label: 'staging', amount: 3875 },
      { percent: 25, label: 'launch', amount: 3875 },
    ],
  });
  const res4 = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload: payload4 });
  const payEl4 = res4.dom.window.document.querySelector('#p1 .pay');
  assert.equal(payEl4.style.gridTemplateColumns, 'repeat(4, 1fr)');
  assert.equal(payEl4.children.length, 4);

  // 3 rows does NOT set inline grid-template-columns
  const payload3 = buildFinalPayload({
    payment_plan: [
      { percent: 40, label: 'to start', amount: 6200 },
      { percent: 30, label: 'at demo', amount: 4650 },
      { percent: 30, label: 'at launch', amount: 4650 },
    ],
  });
  const res3 = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload: payload3 });
  const payEl3 = res3.dom.window.document.querySelector('#p1 .pay');
  assert.equal(payEl3.style.gridTemplateColumns, '');
});

test('final quotation prefills only empty contact form fields', async () => {
  const payload = buildFinalPayload({
    preset: {
      slug: 'prefill-test',
      name: 'Prefill Test',
      badge: 'Final',
      clientName: 'Preset Name',
      businessName: '',
      title: 'Final',
      lead: 'Lead',
      whatWeDo: 'Web',
      thisPlan: 'Scope',
      initialSelected: ['w_front'],
      sections: { p2: ['w_front'], p3: [], p4: [], p5: [] },
    },
    contact: {
      name: 'Contact Name Should Not Overwrite',
      business: 'Contact Business',
      phone: '9876500000',
      email: 'c@example.com',
      notes: 'Contact Notes',
    },
  });

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload });
  const doc = dom.window.document;

  assert.equal(doc.getElementById('f-name').value, 'Preset Name');
  assert.equal(doc.getElementById('f-biz').value, 'Contact Business');
  assert.equal(doc.getElementById('f-phone').value, '9876500000');
  assert.equal(doc.getElementById('f-note').value, 'Contact Notes');
});

test('final quotation PDF outputs Final quotation, TOTAL, recurring, payment terms, timeline, and uploads via save wrapper', async () => {
  const fetchCalls = [];
  const fetchMock = async (url, init) => {
    fetchCalls.push({ url, init });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const payload = buildFinalPayload();
  const { dom, saveCalls, pdfTextCalls } = await mountPitchDom({
    url: 'http://localhost/pitch/?mode=form',
    payload,
    fetchImpl: fetchMock,
  });

  dom.window.downloadPdf();
  await new Promise((r) => setTimeout(r, 0));

  const texts = pdfTextCalls.map((c) => c.text);
  assert.ok(texts.some((t) => t.startsWith('Final quotation') && t.includes('Priya Final')));
  assert.ok(texts.includes('TOTAL'));
  assert.ok(!texts.includes('TOTAL ESTIMATE'));
  assert.ok(texts.includes('RECURRING'));
  assert.ok(texts.some((t) => t.includes('[x]  Monthly Care')));
  assert.ok(texts.some((t) => t.includes('Rs. 2,000 / month')));
  assert.ok(texts.some((t) => t.includes('50% to start (Rs. 7,750)')));
  assert.ok(texts.some((t) => t.includes('Custom payment note for Priya Tech.')));
  assert.ok(texts.includes('TIMELINE'));
  assert.ok(texts.some((t) => t.includes('Kickoff')));
  assert.ok(texts.includes('Day 1'));
  assert.ok(texts.some((t) => t.includes('Confirm scope and access.')));
  assert.ok(texts.includes('PROJECT NOTES & DELIVERABLES'));

  assert.equal(saveCalls.length, 1);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, '/api/pitch/proposals/prop-final-1/pdf');
  assert.equal(fetchCalls[0].init.method, 'POST');
});

test('vendored downloadPdf source hash guard matches expected hash', () => {
  const start = appJs.indexOf('function downloadPdf()');
  assert.ok(start !== -1, 'downloadPdf() not found in vendor/magnm-pitch/app.js');
  let depth = 0;
  let i = appJs.indexOf('{', start);
  for (; i < appJs.length; i++) {
    if (appJs[i] === '{') depth++;
    else if (appJs[i] === '}') {
      depth--;
      if (depth === 0) {
        i++;
        break;
      }
    }
  }
  const fnSource = appJs.slice(start, i);
  const actualHash = crypto.createHash('sha256').update(fnSource).digest('hex');
  const EXPECTED_DOWNLOAD_PDF_HASH = 'fff011d65840832488f5e604359344825d3757f358e9188ddcf948a1fa402540';
  assert.equal(
    actualHash,
    EXPECTED_DOWNLOAD_PDF_HASH,
    'Vendored downloadPdf() changed — review finalQuotationPdf in vendor-host/magnm-host.js and update the hash constant.',
  );
});

test('default quotation produces the same DOM text as Pitch general planner with equal prices', async () => {
  const catalog = JSON.parse(fs.readFileSync(path.join(vendorDir, 'catalog.json'), 'utf8'));
  const basePrices = {};
  for (const [id, item] of Object.entries(catalog.items)) {
    basePrices[id] = item.p;
  }

  const generalDom = await mountPitchDom({
    url: 'http://localhost/pitch/',
    includeHost: false,
  });
  const defaultDom = await mountPitchDom({
    url: 'http://localhost/pitch/?proposal=prop_default',
    includeHost: true,
    payload: {
      mode: 'form',
      kind: 'default',
      locked: false,
      prices: basePrices,
      custom_items: [],
      preset: catalog.presets.general,
      proposal_id: 'prop_default',
      contact: { name: '', business: '', phone: '', email: '', notes: '' },
      payment_plan: [],
      payment_note: '',
      timeline: [],
      service_plans: [],
    },
  });

  const generalDoc = generalDom.dom.window.document;
  const defaultDoc = defaultDom.dom.window.document;
  assert.equal(defaultDoc.title, generalDoc.title);
  for (const elId of ['client-banner', 'main-nav', 'planner-view', 'bottom-bar', 'toast']) {
    assert.equal(
      defaultDoc.getElementById(elId).textContent,
      generalDoc.getElementById(elId).textContent,
      `#${elId} textContent should match between default quotation and general planner`,
    );
  }
});

test('strips < and > from all new final quotation fields', async () => {
  const xss = '<img src=x onerror="window.__pwned2=1">';
  const payload = buildFinalPayload({
    contact: {
      name: `Name ${xss}`,
      business: `Biz ${xss}`,
      phone: `123 ${xss}`,
      email: `a@b.c ${xss}`,
      notes: `Notes ${xss}`,
    },
    payment_plan: [
      { percent: 50, label: `Start ${xss}`, amount: 7750 },
      { percent: 50, label: `End ${xss}`, amount: 7750 },
    ],
    payment_note: `PayNote ${xss}`,
    timeline: [
      { title: `Step ${xss}`, when: `W1 ${xss}`, detail: `Detail ${xss}` },
    ],
    service_plans: [
      {
        id: 'plan_xss_1',
        name: `PlanOne ${xss}`,
        description: `DescOne ${xss}`,
        price: 1000,
        period: 'one_time',
      },
      {
        id: 'plan_xss_2',
        name: `PlanTwo ${xss}`,
        description: `DescTwo ${xss}`,
        price: 500,
        period: 'monthly',
      },
    ],
  });

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload });
  const win = dom.window;
  const doc = win.document;

  assert.equal(doc.querySelectorAll('img').length, 0);
  assert.equal(win.__pwned2, undefined);

  const clone = doc.body.cloneNode(true);
  clone.querySelectorAll('script').forEach((s) => s.remove());
  const visibleText = clone.textContent;
  assert.ok(!visibleText.includes('<'));
  assert.ok(!visibleText.includes('>'));

  for (const fieldId of ['f-name', 'f-biz', 'f-phone', 'f-note']) {
    const val = doc.getElementById(fieldId).value;
    assert.ok(!val.includes('<'), `${fieldId} should strip <`);
    assert.ok(!val.includes('>'), `${fieldId} should strip >`);
  }
});

test('wraps instance-own doc.save (jsPDF 2.x UMD pattern) to POST PDF blob once', async () => {
  const fetchCalls = [];
  const fetchMock = async (url, init) => {
    fetchCalls.push({ url, init });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const payload = buildFinalPayload();
  const { dom, saveCalls } = await mountPitchDom({
    url: 'http://localhost/pitch/?mode=form',
    payload,
    fetchImpl: fetchMock,
  });

  // Replace jsPDF with a UMD-style constructor that defines `save` as an own property on `this`.
  // Wait: test via a fresh JSDOM? No, verify by testing hostJs behavior when FakeJsPdf defines own `save`:
  // We can re-evaluate via a tiny JSDOM with own-property save, or mountPitchDom option.
  // Actually let's test the constructor wrapper directly with a fresh JSDOM:
  let html = indexHtmlRaw.replace(
    /<link\s+rel="preconnect"[\s\S]*?<link\s+rel="stylesheet"\s+href="https:\/\/fonts\.googleapis\.com\/[^"]+"\s*>/i,
    '<link rel="stylesheet" href="fonts/fonts.css">',
  );
  html = html.replace(/<link\s+rel="stylesheet"\s+href="style\.css"\s*>/i, '');
  const scripts = [
    inlineScript(presetsJs),
    inlineScript(appJs),
    `<script type="application/json" id="magnm-pitch-data">${escapeJsonForScript(payload)}</script>`,
    inlineScript(hostJs),
  ].join('\n');
  html = html.replace(
    /<script\s+src="vendor\/jspdf\.umd\.min\.js"\s*><\/script>\s*<script\s+src="presets\.js"\s*><\/script>\s*<script\s+src="app\.js"\s*><\/script>/i,
    () => scripts,
  );
  const ownSaveCalls = [];
  const umdDom = new JSDOM(html, {
    url: 'http://localhost/pitch/?mode=form',
    runScripts: 'dangerously',
    beforeParse(win) {
      win.scrollTo = () => {};
      win.HTMLElement.prototype.scrollIntoView = () => {};
      win.fetch = fetchMock;
      function UmdJsPdf() {
        this.save = function (name) {
          ownSaveCalls.push(name);
        };
      }
      UmdJsPdf.prototype.setTextColor = function () {};
      UmdJsPdf.prototype.setFont = function () {};
      UmdJsPdf.prototype.setFontSize = function () {};
      UmdJsPdf.prototype.setDrawColor = function () {};
      UmdJsPdf.prototype.setLineWidth = function () {};
      UmdJsPdf.prototype.line = function () {};
      UmdJsPdf.prototype.addPage = function () {};
      UmdJsPdf.prototype.splitTextToSize = function (t) {
        return [String(t || '')];
      };
      UmdJsPdf.prototype.text = function () {};
      UmdJsPdf.prototype.output = function () {
        return new win.Blob(['%PDF-1.4'], { type: 'application/pdf' });
      };
      win.jspdf = { jsPDF: UmdJsPdf };
    },
  });
  if (umdDom.window.document.readyState === 'loading') {
    await new Promise((r) => umdDom.window.addEventListener('DOMContentLoaded', r, { once: true }));
  }

  // Also ensure unused vars from first mount don't warn
  assert.equal(saveCalls.length, 0);
  assert.equal(dom.window.total(), 15500);

  umdDom.window.downloadPdf();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(ownSaveCalls.length, 1);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, '/api/pitch/proposals/prop-final-1/pdf');
});
