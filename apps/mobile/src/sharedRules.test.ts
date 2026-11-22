import { expect, it } from 'vitest';
import bundled from '../../../config/product_rules.json';
import {
  findConfidentialMention,
  formatProductRulesPrompt,
  unwrapSharedProductRules,
  validateProductRules,
} from './productRules';

// The exact shape engine/team_knowledge.py local_snapshot('product_rules') publishes.
const published = { rules: bundled, version: 1 };

it('reads the product rules in the shape the laptop publishes', () => {
  const rules = validateProductRules(unwrapSharedProductRules(published));
  expect(rules.products.length).toBeGreaterThan(0);
});

it('never puts confidential products in the cloud prompt, but still detects them in messages', () => {
  const rules = validateProductRules(unwrapSharedProductRules(published));
  const secret = rules.products.filter((p) => p.confidential);
  expect(secret.length).toBeGreaterThan(0);
  const prompt = formatProductRulesPrompt(rules);
  for (const product of secret) {
    expect(prompt.toLowerCase()).not.toContain(product.name!.toLowerCase());
    expect(findConfidentialMention(`Write about ${product.name}`, rules)).not.toBeNull();
  }
});
