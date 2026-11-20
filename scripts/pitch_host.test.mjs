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

test('hidden sheets hide nav buttons and sections, renumber nav and .pageno, update nav grid, and render Included services once', async () => {
  // Hide p2 and p5 -> visible sheets: p1 (About), p3 (Mobile App), p4 (Backend) => 3 sheets
  // Also test hiding p4 instead to verify Included services on p3
  const payload = {
    mode: 'preview',
    kind: 'proposal',
    proposal_id: 'prop-hidden-1',
    prices: {},
    custom_items: [],
    hidden_sheets: ['p2', 'p5'],
    hidden_about: [],
    custom_categories: [],
    included_from_hidden: [
      {
        item_id: 'ops_cloud',
        name: 'Production Cloud Infrastructure & CDN',
        sheet_shown_on: 'p3',
        needed_for: ['Cross-Platform Mobile App (Android)'],
        also_needed_on: ['p4'],
      },
    ],
    preset: {
      slug: 'hidden-test',
      name: 'Hidden Test',
      badge: 'Proposal for Hidden Test',
      initialSelected: ['a_base'],
      sections: {
        p2: [],
        p3: ['a_base', 'a_ios'],
        p4: ['b_db', 'b_admin'],
        p5: [],
      },
    },
  };

  const scrollCalls = [];
  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;
  const doc = win.document;

  win.HTMLElement.prototype.scrollIntoView = function (opts) {
    scrollCalls.push({ id: this.id, opts });
  };

  // p2 and p5 sections and nav buttons are hidden
  for (const hid of ['p2', 'p5']) {
    const sec = doc.getElementById(hid);
    const btn = doc.querySelector(`#main-nav button[data-go="${hid}"]`);
    assert.equal(sec.hidden, true);
    assert.equal(sec.style.display, 'none');
    assert.equal(btn.hidden, true);
    assert.equal(btn.style.display, 'none');
  }

  // Visible nav buttons are p1, p3, p4 numbered 1..3, and nav grid is repeat(3, 1fr)
  const navWrap = doc.querySelector('#main-nav .wrap');
  assert.equal(navWrap.style.gridTemplateColumns, 'repeat(3, 1fr)');
  const visibleBtns = Array.from(doc.querySelectorAll('#main-nav button[data-go]')).filter((b) => !b.hidden);
  assert.deepEqual(
    visibleBtns.map((b) => ({ go: b.getAttribute('data-go'), num: b.querySelector('i').textContent })),
    [
      { go: 'p1', num: '1' },
      { go: 'p3', num: '2' },
      { go: 'p4', num: '3' },
    ],
  );

  // Page labels say Page 1 of 3, Page 2 of 3, Page 3 of 3
  assert.equal(doc.querySelector('#p1 .pageno span:first-child').textContent, 'Page 1 of 3');
  assert.equal(doc.querySelector('#p3 .pageno span:first-child').textContent, 'Page 2 of 3');
  assert.equal(doc.querySelector('#p4 .pageno span:first-child').textContent, 'Page 3 of 3');

  // Summary stays visible on the last visible sheet (#p4 when p5 is hidden)
  assert.equal(doc.getElementById('summary').parentNode.id, 'p4');

  // go() ignores hidden sections and scrolls visible sections
  win.go('p2');
  win.go('p5');
  assert.equal(scrollCalls.length, 0);
  win.go('p3');
  assert.equal(scrollCalls.length, 1);
  assert.equal(scrollCalls[0].id, 'p3');

  // spy() only considers visible sections
  assert.deepEqual(Array.from(win.secs), ['p1', 'p3', 'p4']);
  Object.defineProperty(doc.getElementById('p1'), 'offsetTop', { value: 0, configurable: true });
  Object.defineProperty(doc.getElementById('p2'), 'offsetTop', { value: 100, configurable: true });
  Object.defineProperty(doc.getElementById('p3'), 'offsetTop', { value: 200, configurable: true });
  Object.defineProperty(doc.getElementById('p4'), 'offsetTop', { value: 600, configurable: true });
  win.scrollY = 120; // 120 + 140 = 260 >= p3.offsetTop (200)
  win.spy();
  assert.equal(doc.querySelector('#main-nav button[data-go="p3"]').getAttribute('aria-current'), 'true');
  assert.equal(doc.querySelector('#main-nav button[data-go="p2"]').getAttribute('aria-current'), 'false');

  // "Included services" appears on p3 (sheet_shown_on) once with working selection
  assert.deepEqual(readSheetStructure(doc, 'p3'), [
    { heading: 'Mobile Apps & Platforms', items: ['a_base', 'a_ios'] },
    { heading: 'Included services', items: ['ops_cloud'] },
  ]);
  const incBtns = doc.querySelectorAll('button.item[data-id="ops_cloud"]');
  assert.equal(incBtns.length, 1);
  assert.equal(incBtns[0].getAttribute('aria-checked'), 'false');
  incBtns[0].click();
  assert.equal(incBtns[0].getAttribute('aria-checked'), 'true');
  assert.equal(win.sel.ops_cloud, true);
});

test('hidden About keys hide the right blocks, hide dl.who when all 3 rows are hidden, keep wordmark/title, and final PDF skips hidden timeline/payment', async () => {
  // Partial hide: lead, what_we_do, architecture, timeline
  const payload1 = buildFinalPayload({
    hidden_about: ['lead', 'what_we_do', 'architecture', 'timeline'],
    preset: {
      ...buildFinalPayload().preset,
      architectureBlock: {
        heading: 'Arch Heading',
        sub: 'Arch Sub',
        roles: [{ label: 'Role 1', points: ['Point 1'] }],
      },
    },
  });
  const res1 = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload: payload1 });
  const doc1 = res1.dom.window.document;

  // Wordmark and title are never removed or hidden
  const mark = doc1.querySelector('#p1 h1.mark');
  assert.ok(mark);
  assert.equal(mark.hidden, false);
  assert.equal(mark.textContent, 'magnm');

  assert.equal(doc1.querySelector('#p1 .lead').hidden, true);
  assert.equal(doc1.getElementById('architecture-block-container').hidden, true);
  assert.equal(doc1.querySelector('#p1 ol.steps').closest('.block').hidden, true);
  assert.equal(doc1.querySelector('#p1 .pay').closest('.block').hidden, false);

  const whoRows1 = Array.from(doc1.querySelectorAll('#p1 dl.who > div'));
  assert.equal(whoRows1[0].hidden, true); // what_we_do
  assert.equal(whoRows1[1].hidden, false); // portfolio
  assert.equal(whoRows1[2].hidden, false); // this_plan
  assert.equal(doc1.querySelector('#p1 dl.who').hidden, false);

  // Final PDF skips timeline when timeline is hidden, but keeps payment terms
  res1.dom.window.downloadPdf();
  const texts1 = res1.pdfTextCalls.map((c) => c.text);
  assert.ok(!texts1.includes('TIMELINE'));
  assert.ok(!texts1.some((t) => t.includes('Kickoff')));
  assert.ok(texts1.some((t) => t.includes('50% to start')));

  // Hide all 3 who rows and payment too
  const payload2 = buildFinalPayload({
    hidden_about: ['what_we_do', 'portfolio', 'this_plan', 'payment', 'timeline'],
  });
  const res2 = await mountPitchDom({ url: 'http://localhost/pitch/?mode=form', payload: payload2 });
  const doc2 = res2.dom.window.document;
  assert.equal(doc2.querySelector('#p1 dl.who').hidden, true);
  assert.equal(doc2.querySelector('#p1 dl.who').style.display, 'none');
  assert.equal(doc2.querySelector('#p1 .pay').closest('.block').hidden, true);

  res2.dom.window.downloadPdf();
  const texts2 = res2.pdfTextCalls.map((c) => c.text);
  assert.ok(!texts2.includes('TIMELINE'));
  assert.ok(!texts2.some((t) => t.includes('50% to start')));
  assert.ok(!texts2.some((t) => t.includes('Custom payment note')));
});

test('custom categories on p3 and new_sheet render groups, nav button, numbering, go(), summary, PDF, and strip < and >', async () => {
  const payload = {
    mode: 'preview',
    kind: 'proposal',
    proposal_id: 'prop-cats',
    prices: {},
    hidden_sheets: ['p2', 'p5'],
    hidden_about: [],
    custom_categories: [
      {
        id: 'cat_wearables',
        name: 'Wearables <XSS>',
        placement: 'p3',
        sheet_title: '',
        sheet_lead: '',
        item_ids: ['custom_watch'],
      },
      {
        id: 'cat_drone',
        name: 'Drone software',
        placement: 'new_sheet',
        sheet_title: 'Drone <Sheet> Title',
        sheet_lead: 'Autonomous <Lead> text',
        item_ids: ['custom_flight', 'custom_gimbal'],
      },
    ],
    custom_items: [
      {
        id: 'custom_watch',
        g: 'Wearables <XSS>',
        n: 'Watch Companion',
        d: 'WearOS and watchOS sync.',
        p: 3200,
        r: ['a_base'],
      },
      {
        id: 'custom_flight',
        g: 'Drone software',
        n: 'Flight Controller',
        d: 'Waypoint autopilot.',
        p: 8500,
        r: ['b_db'],
      },
      {
        id: 'custom_gimbal',
        g: 'Drone software',
        n: 'Gimbal Telemetry',
        d: 'Camera stabilization feed.',
        p: 4100,
        r: ['custom_flight'],
      },
    ],
    included_from_hidden: [],
    preset: {
      slug: 'drone-co',
      name: 'Drone Co',
      badge: 'Proposal for Drone Co',
      initialSelected: ['a_base', 'b_db', 'custom_watch', 'custom_flight', 'custom_gimbal'],
      sections: {
        p2: [],
        p3: ['a_base'],
        p4: ['b_db'],
        p5: [],
      },
    },
  };

  const { dom, pdfTextCalls } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;
  const doc = win.document;

  // < and > stripped from category name, sheet_title, and sheet_lead
  assert.ok(win.GROUPS.includes('Wearables XSS'));
  assert.ok(win.GROUPS.includes('Drone software'));
  assert.ok(!doc.body.textContent.includes('<XSS>'));
  assert.ok(!doc.body.textContent.includes('<Sheet>'));
  assert.ok(!doc.body.textContent.includes('<Lead>'));

  // Category on p3 renders after filtered built-in groups
  assert.deepEqual(readSheetStructure(doc, 'p3'), [
    { heading: 'Mobile Apps & Platforms', items: ['a_base'] },
    { heading: 'Wearables XSS', items: ['custom_watch'] },
  ]);

  // new_sheet category creates section #pc1 and nav button [data-go="pc1"], total visible = 4 (p1, p3, p4, pc1)
  const pc1 = doc.getElementById('pc1');
  assert.ok(pc1);
  assert.equal(pc1.querySelector('.pageno span:first-child').textContent, 'Page 4 of 4');
  assert.equal(pc1.querySelector('.pageno span:last-child').textContent, 'Drone Sheet Title');
  assert.equal(pc1.querySelector('h2.big').textContent, 'Drone Sheet Title');
  assert.equal(pc1.querySelector('p.lead').textContent, 'Autonomous Lead text');
  assert.deepEqual(readSheetStructure(doc, 'pc1'), [
    { heading: 'Drone software', items: ['custom_flight', 'custom_gimbal'] },
  ]);

  const visibleBtns = Array.from(doc.querySelectorAll('#main-nav button[data-go]')).filter((b) => !b.hidden);
  assert.deepEqual(
    visibleBtns.map((b) => ({ go: b.getAttribute('data-go'), num: b.querySelector('i').textContent })),
    [
      { go: 'p1', num: '1' },
      { go: 'p3', num: '2' },
      { go: 'p4', num: '3' },
      { go: 'pc1', num: '4' },
    ],
  );

  // go('pc1') and go('cat_drone') scroll pc1 into view
  const goTargets = [];
  win.HTMLElement.prototype.scrollIntoView = function () {
    goTargets.push(this.id);
  };
  win.go('pc1');
  win.go('cat_drone');
  assert.deepEqual(goTargets, ['pc1', 'pc1']);

  // Summary and PDF show items grouped under the custom category names
  const sumText = doc.getElementById('sumlines').textContent;
  assert.ok(sumText.includes('Wearables XSS'));
  assert.ok(sumText.includes('Watch Companion'));
  assert.ok(sumText.includes('Drone software'));
  assert.ok(sumText.includes('Flight Controller'));
  assert.ok(sumText.includes('Gimbal Telemetry'));

  win.downloadPdf();
  const pdfTexts = pdfTextCalls.map((c) => c.text);
  assert.ok(pdfTexts.includes('WEARABLES XSS'));
  assert.ok(pdfTexts.includes('DRONE SOFTWARE'));
  assert.ok(pdfTexts.some((t) => t.includes('Flight Controller')));
  assert.ok(pdfTexts.some((t) => t.includes('Gimbal Telemetry')));
});

test('scroll-to message scrolls matching visible targets, respects prefers-reduced-motion, and ignores unknown/hidden targets and foreign origins', async () => {
  const payload = {
    mode: 'preview',
    kind: 'proposal',
    proposal_id: 'prop-scroll',
    prices: {},
    hidden_sheets: ['p2'],
    hidden_about: ['portfolio'],
    custom_categories: [
      {
        id: 'cat_drone',
        name: 'Drone software',
        placement: 'new_sheet',
        sheet_title: 'Drone software',
        sheet_lead: 'Drone lead',
        item_ids: ['custom_drone_1'],
      },
    ],
    custom_items: [
      {
        id: 'custom_drone_1',
        g: 'Drone software',
        n: 'Drone Item',
        d: 'Desc',
        p: 5000,
        r: [],
      },
    ],
    preset: {
      slug: 'scroll-test',
      name: 'Scroll Test',
      badge: 'Scroll Test',
      architectureBlock: {
        heading: 'Arch',
        sub: 'Sub',
        roles: [{ label: 'R1', points: ['P1'] }],
      },
      initialSelected: ['a_base', 'custom_drone_1'],
      sections: { p2: [], p3: ['a_base'], p4: ['b_db'], p5: ['ops_cloud'] },
    },
  };

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;

  const scrollCalls = [];
  win.scrollTo = (arg1, arg2) => {
    scrollCalls.push({ arg1, arg2 });
  };
  let reducedMotion = false;
  win.matchMedia = (q) => ({
    matches: q.includes('prefers-reduced-motion') ? reducedMotion : false,
  });

  function postScroll(target, origin = win.location.origin) {
    win.dispatchEvent(
      new win.MessageEvent('message', {
        origin,
        data: { type: 'magnm:pitch-scroll-to', target },
      }),
    );
  }

  // Foreign origin is ignored
  postScroll('about', 'https://attacker.example');
  assert.equal(scrollCalls.length, 0);

  // Unknown and hidden targets are ignored
  postScroll('unknown_target');
  postScroll('about:portfolio'); // hidden in hidden_about
  postScroll('p2'); // hidden in hidden_sheets
  postScroll('item:w_front'); // on hidden sheet p2
  assert.equal(scrollCalls.length, 0);

  // Visible targets trigger smooth scrollTo
  const validTargets = [
    'about',
    'about:lead',
    'about:what_we_do',
    'about:this_plan',
    'about:architecture',
    'about:timeline',
    'about:payment',
    'p3',
    'p4',
    'p5',
    'pc1',
    'cat_drone',
    'item:a_base',
    'item:custom_drone_1',
    'summary',
  ];
  for (const t of validTargets) {
    const before = scrollCalls.length;
    postScroll(t);
    assert.equal(scrollCalls.length, before + 1, `Expected scrollTo for target "${t}"`);
    assert.equal(scrollCalls[scrollCalls.length - 1].arg1.behavior, 'smooth');
  }

  // Under prefers-reduced-motion, behavior is instant
  reducedMotion = true;
  postScroll('summary');
  assert.equal(scrollCalls[scrollCalls.length - 1].arg1.behavior, 'instant');
});

test('live preview update applies price overrides, custom categories, hidden sheets and hidden About without reload while preserving scroll position', async () => {
  const payload = {
    mode: 'preview',
    kind: 'proposal',
    proposal_id: 'prop-live',
    prices: { w_front: 6000, b_db: 2000 },
    custom_items: [],
    hidden_sheets: [],
    hidden_about: [],
    custom_categories: [],
    preset: {
      slug: 'live-client',
      name: 'Live Client',
      badge: 'Live Client',
      initialSelected: ['w_front', 'b_db'],
      sections: { p2: ['w_front'], p3: ['a_base'], p4: ['b_db'], p5: ['ops_cloud'] },
    },
  };

  const { dom } = await mountPitchDom({ url: 'http://localhost/pitch/?mode=preview', payload });
  const win = dom.window;
  const doc = win.document;

  assert.equal(win.total(), 8000);
  win.scrollX = 0;
  win.scrollY = 420;
  const restoredScrolls = [];
  win.scrollTo = (x, y) => {
    restoredScrolls.push({ x, y });
  };

  // Send live preview update: change w_front price to 9500, hide p3 and p5, hide lead, add custom category & item
  win.dispatchEvent(
    new win.MessageEvent('message', {
      origin: win.location.origin,
      data: {
        type: 'magnm:pitch-preview-update',
        payload: {
          prices: { w_front: 9500, b_db: 2000 },
          hidden_sheets: ['p3', 'p5'],
          hidden_about: ['lead'],
          custom_categories: [
            {
              id: 'cat_edge',
              name: 'Edge Compute',
              placement: 'p4',
              sheet_title: '',
              sheet_lead: '',
              item_ids: ['custom_edge_node'],
            },
          ],
          custom_items: [
            {
              id: 'custom_edge_node',
              g: 'Edge Compute',
              n: 'Edge Node Agent',
              d: 'On-premise sync agent.',
              p: 3000,
              r: ['b_db'],
            },
          ],
          preset: {
            slug: 'live-client',
            name: 'Live Client',
            badge: 'Live Client',
            initialSelected: ['w_front', 'b_db', 'custom_edge_node'],
            sections: { p2: ['w_front'], p3: [], p4: ['b_db'], p5: [] },
          },
        },
      },
    }),
  );

  // Total updated to 9500 + 2000 + 3000 = 14500 without reload
  assert.equal(win.total(), 14500);
  assert.equal(doc.getElementById('total').textContent, '\u20b914,500');
  assert.equal(doc.getElementById('p3').hidden, true);
  assert.equal(doc.getElementById('p5').hidden, true);
  assert.equal(doc.querySelector('#p1 .lead').hidden, true);
  assert.equal(doc.querySelector('#p4 .pageno span:first-child').textContent, 'Page 3 of 3');
  assert.deepEqual(readSheetStructure(doc, 'p4'), [
    { heading: 'Database, API & Authentication', items: ['b_db'] },
    { heading: 'Edge Compute', items: ['custom_edge_node'] },
  ]);
  assert.deepEqual(restoredScrolls[restoredScrolls.length - 1], { x: 0, y: 420 });

  // Resetting price override back to base works on subsequent preview update
  win.dispatchEvent(
    new win.MessageEvent('message', {
      origin: win.location.origin,
      data: {
        type: 'magnm:pitch-preview-update',
        payload: {
          prices: {},
        },
      },
    }),
  );
  assert.equal(win.ITEMS.w_front.p, 6000);
  assert.equal(win.total(), 6000 + 2000 + 3000);
});

