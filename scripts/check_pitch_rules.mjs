#!/usr/bin/env node
/**
 * Vendored Pitch rules check.
 *
 * Runs the anti-hijacking and catalog integrity checks from
 * vendor/magnm-pitch/scripts/check-rules.js that apply to the vendored
 * files (index.html, presets.js, app.js). Upstream checks for vercel.json
 * and package.json are omitted because those deployment files are not vendored.
 */

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const vendorDir = path.resolve(__dirname, '..', 'vendor', 'magnm-pitch');

const errors = [];

function readRequired(relPath) {
  const fullPath = path.join(vendorDir, relPath);
  if (!fs.existsSync(fullPath)) {
    errors.push(`Missing required vendored file: ${relPath}`);
    return null;
  }
  return fs.readFileSync(fullPath, 'utf8');
}

const indexHtml = readRequired('index.html');
if (indexHtml !== null) {
  const forbiddenTerms = [
    'Urban Company',
    'Urban-style',
    'urban-style',
    'An Urban Company-style booking platform',
    'What an Urban-style platform does',
  ];
  for (const term of forbiddenTerms) {
    if (indexHtml.includes(term)) {
      errors.push(`[RULE VIOLATION] index.html contains hardcoded client term "${term}".`);
    }
  }
  if (!indexHtml.includes('magnm Software Cost Planner')) {
    errors.push('[RULE VIOLATION] index.html missing title "magnm Software Cost Planner".');
  }
  if (!indexHtml.includes('presets.js')) {
    errors.push('[RULE VIOLATION] index.html must import presets.js.');
  }
  if (!indexHtml.includes('id="admin-view"')) {
    errors.push('[RULE VIOLATION] index.html missing #admin-view container.');
  }
  if (!indexHtml.includes('id="client-banner"')) {
    errors.push('[RULE VIOLATION] index.html missing #client-banner container.');
  }
}

const presetsPath = path.join(vendorDir, 'presets.js');
if (readRequired('presets.js') !== null) {
  try {
    const { PLANNER_PRESETS } = require(presetsPath);
    if (!PLANNER_PRESETS) {
      errors.push('[RULE VIOLATION] presets.js must export PLANNER_PRESETS.');
    } else {
      if (!PLANNER_PRESETS.general || !PLANNER_PRESETS.general.title || !PLANNER_PRESETS.general.lead) {
        errors.push('[RULE VIOLATION] PLANNER_PRESETS.general must define title and lead.');
      }
      if (!PLANNER_PRESETS['urban-company'] || PLANNER_PRESETS['urban-company'].slug !== 'urban-company') {
        errors.push('[RULE VIOLATION] Preserved preset "urban-company" is missing or has mismatched slug.');
      }
      for (const slug of Object.keys(PLANNER_PRESETS)) {
        const preset = PLANNER_PRESETS[slug];
        if (!preset.slug || !preset.name) {
          errors.push(`[RULE VIOLATION] Preset "${slug}" missing slug or name.`);
        }
        if (slug !== preset.slug) {
          errors.push(`[RULE VIOLATION] Preset key "${slug}" does not match internal slug "${preset.slug}".`);
        }
      }
    }
  } catch (err) {
    errors.push(`[SYNTAX ERROR] Failed to load presets.js: ${err.message}`);
  }
}

const appPath = path.join(vendorDir, 'app.js');
if (readRequired('app.js') !== null) {
  try {
    const app = require(appPath);
    if (!app.ITEMS || !app.GROUPS) {
      errors.push('[ENGINE ERROR] app.js must export ITEMS and GROUPS.');
    } else {
      const requiredGroups = [
        'Web & SaaS',
        'Mobile app',
        'Backend',
        'AI & Automation',
        'DevOps & Maintenance',
      ];
      for (const g of requiredGroups) {
        if (!app.GROUPS.includes(g)) {
          errors.push(`[RULE VIOLATION] app.js GROUPS missing category "${g}".`);
        }
      }
      for (const id of Object.keys(app.ITEMS)) {
        const item = app.ITEMS[id];
        if (!item.n || !item.d || typeof item.p !== 'number' || !Array.isArray(item.r)) {
          errors.push(`[SCHEMA ERROR] Item "${id}" missing required fields (n, d, p, r).`);
        }
        for (const dep of item.r || []) {
          if (!app.ITEMS[dep]) {
            errors.push(`[DEPENDENCY ERROR] Item "${id}" references unknown dependency "${dep}".`);
          }
        }
      }
    }
  } catch (err) {
    errors.push(`[EXECUTION ERROR] Failed to load app.js: ${err.message}`);
  }
}

if (errors.length > 0) {
  console.error('Vendored Pitch rules check failed:');
  errors.forEach((err, i) => console.error(`  ${i + 1}. ${err}`));
  process.exit(1);
}

console.log('Vendored Pitch rules check passed.');
