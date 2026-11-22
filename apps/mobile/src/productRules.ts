import type { ProductRuleEntry, ProductRulesConfig } from './types';

const ALLOWED_STATUSES = new Set(['released', 'in_development', 'secret']);
const ALLOWED_SHOW_LEVELS = new Set(['everything', 'text_only', 'teasers_only', 'nothing']);

function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildWordPattern(term: string): RegExp | null {
  const parts = term.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return null;
  const body = parts.map(escapeRegex).join('\\s+');
  return new RegExp(`\\b${body}\\b`, 'i');
}

/** Validate a product_rules payload from shared_knowledge. */
export function validateProductRules(data: unknown): ProductRulesConfig {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('Invalid product_rules payload.');
  }
  const obj = data as Partial<ProductRulesConfig>;
  if (!obj.show_levels || typeof obj.show_levels !== 'object') {
    throw new Error('product_rules.show_levels is required.');
  }
  for (const level of Object.keys(obj.show_levels)) {
    if (!ALLOWED_SHOW_LEVELS.has(level)) {
      throw new Error(`Invalid show_levels key: ${level}`);
    }
  }
  if (!Array.isArray(obj.products)) {
    throw new Error('product_rules.products must be an array.');
  }
  for (const prod of obj.products) {
    if (!prod || typeof prod !== 'object') {
      throw new Error('Each product rule must be an object.');
    }
    if (!ALLOWED_STATUSES.has(prod.status)) {
      throw new Error(`Invalid product status: ${String(prod.status)}`);
    }
    if (!ALLOWED_SHOW_LEVELS.has(prod.show)) {
      throw new Error(`Invalid product show level: ${String(prod.show)}`);
    }
  }
  return obj as ProductRulesConfig;
}

/** The laptop publishes shared_knowledge product_rules as {rules: {...}, version}. */
export function unwrapSharedProductRules(data: unknown): unknown {
  if (data && typeof data === 'object' && 'rules' in data) {
    return (data as { rules: unknown }).rules;
  }
  return data;
}

function isConfidentialProduct(prod: ProductRuleEntry): boolean {
  return Boolean(prod.confidential) || prod.status === 'secret' || prod.show === 'nothing';
}

/** Return confidential products matched by name, id, or blocked terms in text. */
export function findConfidentialMention(
  text: string,
  rules: ProductRulesConfig,
): ProductRuleEntry | null {
  const raw = String(text || '').trim();
  if (!raw) return null;

  for (const prod of rules.products) {
    if (!isConfidentialProduct(prod)) continue;

    const candidates = [prod.name, prod.id, ...(prod.blocked_terms ?? [])];
    for (const term of candidates) {
      if (!term || !term.trim()) continue;
      const pattern = buildWordPattern(term);
      if (pattern && pattern.test(raw)) {
        return prod;
      }
    }
  }
  return null;
}

/** Format a compact product rules block for Phone Lite system prompts. */
export function formatProductRulesPrompt(rules: ProductRulesConfig): string {
  const lines = ['Product rules (from shared_knowledge):'];
  for (const prod of rules.products) {
    // This prompt goes to a cloud model, so confidential products aren't named in it.
    if (isConfidentialProduct(prod)) continue;
    const name = prod.name || prod.id;
    const meaning = rules.show_levels[prod.show] ?? '';
    let line = `- ${name} (status: ${prod.status}, show: ${prod.show}): ${meaning}`.trim();
    if (prod.notes && prod.notes.trim()) {
      line += ` Notes: ${prod.notes.trim()}`;
    }
    lines.push(line);
  }
  lines.push('- Any other Magnm product is confidential: never name it, describe it, or guess about it.');
  return lines.join('\n');
}
