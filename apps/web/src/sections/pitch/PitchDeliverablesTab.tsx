import { useMemo, useState } from 'react';
import {
  Card,
  Checkbox,
  Dialog,
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
  type PitchCategoryPlacement,
  type PitchCustomCategory,
  type PitchCustomItem,
  type PitchIncludedFromHidden,
  type PitchRuleViolation,
  type PitchSectionKey,
} from '../../api/pitch';
import {
  MAX_CUSTOM_CATEGORIES,
  PITCH_SHEETS,
  buildAllItemsMap,
  computeLocalIncludedFromHidden,
  countVisibleSoftwareOptions,
  createCustomCategoryId,
  createCustomItemId,
  deriveFinalSections,
  expandSelectionWithDeps,
  sectionForGroup,
  swapListItems,
  validatePitchText,
  type PitchDraftState,
} from './pitchUtils';

export interface PitchDeliverablesTabProps {
  draft: PitchDraftState;
  catalog: PitchCatalog | null;
  includedFromHidden?: PitchIncludedFromHidden[];
  violations: PitchRuleViolation[];
  onChange: (updater: (prev: PitchDraftState) => PitchDraftState) => void;
  onScrollTo?: (target: string) => void;
}

interface CategoryServiceFormState {
  editingId: string | null;
  name: string;
  desc: string;
  price: string;
  requires: string[];
  error: string | null;
}

const DEFAULT_CAT_SERVICE_FORM: CategoryServiceFormState = {
  editingId: null,
  name: '',
  desc: '',
  price: '0',
  requires: [],
  error: null,
};

/** Editor tab 2: 4 Pitch sheets (with Remove/Add back), custom categories, and custom line items. */
export function PitchDeliverablesTab({
  draft,
  catalog,
  includedFromHidden,
  violations,
  onChange,
  onScrollTo,
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
  const hiddenSheetsSet = useMemo(
    () => new Set<PitchSectionKey>(draft.hidden_sheets || []),
    [draft.hidden_sheets],
  );

  const effectiveIncludedFromHidden = useMemo(() => {
    if (Array.isArray(includedFromHidden)) {
      return includedFromHidden;
    }
    return computeLocalIncludedFromHidden(draft, allItems);
  }, [includedFromHidden, draft, allItems]);

  const { totalSoftwareOptions } = useMemo(
    () => countVisibleSoftwareOptions(draft),
    [draft],
  );

  const isFinal = draft.kind === 'final';
  const [searchQuery, setSearchQuery] = useState('');

  // Custom category form state
  const [catFormOpen, setCatFormOpen] = useState(false);
  const [editingCatId, setEditingCatId] = useState<string | null>(null);
  const [catName, setCatName] = useState('');
  const [catPlacement, setCatPlacement] = useState<PitchCategoryPlacement>('p2');
  const [catSheetTitle, setCatSheetTitle] = useState('');
  const [catSheetLead, setCatSheetLead] = useState('');
  const [catError, setCatError] = useState<string | null>(null);
  const [confirmDeleteCat, setConfirmDeleteCat] = useState<PitchCustomCategory | null>(null);

  // Per-category service form state
  const [catServiceForms, setCatServiceForms] = useState<Record<string, CategoryServiceFormState>>(
    {},
  );

  // Built-in group custom item form state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [customName, setCustomName] = useState('');
  const [customDesc, setCustomDesc] = useState('');
  const [customGroup, setCustomGroup] = useState(catalog?.groups?.[0] || 'Web & SaaS');
  const [customPrice, setCustomPrice] = useState('0');
  const [customRequires, setCustomRequires] = useState<string[]>([]);
  const [customError, setCustomError] = useState<string | null>(null);

  const builtinGroups = catalog?.groups || [
    'Web & SaaS',
    'Mobile app',
    'Backend',
    'AI & Automation',
    'DevOps & Maintenance',
  ];
  const customCategoryNames = useMemo(
    () => (draft.custom_categories || []).map((c) => c.name).filter(Boolean),
    [draft.custom_categories],
  );
  const allGroupOptions = useMemo(
    () => [...builtinGroups, ...customCategoryNames.filter((n) => !builtinGroups.includes(n))],
    [builtinGroups, customCategoryNames],
  );

  const visibleBuiltinSheets = useMemo(
    () => PITCH_SHEETS.filter((s) => !hiddenSheetsSet.has(s.key)),
    [hiddenSheetsSet],
  );

  const toggleHideSheet = (sheetKey: PitchSectionKey, shouldHide: boolean) => {
    if (shouldHide && !hiddenSheetsSet.has(sheetKey) && totalSoftwareOptions <= 1) {
      return;
    }
    onChange((prev) => {
      const curHidden = prev.hidden_sheets || [];
      const nextHidden = shouldHide
        ? curHidden.includes(sheetKey)
          ? curHidden
          : [...curHidden, sheetKey]
        : curHidden.filter((k) => k !== sheetKey);
      return {
        ...prev,
        hidden_sheets: nextHidden,
      };
    });
  };

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
          sections:
            prev.kind === 'final'
              ? deriveFinalSections(
                  expanded,
                  allItems,
                  prev.custom_categories,
                  prev.hidden_sheets,
                )
              : prev.preset.sections,
        },
      };
    });
  };

  const openAddCategoryForm = () => {
    const defaultPlacement: PitchCategoryPlacement =
      visibleBuiltinSheets[0]?.key || 'new_sheet';
    setEditingCatId(null);
    setCatName('');
    setCatPlacement(defaultPlacement);
    setCatSheetTitle('');
    setCatSheetLead('');
    setCatError(null);
    setCatFormOpen(true);
  };

  const startEditCategory = (cat: PitchCustomCategory) => {
    setEditingCatId(cat.id);
    setCatName(cat.name);
    setCatPlacement(cat.placement);
    setCatSheetTitle(cat.sheet_title || '');
    setCatSheetLead(cat.sheet_lead || '');
    setCatError(null);
    setCatFormOpen(true);
  };

  const resetCategoryForm = () => {
    setCatFormOpen(false);
    setEditingCatId(null);
    setCatName('');
    setCatPlacement(visibleBuiltinSheets[0]?.key || 'new_sheet');
    setCatSheetTitle('');
    setCatSheetLead('');
    setCatError(null);
  };

  const catNameErr = validatePitchText(catName);
  const catSheetTitleErr = validatePitchText(catSheetTitle);
  const catSheetLeadErr = validatePitchText(catSheetLead);

  const handleSaveCategory = (event: React.FormEvent) => {
    event.preventDefault();
    const cleanName = catName.trim();
    const cleanTitle = catSheetTitle.trim();
    const cleanLead = catSheetLead.trim();

    if (!cleanName) {
      setCatError('Category name is required.');
      return;
    }
    if (validatePitchText(cleanName)) {
      setCatError('Text cannot contain < or >');
      return;
    }
    if (builtinGroups.some((g) => g.toLowerCase() === cleanName.toLowerCase())) {
      setCatError(`Category name "${cleanName}" cannot equal a Pitch group name.`);
      return;
    }
    const dupCat = (draft.custom_categories || []).find(
      (c) => c.id !== editingCatId && c.name.toLowerCase() === cleanName.toLowerCase(),
    );
    if (dupCat) {
      setCatError(`Duplicate custom category name "${cleanName}".`);
      return;
    }
    if (catPlacement === 'new_sheet') {
      if (!cleanTitle) {
        setCatError('Page title is required when placement is Its own page.');
        return;
      }
      if (validatePitchText(cleanTitle)) {
        setCatError('Text cannot contain < or >');
        return;
      }
      if (validatePitchText(cleanLead)) {
        setCatError('Text cannot contain < or >');
        return;
      }
    }

    const existingCatIds = new Set((draft.custom_categories || []).map((c) => c.id));
    const targetCatId = editingCatId || createCustomCategoryId(cleanName, existingCatIds);
    const nextCat: PitchCustomCategory = {
      id: targetCatId,
      name: cleanName,
      placement: catPlacement,
      sheet_title: catPlacement === 'new_sheet' ? cleanTitle : '',
      sheet_lead: catPlacement === 'new_sheet' ? cleanLead : '',
    };

    onChange((prev) => {
      const oldCat = editingCatId
        ? prev.custom_categories.find((c) => c.id === editingCatId)
        : null;
      const nextCats = editingCatId
        ? prev.custom_categories.map((c) => (c.id === editingCatId ? nextCat : c))
        : [...prev.custom_categories, nextCat];

      const nextCustomItems =
        oldCat && oldCat.name !== cleanName
          ? prev.custom_items.map((ci) =>
              ci.g === oldCat.name ? { ...ci, g: cleanName } : ci,
            )
          : prev.custom_items;

      const catItemIds = nextCustomItems
        .filter((ci) => ci.g === cleanName)
        .map((ci) => ci.id);
      const nextSections = {
        p2: [...(prev.preset.sections.p2 || [])],
        p3: [...(prev.preset.sections.p3 || [])],
        p4: [...(prev.preset.sections.p4 || [])],
        p5: [...(prev.preset.sections.p5 || [])],
      };

      if (oldCat && oldCat.placement !== catPlacement && catItemIds.length > 0) {
        if (
          oldCat.placement === 'p2' ||
          oldCat.placement === 'p3' ||
          oldCat.placement === 'p4' ||
          oldCat.placement === 'p5'
        ) {
          nextSections[oldCat.placement] = nextSections[oldCat.placement].filter(
            (id) => !catItemIds.includes(id),
          );
        }
        if (
          catPlacement === 'p2' ||
          catPlacement === 'p3' ||
          catPlacement === 'p4' ||
          catPlacement === 'p5'
        ) {
          for (const id of catItemIds) {
            if (!nextSections[catPlacement].includes(id)) {
              nextSections[catPlacement].push(id);
            }
          }
        }
      }

      return {
        ...prev,
        custom_categories: nextCats,
        custom_items: nextCustomItems,
        preset: {
          ...prev.preset,
          sections: nextSections,
        },
      };
    });

    resetCategoryForm();
  };

  const handleConfirmDeleteCategory = () => {
    if (!confirmDeleteCat) return;
    const targetCat = confirmDeleteCat;
    setConfirmDeleteCat(null);
    onChange((prev) => {
      const removedIds = new Set(
        prev.custom_items.filter((ci) => ci.g === targetCat.name).map((ci) => ci.id),
      );
      const nextCustomItems = prev.custom_items
        .filter((ci) => !removedIds.has(ci.id))
        .map((ci) => ({
          ...ci,
          r: ci.r.filter((dep) => !removedIds.has(dep)),
        }));
      const nextCats = prev.custom_categories.filter((c) => c.id !== targetCat.id);
      const nextSections = {
        p2: (prev.preset.sections.p2 || []).filter((id) => !removedIds.has(id)),
        p3: (prev.preset.sections.p3 || []).filter((id) => !removedIds.has(id)),
        p4: (prev.preset.sections.p4 || []).filter((id) => !removedIds.has(id)),
        p5: (prev.preset.sections.p5 || []).filter((id) => !removedIds.has(id)),
      };
      const nextInitial = (prev.preset.initialSelected || []).filter(
        (id) => !removedIds.has(id),
      );
      return {
        ...prev,
        custom_categories: nextCats,
        custom_items: nextCustomItems,
        preset: {
          ...prev.preset,
          sections: nextSections,
          initialSelected: nextInitial,
        },
      };
    });
    if (editingCatId === targetCat.id) {
      resetCategoryForm();
    }
  };

  const getCatServiceForm = (catId: string): CategoryServiceFormState =>
    catServiceForms[catId] || DEFAULT_CAT_SERVICE_FORM;

  const updateCatServiceForm = (
    catId: string,
    patch: Partial<CategoryServiceFormState>,
  ) => {
    setCatServiceForms((prev) => ({
      ...prev,
      [catId]: {
        ...(prev[catId] || DEFAULT_CAT_SERVICE_FORM),
        ...patch,
      },
    }));
  };

  const handleSaveCategoryService = (cat: PitchCustomCategory, event: React.FormEvent) => {
    event.preventDefault();
    const form = getCatServiceForm(cat.id);
    const n = form.name.trim();
    const d = form.desc.trim();
    const parsedPrice = Number(form.price);

    if (!n) {
      updateCatServiceForm(cat.id, { error: 'Service name is required.' });
      return;
    }
    if (validatePitchText(n)) {
      updateCatServiceForm(cat.id, { error: 'Text cannot contain < or >' });
      return;
    }
    if (!d) {
      updateCatServiceForm(cat.id, { error: 'Service description is required.' });
      return;
    }
    if (validatePitchText(d)) {
      updateCatServiceForm(cat.id, { error: 'Text cannot contain < or >' });
      return;
    }
    if (!Number.isInteger(parsedPrice) || parsedPrice < 0 || parsedPrice > 10_000_000) {
      updateCatServiceForm(cat.id, {
        error: 'Price must be an integer between 0 and 10,000,000.',
      });
      return;
    }

    const existingIds = new Set(Object.keys(allItems));
    const targetId = form.editingId || createCustomItemId(n, existingIds);
    const nextItem: PitchCustomItem = {
      id: targetId,
      g: cat.name,
      n,
      d,
      p: parsedPrice,
      r: form.requires.filter((dep) => dep !== targetId),
    };

    onChange((prev) => {
      const nextCustom = form.editingId
        ? prev.custom_items.map((c) => (c.id === form.editingId ? nextItem : c))
        : [...prev.custom_items, nextItem];

      const nextSections = { ...prev.preset.sections };
      if (
        !form.editingId &&
        (cat.placement === 'p2' ||
          cat.placement === 'p3' ||
          cat.placement === 'p4' ||
          cat.placement === 'p5')
      ) {
        const curSheet = nextSections[cat.placement] || [];
        if (!curSheet.includes(targetId)) {
          nextSections[cat.placement] = [...curSheet, targetId];
        }
      }

      const nextMap = buildAllItemsMap(catalog, nextCustom);
      const nextSelected = form.editingId
        ? prev.preset.initialSelected
        : [...prev.preset.initialSelected, targetId];
      const { selected: expanded } = expandSelectionWithDeps(nextSelected, nextMap);

      return {
        ...prev,
        custom_items: nextCustom,
        preset: {
          ...prev.preset,
          sections:
            prev.kind === 'final'
              ? deriveFinalSections(
                  expanded,
                  nextMap,
                  prev.custom_categories,
                  prev.hidden_sheets,
                )
              : nextSections,
          initialSelected: expanded,
        },
      };
    });

    setCatServiceForms((prev) => ({
      ...prev,
      [cat.id]: DEFAULT_CAT_SERVICE_FORM,
    }));
  };

  const resetCustomForm = () => {
    setEditingId(null);
    setCustomName('');
    setCustomDesc('');
    setCustomGroup(allGroupOptions[0] || 'Web & SaaS');
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

    const sheetKey = sectionForGroup(customGroup, draft.custom_categories);

    onChange((prev) => {
      const nextCustom = editingId
        ? prev.custom_items.map((c) => (c.id === editingId ? nextItem : c))
        : [...prev.custom_items, nextItem];

      const nextSections = { ...prev.preset.sections };
      if (!editingId && sheetKey) {
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
          sections:
            prev.kind === 'final'
              ? deriveFinalSections(
                  expanded,
                  nextMap,
                  prev.custom_categories,
                  prev.hidden_sheets,
                )
              : nextSections,
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
      v.field.startsWith('custom_items') ||
      v.field.startsWith('hidden_sheets') ||
      v.field.startsWith('custom_categories'),
  );

  const normalizedSearch = isFinal ? searchQuery.trim().toLowerCase() : '';
  const sheetsWithData = PITCH_SHEETS.map((sheet) => {
    const isHidden = hiddenSheetsSet.has(sheet.key);
    const shownIds = new Set(draft.preset.sections[sheet.key] || []);
    const placedCatNames = (draft.custom_categories || [])
      .filter((c) => c.placement === sheet.key && c.name)
      .map((c) => c.name);
    const sheetGroups = [...sheet.groups, ...placedCatNames];
    const allSheetItems = Object.entries(allItems).filter(([, item]) =>
      sheetGroups.includes(item.g),
    );
    const sheetItems = normalizedSearch
      ? allSheetItems.filter(([itemId, item]) => {
          const hay = `${itemId} ${item.n} ${item.d} ${item.g}`.toLowerCase();
          return hay.includes(normalizedSearch);
        })
      : allSheetItems;
    const includedForSheet = effectiveIncludedFromHidden.filter(
      (entry) =>
        entry.sheet_shown_on === sheet.key ||
        (Array.isArray(entry.also_needed_on) && entry.also_needed_on.includes(sheet.key)),
    );
    return {
      sheet,
      isHidden,
      shownIds,
      allSheetItems,
      sheetItems,
      includedForSheet,
    };
  });

  const formatPlacementLabel = (placement: PitchCategoryPlacement): string => {
    if (placement === 'new_sheet') return 'Its own page';
    const found = PITCH_SHEETS.find((s) => s.key === placement);
    return found ? found.label : placement;
  };

  return (
    <div className="studio-stack">
      {sectionViolations.length > 0 ? (
        <div className="mg-notice" role="alert">
          {sectionViolations.map((v) => v.message).join(' ')}
        </div>
      ) : null}

      {isFinal ? (
        <Card>
          <Field
            label="Search services"
            htmlFor="pitch-services-search"
            hint="Pick agreed services from the catalog. Required dependencies are added automatically."
          >
            <TextInput
              id="pitch-services-search"
              placeholder="Filter by service name, description, or group"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </Field>
        </Card>
      ) : null}

      {normalizedSearch && sheetsWithData.every((s) => s.sheetItems.length === 0) ? (
        <div className="mg-empty">No services match &ldquo;{searchQuery.trim()}&rdquo;.</div>
      ) : null}

      {sheetsWithData.map(
        ({ sheet, isHidden, shownIds, allSheetItems, sheetItems, includedForSheet }) => {
          if (normalizedSearch && sheetItems.length === 0 && !isHidden) {
            return null;
          }
          const agreedCount = allSheetItems.filter(([id]) => selectedSet.has(id)).length;
          const isLastVisibleOption = !isHidden && totalSoftwareOptions <= 1;

          return (
            <section
              key={sheet.key}
              className="studio-section"
              aria-label={`${sheet.label} ${isFinal ? 'services' : 'deliverables'}`}
              data-scroll-target={sheet.key}
            >
              <div className="studio-section-bar">
                <div className="studio-btn-group">
                  <h3 className="studio-section-title">{sheet.label}</h3>
                  {isHidden ? (
                    <span className="mg-chip">Removed from pitch</span>
                  ) : (
                    <span className="studio-mono">
                      {isFinal
                        ? `${agreedCount} agreed`
                        : `${shownIds.size} shown · ${agreedCount} pre-selected`}
                    </span>
                  )}
                </div>
                <div className="studio-btn-group">
                  {isLastVisibleOption ? (
                    <span className="studio-mono">Keep at least one software option</span>
                  ) : null}
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={isLastVisibleOption}
                    title={isLastVisibleOption ? 'Keep at least one software option' : undefined}
                    aria-label={
                      isHidden
                        ? `Add back ${sheet.label}`
                        : `Remove ${sheet.label} from pitch`
                    }
                    onClick={() => toggleHideSheet(sheet.key, !isHidden)}
                  >
                    {isHidden ? 'Add back' : 'Remove from pitch'}
                  </PillButton>
                </div>
              </div>

              {!isHidden && includedForSheet.length > 0 ? (
                <div
                  className="mg-notice"
                  role="note"
                  aria-label={`Included services on ${sheet.label}`}
                >
                  {includedForSheet.map((entry) => (
                    <div key={entry.item_id}>
                      <strong>{entry.name}</strong> — Included because{' '}
                      {entry.needed_for.join(', ')} needs it
                    </div>
                  ))}
                </div>
              ) : null}

              {isHidden ? null : sheetItems.length === 0 ? (
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
                        data-scroll-target={`item:${itemId}`}
                        onFocusCapture={() => onScrollTo?.(`item:${itemId}`)}
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
                            {!isFinal ? (
                              <Checkbox
                                label="Show on this sheet"
                                checked={isShown}
                                onChange={(e) =>
                                  toggleShownOnSheet(sheet.key, itemId, e.target.checked)
                                }
                              />
                            ) : null}
                            <Checkbox
                              label={isFinal ? 'Agreed' : 'Pre-selected'}
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
        },
      )}

      {/* Custom categories of deliverables (Max 6) */}
      <Card>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <span className="mg-label">
              Custom categories ({draft.custom_categories.length}/{MAX_CUSTOM_CATEGORIES})
            </span>
            <div className="studio-btn-group">
              {catFormOpen ? (
                <PillButton variant="quiet" size="sm" onClick={resetCategoryForm}>
                  Cancel
                </PillButton>
              ) : (
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={draft.custom_categories.length >= MAX_CUSTOM_CATEGORIES}
                  onClick={openAddCategoryForm}
                >
                  Add category
                </PillButton>
              )}
            </div>
          </div>

          {catFormOpen ? (
            <form
              className="studio-stack-sm"
              aria-label={editingCatId ? 'Edit category' : 'Add category'}
              onSubmit={handleSaveCategory}
            >
              {catError ? (
                <div className="mg-notice" role="alert">
                  {catError}
                </div>
              ) : null}

              <div className="studio-grid-2">
                <Field
                  label="Category name"
                  htmlFor="pitch-cat-name"
                  hint={
                    catNameErr ? (
                      <span className="studio-field-error" role="alert">
                        {catNameErr}
                      </span>
                    ) : undefined
                  }
                >
                  <TextInput
                    id="pitch-cat-name"
                    aria-invalid={Boolean(catNameErr)}
                    placeholder="e.g. Drone software"
                    value={catName}
                    onChange={(e) => setCatName(e.target.value)}
                  />
                </Field>

                <Field label="Placement" htmlFor="pitch-cat-placement">
                  <Select
                    id="pitch-cat-placement"
                    value={catPlacement}
                    onChange={(e) =>
                      setCatPlacement(e.target.value as PitchCategoryPlacement)
                    }
                  >
                    {visibleBuiltinSheets.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.label}
                      </option>
                    ))}
                    <option value="new_sheet">Its own page</option>
                  </Select>
                </Field>
              </div>

              {catPlacement === 'new_sheet' ? (
                <div className="studio-grid-2">
                  <Field
                    label="Page title"
                    htmlFor="pitch-cat-sheet-title"
                    hint={
                      catSheetTitleErr ? (
                        <span className="studio-field-error" role="alert">
                          {catSheetTitleErr}
                        </span>
                      ) : undefined
                    }
                  >
                    <TextInput
                      id="pitch-cat-sheet-title"
                      aria-invalid={Boolean(catSheetTitleErr)}
                      placeholder="e.g. Autonomous Drone Stack"
                      value={catSheetTitle}
                      onChange={(e) => setCatSheetTitle(e.target.value)}
                    />
                  </Field>

                  <Field
                    label="Page intro (optional)"
                    htmlFor="pitch-cat-sheet-lead"
                    hint={
                      catSheetLeadErr ? (
                        <span className="studio-field-error" role="alert">
                          {catSheetLeadErr}
                        </span>
                      ) : undefined
                    }
                  >
                    <TextInput
                      id="pitch-cat-sheet-lead"
                      aria-invalid={Boolean(catSheetLeadErr)}
                      placeholder="Optional intro line shown at the top of the page"
                      value={catSheetLead}
                      onChange={(e) => setCatSheetLead(e.target.value)}
                    />
                  </Field>
                </div>
              ) : null}

              <div>
                <PillButton type="submit" size="sm">
                  {editingCatId ? 'Update category' : 'Save category'}
                </PillButton>
              </div>
            </form>
          ) : null}

          {draft.custom_categories.length === 0 ? (
            <div className="studio-hint">
              Add up to {MAX_CUSTOM_CATEGORIES} custom deliverable categories placed on an existing sheet or on their own page.
            </div>
          ) : (
            <div className="studio-stack-sm">
              {draft.custom_categories.map((cat, cIdx) => {
                const catItems = draft.custom_items.filter((ci) => ci.g === cat.name);
                const svcForm = getCatServiceForm(cat.id);
                const svcNameErr = validatePitchText(svcForm.name);
                const svcDescErr = validatePitchText(svcForm.desc);
                const scrollTarget =
                  cat.placement === 'new_sheet' ? cat.id : cat.placement;
                const incForCat =
                  cat.placement === 'new_sheet'
                    ? effectiveIncludedFromHidden.filter(
                        (entry) =>
                          entry.sheet_shown_on === cat.id ||
                          (Array.isArray(entry.also_needed_on) &&
                            entry.also_needed_on.includes(cat.id)),
                      )
                    : [];

                return (
                  <Card
                    key={cat.id}
                    outline
                    data-scroll-target={scrollTarget}
                  >
                    <div className="studio-stack-sm">
                      <div className="studio-section-bar">
                        <div className="studio-btn-group">
                          <strong>{cat.name}</strong>
                          <span className="mg-chip">
                            {formatPlacementLabel(cat.placement)}
                          </span>
                          {cat.placement === 'new_sheet' && cat.sheet_title ? (
                            <span className="studio-mono">{cat.sheet_title}</span>
                          ) : null}
                        </div>
                        <div className="studio-btn-group">
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Move category ${cat.name} up`}
                            disabled={cIdx === 0}
                            onClick={() =>
                              onChange((prev) => ({
                                ...prev,
                                custom_categories: swapListItems(
                                  prev.custom_categories,
                                  cIdx,
                                  cIdx - 1,
                                ),
                              }))
                            }
                          >
                            Up
                          </PillButton>
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Move category ${cat.name} down`}
                            disabled={cIdx === draft.custom_categories.length - 1}
                            onClick={() =>
                              onChange((prev) => ({
                                ...prev,
                                custom_categories: swapListItems(
                                  prev.custom_categories,
                                  cIdx,
                                  cIdx + 1,
                                ),
                              }))
                            }
                          >
                            Down
                          </PillButton>
                          <PillButton
                            variant="outline"
                            size="sm"
                            aria-label={`Edit category ${cat.name}`}
                            onClick={() => startEditCategory(cat)}
                          >
                            Edit
                          </PillButton>
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Delete category ${cat.name}`}
                            onClick={() => setConfirmDeleteCat(cat)}
                          >
                            Delete
                          </PillButton>
                        </div>
                      </div>

                      {incForCat.length > 0 ? (
                        <div className="mg-notice" role="note">
                          {incForCat.map((entry) => (
                            <div key={entry.item_id}>
                              <strong>{entry.name}</strong> — Included because{' '}
                              {entry.needed_for.join(', ')} needs it
                            </div>
                          ))}
                        </div>
                      ) : null}

                      {catItems.length > 0 ? (
                        <RowList aria-label={`${cat.name} services`}>
                          {catItems.map((item) => {
                            const isSelected = selectedSet.has(item.id);
                            return (
                              <Row
                                key={item.id}
                                data-scroll-target={`item:${item.id}`}
                                onFocusCapture={() => onScrollTo?.(`item:${item.id}`)}
                                title={item.n}
                                description={
                                  <div className="studio-stack-xs">
                                    <span>{item.d}</span>
                                    <span className="studio-mono">
                                      {item.id} · {formatRupees(item.p)}
                                      {item.r.length > 0
                                        ? ` · requires ${item.r.join(', ')}`
                                        : ''}
                                    </span>
                                  </div>
                                }
                                meta={
                                  <div className="studio-row-actions">
                                    <Checkbox
                                      label={isFinal ? 'Agreed' : 'Pre-selected'}
                                      checked={isSelected}
                                      onChange={(e) =>
                                        togglePreSelected(item.id, e.target.checked)
                                      }
                                    />
                                    <PillButton
                                      variant="outline"
                                      size="sm"
                                      onClick={() =>
                                        updateCatServiceForm(cat.id, {
                                          editingId: item.id,
                                          name: item.n,
                                          desc: item.d,
                                          price: String(item.p),
                                          requires: [...item.r],
                                          error: null,
                                        })
                                      }
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
                            );
                          })}
                        </RowList>
                      ) : (
                        <div className="studio-hint">
                          No services in {cat.name} yet. Add a service below.
                        </div>
                      )}

                      <form
                        className="studio-stack-sm"
                        aria-label={`Add service to ${cat.name}`}
                        onSubmit={(e) => handleSaveCategoryService(cat, e)}
                      >
                        {svcForm.error ? (
                          <div className="mg-notice" role="alert">
                            {svcForm.error}
                          </div>
                        ) : null}

                        <div className="studio-grid-2">
                          <Field
                            label={`Service name (${cat.name})`}
                            htmlFor={`cat-svc-name-${cat.id}`}
                            hint={
                              svcNameErr ? (
                                <span className="studio-field-error" role="alert">
                                  {svcNameErr}
                                </span>
                              ) : undefined
                            }
                          >
                            <TextInput
                              id={`cat-svc-name-${cat.id}`}
                              aria-invalid={Boolean(svcNameErr)}
                              placeholder="e.g. Flight Controller"
                              value={svcForm.name}
                              onFocus={() => onScrollTo?.(scrollTarget)}
                              onChange={(e) =>
                                updateCatServiceForm(cat.id, { name: e.target.value })
                              }
                            />
                          </Field>

                          <Field
                            label={`Price (INR)`}
                            htmlFor={`cat-svc-price-${cat.id}`}
                          >
                            <TextInput
                              id={`cat-svc-price-${cat.id}`}
                              type="number"
                              min={0}
                              max={10000000}
                              step={1}
                              value={svcForm.price}
                              onFocus={() => onScrollTo?.(scrollTarget)}
                              onChange={(e) =>
                                updateCatServiceForm(cat.id, { price: e.target.value })
                              }
                            />
                          </Field>
                        </div>

                        <div className="studio-grid-2">
                          <Field
                            label={`Service description (${cat.name})`}
                            htmlFor={`cat-svc-desc-${cat.id}`}
                            hint={
                              svcDescErr ? (
                                <span className="studio-field-error" role="alert">
                                  {svcDescErr}
                                </span>
                              ) : undefined
                            }
                          >
                            <TextInput
                              id={`cat-svc-desc-${cat.id}`}
                              aria-invalid={Boolean(svcDescErr)}
                              placeholder="What this service delivers"
                              value={svcForm.desc}
                              onFocus={() => onScrollTo?.(scrollTarget)}
                              onChange={(e) =>
                                updateCatServiceForm(cat.id, { desc: e.target.value })
                              }
                            />
                          </Field>

                          <Field
                            label="Requires (optional)"
                            htmlFor={`cat-svc-req-${cat.id}`}
                          >
                            <Select
                              id={`cat-svc-req-${cat.id}`}
                              value=""
                              onFocus={() => onScrollTo?.(scrollTarget)}
                              onChange={(e) => {
                                const val = e.target.value;
                                if (val && !svcForm.requires.includes(val)) {
                                  updateCatServiceForm(cat.id, {
                                    requires: [...svcForm.requires, val],
                                  });
                                }
                              }}
                            >
                              <option value="">Add dependency...</option>
                              {Object.entries(allItems)
                                .filter(
                                  ([id]) =>
                                    id !== svcForm.editingId &&
                                    !svcForm.requires.includes(id),
                                )
                                .map(([id, item]) => (
                                  <option key={id} value={id}>
                                    {item.n} ({id})
                                  </option>
                                ))}
                            </Select>
                          </Field>
                        </div>

                        {svcForm.requires.length > 0 ? (
                          <div className="studio-chip-row">
                            {svcForm.requires.map((depId) => (
                              <PillButton
                                key={depId}
                                variant="outline"
                                size="sm"
                                onClick={() =>
                                  updateCatServiceForm(cat.id, {
                                    requires: svcForm.requires.filter(
                                      (id) => id !== depId,
                                    ),
                                  })
                                }
                              >
                                {allItems[depId]?.n || depId} ×
                              </PillButton>
                            ))}
                          </div>
                        ) : null}

                        <div className="studio-btn-group">
                          <PillButton type="submit" size="sm">
                            {svcForm.editingId
                              ? 'Update service'
                              : `Add service to ${cat.name}`}
                          </PillButton>
                          {svcForm.editingId ? (
                            <PillButton
                              variant="quiet"
                              size="sm"
                              onClick={() =>
                                setCatServiceForms((prev) => ({
                                  ...prev,
                                  [cat.id]: DEFAULT_CAT_SERVICE_FORM,
                                }))
                              }
                            >
                              Cancel
                            </PillButton>
                          ) : null}
                        </div>
                      </form>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      </Card>

      {/* Custom line items in built-in or custom groups */}
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
                  data-scroll-target={`item:${item.id}`}
                  onFocusCapture={() => onScrollTo?.(`item:${item.id}`)}
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
                hint={
                  customNameErr ? (
                    <span className="studio-field-error" role="alert">
                      {customNameErr}
                    </span>
                  ) : undefined
                }
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
                  {allGroupOptions.map((grp) => (
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
              hint={
                customDescErr ? (
                  <span className="studio-field-error" role="alert">
                    {customDescErr}
                  </span>
                ) : undefined
              }
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

      <Dialog
        open={Boolean(confirmDeleteCat)}
        onClose={() => setConfirmDeleteCat(null)}
        title="Delete category"
        subtitle={confirmDeleteCat?.name}
        actions={
          <>
            <PillButton
              variant="quiet"
              size="sm"
              onClick={() => setConfirmDeleteCat(null)}
            >
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              onClick={handleConfirmDeleteCategory}
            >
              Delete category
            </PillButton>
          </>
        }
      >
        {confirmDeleteCat ? (
          <p className="mg-sub">
            Delete category <strong>{confirmDeleteCat.name}</strong>? All services in this category will be deleted too.
          </p>
        ) : null}
      </Dialog>
    </div>
  );
}
