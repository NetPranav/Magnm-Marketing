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
}

/** Editor tab 3: Price table showing Pitch/Admin/Proposal sources and engine totals. */
export function PitchPricesTab({
  draft,
  catalog,
  pricingSummary,
  dirty,
  violations,
  onChange,
}: PitchPricesTabProps) {
  const selectedSet = useMemo(
    () => new Set(draft.preset.initialSelected || []),
    [draft.preset.initialSelected],
  );
  const shownSet = useMemo(() => {
    const out = new Set<string>();
    for (const sheet of PITCH_SHEETS) {
      for (const id of draft.preset.sections[sheet.key] || []) {
        out.add(id);
      }
    }
    return out;
  }, [draft.preset.sections]);

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
        pushIfActive(id);
      }
    }
    for (const c of draft.custom_items) {
      pushIfActive(c.id);
    }
    return ids;
  }, [catalog, draft.custom_items, draft.price_overrides, selectedSet, shownSet]);

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

  const handleOverrideChange = (itemId: string, rawVal: string) => {
    const trimmed = rawVal.trim();
    onChange((prev) => {
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

  const handleResetRow = (itemId: string) => {
    onChange((prev) => {
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
                <th>This proposal</th>
                <th>Effective</th>
                <th>Source</th>
                <th>Action</th>
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
                    <tr key={itemId}>
                      <td data-label="Item" className="studio-pitch-price-item">
                        <div className="studio-stack-xs">
                          <span>{catItem.n}</span>
                          <span className="studio-mono">
                            {catItem.g} · {isSelected ? (isFinal ? 'agreed' : 'pre-selected') : 'shown'}
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
                      <td data-label="This proposal">
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
                          onChange={(e) => handleOverrideChange(itemId, e.target.value)}
                        />
                      </td>
                      <td data-label="Effective" className="mg-num">
                        {formatRupees(p.effective)}
                      </td>
                      <td data-label="Source">
                        <span className="mg-chip">{p.sourceLabel}</span>
                      </td>
                      <td data-label="Action" className="studio-pitch-price-action">
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
                    <tr key={itemId}>
                      <td data-label="Item" className="studio-pitch-price-item">
                        <div className="studio-stack-xs">
                          <span>{customItem.n}</span>
                          <span className="studio-mono">
                            {customItem.g} · {isSelected ? (isFinal ? 'agreed' : 'pre-selected') : 'shown'}
                          </span>
                        </div>
                      </td>
                      <td data-label="Base" className="mg-num studio-pitch-price-base">
                        —
                      </td>
                      <td data-label="This proposal" className="studio-mono">
                        Custom item
                      </td>
                      <td data-label="Effective" className="mg-num">
                        {formatRupees(customItem.p)}
                      </td>
                      <td data-label="Source">
                        <span className="mg-chip">This proposal</span>
                      </td>
                      <td
                        data-label="Action"
                        className="studio-mono studio-pitch-price-action"
                      >
                        —
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
  );
}
