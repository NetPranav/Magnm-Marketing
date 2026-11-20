import type { StatusState } from '@magnm/design/react';
import { ApiError } from '../../api';
import {
  formatRupees,
  type PitchAboutSectionKey,
  type PitchCatalog,
  type PitchCatalogItem,
  type PitchCategoryPlacement,
  type PitchContact,
  type PitchCustomCategory,
  type PitchCustomItem,
  type PitchDefaultPlans,
  type PitchDroppedItemChange,
  type PitchIncludedFromHidden,
  type PitchPaymentPlanRow,
  type PitchPreset,
  type PitchPricingSummary,
  type PitchProposal,
  type PitchProposalKind,
  type PitchRuleViolation,
  type PitchSectionKey,
  type PitchSections,
  type PitchServicePlan,
  type PitchServicePlanPeriod,
  type PitchTimelineStep,
} from '../../api/pitch';

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;
export const RESERVED_STATIC_SLUGS = new Set(['general', 'admin', 'clients', 'default-quotation']);
export const MAX_ARCH_ROLES = 3;
export const MAX_ROLE_POINTS = 6;
export const MIN_PAYMENT_ROWS = 2;
export const MAX_PAYMENT_ROWS = 4;
export const MIN_TIMELINE_STEPS = 1;
export const MAX_TIMELINE_STEPS = 8;
export const MAX_SERVICE_PLANS = 6;
export const MAX_CUSTOM_CATEGORIES = 6;
export const FOLLOW_PREVIEW_STORAGE_KEY = 'magnm-pitch-follow-preview';

export const PITCH_ABOUT_SECTIONS: Array<{
  key: PitchAboutSectionKey;
  label: string;
}> = [
  { key: 'lead', label: 'Intro' },
  { key: 'what_we_do', label: 'What we do' },
  { key: 'portfolio', label: 'Portfolio' },
  { key: 'this_plan', label: 'This plan' },
  { key: 'architecture', label: 'Architecture block' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'payment', label: 'How you pay' },
];

export const SERVICE_PLAN_PERIOD_OPTIONS: Array<{
  value: PitchServicePlanPeriod;
  label: string;
}> = [
  { value: 'one_time', label: 'One-time' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'yearly', label: 'Yearly' },
];

export const FALLBACK_DEFAULT_PLANS: PitchDefaultPlans = {
  payment_plan: [
    { percent: 40, label: 'to start' },
    { percent: 30, label: 'at demo' },
    { percent: 30, label: 'at launch' },
  ],
  payment_note:
    'Third-party infrastructure (domain, cloud hosting, SMS credits, and Play/App Store developer licenses) are paid at actual cost directly to providers.',
  timeline: [
    {
      title: 'Scope & Cost Estimation',
      when: 'Day 1',
      detail: 'Configure components across web, mobile, backend, AI and DevOps. The price calculates live.',
    },
    {
      title: 'Architecture & UI/UX Screens',
      when: 'Week 1',
      detail: 'We wireframe user flows and share interactive Figma prototypes for your approval before development.',
    },
    {
      title: 'Sprint Development & QA Testing',
      when: 'Weeks 2 to 6',
      detail: 'Bi-weekly progress demos on staging environments with continuous integration and testing.',
    },
    {
      title: 'Cloud Deployment & Handover',
      when: 'Final Week',
      detail: 'Production launch on your domain, app store submissions, complete source code handover, and docs.',
    },
  ],
};

export interface SheetGroupSpec {
  key: PitchSectionKey;
  label: string;
  groups: string[];
}

export const PITCH_SHEETS: SheetGroupSpec[] = [
  { key: 'p2', label: 'Web & SaaS', groups: ['Web & SaaS'] },
  { key: 'p3', label: 'Mobile app', groups: ['Mobile app'] },
  { key: 'p4', label: 'Backend', groups: ['Backend'] },
  { key: 'p5', label: 'AI & Ops', groups: ['AI & Automation', 'DevOps & Maintenance'] },
];

export const GROUP_TO_SECTION: Record<string, PitchSectionKey> = {
  'Web & SaaS': 'p2',
  'Mobile app': 'p3',
  Backend: 'p4',
  'AI & Automation': 'p5',
  'DevOps & Maintenance': 'p5',
};

export function sectionForGroup(
  groupName: string,
  customCategories: PitchCustomCategory[] = [],
): PitchSectionKey | null {
  const cat = customCategories.find((c) => c.name === groupName);
  if (cat) {
    if (
      cat.placement === 'p2' ||
      cat.placement === 'p3' ||
      cat.placement === 'p4' ||
      cat.placement === 'p5'
    ) {
      return cat.placement;
    }
    return null;
  }
  return GROUP_TO_SECTION[groupName] || 'p5';
}

export function itemSheetKey(
  itemId: string,
  allItems: Record<string, { g: string }>,
  customCategories: PitchCustomCategory[] = [],
): string {
  const item = allItems[itemId];
  if (!item) return 'p5';
  const cat = customCategories.find((c) => c.name === item.g);
  if (cat) {
    if (
      cat.placement === 'p2' ||
      cat.placement === 'p3' ||
      cat.placement === 'p4' ||
      cat.placement === 'p5'
    ) {
      return cat.placement;
    }
    return cat.id || 'new_sheet';
  }
  return GROUP_TO_SECTION[item.g] || 'p5';
}

/** Count visible software options (visible built-in p2..p5 + new_sheet categories with >= 1 item). */
export function countVisibleSoftwareOptions(draft: {
  hidden_sheets: PitchSectionKey[];
  custom_categories: PitchCustomCategory[];
  custom_items: PitchCustomItem[];
}): {
  visibleBuiltinCount: number;
  activeNewSheetCount: number;
  totalSoftwareOptions: number;
} {
  const hiddenSet = new Set(draft.hidden_sheets || []);
  const visibleBuiltinCount = PITCH_SHEETS.filter((s) => !hiddenSet.has(s.key)).length;
  const activeNewSheetCount = (draft.custom_categories || []).filter(
    (cat) =>
      cat.placement === 'new_sheet' &&
      Boolean(cat.name) &&
      (draft.custom_items || []).some((ci) => ci.g === cat.name),
  ).length;
  return {
    visibleBuiltinCount,
    activeNewSheetCount,
    totalSoftwareOptions: visibleBuiltinCount + activeNewSheetCount,
  };
}

/** Compute required items from hidden sheets needed by items on visible sheets (mirroring pitch_host.py). */
export function computeLocalIncludedFromHidden(
  draft: {
    kind: PitchProposalKind;
    hidden_sheets: PitchSectionKey[];
    custom_items: PitchCustomItem[];
    custom_categories: PitchCustomCategory[];
    preset: PitchPreset;
  },
  allItems: Record<string, { g: string; n: string; r: string[] }>,
): PitchIncludedFromHidden[] {
  if (draft.kind === 'default') return [];
  const hiddenSet = new Set<string>(draft.hidden_sheets || []);
  const customCategories = draft.custom_categories || [];
  const customItems = draft.custom_items || [];

  if (draft.kind === 'final') {
    const rawSelected = (draft.preset.initialSelected || []).filter((id) => Boolean(allItems[id]));
    const visibleSeeds = rawSelected.filter(
      (id) => !hiddenSet.has(itemSheetKey(id, allItems, customCategories)),
    );
    const { selected: expanded } = expandSelectionWithDeps(visibleSeeds, allItems);
    const sheetsWithAgreed = new Set(
      expanded
        .map((id) => itemSheetKey(id, allItems, customCategories))
        .filter((sk) => !hiddenSet.has(sk)),
    );
    for (const s of PITCH_SHEETS) {
      if (!hiddenSet.has(s.key) && !sheetsWithAgreed.has(s.key)) {
        hiddenSet.add(s.key);
      }
    }
  }

  if (hiddenSet.size === 0) return [];

  const visibleSheets: Array<[string, string[]]> = [];
  if (draft.kind === 'final') {
    const rawSelected = (draft.preset.initialSelected || []).filter((id) => Boolean(allItems[id]));
    const visibleSeeds = rawSelected.filter(
      (id) => !hiddenSet.has(itemSheetKey(id, allItems, customCategories)),
    );
    const { selected: expanded } = expandSelectionWithDeps(visibleSeeds, allItems);
    const agreedSet = new Set(expanded);
    for (const s of PITCH_SHEETS) {
      if (hiddenSet.has(s.key)) continue;
      const sheetItems = expanded.filter(
        (id) => itemSheetKey(id, allItems, customCategories) === s.key,
      );
      if (sheetItems.length > 0) {
        visibleSheets.push([s.key, sheetItems]);
      }
    }
    for (const cat of customCategories) {
      if (cat.placement === 'new_sheet' && cat.id) {
        const sheetItems = customItems
          .filter((c) => c.g === cat.name && agreedSet.has(c.id))
          .map((c) => c.id);
        if (sheetItems.length > 0) {
          visibleSheets.push([cat.id, sheetItems]);
        }
      }
    }
  } else {
    const sections = draft.preset.sections || { p2: [], p3: [], p4: [], p5: [] };
    const initialSel = (draft.preset.initialSelected || []).filter((id) => Boolean(allItems[id]));
    for (const s of PITCH_SHEETS) {
      if (hiddenSet.has(s.key)) continue;
      const sheetItems: string[] = [];
      const seenOnSheet = new Set<string>();
      for (const id of sections[s.key] || []) {
        if (allItems[id] && !seenOnSheet.has(id)) {
          if (!hiddenSet.has(itemSheetKey(id, allItems, customCategories))) {
            seenOnSheet.add(id);
            sheetItems.push(id);
          }
        }
      }
      for (const c of customItems) {
        if (!seenOnSheet.has(c.id) && itemSheetKey(c.id, allItems, customCategories) === s.key) {
          seenOnSheet.add(c.id);
          sheetItems.push(c.id);
        }
      }
      for (const id of initialSel) {
        if (!seenOnSheet.has(id) && itemSheetKey(id, allItems, customCategories) === s.key) {
          seenOnSheet.add(id);
          sheetItems.push(id);
        }
      }
      visibleSheets.push([s.key, sheetItems]);
    }
    for (const cat of customCategories) {
      if (cat.placement === 'new_sheet' && cat.id) {
        const sheetItems = customItems.filter((c) => c.g === cat.name).map((c) => c.id);
        if (sheetItems.length > 0) {
          visibleSheets.push([cat.id, sheetItems]);
        }
      }
    }
  }

  const outById: Record<string, PitchIncludedFromHidden> = {};
  const outOrder: string[] = [];

  for (const [sheetKey, sheetItems] of visibleSheets) {
    const entriesById: Record<string, PitchIncludedFromHidden> = {};
    const orderedIds: string[] = [];
    for (const vid of sheetItems) {
      const vitem = allItems[vid];
      if (!vitem) continue;
      const vname = vitem.n || vid;
      for (const depId of vitem.r || []) {
        if (!allItems[depId]) continue;
        if (hiddenSet.has(itemSheetKey(depId, allItems, customCategories))) {
          if (!entriesById[depId]) {
            entriesById[depId] = {
              item_id: depId,
              name: allItems[depId].n || depId,
              sheet_shown_on: sheetKey,
              needed_for: [vname],
            };
            orderedIds.push(depId);
          } else if (!entriesById[depId].needed_for.includes(vname)) {
            entriesById[depId].needed_for.push(vname);
          }
        }
      }
    }

    let idx = 0;
    while (idx < orderedIds.length) {
      const hid = orderedIds[idx];
      idx += 1;
      const parentNeeded = entriesById[hid].needed_for;
      for (const transId of allItems[hid]?.r || []) {
        if (!allItems[transId]) continue;
        if (hiddenSet.has(itemSheetKey(transId, allItems, customCategories))) {
          if (!entriesById[transId]) {
            entriesById[transId] = {
              item_id: transId,
              name: allItems[transId].n || transId,
              sheet_shown_on: sheetKey,
              needed_for: [...parentNeeded],
            };
            orderedIds.push(transId);
          } else {
            for (const pname of parentNeeded) {
              if (!entriesById[transId].needed_for.includes(pname)) {
                entriesById[transId].needed_for.push(pname);
              }
            }
          }
        }
      }
    }

    for (const did of orderedIds) {
      const entry = entriesById[did];
      if (!outById[did]) {
        outById[did] = {
          item_id: entry.item_id,
          name: entry.name,
          sheet_shown_on: entry.sheet_shown_on,
          needed_for: [...entry.needed_for],
          also_needed_on: [],
        };
        outOrder.push(did);
      } else {
        const existing = outById[did];
        if (!existing.also_needed_on) existing.also_needed_on = [];
        if (sheetKey !== existing.sheet_shown_on && !existing.also_needed_on.includes(sheetKey)) {
          existing.also_needed_on.push(sheetKey);
        }
        for (const nname of entry.needed_for) {
          if (!existing.needed_for.includes(nname)) {
            existing.needed_for.push(nname);
          }
        }
      }
    }
  }

  return outOrder.map((did) => outById[did]);
}

/** Reject < and > in text fields rendered on the hosted Pitch page. */
export function validatePitchText(value: string): string | null {
  if (value.includes('<') || value.includes('>')) {
    return 'Text cannot contain < or >';
  }
  return null;
}

/** Validate a proposal slug using the same rules as engine/pitch.py. */
export function validateSlug(slug: string, reservedPresets: string[] = []): string | null {
  const trimmed = slug.trim();
  if (!trimmed) {
    return 'Slug is required.';
  }
  if (!SLUG_RE.test(trimmed)) {
    return 'Slug must be 2-63 lowercase letters, digits, or hyphens, starting with a letter or digit.';
  }
  if (RESERVED_STATIC_SLUGS.has(trimmed) || reservedPresets.includes(trimmed)) {
    return `Slug "${trimmed}" is reserved and cannot be used.`;
  }
  return null;
}

/** Build a combined item map of catalog items and proposal custom items. */
export function buildAllItemsMap(
  catalog: PitchCatalog | null,
  customItems: PitchCustomItem[],
): Record<string, { g: string; n: string; d: string; p: number; r: string[] }> {
  const out: Record<string, { g: string; n: string; d: string; p: number; r: string[] }> = {};
  if (catalog?.items) {
    for (const [id, item] of Object.entries(catalog.items)) {
      out[id] = {
        g: item.g,
        n: item.n,
        d: item.d,
        p: item.price ?? item.p,
        r: Array.isArray(item.r) ? item.r : [],
      };
    }
  }
  for (const c of customItems) {
    if (c.id) {
      out[c.id] = {
        g: c.g,
        n: c.n,
        d: c.d,
        p: c.p,
        r: Array.isArray(c.r) ? c.r : [],
      };
    }
  }
  return out;
}

/**
 * Expand initialSelected with transitive `r` dependencies (mirroring Pitch addWithDeps)
 * and compute `neededBy[id]` = names of selected items that depend on `id`.
 */
export function expandSelectionWithDeps(
  initialIds: string[],
  allItems: Record<string, { n?: string; r?: string[] }>,
): { selected: string[]; addedDeps: string[]; neededBy: Record<string, string[]> } {
  const selected: string[] = [];
  const seen = new Set<string>();
  const addedDeps: string[] = [];

  function addWithDeps(itemId: string, isDep: boolean) {
    if (seen.has(itemId) || !allItems[itemId]) return;
    seen.add(itemId);
    selected.push(itemId);
    if (isDep) addedDeps.push(itemId);
    for (const depId of allItems[itemId].r || []) {
      addWithDeps(depId, true);
    }
  }

  for (const id of initialIds) {
    addWithDeps(id, false);
  }

  const neededBy: Record<string, string[]> = {};
  for (const parentId of selected) {
    const parent = allItems[parentId];
    if (!parent) continue;
    const parentName = parent.n || parentId;
    for (const depId of parent.r || []) {
      if (!seen.has(depId)) continue;
      if (!neededBy[depId]) neededBy[depId] = [];
      if (!neededBy[depId].includes(parentName)) {
        neededBy[depId].push(parentName);
      }
    }
  }

  return { selected, addedDeps, neededBy };
}

/** Map proposal status to monochrome StatusDot state. */
export function mapProposalStatusState(status: string): StatusState {
  switch ((status || '').toLowerCase()) {
    case 'ready':
    case 'sent':
      return 'online';
    case 'draft':
      return 'idle';
    case 'archived':
    default:
      return 'off';
  }
}

export function formatKindLabel(kind: PitchProposalKind | string): string {
  switch (kind) {
    case 'quote_form':
      return 'Quote form';
    case 'final':
      return 'Final quotation';
    case 'default':
      return 'Default';
    default:
      return 'Proposal';
  }
}

export function formatServicePlanPeriodLabel(period: PitchServicePlanPeriod | string): string {
  const found = SERVICE_PLAN_PERIOD_OPTIONS.find((opt) => opt.value === period);
  return found ? found.label : String(period || 'One-time');
}

export function formatServicePlanPeriodSuffix(period: PitchServicePlanPeriod | string): string {
  switch (period) {
    case 'monthly':
      return ' / month';
    case 'quarterly':
      return ' / quarter';
    case 'yearly':
      return ' / year';
    default:
      return '';
  }
}

/** Build a compact recurring totals string for the bottom bar (e.g. "+ ₹2,500 / month"). */
export function formatRecurringTotalsLine(summary?: PitchPricingSummary | null): string | null {
  if (!summary) return null;
  const totals = summary.recurring_totals;
  const parts: string[] = [];
  if (totals) {
    if (totals.monthly > 0) parts.push(`+ ${formatRupees(totals.monthly)} / month`);
    if (totals.quarterly > 0) parts.push(`+ ${formatRupees(totals.quarterly)} / quarter`);
    if (totals.yearly > 0) parts.push(`+ ${formatRupees(totals.yearly)} / year`);
  }
  if (parts.length === 0 && Array.isArray(summary.recurring) && summary.recurring.length > 0) {
    const grouped: Record<'monthly' | 'quarterly' | 'yearly', number> = {
      monthly: 0,
      quarterly: 0,
      yearly: 0,
    };
    for (const item of summary.recurring) {
      if (item.period in grouped) {
        grouped[item.period] += Number(item.price) || 0;
      }
    }
    if (grouped.monthly > 0) parts.push(`+ ${formatRupees(grouped.monthly)} / month`);
    if (grouped.quarterly > 0) parts.push(`+ ${formatRupees(grouped.quarterly)} / quarter`);
    if (grouped.yearly > 0) parts.push(`+ ${formatRupees(grouped.yearly)} / year`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** Return cloned default payment plan, note, and timeline from catalog or built-in Pitch fallback. */
export function getDefaultFinalPlans(catalog: PitchCatalog | null): {
  payment_plan: PitchPaymentPlanRow[];
  payment_note: string;
  timeline: PitchTimelineStep[];
} {
  const src = catalog?.default_plans;
  const paymentPlan =
    src?.payment_plan && src.payment_plan.length >= MIN_PAYMENT_ROWS
      ? src.payment_plan
      : FALLBACK_DEFAULT_PLANS.payment_plan;
  const timeline =
    src?.timeline && src.timeline.length >= MIN_TIMELINE_STEPS
      ? src.timeline
      : FALLBACK_DEFAULT_PLANS.timeline;
  const paymentNote =
    typeof src?.payment_note === 'string' && src.payment_note.trim()
      ? src.payment_note
      : FALLBACK_DEFAULT_PLANS.payment_note;

  return {
    payment_plan: paymentPlan.map((row) => ({ percent: row.percent, label: row.label })),
    payment_note: paymentNote,
    timeline: timeline.map((step) => ({
      title: step.title,
      when: step.when,
      detail: step.detail,
    })),
  };
}

/** Swap two elements in an immutable array copy. */
export function swapListItems<T>(list: T[], indexA: number, indexB: number): T[] {
  if (indexA < 0 || indexB < 0 || indexA >= list.length || indexB >= list.length) {
    return list;
  }
  const next = [...list];
  const tmp = next[indexA];
  next[indexA] = next[indexB];
  next[indexB] = tmp;
  return next;
}

/** Derive p2..p5 sections strictly from agreed items for final quotations. */
export function deriveFinalSections(
  selectedIds: string[],
  allItems: Record<string, { g: string }>,
  customCategories: PitchCustomCategory[] = [],
  hiddenSheets: PitchSectionKey[] = [],
): PitchSections {
  const sections: PitchSections = { p2: [], p3: [], p4: [], p5: [] };
  const hiddenSet = new Set(hiddenSheets);
  const seen = new Set<string>();
  for (const id of selectedIds) {
    if (seen.has(id) || !allItems[id]) continue;
    seen.add(id);
    const secKey = sectionForGroup(allItems[id].g, customCategories);
    if (secKey && !hiddenSet.has(secKey)) {
      sections[secKey].push(id);
    }
  }
  return sections;
}

/** Return an honest agent chip label when created by an agent, or null for user-created proposals. */
export function formatAgentChip(createdBy: string | null | undefined): string | null {
  const clean = (createdBy || '').trim().toLowerCase();
  if (!clean || clean === 'user') return null;
  if (clean === 'writer') return 'Drafted by Writer';
  if (clean === 'cmo') return 'Drafted by CMO';
  return `Drafted by ${createdBy}`;
}

/** Extract field-level validation errors from an ApiError thrown by /api/pitch/* endpoints. */
export function extractRuleViolations(err: unknown): PitchRuleViolation[] {
  if (!(err instanceof ApiError)) return [];
  const data = err.data as { errors?: unknown } | undefined;
  if (!data || !Array.isArray(data.errors)) return [];
  return data.errors.filter(
    (entry): entry is PitchRuleViolation =>
      Boolean(entry) &&
      typeof entry === 'object' &&
      typeof (entry as PitchRuleViolation).message === 'string',
  );
}

/** Generate a valid unique custom item ID starting with `custom_`. */
export function createCustomItemId(name: string, existingIds: Set<string>): string {
  const baseSlug = name
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40);
  const seed = `custom_${baseSlug || 'item'}`;
  if (!existingIds.has(seed)) return seed;
  let idx = 2;
  while (existingIds.has(`${seed}_${idx}`)) {
    idx += 1;
  }
  return `${seed}_${idx}`;
}

/** Generate a valid unique service plan ID matching ^plan_[a-z0-9][a-z0-9-]{1,57}$. */
export function createServicePlanId(name: string, existingIds: Set<string>): string {
  const rawSlug = name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  const body = rawSlug.length >= 2 ? rawSlug : `${rawSlug || 'service'}-01`;
  const seed = `plan_${body}`;
  if (!existingIds.has(seed)) return seed;
  let idx = 2;
  while (existingIds.has(`${seed}-${idx}`)) {
    idx += 1;
  }
  return `${seed}-${idx}`;
}

/** Generate a valid unique custom category ID matching ^cat_[a-z0-9][a-z0-9_-]{0,58}$. */
export function createCustomCategoryId(name: string, existingIds: Set<string>): string {
  const rawSlug = name
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 40);
  const body = rawSlug && /^[a-z0-9]/.test(rawSlug) ? rawSlug : `cat${rawSlug ? `_${rawSlug}` : ''}`;
  const seed = `cat_${body}`;
  if (!existingIds.has(seed)) return seed;
  let idx = 2;
  while (existingIds.has(`${seed}_${idx}`)) {
    idx += 1;
  }
  return `${seed}_${idx}`;
}

export interface PitchDraftState {
  slug: string;
  kind: PitchProposalKind;
  status: PitchProposal['status'];
  client_name: string;
  business_name: string;
  contact: {
    name: string;
    email: string;
    phone: string;
    notes: string;
  };
  preset: PitchPreset;
  price_overrides: Record<string, number>;
  custom_items: PitchCustomItem[];
  hidden_sheets: PitchSectionKey[];
  hidden_about: PitchAboutSectionKey[];
  custom_categories: PitchCustomCategory[];
  payment_plan: PitchPaymentPlanRow[];
  payment_note: string;
  timeline: PitchTimelineStep[];
  service_plans: PitchServicePlan[];
  notes: string;
}

/** Convert an API PitchProposal into editable PitchDraftState. */
export function proposalToDraft(proposal: PitchProposal): PitchDraftState {
  const rawPreset = proposal.preset || ({} as PitchPreset);
  const arch = rawPreset.architectureBlock || { heading: '', sub: '', roles: [] };
  return {
    slug: proposal.slug || '',
    kind: proposal.kind || 'proposal',
    status: proposal.status || 'draft',
    client_name: proposal.client_name || '',
    business_name: proposal.business_name || '',
    contact: {
      name: proposal.contact?.name || '',
      email: proposal.contact?.email || '',
      phone: proposal.contact?.phone || '',
      notes: proposal.contact?.notes || '',
    },
    preset: {
      slug: proposal.slug || rawPreset.slug || '',
      name: rawPreset.name || '',
      badge: rawPreset.badge || '',
      clientName: rawPreset.clientName || '',
      businessName: rawPreset.businessName || '',
      title: rawPreset.title || '',
      lead: rawPreset.lead || '',
      whatWeDo: rawPreset.whatWeDo || '',
      portfolio: rawPreset.portfolio || '',
      thisPlan: rawPreset.thisPlan || '',
      architectureBlock: {
        heading: arch.heading || '',
        sub: arch.sub || '',
        roles: (Array.isArray(arch.roles) ? arch.roles : []).map((role) => ({
          label: role?.label || '',
          points: Array.isArray(role?.points) ? [...role.points] : [],
        })),
      },
      initialSelected: Array.isArray(rawPreset.initialSelected) ? [...rawPreset.initialSelected] : [],
      sections: {
        p2: Array.isArray(rawPreset.sections?.p2) ? [...rawPreset.sections.p2] : [],
        p3: Array.isArray(rawPreset.sections?.p3) ? [...rawPreset.sections.p3] : [],
        p4: Array.isArray(rawPreset.sections?.p4) ? [...rawPreset.sections.p4] : [],
        p5: Array.isArray(rawPreset.sections?.p5) ? [...rawPreset.sections.p5] : [],
      },
    },
    price_overrides: { ...(proposal.price_overrides || {}) },
    custom_items: (proposal.custom_items || []).map((c) => ({
      ...c,
      r: Array.isArray(c.r) ? [...c.r] : [],
    })),
    hidden_sheets: Array.isArray(proposal.hidden_sheets) ? [...proposal.hidden_sheets] : [],
    hidden_about: Array.isArray(proposal.hidden_about) ? [...proposal.hidden_about] : [],
    custom_categories: (proposal.custom_categories || []).map((cat) => ({
      id: cat.id || '',
      name: cat.name || '',
      placement: (cat.placement as PitchCategoryPlacement) || 'p2',
      sheet_title: cat.sheet_title || '',
      sheet_lead: cat.sheet_lead || '',
    })),
    payment_plan: (proposal.payment_plan || []).map((row) => ({
      percent: Number(row.percent) || 0,
      label: row.label || '',
    })),
    payment_note: proposal.payment_note || '',
    timeline: (proposal.timeline || []).map((step) => ({
      title: step.title || '',
      when: step.when || '',
      detail: step.detail || '',
    })),
    service_plans: (proposal.service_plans || []).map((sp) => ({
      id: sp.id || '',
      name: sp.name || '',
      description: sp.description || '',
      price: Number(sp.price) || 0,
      period: sp.period || 'monthly',
    })),
    notes: proposal.notes || '',
  };
}

/** Build effective price map for all catalog items + proposal price overrides. */
export function buildEffectivePriceMap(
  draft: Pick<PitchDraftState, 'price_overrides'>,
  catalog: PitchCatalog | null,
): Record<string, number> {
  const prices: Record<string, number> = {};
  if (catalog?.items) {
    for (const [id, item] of Object.entries(catalog.items)) {
      prices[id] = item.price;
    }
  }
  for (const [id, override] of Object.entries(draft.price_overrides)) {
    if (Number.isInteger(override) && override >= 0) {
      prices[id] = override;
    }
  }
  return prices;
}

/** Format dropped items notice returned by engine `changes` after saving. */
export function formatDroppedItemsNotice(changes?: PitchDroppedItemChange[] | null): string | null {
  if (!Array.isArray(changes) || changes.length === 0) return null;
  const messages = changes
    .map((c) => c.message || (c.name ? `Dropped '${c.name}' (${c.item_id})` : ''))
    .filter(Boolean);
  if (messages.length === 0) return null;
  return messages.join(' · ');
}

/** Build live preview postMessage payload for the embedded Pitch iframe. */
export function buildLivePreviewPayload(
  draft: PitchDraftState,
  catalog: PitchCatalog | null,
): {
  kind: PitchProposalKind;
  locked: boolean;
  preset: PitchPreset;
  custom_items: PitchCustomItem[];
  prices: Record<string, number>;
  contact: PitchContact & { business?: string };
  hidden_sheets: PitchSectionKey[];
  hidden_about: PitchAboutSectionKey[];
  custom_categories: PitchCustomCategory[];
  included_from_hidden: PitchIncludedFromHidden[];
  payment_plan?: PitchPaymentPlanRow[];
  payment_note?: string;
  timeline?: PitchTimelineStep[];
  service_plans?: PitchServicePlan[];
} {
  const prices = buildEffectivePriceMap(draft, catalog);
  const isFinal = draft.kind === 'final';
  const allItems = buildAllItemsMap(catalog, draft.custom_items);
  const hiddenSet = new Set(draft.hidden_sheets || []);
  const visibleInitialSeeds = (draft.preset.initialSelected || []).filter(
    (id) => !hiddenSet.has(itemSheetKey(id, allItems, draft.custom_categories) as PitchSectionKey),
  );
  const { selected: expandedSelected } = expandSelectionWithDeps(
    visibleInitialSeeds,
    allItems,
  );
  const sections = isFinal
    ? deriveFinalSections(
        expandedSelected,
        allItems,
        draft.custom_categories,
        draft.hidden_sheets,
      )
    : {
        p2: hiddenSet.has('p2') ? [] : draft.preset.sections.p2 || [],
        p3: hiddenSet.has('p3') ? [] : draft.preset.sections.p3 || [],
        p4: hiddenSet.has('p4') ? [] : draft.preset.sections.p4 || [],
        p5: hiddenSet.has('p5') ? [] : draft.preset.sections.p5 || [],
      };

  const agreedSet = new Set(expandedSelected);
  const customCategoriesWithItems = (draft.custom_categories || []).map((cat) => ({
    ...cat,
    item_ids: (draft.custom_items || [])
      .filter((ci) => ci.g === cat.name && (!isFinal || agreedSet.has(ci.id)))
      .map((ci) => ci.id),
  }));

  let effectiveHiddenSheets = [...(draft.hidden_sheets || [])];
  if (isFinal) {
    const sheetsWithAgreed = new Set(
      expandedSelected
        .map((id) => itemSheetKey(id, allItems, draft.custom_categories))
        .filter((sk) => !hiddenSet.has(sk as PitchSectionKey)),
    );
    for (const s of PITCH_SHEETS) {
      if (!effectiveHiddenSheets.includes(s.key) && !sheetsWithAgreed.has(s.key)) {
        effectiveHiddenSheets.push(s.key);
      }
    }
  }

  const includedFromHidden = computeLocalIncludedFromHidden(draft, allItems);

  return {
    kind: draft.kind,
    locked: isFinal,
    preset: {
      ...draft.preset,
      slug: draft.slug,
      clientName: draft.preset.clientName || draft.client_name,
      businessName: draft.preset.businessName || draft.business_name,
      name: draft.preset.name || draft.business_name || draft.client_name || draft.slug,
      initialSelected: isFinal ? expandedSelected : visibleInitialSeeds,
      sections,
    },
    custom_items: draft.custom_items,
    prices,
    contact: {
      name: draft.client_name || draft.contact.name,
      business: draft.business_name,
      phone: draft.contact.phone,
      email: draft.contact.email,
      notes: draft.contact.notes,
    },
    hidden_sheets: effectiveHiddenSheets,
    hidden_about: draft.hidden_about || [],
    custom_categories: customCategoriesWithItems,
    included_from_hidden: includedFromHidden,
    ...(isFinal
      ? {
          payment_plan: draft.payment_plan,
          payment_note: draft.payment_note,
          timeline: draft.timeline,
          service_plans: draft.service_plans,
        }
      : {}),
  };
}

/** Resolve effective price and clear source label for a catalog item. */
export function resolveCatalogItemPricing(
  itemId: string,
  catalogItem: PitchCatalogItem,
  priceOverrides: Record<string, number>,
): {
  pitchBase: number;
  adminBase: number;
  adminOverridden: boolean;
  proposalOverride: number | undefined;
  effective: number;
  sourceLabel: 'Pitch' | 'Admin' | 'This proposal';
} {
  const pitchBase = catalogItem.base_price;
  const adminBase = catalogItem.price;
  const adminOverridden = Boolean(catalogItem.overridden);
  const hasProposal = Object.prototype.hasOwnProperty.call(priceOverrides, itemId);
  const proposalOverride = hasProposal ? priceOverrides[itemId] : undefined;

  if (hasProposal && typeof proposalOverride === 'number') {
    return {
      pitchBase,
      adminBase,
      adminOverridden,
      proposalOverride,
      effective: proposalOverride,
      sourceLabel: 'This proposal',
    };
  }
  if (adminOverridden) {
    return {
      pitchBase,
      adminBase,
      adminOverridden,
      proposalOverride: undefined,
      effective: adminBase,
      sourceLabel: 'Admin',
    };
  }
  return {
    pitchBase,
    adminBase,
    adminOverridden: false,
    proposalOverride: undefined,
    effective: pitchBase,
    sourceLabel: 'Pitch',
  };
}
