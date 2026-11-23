// Pitch on the phone without the laptop: what the phone shows from the cloud mirror (kind pitch_proposal) and the
// phone actions it queues to change a proposal. Nothing here reaches the network: the mirror is read by cache.ts /
// api.ts, and the actions are sent by cloudActions.ts like every other phone action.
//
// A confidential proposal is only a stub and stays one. A record that is not well formed is skipped, never guessed at.

import type { ActionInput } from './actions';
import { ABOUT_KEYS, SHEET_KEYS, isValidPrice } from './pitchRules';
import type { ContextCacheState } from './cache';
import type {
  PhoneAction,
  PitchCatalog,
  PitchCloudInfo,
  PitchContactFields,
  PitchFields,
  PitchProposalItem,
} from './types';

export const MIRROR_PITCH_KIND = 'pitch_proposal';
type SheetKey = (typeof SHEET_KEYS)[number];

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

function isObj(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cleanCatalog(raw: unknown): PitchCatalog | null {
  if (!isObj(raw) || !isObj(raw.items)) return null;
  const items: PitchCatalog['items'] = {};
  for (const [id, item] of Object.entries(raw.items)) {
    if (!isObj(item) || typeof item.n !== 'string' || typeof item.p !== 'number') continue;
    items[id] = { g: str(item.g) ?? '', n: item.n, p: item.p };
  }
  const presets = Array.isArray(raw.presets)
    ? raw.presets
        .filter((p): p is { slug: string; name: string } => isObj(p) && typeof p.slug === 'string' && typeof p.name === 'string')
        .map((p) => ({ slug: p.slug, name: p.name }))
    : [];
  return { items, groups: strList(raw.groups), presets };
}

function cleanContact(raw: unknown): PitchContactFields {
  const out: PitchContactFields = {};
  if (!isObj(raw)) return out;
  for (const key of ['name', 'business', 'email', 'phone', 'notes'] as const) {
    if (typeof raw[key] === 'string') out[key] = raw[key] as string;
  }
  return out;
}

function cleanSections(raw: unknown): Partial<Record<SheetKey, string[]>> {
  const out: Partial<Record<SheetKey, string[]>> = {};
  if (!isObj(raw)) return out;
  for (const sheet of SHEET_KEYS) out[sheet] = strList(raw[sheet]);
  return out;
}

function cleanCloudInfo(rec: { data: Record<string, unknown>; local_updated_at: string }): PitchCloudInfo | null {
  const d = rec.data;
  const version = str(d.version) ?? rec.local_updated_at;
  if (!version || version.length > 64) return null;
  const preset = isObj(d.preset) ? d.preset : {};
  const prices: Record<string, number> = {};
  if (isObj(d.price_overrides)) {
    for (const [id, price] of Object.entries(d.price_overrides)) if (isValidPrice(price)) prices[id] = price;
  }
  const linkStatus = d.link_status === 'live' || d.link_status === 'withdrawn' ? d.link_status : null;
  const linkUrl = str(d.link_url);
  return {
    version,
    // A cut-down copy (the cloud's size cap) or the default quotation is shown, never edited, on the phone.
    editable: d.editable !== false && d.truncated !== true,
    status: str(d.status),
    client_name: str(d.client_name) ?? '',
    business_name: str(d.business_name) ?? '',
    contact: cleanContact(d.contact),
    notes: str(d.notes) ?? '',
    preset: {
      title: str(preset.title) ?? '',
      lead: str(preset.lead) ?? '',
      whatWeDo: str(preset.whatWeDo) ?? '',
      portfolio: str(preset.portfolio) ?? '',
      thisPlan: str(preset.thisPlan) ?? '',
      initialSelected: strList(preset.initialSelected),
      sections: cleanSections(preset.sections),
    },
    hidden_sheets: strList(d.hidden_sheets).filter((s) => (SHEET_KEYS as readonly string[]).includes(s)),
    hidden_about: strList(d.hidden_about).filter((s) => (ABOUT_KEYS as readonly string[]).includes(s)),
    price_overrides: prices,
    share_id: str(d.share_id),
    link_status: linkStatus,
    // Only a live link has an address, and only an https one is ever offered for copying.
    link_url: linkStatus === 'live' && linkUrl && /^https:\/\/[^\s]+$/.test(linkUrl) ? linkUrl : null,
    truncated: d.truncated === true,
    catalog: cleanCatalog(d.catalog),
  };
}

/** Proposals from mirror records only. A confidential record is a stub with no text. */
export function proposalsFromMirror(cache: ContextCacheState): PitchProposalItem[] {
  const items: PitchProposalItem[] = [];
  for (const rec of cache.mirrorRecords) {
    if (rec.kind !== MIRROR_PITCH_KIND || !rec.local_id) continue;
    if (rec.data?.confidential) {
      items.push({ id: rec.local_id, confidential: true });
      continue;
    }
    const cloud = cleanCloudInfo(rec);
    if (!cloud) continue;
    const kind = str(rec.data.kind) ?? 'proposal';
    items.push({
      id: rec.local_id,
      name: str(rec.data.name) ?? rec.title ?? rec.local_id,
      kind,
      updated_at: cloud.version,
      payload: isObj(rec.data.payload) ? rec.data.payload : undefined,
      cloud,
    });
  }
  // The default quotation first, then the newest.
  return items.sort((a, b) => {
    if ((a.kind === 'default') !== (b.kind === 'default')) return a.kind === 'default' ? -1 : 1;
    return String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? ''));
  });
}

/**
 * The proposals to show: the live copies from the laptop, the cloud copies, or both. A proposal in both keeps the
 * newer payload and always carries the cloud information the editor needs. Confidential in either is a stub.
 */
export function mergePitchLists(laptop: PitchProposalItem[], mirror: PitchProposalItem[]): PitchProposalItem[] {
  const byId = new Map<string, PitchProposalItem>();
  for (const item of mirror) byId.set(item.id, item);
  const out: PitchProposalItem[] = [];
  const seen = new Set<string>();
  for (const live of laptop) {
    seen.add(live.id);
    const cloud = byId.get(live.id);
    if (live.confidential || cloud?.confidential) {
      out.push({ id: live.id, confidential: true });
    } else if (!cloud) {
      out.push(live);
    } else {
      const livePayload = live.payload && String(live.updated_at ?? '') >= String(cloud.updated_at ?? '');
      out.push({ ...cloud, ...(livePayload ? { payload: live.payload, updated_at: live.updated_at, name: live.name ?? cloud.name } : {}) });
    }
  }
  for (const item of mirror) if (!seen.has(item.id)) out.push(item);
  return out;
}

export function pitchProposalsFromCache(cache: ContextCacheState): PitchProposalItem[] {
  return mergePitchLists(cache.laptop?.pitchProposals ?? [], proposalsFromMirror(cache));
}

/** The catalog (item names, groups, base prices, built-in presets) from any proposal that carries it. */
export function catalogFromProposals(items: PitchProposalItem[]): PitchCatalog | null {
  for (const item of items) if (item.cloud?.catalog) return item.cloud.catalog;
  return null;
}

/** The sheet an item is shown on, by its group (the same grouping Pitch uses). */
export function sheetOfGroup(group: string): SheetKey {
  if (group === 'Web & SaaS') return 'p2';
  if (group === 'Mobile app') return 'p3';
  if (group === 'Backend') return 'p4';
  return 'p5';
}

// --- Editing ---

export interface PitchEditValues {
  client_name: string;
  business_name: string;
  contact: PitchContactFields;
  notes: string;
  title: string;
  lead: string;
  whatWeDo: string;
  portfolio: string;
  thisPlan: string;
  initialSelected: string[];
  sections: Partial<Record<SheetKey, string[]>>;
  hidden_sheets: string[];
  hidden_about: string[];
  price_overrides: Record<string, number>;
}

export function editValuesOf(cloud: PitchCloudInfo): PitchEditValues {
  return {
    client_name: cloud.client_name,
    business_name: cloud.business_name,
    contact: { ...cloud.contact },
    notes: cloud.notes,
    title: cloud.preset.title,
    lead: cloud.preset.lead,
    whatWeDo: cloud.preset.whatWeDo,
    portfolio: cloud.preset.portfolio,
    thisPlan: cloud.preset.thisPlan,
    initialSelected: [...cloud.preset.initialSelected],
    sections: Object.fromEntries(SHEET_KEYS.map((s) => [s, [...(cloud.preset.sections[s] ?? [])]])),
    hidden_sheets: [...cloud.hidden_sheets],
    hidden_about: [...cloud.hidden_about],
    price_overrides: { ...cloud.price_overrides },
  };
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function samePrices(a: Record<string, number>, b: Record<string, number>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
}

/** Only what the person changed, in the shape the action sends. An empty result means nothing changed. */
export function changedFields(cloud: PitchCloudInfo, values: PitchEditValues): PitchFields {
  const base = editValuesOf(cloud);
  const out: PitchFields = {};
  if (values.client_name.trim() !== base.client_name.trim()) out.client_name = values.client_name;
  if (values.business_name.trim() !== base.business_name.trim()) out.business_name = values.business_name;
  if (values.notes.trim() !== base.notes.trim()) out.notes = values.notes;
  const contact: PitchContactFields = {};
  for (const key of ['name', 'business', 'email', 'phone', 'notes'] as const) {
    if ((values.contact[key] ?? '').trim() !== (base.contact[key] ?? '').trim()) contact[key] = values.contact[key] ?? '';
  }
  if (Object.keys(contact).length > 0) out.contact = contact;
  const preset: NonNullable<PitchFields['preset']> = {};
  for (const key of ['title', 'lead', 'whatWeDo', 'portfolio', 'thisPlan'] as const) {
    if (values[key].trim() !== base[key].trim()) preset[key] = values[key];
  }
  if (!sameList(values.initialSelected, base.initialSelected)) preset.initialSelected = values.initialSelected;
  if (SHEET_KEYS.some((s) => !sameList(values.sections[s] ?? [], base.sections[s] ?? []))) {
    preset.sections = Object.fromEntries(SHEET_KEYS.map((s) => [s, values.sections[s] ?? []]));
  }
  if (Object.keys(preset).length > 0) out.preset = preset;
  if (!sameList(values.hidden_sheets, base.hidden_sheets)) out.hidden_sheets = values.hidden_sheets;
  if (!sameList(values.hidden_about, base.hidden_about)) out.hidden_about = values.hidden_about;
  if (!samePrices(values.price_overrides, base.price_overrides)) out.price_overrides = values.price_overrides;
  return out;
}

function label(item: PitchProposalItem): string {
  return (item.name || 'Proposal').slice(0, 80);
}

/** The action that saves the person's edit, based on the version the phone saw. */
export function buildUpdateInput(item: PitchProposalItem, fields: PitchFields): ActionInput {
  if (!item.cloud) throw new Error('This proposal has not reached the cloud yet.');
  return {
    kind: 'pitch_update',
    target_id: item.id,
    payload: { base_version: item.cloud.version, fields },
    label: label(item),
    targetConfidential: item.confidential,
  };
}

export function buildCreateInput(fields: PitchFields & { kind?: 'proposal' | 'quote_form'; from_preset?: string }): ActionInput {
  const name = [fields.business_name, fields.client_name].filter(Boolean).join(' · ') || 'New proposal';
  return { kind: 'pitch_create', target_id: 'new', payload: fields, label: name.slice(0, 80) };
}

export function buildFinalizeInput(item: PitchProposalItem): ActionInput {
  return { kind: 'pitch_finalize', target_id: item.id, payload: {}, label: label(item), targetConfidential: item.confidential };
}

export function buildLinkInput(item: PitchProposalItem, action: 'publish' | 'withdraw'): ActionInput {
  return { kind: 'pitch_link', target_id: item.id, payload: { action }, label: label(item), targetConfidential: item.confidential };
}

/** What the phone shows for a proposal's client link, from the cloud copy and the actions still waiting. */
export function linkStatusOf(
  item: PitchProposalItem,
  actions: PhoneAction[],
): { state: 'live' | 'withdrawn' | 'none'; waiting: 'publish' | 'withdraw' | null; url: string | null } {
  const waiting = actions
    .filter((a) => a.kind === 'pitch_link' && a.target_id === item.id && a.state === 'waiting')
    .map((a) => a.payload.action)
    .filter((a): a is 'publish' | 'withdraw' => a === 'publish' || a === 'withdraw');
  const state = item.cloud?.link_status === 'live' ? 'live' : item.cloud?.link_status === 'withdrawn' ? 'withdrawn' : 'none';
  return { state, waiting: waiting.length > 0 ? waiting[waiting.length - 1] : null, url: state === 'live' ? item.cloud?.link_url ?? null : null };
}

/** True while an edit of this proposal is still waiting for the laptop (a second one would have to wait). */
export function hasWaitingEdit(item: PitchProposalItem, actions: PhoneAction[]): boolean {
  return actions.some((a) => a.kind === 'pitch_update' && a.target_id === item.id && a.state === 'waiting');
}

// --- Quote requests ---

/** Turn the cloud's quote_submissions rows into what the phone lists. Rows of another owner are never used. */
export function cleanQuoteRequests(rows: unknown, ownerUid: string): import('./types').QuoteRequestItem[] {
  if (!Array.isArray(rows)) return [];
  const out: import('./types').QuoteRequestItem[] = [];
  for (const raw of rows) {
    if (!isObj(raw) || raw.owner_id !== ownerUid || typeof raw.id !== 'string' || typeof raw.share_id !== 'string') continue;
    const contact = isObj(raw.contact) ? raw.contact : {};
    const choices = isObj(raw.choices) ? raw.choices : {};
    const selected = [...new Set([...strList(choices.selected), ...strList(choices.custom_selected)])].slice(0, 100);
    const total = choices.total_shown;
    out.push({
      id: raw.id,
      share_id: raw.share_id,
      created_at: str(raw.created_at) ?? '',
      seen_at: str(raw.seen_at),
      name: (str(contact.name) ?? '').slice(0, 200),
      business: (str(contact.business) ?? '').slice(0, 200),
      phone: (str(contact.phone) ?? '').slice(0, 200),
      notes: (str(contact.notes) ?? '').slice(0, 1000),
      selected,
      total_shown: typeof total === 'number' && Number.isFinite(total) ? total : null,
    });
  }
  return out;
}

/** Names for the services a client chose: catalog names, then the proposal's own custom items, then the id. */
export function requestItemNames(
  req: import('./types').QuoteRequestItem,
  proposals: PitchProposalItem[],
): string[] {
  const catalog = catalogFromProposals(proposals);
  const owner = proposals.find((p) => p.cloud?.share_id === req.share_id);
  const custom = new Map<string, string>();
  const payloadCustom = owner?.payload?.custom_items;
  if (Array.isArray(payloadCustom)) {
    for (const c of payloadCustom) {
      if (isObj(c) && typeof c.id === 'string' && typeof c.n === 'string') custom.set(c.id, c.n);
    }
  }
  return req.selected.map((id) => catalog?.items[id]?.n ?? custom.get(id) ?? id);
}

/** The proposal a request belongs to (by its link), or null if this phone does not know it. */
export function proposalOfRequest(req: import('./types').QuoteRequestItem, proposals: PitchProposalItem[]): PitchProposalItem | null {
  return proposals.find((p) => !p.confidential && p.cloud?.share_id === req.share_id) ?? null;
}
