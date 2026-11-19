import type { StatusState } from '@magnm/design/react';
import { ApiError } from '../../api';
import type {
  PitchCatalog,
  PitchCatalogItem,
  PitchCustomItem,
  PitchPreset,
  PitchProposal,
  PitchProposalKind,
  PitchRuleViolation,
  PitchSectionKey,
} from '../../api/pitch';

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;
export const RESERVED_STATIC_SLUGS = new Set(['general', 'admin', 'clients']);
export const MAX_ARCH_ROLES = 3;
export const MAX_ROLE_POINTS = 6;

export interface SheetGroupSpec {
  key: PitchSectionKey;
  label: string;
  groups: string[];
}

export const PITCH_SHEETS: SheetGroupSpec[] = [
  { key: 'p2', label: 'Web & SaaS', groups: ['Web & SaaS'] },
  { key: 'p3', label: 'Mobile', groups: ['Mobile app'] },
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

export function sectionForGroup(groupName: string): PitchSectionKey {
  return GROUP_TO_SECTION[groupName] || 'p5';
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
  return kind === 'quote_form' ? 'Quote form' : 'Proposal';
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
    notes: proposal.notes || '',
  };
}

/** Build live preview postMessage payload for the embedded Pitch iframe. */
export function buildLivePreviewPayload(
  draft: PitchDraftState,
  catalog: PitchCatalog | null,
): {
  preset: PitchPreset;
  custom_items: PitchCustomItem[];
  prices: Record<string, number>;
} {
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
  return {
    preset: {
      ...draft.preset,
      slug: draft.slug,
      clientName: draft.preset.clientName || draft.client_name,
      businessName: draft.preset.businessName || draft.business_name,
      name: draft.preset.name || draft.business_name || draft.client_name || draft.slug,
    },
    custom_items: draft.custom_items,
    prices,
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
