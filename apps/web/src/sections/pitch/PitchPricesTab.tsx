import { useMemo } from 'react';
import { Card, PillButton, TextInput } from '@magnm/design/react';
import {
  formatRupees,
  type PitchCatalog,
  type PitchPricingSummary,
  type PitchRuleViolation,
} from '../../api/pitch';
import {
  PITCH_SHEETS,
  formatServicePlanPeriodSuffix,
  itemSheetKey,
  resolveCatalogItemPricing,
  type PitchDraftState,
} from './pitchUtils';

export interface PitchPricesTabProps {
  draft: PitchDraftState;
  catalog: PitchCatalog | null;
  pricingSummary: PitchPricingSummary;
  dirty: boolean;
  violations: PitchRuleViolation[];
  onChange: (updater: (prev: PitchDraftState) => PitchDraftState) => void;
  onPriceChange?: (itemId: string, nextDraftUpdater: (prev: PitchDraftState) => PitchDraftState) => void;
  onScrollTo?: (target: string) => void;
}

/** Editor tab 3: Compact 4-column price table (Item, Base, Price, Reset) next to live preview. */
export function PitchPricesTab({
  draft,
  catalog,
  pricingSummary,
  dirty,
  violations,
  onChange,
  onPriceChange,
  onScrollTo,
}: PitchPricesTabProps) {
  const hiddenSet = useMemo(
    () => new Set(draft.hidden_sheets || []),
    [draft.hidden_sheets],
  );
  const selectedSet = useMemo(
    () => new Set(draft.preset.initialSelected || []),
    [draft.preset.initialSelected],
  );
  const shownSet = useMemo(() => {
    const out = new Set<string>();
    for (const sheet of PITCH_SHEETS) {
      if (hiddenSet.has(sheet.key)) continue;
      for (const id of draft.preset.sections[sheet.key] || []) {
        out.add(id);
      }
    }
    for (const c of draft.custom_items) {
      const cat = (draft.custom_categories || []).find((cc) => cc.name === c.g);
      if (cat) {
        if (
          cat.placement === 'new_sheet' ||
          !hiddenSet.has(cat.placement)
        ) {
          out.add(c.id);
        }
      }
    }
    return out;
  }, [draft.preset.sections, draft.custom_items, draft.custom_categories, hiddenSet]);

  const orderedIds = useMemo(() => {
    const seen = new Set<string>();
    const ids: string[] = [];
    const pushIfActive = (id: string) => {
      if (seen.has(id)) return;
      if (!selectedSet.has(id) && !shownSet.has(id) && !(id in draft.price_overrides)) return;
      seen.add(id);
      ids.push(id);
    };
    if (catalog?.items) {
      for (const id of Object.keys(catalog.items)) {
        const item = catalog.items[id];
        const homeSheet = itemSheetKey(id, { [id]: item }, draft.custom_categories);
        if (
          hiddenSet.has(homeSheet as 'p2' | 'p3' | 'p4' | 'p5') &&
          !selectedSet.has(id) &&
          !(id in draft.price_overrides)
        ) {
          continue;
        }
        pushIfActive(id);
      }
    }
    for (const c of draft.custom_items) {
      pushIfActive(c.id);
    }
    return ids;
  }, [
    catalog,
    draft.custom_items,
    draft.custom_categories,
    draft.price_overrides,
    selectedSet,
    shownSet,
    hiddenSet,
  ]);

  const customMap = useMemo(
    () => new Map(draft.custom_items.map((c) => [c.id, c])),
    [draft.custom_items],
  );

  const isFinal = draft.kind === 'final';
  const overrideCount = Object.keys(draft.price_overrides).length;
  const priceViolations = violations.filter((v) => v.field.startsWith('price_overrides'));
  const oneTimePlans = pricingSummary.one_time_plans || [];
  const recurringPlans = pricingSummary.recurring || [];
  const recurringTotals = pricingSummary.recurring_totals;

  const applyPriceUpdater = (
    itemId: string,
    updater: (prev: PitchDraftState) => PitchDraftState,
  ) => {
    if (onPriceChange) {
      onPriceChange(itemId, updater);
    } else {
      onChange(updater);
      onScrollTo?.(`item:${itemId}`);
    }
  };

  const handleOverrideChange = (itemId: string, rawVal: string) => {
    const trimmed = rawVal.trim();
    applyPriceUpdater(itemId, (prev) => {
      const nextOverrides = { ...prev.price_overrides };
      if (trimmed === '') {
        delete nextOverrides[itemId];
      } else {
        const parsed = Number(trimmed);
        if (!Number.isNaN(parsed) && Number.isFinite(parsed)) {
          nextOverrides[itemId] = Math.trunc(parsed);
        }
      }
      return {
        ...prev,
        price_overrides: nextOverrides,
      };
    });
  };

  const handleCustomPriceChange = (itemId: string, rawVal: string) => {
    const parsed = Math.max(0, Math.trunc(Number(rawVal) || 0));
    applyPriceUpdater(itemId, (prev) => ({
      ...prev,
      custom_items: prev.custom_items.map((ci) =>
        ci.id === itemId ? { ...ci, p: parsed } : ci,
      ),
    }));
  };

  const handleResetRow = (itemId: string) => {
    applyPriceUpdater(itemId, (prev) => {
      const nextOverrides = { ...prev.price_overrides };
      delete nextOverrides[itemId];
      return {
        ...prev,
        price_overrides: nextOverrides,
      };
    });
  };

  const handleResetAll = () => {
    onChange((prev) => ({
      ...prev,
      price_overrides: {},
    }));
  };

  return (
    <div className="studio-stack">
      <div className="mg-notice" role="note">
        Price changes on this proposal are shared with the admin when the team connection is ready (Phase 4).
      </div>

      {priceViolations.length > 0 ? (
        <div className="mg-notice" role="alert">
          {priceViolations.map((v) => v.message).join(' ')}
        </div>
      ) : null}

      <div className="studio-section-bar">
        <span className="studio-mono">
          {orderedIds.length} active item{orderedIds.length === 1 ? '' : 's'} · {overrideCount} proposal override{overrideCount === 1 ? '' : 's'}
        </span>
        <PillButton
          variant="outline"
          size="sm"
          disabled={overrideCount === 0}
          onClick={handleResetAll}
        >
          Reset all to base
        </PillButton>
      </div>

      {orderedIds.length === 0 ? (
        <div className="mg-empty">
          No deliverables are selected or shown yet. Enable items on the Deliverables tab first.
        </div>
      ) : (
        <div className="studio-table-wrap studio-pitch-price-table-wrap">
          <table className="studio-table studio-pitch-price-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Base</th>
                <th>Price</th>
                <th>Reset</th>
              </tr>
            </thead>
            <tbody>
              {orderedIds.map((itemId) => {
                const catItem = catalog?.items?.[itemId];
                const customItem = customMap.get(itemId);
                const isSelected = selectedSet.has(itemId);
                const rowViolation = violations.find((v) => v.field === `price_overrides.${itemId}`);

                if (catItem) {
                  const p = resolveCatalogItemPricing(itemId, catItem, draft.price_overrides);
                  return (
                    <tr
                      key={itemId}
                      data-scroll-target={`item:${itemId}`}
                    >
                      <td data-label="Item" className="studio-pitch-price-item">
                        <div className="studio-stack-xs">
                          <span className="studio-pitch-price-item-name">{catItem.n}</span>
                          <span className="studio-mono">
                            {catItem.g} · {isSelected ? (isFinal ? 'agreed' : 'pre-selected') : 'shown'}
                          </span>
                          <span className="studio-mono studio-pitch-price-source">
                            {p.sourceLabel}
                          </span>
                          {rowViolation ? (
                            <span className="studio-field-error" role="alert">
                              {rowViolation.message}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td data-label="Base" className="mg-num studio-pitch-price-base">
                        <div className="studio-stack-xs studio-pitch-base-cell">
                          <span>{formatRupees(p.pitchBase)}</span>
                          {p.adminOverridden ? (
                            <span className="studio-mono">
                              Admin {formatRupees(p.adminBase)}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td data-label="Price" className="studio-pitch-price-cell">
                        <div className="studio-stack-xs">
                          <TextInput
                            type="number"
                            min={0}
                            max={10000000}
                            step={1}
                            className="studio-pitch-price-input"
                            aria-label={`Proposal price for ${catItem.n}`}
                            placeholder={String(p.adminOverridden ? p.adminBase : p.pitchBase)}
                            value={
                              p.proposalOverride !== undefined ? String(p.proposalOverride) : ''
                            }
                            onFocus={() => onScrollTo?.(`item:${itemId}`)}
                            onChange={(e) => handleOverrideChange(itemId, e.target.value)}
                          />
                          <span className="studio-mono mg-num">{formatRupees(p.effective)}</span>
                        </div>
                      </td>
                      <td data-label="Reset" className="studio-pitch-price-action">
                        <PillButton
                          variant="quiet"
                          size="sm"
                          className="studio-pitch-reset-btn"
                          disabled={p.proposalOverride === undefined}
                          aria-label={`Reset to base for ${catItem.n}`}
                          onClick={() => handleResetRow(itemId)}
                        >
                          Reset
                        </PillButton>
                      </td>
                    </tr>
                  );
                }

                if (customItem) {
                  return (
                    <tr
                      key={itemId}
                      data-scroll-target={`item:${itemId}`}
                    >
                      <td data-label="Item" className="studio-pitch-price-item">
                        <div className="studio-stack-xs">
                          <span className="studio-pitch-price-item-name">{customItem.n}</span>
                          <span className="studio-mono">
                            {customItem.g} · {isSelected ? (isFinal ? 'agreed' : 'pre-selected') : 'shown'}
                          </span>
                          <span className="studio-mono studio-pitch-price-source">
                            This proposal
                          </span>
                        </div>
                      </td>
                      <td data-label="Base" className="mg-num studio-pitch-price-base">
                        —
                      </td>
                      <td data-label="Price" className="studio-pitch-price-cell">
                        <div className="studio-stack-xs">
                          <TextInput
                            type="number"
                            min={0}
                            max={10000000}
                            step={1}
                            className="studio-pitch-price-input"
                            aria-label={`Proposal price for ${customItem.n}`}
                            value={String(customItem.p)}
                            onFocus={() => onScrollTo?.(`item:${itemId}`)}
                            onChange={(e) => handleCustomPriceChange(itemId, e.target.value)}
                          />
                          <span className="studio-mono mg-num">{formatRupees(customItem.p)}</span>
                        </div>
                      </td>
                      <td data-label="Reset" className="studio-pitch-price-action">
                        <PillButton
                          variant="quiet"
                          size="sm"
                          className="studio-pitch-reset-btn"
                          disabled
                          aria-label={`Reset to base for ${customItem.n}`}
                        >
                          Reset
                        </PillButton>
                      </td>
                    </tr>
                  );
                }

                return null;
              })}
            </tbody>
          </table>
        </div>
      )}

      <div
        data-scroll-target="summary"
        onFocusCapture={() => onScrollTo?.('summary')}
      >
        <Card>
          <div className="studio-stack-sm" aria-label="Engine pricing totals">
            <div className="studio-section-bar">
              <span className="mg-label">Engine pricing summary</span>
              {dirty ? (
                <span className="studio-mono">Save proposal to refresh totals</span>
              ) : null}
            </div>

            <div className="studio-pitch-totals">
              {oneTimePlans.map((plan) => (
                <div key={plan.id} className="studio-pitch-total-line">
                  <span>
                    {plan.name} <span className="mg-chip">One-time plan</span>
                  </span>
                  <span className="mg-num">{formatRupees(plan.price)}</span>
                </div>
              ))}
              <div className="studio-pitch-total-line">
                <span>Subtotal</span>
                <span className="mg-num">{formatRupees(pricingSummary.subtotal)}</span>
              </div>
              {pricingSummary.standalone_fee > 0 ? (
                <div className="studio-pitch-total-line">
                  <span>
                    {pricingSummary.standalone_label ||
                      'App-only setup (no web application to share foundation)'}
                  </span>
                  <span className="mg-num">{formatRupees(pricingSummary.standalone_fee)}</span>
                </div>
              ) : null}
              <div className="studio-pitch-total-line studio-pitch-total-grand">
                <span>{isFinal ? 'Total (one-time)' : 'Total'}</span>
                <strong className="mg-num">{formatRupees(pricingSummary.total)}</strong>
              </div>

              {recurringPlans.length > 0 ? (
                <div className="studio-pitch-recurring-summary" aria-label="Recurring service plans">
                  <div className="mg-label">Recurring service plans</div>
                  {recurringPlans.map((plan) => (
                    <div key={plan.id} className="studio-pitch-total-line">
                      <span>{plan.name}</span>
                      <span className="mg-num">
                        {formatRupees(plan.price)}
                        {formatServicePlanPeriodSuffix(plan.period)}
                      </span>
                    </div>
                  ))}
                  {recurringTotals && recurringTotals.monthly > 0 ? (
                    <div className="studio-pitch-total-line">
                      <span>Recurring total (monthly)</span>
                      <strong className="mg-num">
                        {formatRupees(recurringTotals.monthly)} / month
                      </strong>
                    </div>
                  ) : null}
                  {recurringTotals && recurringTotals.quarterly > 0 ? (
                    <div className="studio-pitch-total-line">
                      <span>Recurring total (quarterly)</span>
                      <strong className="mg-num">
                        {formatRupees(recurringTotals.quarterly)} / quarter
                      </strong>
                    </div>
                  ) : null}
                  {recurringTotals && recurringTotals.yearly > 0 ? (
                    <div className="studio-pitch-total-line">
                      <span>Recurring total (yearly)</span>
                      <strong className="mg-num">
                        {formatRupees(recurringTotals.yearly)} / year
                      </strong>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
