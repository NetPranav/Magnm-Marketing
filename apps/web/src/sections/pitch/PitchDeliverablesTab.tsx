import { useMemo, useState } from 'react';
import {
  Card,
  Checkbox,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  TextArea,
  TextInput,
} from '@magnm/design/react';
import {
  formatRupees,
  type PitchCatalog,
  type PitchCustomItem,
  type PitchRuleViolation,
  type PitchSectionKey,
} from '../../api/pitch';
import {
  PITCH_SHEETS,
  buildAllItemsMap,
  createCustomItemId,
  expandSelectionWithDeps,
  sectionForGroup,
  validatePitchText,
  type PitchDraftState,
} from './pitchUtils';

export interface PitchDeliverablesTabProps {
  draft: PitchDraftState;
  catalog: PitchCatalog | null;
  violations: PitchRuleViolation[];
  onChange: (updater: (prev: PitchDraftState) => PitchDraftState) => void;
}

/** Editor tab 2: 4 Pitch sheets (show/pre-selected toggles + dependency notes) and custom line items. */
export function PitchDeliverablesTab({
  draft,
  catalog,
  violations,
  onChange,
}: PitchDeliverablesTabProps) {
  const allItems = useMemo(
    () => buildAllItemsMap(catalog, draft.custom_items),
    [catalog, draft.custom_items],
  );

  const { selected, neededBy } = useMemo(
    () => expandSelectionWithDeps(draft.preset.initialSelected, allItems),
    [draft.preset.initialSelected, allItems],
  );
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  // Custom item form state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [customName, setCustomName] = useState('');
  const [customDesc, setCustomDesc] = useState('');
  const [customGroup, setCustomGroup] = useState(catalog?.groups?.[0] || 'Web & SaaS');
  const [customPrice, setCustomPrice] = useState('0');
  const [customRequires, setCustomRequires] = useState<string[]>([]);
  const [customError, setCustomError] = useState<string | null>(null);

  const groups = catalog?.groups || [
    'Web & SaaS',
    'Mobile app',
    'Backend',
    'AI & Automation',
    'DevOps & Maintenance',
  ];

  const toggleShownOnSheet = (sheetKey: PitchSectionKey, itemId: string, show: boolean) => {
    onChange((prev) => {
      const current = prev.preset.sections[sheetKey] || [];
      const nextList = show
        ? current.includes(itemId)
          ? current
          : [...current, itemId]
        : current.filter((id) => id !== itemId);
      return {
        ...prev,
        preset: {
          ...prev.preset,
          sections: {
            ...prev.preset.sections,
            [sheetKey]: nextList,
          },
        },
      };
    });
  };

  const togglePreSelected = (itemId: string, checked: boolean) => {
    if (!checked && (neededBy[itemId]?.length || 0) > 0) {
      return;
    }
    onChange((prev) => {
      const current = prev.preset.initialSelected || [];
      const nextRaw = checked
        ? current.includes(itemId)
          ? current
          : [...current, itemId]
        : current.filter((id) => id !== itemId);
      const { selected: expanded } = expandSelectionWithDeps(nextRaw, allItems);
      return {
        ...prev,
        preset: {
          ...prev.preset,
          initialSelected: expanded,
        },
      };
    });
  };

  const resetCustomForm = () => {
    setEditingId(null);
    setCustomName('');
    setCustomDesc('');
    setCustomGroup(groups[0] || 'Web & SaaS');
    setCustomPrice('0');
    setCustomRequires([]);
    setCustomError(null);
  };

  const startEditCustom = (item: PitchCustomItem) => {
    setEditingId(item.id);
    setCustomName(item.n);
    setCustomDesc(item.d);
    setCustomGroup(item.g);
    setCustomPrice(String(item.p));
    setCustomRequires([...item.r]);
    setCustomError(null);
  };

  const customNameErr = validatePitchText(customName);
  const customDescErr = validatePitchText(customDesc);

  const handleSaveCustomItem = (event: React.FormEvent) => {
    event.preventDefault();
    const n = customName.trim();
    const d = customDesc.trim();
    const parsedPrice = Number(customPrice);
    if (!n) {
      setCustomError('Custom item name is required.');
      return;
    }
    if (validatePitchText(n)) {
      setCustomError('Text cannot contain < or >');
      return;
    }
    if (!d) {
      setCustomError('Custom item description is required.');
      return;
    }
    if (validatePitchText(d)) {
      setCustomError('Text cannot contain < or >');
      return;
    }
    if (!Number.isInteger(parsedPrice) || parsedPrice < 0 || parsedPrice > 10_000_000) {
      setCustomError('Price must be an integer between 0 and 10,000,000.');
      return;
    }

    const existingIds = new Set(Object.keys(allItems));
    const targetId = editingId || createCustomItemId(n, existingIds);
    const nextItem: PitchCustomItem = {
      id: targetId,
      g: customGroup,
      n,
      d,
      p: parsedPrice,
      r: customRequires.filter((dep) => dep !== targetId),
    };

    const sheetKey = sectionForGroup(customGroup);

    onChange((prev) => {
      const nextCustom = editingId
        ? prev.custom_items.map((c) => (c.id === editingId ? nextItem : c))
        : [...prev.custom_items, nextItem];

      const nextSections = { ...prev.preset.sections };
      if (!editingId) {
        const curSheet = nextSections[sheetKey] || [];
        if (!curSheet.includes(targetId)) {
          nextSections[sheetKey] = [...curSheet, targetId];
        }
      }

      const nextMap = buildAllItemsMap(catalog, nextCustom);
      const nextSelected = editingId
        ? prev.preset.initialSelected
        : [...prev.preset.initialSelected, targetId];
      const { selected: expanded } = expandSelectionWithDeps(nextSelected, nextMap);

      return {
        ...prev,
        custom_items: nextCustom,
        preset: {
          ...prev.preset,
          sections: nextSections,
          initialSelected: expanded,
        },
      };
    });

    resetCustomForm();
  };

  const handleRemoveCustomItem = (customId: string) => {
    onChange((prev) => {
      const nextCustom = prev.custom_items
        .filter((c) => c.id !== customId)
        .map((c) => ({ ...c, r: c.r.filter((dep) => dep !== customId) }));
      const nextSections = {
        p2: (prev.preset.sections.p2 || []).filter((id) => id !== customId),
        p3: (prev.preset.sections.p3 || []).filter((id) => id !== customId),
        p4: (prev.preset.sections.p4 || []).filter((id) => id !== customId),
        p5: (prev.preset.sections.p5 || []).filter((id) => id !== customId),
      };
      const nextInitial = (prev.preset.initialSelected || []).filter((id) => id !== customId);
      return {
        ...prev,
        custom_items: nextCustom,
        preset: {
          ...prev.preset,
          sections: nextSections,
          initialSelected: nextInitial,
        },
      };
    });
    if (editingId === customId) resetCustomForm();
  };

  const sectionViolations = violations.filter(
    (v) =>
      v.field.startsWith('preset.sections') ||
      v.field.startsWith('preset.initialSelected') ||
      v.field.startsWith('custom_items'),
  );

  return (
    <div className="studio-stack">
      {sectionViolations.length > 0 ? (
        <div className="mg-notice" role="alert">
          {sectionViolations.map((v) => v.message).join(' ')}
        </div>
      ) : null}

      {PITCH_SHEETS.map((sheet) => {
        const shownIds = new Set(draft.preset.sections[sheet.key] || []);
        const sheetItems = Object.entries(allItems).filter(([, item]) =>
          sheet.groups.includes(item.g),
        );

        return (
          <section
            key={sheet.key}
            className="studio-section"
            aria-label={`${sheet.label} deliverables`}
          >
            <div className="studio-section-bar">
              <h3 className="studio-section-title">{sheet.label}</h3>
              <span className="studio-mono">
                {shownIds.size} shown · {sheetItems.filter(([id]) => selectedSet.has(id)).length} pre-selected
              </span>
            </div>

            {sheetItems.length === 0 ? (
              <div className="mg-empty">No deliverables in {sheet.label}.</div>
            ) : (
              <RowList aria-label={`${sheet.label} items`}>
                {sheetItems.map(([itemId, item]) => {
                  const isShown = shownIds.has(itemId);
                  const isSelected = selectedSet.has(itemId);
                  const requiredByNames = neededBy[itemId] || [];
                  const locked = isSelected && requiredByNames.length > 0;
                  const missingDeps = !isSelected
                    ? item.r
                        .filter((depId) => !selectedSet.has(depId) && allItems[depId])
                        .map((depId) => allItems[depId].n)
                    : [];

                  return (
                    <Row
                      key={itemId}
                      title={
                        <span>
                          {item.n}{' '}
                          {itemId.startsWith('custom_') ? (
                            <span className="mg-chip">Custom</span>
                          ) : null}
                        </span>
                      }
                      description={
                        <div className="studio-stack-xs">
                          <span>{item.d}</span>
                          {locked ? (
                            <span className="studio-mono">
                              needed for {requiredByNames.join(', ')}
                            </span>
                          ) : missingDeps.length > 0 ? (
                            <span className="studio-mono">
                              requires {missingDeps.join(', ')}
                            </span>
                          ) : null}
                        </div>
                      }
                      meta={
                        <div className="studio-pitch-deliverable-toggles">
                          <Checkbox
                            label="Show on this sheet"
                            checked={isShown}
                            onChange={(e) =>
                              toggleShownOnSheet(sheet.key, itemId, e.target.checked)
                            }
                          />
                          <Checkbox
                            label="Pre-selected"
                            checked={isSelected}
                            disabled={locked}
                            onChange={(e) => togglePreSelected(itemId, e.target.checked)}
                          />
                        </div>
                      }
                    />
                  );
                })}
              </RowList>
            )}
          </section>
        );
      })}

      <Card>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <span className="mg-label">Custom line items</span>
            {editingId ? (
              <PillButton variant="quiet" size="sm" onClick={resetCustomForm}>
                Cancel edit
              </PillButton>
            ) : null}
          </div>

          {draft.custom_items.length > 0 ? (
            <RowList aria-label="Custom line items">
              {draft.custom_items.map((item) => (
                <Row
                  key={item.id}
                  title={item.n}
                  description={
                    <div className="studio-stack-xs">
                      <span>{item.d}</span>
                      <span className="studio-mono">
                        {item.id} · {item.g}
                        {item.r.length > 0 ? ` · requires ${item.r.join(', ')}` : ''}
                      </span>
                    </div>
                  }
                  meta={
                    <div className="studio-row-actions">
                      <span className="mg-num">{formatRupees(item.p)}</span>
                      <PillButton
                        variant="outline"
                        size="sm"
                        onClick={() => startEditCustom(item)}
                      >
                        Edit
                      </PillButton>
                      <PillButton
                        variant="quiet"
                        size="sm"
                        onClick={() => handleRemoveCustomItem(item.id)}
                      >
                        Remove
                      </PillButton>
                    </div>
                  }
                />
              ))}
            </RowList>
          ) : (
            <div className="studio-hint">
              Add proposal-specific deliverables not in the base Pitch catalog.
            </div>
          )}

          <form className="studio-stack-sm" onSubmit={handleSaveCustomItem}>
            {customError ? (
              <div className="mg-notice" role="alert">
                {customError}
              </div>
            ) : null}

            <div className="studio-grid-2">
              <Field
                label="Custom item name"
                htmlFor="custom-item-name"
                hint={customNameErr ? <span className="studio-field-error" role="alert">{customNameErr}</span> : undefined}
              >
                <TextInput
                  id="custom-item-name"
                  aria-invalid={Boolean(customNameErr)}
                  placeholder="e.g. Custom ERP Sync"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                />
              </Field>
              <Field label="Group" htmlFor="custom-item-group">
                <Select
                  id="custom-item-group"
                  value={customGroup}
                  onChange={(e) => setCustomGroup(e.target.value)}
                >
                  {groups.map((grp) => (
                    <option key={grp} value={grp}>
                      {grp}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <Field
              label="Description"
              htmlFor="custom-item-desc"
              hint={customDescErr ? <span className="studio-field-error" role="alert">{customDescErr}</span> : undefined}
            >
              <TextArea
                id="custom-item-desc"
                aria-invalid={Boolean(customDescErr)}
                rows={2}
                placeholder="Describe what this custom deliverable covers"
                value={customDesc}
                onChange={(e) => setCustomDesc(e.target.value)}
              />
            </Field>

            <div className="studio-grid-2">
              <Field label="Price (INR)" htmlFor="custom-item-price">
                <TextInput
                  id="custom-item-price"
                  type="number"
                  min={0}
                  max={10000000}
                  step={1}
                  value={customPrice}
                  onChange={(e) => setCustomPrice(e.target.value)}
                />
              </Field>

              <Field label="Requires (optional)" htmlFor="custom-item-requires">
                <Select
                  id="custom-item-requires"
                  value=""
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val && !customRequires.includes(val)) {
                      setCustomRequires((prev) => [...prev, val]);
                    }
                  }}
                >
                  <option value="">Add dependency...</option>
                  {Object.entries(allItems)
                    .filter(([id]) => id !== editingId && !customRequires.includes(id))
                    .map(([id, item]) => (
                      <option key={id} value={id}>
                        {item.n} ({id})
                      </option>
                    ))}
                </Select>
              </Field>
            </div>

            {customRequires.length > 0 ? (
              <div className="studio-chip-row">
                {customRequires.map((depId) => (
                  <PillButton
                    key={depId}
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setCustomRequires((prev) => prev.filter((id) => id !== depId))
                    }
                  >
                    {allItems[depId]?.n || depId} ×
                  </PillButton>
                ))}
              </div>
            ) : null}

            <div>
              <PillButton type="submit" size="sm">
                {editingId ? 'Update custom item' : 'Add custom item'}
              </PillButton>
            </div>
          </form>
        </div>
      </Card>
    </div>
  );
}
