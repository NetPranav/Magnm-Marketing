// Product rules for the phone action queue only (Phone Lite keeps using the shared or cached rules as before).
//
// The team may never have published product_rules to shared_knowledge, so the queue needs a baseline that does not
// depend on the network: config/product_rules.json, bundled at build time (never copied by hand). The shared or
// cached rules can only ADD confidential products to it. They can never remove or downgrade one. The laptop's own
// check stays the final authority.

import baselineRules from '@magnm/config/product_rules.json';
import { validateProductRules } from './productRules';
import type { ProductRuleEntry, ProductRulesConfig } from './types';

function isConfidential(prod: ProductRuleEntry): boolean {
  return Boolean(prod.confidential) || prod.status === 'secret' || prod.show === 'nothing';
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** The bundled baseline, or null if the bundled file is not valid (then only shared rules can be used). */
export function queueBaselineRules(): ProductRulesConfig | null {
  try {
    return clone(validateProductRules(baselineRules));
  } catch {
    return null;
  }
}

function sameProduct(a: ProductRuleEntry, b: ProductRuleEntry): boolean {
  return (
    String(a.id ?? '').toLowerCase() === String(b.id ?? '').toLowerCase() ||
    String(a.name ?? '').trim().toLowerCase() === String(b.name ?? '').trim().toLowerCase()
  );
}

/** Shared or cached rules (may be null) plus the baseline: every baseline confidential product stays confidential. */
export function rulesForQueue(shared: ProductRulesConfig | null): ProductRulesConfig | null {
  const baseline = queueBaselineRules();
  if (!baseline) return shared ? clone(shared) : null;
  if (!shared) return baseline;
  const merged = clone(shared);
  for (const base of baseline.products.filter(isConfidential)) {
    const found = merged.products.find((p) => sameProduct(p, base));
    if (!found) {
      merged.products.push(base);
      continue;
    }
    found.confidential = true;
    found.blocked_terms = Array.from(new Set([...(found.blocked_terms ?? []), ...(base.blocked_terms ?? [])]));
  }
  return merged;
}
