import baseline from '@magnm/config/product_rules.json';
import { describe, expect, it } from 'vitest';
import { findConfidentialMention } from './productRules';
import { queueBaselineRules, rulesForQueue } from './queueRules';
import type { ProductRulesConfig } from './types';

// The baseline is bundled from config/product_rules.json at build time. Names are read from it, never spelled here.
const confidentialName = (baseline.products as Array<{ name: string; confidential?: boolean }>).find((p) => p.confidential)!.name;
const publicName = (baseline.products as Array<{ name: string; confidential?: boolean }>).find((p) => !p.confidential)!.name;

const shared = (products: ProductRulesConfig['products']): ProductRulesConfig => ({
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products,
});

describe('product rules for the phone action queue', () => {
  it('has a bundled baseline that names the confidential products', () => {
    const rules = queueBaselineRules();
    expect(rules).not.toBeNull();
    expect(findConfidentialMention(`about ${confidentialName}`, rules!)).not.toBeNull();
    expect(findConfidentialMention(`about ${publicName}`, rules!)).toBeNull();
  });

  it('works with no shared or cached rules at all (the team never published any)', () => {
    const rules = rulesForQueue(null);
    expect(findConfidentialMention(`say ${confidentialName}`, rules!)).not.toBeNull();
    expect(findConfidentialMention('a harmless sentence', rules!)).toBeNull();
  });

  it('lets shared rules add confidential products', () => {
    const rules = rulesForQueue(
      shared([{ id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true }]),
    );
    expect(findConfidentialMention('ProjectNightfall plans', rules!)).not.toBeNull();
    expect(findConfidentialMention(`say ${confidentialName}`, rules!)).not.toBeNull();
  });

  it('never lets shared rules remove or downgrade a baseline confidential product', () => {
    const base = (baseline.products as Array<{ id: string; name: string }>).find((p) => p.name === confidentialName)!;
    const downgraded = rulesForQueue(
      shared([{ id: base.id, name: base.name, status: 'released', show: 'everything', confidential: false }]),
    );
    expect(findConfidentialMention(`say ${confidentialName}`, downgraded!)).not.toBeNull();
    const dropped = rulesForQueue(shared([]));
    expect(findConfidentialMention(`say ${confidentialName}`, dropped!)).not.toBeNull();
  });

  it('does not change the rules it is given', () => {
    const given = shared([{ id: 'x', name: 'Xyz', status: 'released', show: 'everything', confidential: false }]);
    const before = JSON.stringify(given);
    rulesForQueue(given);
    expect(JSON.stringify(given)).toBe(before);
  });
});
