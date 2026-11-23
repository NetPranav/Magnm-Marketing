// The rules for what a phone may send about a Pitch proposal. The cloud (phone_pitch_fields_ok) and the laptop
// (engine/phone_pitch.py, then the Pitch rules themselves) check the same things again: this copy only makes the
// phone say no early, with a clear reason, instead of queueing something that would be refused.

import type { PitchContactFields, PitchFields, PitchPresetFields } from './types';

export const NEW_PROPOSAL_TARGET = 'new';
export const SHEET_KEYS = ['p2', 'p3', 'p4', 'p5'] as const;
export const ABOUT_KEYS = ['lead', 'what_we_do', 'portfolio', 'this_plan', 'architecture', 'timeline', 'payment'] as const;
export const MAX_PRICE = 10_000_000;
export const MAX_NAME_CHARS = 200;
export const MAX_NOTES_CHARS = 4000;
export const MAX_LONG_TEXT_CHARS = 1000;
export const MAX_VERSION_CHARS = 64;
export const MAX_PRICE_OVERRIDES = 100;

// eslint-disable-next-line no-control-regex
const BAD_CHARS_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;
const PRESET_SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const CONTACT_KEYS = ['name', 'business', 'email', 'phone', 'notes'] as const;
const PRESET_TEXT_LIMITS: Record<string, number> = {
  title: MAX_NAME_CHARS,
  lead: MAX_LONG_TEXT_CHARS,
  whatWeDo: MAX_LONG_TEXT_CHARS,
  portfolio: MAX_NAME_CHARS,
  thisPlan: MAX_LONG_TEXT_CHARS,
};
const PRESET_KEYS = [...Object.keys(PRESET_TEXT_LIMITS), 'initialSelected', 'sections'];

export class PitchRuleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PitchRuleError';
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/** One text: a string without control characters or angle brackets, trimmed and within its length. */
export function cleanPitchText(value: unknown, what: string, limit: number): string {
  if (typeof value !== 'string' || BAD_CHARS_RE.test(value)) {
    throw new PitchRuleError(`The ${what} is not valid text.`);
  }
  if (value.includes('<') || value.includes('>')) {
    throw new PitchRuleError('Text cannot contain < or >.');
  }
  const text = value.trim();
  if (text.length > limit) {
    throw new PitchRuleError(`The ${what} can have at most ${limit} characters.`);
  }
  return text;
}

function cleanList(value: unknown, what: string, allowed: readonly string[] | null, limit: number): string[] {
  if (!Array.isArray(value) || value.length > limit) {
    throw new PitchRuleError(`The ${what} is not valid.`);
  }
  const out: string[] = [];
  for (const item of value) {
    if (
      typeof item !== 'string' ||
      item.length < 1 ||
      item.length > 80 ||
      BAD_CHARS_RE.test(item) ||
      item.includes('<') ||
      item.includes('>') ||
      (allowed !== null && !allowed.includes(item))
    ) {
      throw new PitchRuleError(`The ${what} is not valid.`);
    }
    out.push(item);
  }
  return out;
}

/** A price a person typed: a whole number from 0 to 10,000,000. */
export function isValidPrice(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= MAX_PRICE;
}

/**
 * Check the fields of a proposal the way the cloud and the laptop will and return the cleaned fields.
 * allowCreate adds the two fields only a new proposal has: its kind and the built-in preset it starts from.
 */
export function cleanPitchFields(
  raw: unknown,
  allowCreate: boolean,
): PitchFields & { kind?: 'proposal' | 'quote_form'; from_preset?: string } {
  if (!isPlainObject(raw)) throw new PitchRuleError('The proposal details are not valid.');
  const allowed = [
    'client_name',
    'business_name',
    'contact',
    'notes',
    'preset',
    'hidden_sheets',
    'hidden_about',
    'price_overrides',
    ...(allowCreate ? ['kind', 'from_preset'] : []),
  ];
  if (Object.keys(raw).some((k) => !allowed.includes(k))) {
    throw new PitchRuleError('That change is not something a phone can make.');
  }
  const out: PitchFields & { kind?: 'proposal' | 'quote_form'; from_preset?: string } = {};
  if (raw.client_name !== undefined) out.client_name = cleanPitchText(raw.client_name, 'client name', MAX_NAME_CHARS);
  if (raw.business_name !== undefined) {
    out.business_name = cleanPitchText(raw.business_name, 'business name', MAX_NAME_CHARS);
  }
  if (raw.notes !== undefined) out.notes = cleanPitchText(raw.notes, 'notes', MAX_NOTES_CHARS);
  if (raw.contact !== undefined) {
    if (!isPlainObject(raw.contact) || Object.keys(raw.contact).some((k) => !(CONTACT_KEYS as readonly string[]).includes(k))) {
      throw new PitchRuleError('The contact details are not valid.');
    }
    const contact: PitchContactFields = {};
    for (const key of CONTACT_KEYS) {
      if (raw.contact[key] !== undefined) {
        contact[key] = cleanPitchText(raw.contact[key], `contact ${key}`, key === 'notes' ? MAX_LONG_TEXT_CHARS : MAX_NAME_CHARS);
      }
    }
    out.contact = contact;
  }
  if (raw.preset !== undefined) {
    if (!isPlainObject(raw.preset) || Object.keys(raw.preset).some((k) => !PRESET_KEYS.includes(k))) {
      throw new PitchRuleError('The plan texts are not valid.');
    }
    const preset: PitchPresetFields = {};
    for (const [key, limit] of Object.entries(PRESET_TEXT_LIMITS)) {
      if (raw.preset[key] !== undefined) {
        (preset as Record<string, string>)[key] = cleanPitchText(raw.preset[key], 'plan text', limit);
      }
    }
    if (raw.preset.initialSelected !== undefined) {
      preset.initialSelected = cleanList(raw.preset.initialSelected, 'selected services', null, 200);
    }
    if (raw.preset.sections !== undefined) {
      const sections = raw.preset.sections;
      if (!isPlainObject(sections) || Object.keys(sections).some((k) => !(SHEET_KEYS as readonly string[]).includes(k))) {
        throw new PitchRuleError('The sheet services are not valid.');
      }
      preset.sections = {};
      for (const sheet of SHEET_KEYS) {
        if (sections[sheet] !== undefined) preset.sections[sheet] = cleanList(sections[sheet], 'sheet services', null, 200);
      }
    }
    out.preset = preset;
  }
  if (raw.hidden_sheets !== undefined) out.hidden_sheets = cleanList(raw.hidden_sheets, 'hidden sheets', SHEET_KEYS, 8);
  if (raw.hidden_about !== undefined) out.hidden_about = cleanList(raw.hidden_about, 'hidden sections', ABOUT_KEYS, 16);
  if (raw.price_overrides !== undefined) {
    const prices = raw.price_overrides;
    if (!isPlainObject(prices) || Object.keys(prices).length > MAX_PRICE_OVERRIDES) {
      throw new PitchRuleError('The prices are not valid.');
    }
    const clean: Record<string, number> = {};
    for (const [id, price] of Object.entries(prices)) {
      if (!id || id.length > 64 || BAD_CHARS_RE.test(id) || !isValidPrice(price)) {
        throw new PitchRuleError('A price must be a whole number from 0 to 10,000,000.');
      }
      clean[id] = price;
    }
    out.price_overrides = clean;
  }
  if (allowCreate) {
    if (raw.kind !== undefined) {
      if (raw.kind !== 'proposal' && raw.kind !== 'quote_form') {
        throw new PitchRuleError('A phone can only make a proposal or a quote form.');
      }
      out.kind = raw.kind;
    }
    if (raw.from_preset !== undefined) {
      if (typeof raw.from_preset !== 'string' || !PRESET_SLUG_RE.test(raw.from_preset)) {
        throw new PitchRuleError('The preset is not valid.');
      }
      out.from_preset = raw.from_preset;
    }
  }
  return out;
}

/** Every string (keys included) in a nested value, for the confidential-product check. */
export function collectStrings(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => collectStrings(v, out));
  else if (isPlainObject(value)) {
    for (const [k, v] of Object.entries(value)) {
      out.push(k);
      collectStrings(v, out);
    }
  }
  return out;
}
