import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BottomBar,
  Card,
  Checkbox,
  NumberedTabs,
  PillButton,
  Row,
  RowList,
  SheetHeader,
  StatusDot,
  type TabItem,
} from '@magnm/design/react';
import {
  formatRupees,
  getPitchCatalog,
  getPitchPreviewPayload,
  getProposal,
  listProposalPdfs,
  publishProposal,
  updateProposal,
  withdrawProposalLink,
  type PitchCatalog,
  type PitchDroppedItemChange,
  type PitchIncludedFromHidden,
  type PitchPdfRecord,
  type PitchPricingSummary,
  type PitchProposal,
  type PitchRuleViolation,
} from '../../api/pitch';
import { getAccount, type AccountState } from '../../api/account';
import { getPhoneStatus, sendToPhone, type PhoneStatus } from '../../api/phone';
import { postAgentChat } from '../../api/team';
import { useStudioEvents } from '../../events';
import { formatTimestamp } from '../../ui/format';
import { PitchDeliverablesTab } from './PitchDeliverablesTab';
import { PitchDetailsTab } from './PitchDetailsTab';
import { DraftWithWriterDialog, UnsavedChangesDialog, WithdrawLinkDialog } from './PitchDialogs';
import { PitchPlansTab } from './PitchPlansTab';
import { PitchPricesTab } from './PitchPricesTab';
import {
  FOLLOW_PREVIEW_STORAGE_KEY,
  buildEffectivePriceMap,
  buildLivePreviewPayload,
  extractRuleViolations,
  formatAgentChip,
  formatDroppedItemsNotice,
  formatKindLabel,
  formatRecurringTotalsLine,
  mapProposalStatusState,
  proposalToDraft,
  type PitchDraftState,
} from './pitchUtils';

type EditorTabId = 'details' | 'deliverables' | 'prices' | 'plans' | 'preview';
type LayoutMode = 'wide' | 'medium' | 'narrow';

function readStoredFollowPreview(): boolean {
  try {
    const raw = window.localStorage.getItem(FOLLOW_PREVIEW_STORAGE_KEY);
    if (raw === '0' || raw === 'false') return false;
    if (raw === '1' || raw === 'true') return true;
  } catch {
    // Ignore localStorage access errors in restricted browsers.
  }
  return true;
}

function writeStoredFollowPreview(enabled: boolean): void {
  try {
    window.localStorage.setItem(FOLLOW_PREVIEW_STORAGE_KEY, enabled ? 'true' : 'false');
  } catch {
    // Ignore localStorage write errors.
  }
}

function useEditorLayoutMode(): LayoutMode {
  const getMode = (): LayoutMode => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return 'wide';
    }
    if (window.matchMedia('(min-width: 1200px)').matches) return 'wide';
    if (window.matchMedia('(min-width: 900px)').matches) return 'medium';
    return 'narrow';
  };

  const [mode, setMode] = useState<LayoutMode>(getMode);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mqWide = window.matchMedia('(min-width: 1200px)');
    const mqMed = window.matchMedia('(min-width: 900px)');
    const update = () => setMode(getMode());
    update();
    if (typeof mqWide.addEventListener === 'function') {
      mqWide.addEventListener('change', update);
      mqMed.addEventListener('change', update);
      return () => {
        mqWide.removeEventListener('change', update);
        mqMed.removeEventListener('change', update);
      };
    }
    mqWide.addListener(update);
    mqMed.addListener(update);
    return () => {
      mqWide.removeListener(update);
      mqMed.removeListener(update);
    };
  }, []);

  return mode;
}

export interface PitchEditorProps {
  proposalId: string;
  onBackToList: () => void;
  onPresent: (proposalId: string) => void;
}

/** Proposal editor (#/pitch/<id>): details, deliverables, prices, live Pitch iframe, PDF export. */
export function PitchEditor({ proposalId, onBackToList, onPresent }: PitchEditorProps) {
  const layoutMode = useEditorLayoutMode();
  const [activeTab, setActiveTab] = useState<EditorTabId>('details');
  const [followPreview, setFollowPreview] = useState<boolean>(readStoredFollowPreview);

  const [proposal, setProposal] = useState<PitchProposal | null>(null);
  const [draft, setDraft] = useState<PitchDraftState | null>(null);
  const [catalog, setCatalog] = useState<PitchCatalog | null>(null);
  const [pdfs, setPdfs] = useState<PitchPdfRecord[]>([]);
  const [savedPdfPath, setSavedPdfPath] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [previewRev, setPreviewRev] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [droppedChanges, setDroppedChanges] = useState<PitchDroppedItemChange[]>([]);
  const [liveIncludedFromHidden, setLiveIncludedFromHidden] = useState<
    PitchIncludedFromHidden[] | undefined
  >(undefined);
  const [livePricingSummary, setLivePricingSummary] = useState<PitchPricingSummary | null>(
    null,
  );
  const [violations, setViolations] = useState<PitchRuleViolation[]>([]);

  const [pendingLeaveAction, setPendingLeaveAction] = useState<(() => void) | null>(null);
  const [writerOpen, setWriterOpen] = useState(false);
  const [writerBusy, setWriterBusy] = useState(false);
  const [account, setAccount] = useState<AccountState | null>(null);
  const [phoneStatus, setPhoneStatus] = useState<PhoneStatus | null>(null);
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [linkBusy, setLinkBusy] = useState(false);
  const [withdrawOpen, setWithdrawOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const formContainerRef = useRef<HTMLDivElement | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const priceDebounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const priceOnlyEditRef = useRef<boolean>(false);
  const pendingPriceItemRef = useRef<string | null>(null);
  const lastScrollPostAtRef = useRef<number>(0);
  const scrollThrottleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingScrollTargetRef = useRef<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [propRes, catRes, pdfRes] = await Promise.all([
        getProposal(proposalId),
        getPitchCatalog(),
        listProposalPdfs(proposalId),
      ]);
      setProposal(propRes);
      setDraft(proposalToDraft(propRes));
      setCatalog(catRes);
      setPdfs(Array.isArray(pdfRes?.pdfs) ? pdfRes.pdfs : []);
      setLiveIncludedFromHidden(propRes.included_from_hidden);
      setLivePricingSummary(null);
      setDroppedChanges([]);
      setDirty(false);
      setViolations([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load proposal.');
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  const refreshPdfs = useCallback(async () => {
    try {
      const res = await listProposalPdfs(proposalId);
      const list = Array.isArray(res?.pdfs) ? res.pdfs : [];
      setPdfs(list);
      if (list.length > 0) {
        setSavedPdfPath(list[0].path);
      }
    } catch {
      // Ignore PDF list refresh failures after export.
    }
  }, [proposalId]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const loadPhoneStatus = useCallback(async () => {
    try {
      const res = await getPhoneStatus();
      setPhoneStatus(res);
    } catch {
      setPhoneStatus(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getAccount()
      .then((res) => {
        if (!cancelled) setAccount(res);
      })
      .catch(() => {
        if (!cancelled) setAccount(null);
      });
    void loadPhoneStatus();
    return () => {
      cancelled = true;
    };
  }, [loadPhoneStatus]);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
    }, 3000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (layoutMode === 'wide' && activeTab === 'preview') {
      setActiveTab('details');
    }
  }, [layoutMode, activeTab]);

  useEffect(() => {
    if (draft && draft.kind !== 'final' && activeTab === 'plans') {
      setActiveTab('details');
    }
  }, [draft, activeTab]);

  // Warn browser tab close/reload when there are unsaved changes.
  useEffect(() => {
    if (!dirty) return undefined;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  // Listen for PDF saved postMessage from the Pitch iframe host adapter.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const msg = event.data as {
        type?: string;
        proposal_id?: string;
        result?: { path?: string };
      } | null;
      if (msg?.type === 'magnm:pitch-pdf-saved' && msg.proposal_id === proposalId) {
        if (msg.result?.path) {
          setSavedPdfPath(msg.result.path);
        }
        void refreshPdfs();
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [proposalId, refreshPdfs]);

  useStudioEvents(
    useCallback(
      (eventName, data) => {
        if (eventName === 'phone.updated') {
          void loadPhoneStatus();
          return;
        }
        if (eventName !== 'pitch.updated' || !data || typeof data !== 'object') return;
        const payload = data as { proposal_id?: string; what?: string; path?: string };
        if (payload.proposal_id === proposalId && payload.what === 'pdf_saved') {
          if (payload.path) setSavedPdfPath(payload.path);
          void refreshPdfs();
        }
      },
      [proposalId, refreshPdfs, loadPhoneStatus],
    ),
  );

  // Scroll sync postMessage helper
  const postScrollToIframe = useCallback((target: string) => {
    const win = iframeRef.current?.contentWindow;
    if (!win || typeof win.postMessage !== 'function') return;
    lastScrollPostAtRef.current = Date.now();
    win.postMessage(
      {
        type: 'magnm:pitch-scroll-to',
        target,
      },
      window.location.origin,
    );
  }, []);

  // Immediate scroll-to on focus when Follow in preview is enabled
  const handleFocusScrollTo = useCallback(
    (target: string) => {
      if (!followPreview || !target) return;
      if (scrollThrottleTimerRef.current) {
        clearTimeout(scrollThrottleTimerRef.current);
        scrollThrottleTimerRef.current = null;
      }
      pendingScrollTargetRef.current = null;
      postScrollToIframe(target);
    },
    [followPreview, postScrollToIframe],
  );

  // Throttled scroll-to (at most 1 per 200 ms) for IntersectionObserver scroll sync
  const handleIntersectionScrollTo = useCallback(
    (target: string) => {
      if (!followPreview || !target) return;
      const now = Date.now();
      const elapsed = now - lastScrollPostAtRef.current;
      if (elapsed >= 200) {
        if (scrollThrottleTimerRef.current) {
          clearTimeout(scrollThrottleTimerRef.current);
          scrollThrottleTimerRef.current = null;
        }
        pendingScrollTargetRef.current = null;
        postScrollToIframe(target);
        return;
      }
      pendingScrollTargetRef.current = target;
      if (!scrollThrottleTimerRef.current) {
        scrollThrottleTimerRef.current = setTimeout(() => {
          scrollThrottleTimerRef.current = null;
          const nextTarget = pendingScrollTargetRef.current;
          pendingScrollTargetRef.current = null;
          if (nextTarget) {
            postScrollToIframe(nextTarget);
          }
        }, Math.max(10, 200 - elapsed));
      }
    },
    [followPreview, postScrollToIframe],
  );

  useEffect(() => {
    return () => {
      if (scrollThrottleTimerRef.current) {
        clearTimeout(scrollThrottleTimerRef.current);
      }
    };
  }, []);

  // Observe form sections with [data-scroll-target] and scroll preview to topmost visible section.
  useEffect(() => {
    if (!followPreview || typeof IntersectionObserver !== 'function') return undefined;
    const container = formContainerRef.current;
    if (!container) return undefined;

    const elements = Array.from(
      container.querySelectorAll<HTMLElement>('[data-scroll-target]'),
    );
    if (elements.length === 0) return undefined;

    const visibleMap = new Map<Element, IntersectionObserverEntry>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visibleMap.set(entry.target, entry);
          } else {
            visibleMap.delete(entry.target);
          }
        }
        if (visibleMap.size === 0) return;
        let topmostEl: HTMLElement | null = null;
        let bestTop = Number.POSITIVE_INFINITY;
        for (const [el, entry] of visibleMap.entries()) {
          const top =
            entry.boundingClientRect && typeof entry.boundingClientRect.top === 'number'
              ? entry.boundingClientRect.top
              : (el as HTMLElement).getBoundingClientRect?.().top ?? 0;
          if (top < bestTop) {
            bestTop = top;
            topmostEl = el as HTMLElement;
          }
        }
        const target = topmostEl?.getAttribute('data-scroll-target');
        if (target) {
          handleIntersectionScrollTo(target);
        }
      },
      { threshold: [0.1, 0.5] },
    );

    for (const el of elements) {
      observer.observe(el);
    }

    return () => {
      observer.disconnect();
    };
  }, [
    followPreview,
    activeTab,
    draft?.hidden_sheets,
    draft?.hidden_about,
    draft?.custom_categories,
    handleIntersectionScrollTo,
  ]);

  // Live preview update via POST /api/pitch/preview_payload (debounced 300 ms) with local fallback.
  const pushLivePreview = useCallback(
    async (nextDraft: PitchDraftState) => {
      const localPayload = buildLivePreviewPayload(nextDraft, catalog);
      setLiveIncludedFromHidden(localPayload.included_from_hidden);
      try {
        const res = await getPitchPreviewPayload({
          id: proposalId,
          slug: nextDraft.slug,
          kind: nextDraft.kind,
          status: nextDraft.status,
          client_name: nextDraft.client_name,
          business_name: nextDraft.business_name,
          contact: nextDraft.contact,
          preset: {
            ...nextDraft.preset,
            slug: nextDraft.slug,
            name:
              nextDraft.preset.name ||
              nextDraft.business_name ||
              nextDraft.client_name ||
              nextDraft.slug,
          },
          price_overrides: nextDraft.price_overrides,
          custom_items: nextDraft.custom_items,
          hidden_sheets: nextDraft.hidden_sheets,
          hidden_about: nextDraft.hidden_about,
          custom_categories: nextDraft.custom_categories,
          ...(nextDraft.kind === 'final'
            ? {
                payment_plan: nextDraft.payment_plan,
                payment_note: nextDraft.payment_note,
                timeline: nextDraft.timeline,
                service_plans: nextDraft.service_plans,
              }
            : {}),
          notes: nextDraft.notes,
        });
        if (res && typeof res === 'object') {
          if (Array.isArray(res.included_from_hidden)) {
            setLiveIncludedFromHidden(res.included_from_hidden);
          }
          if (res.pricing_summary && typeof res.pricing_summary === 'object') {
            setLivePricingSummary(res.pricing_summary);
          }
          if (res.preset) {
            const win = iframeRef.current?.contentWindow;
            if (win && typeof win.postMessage === 'function') {
              win.postMessage(
                {
                  type: 'magnm:pitch-preview-update',
                  payload: res,
                },
                window.location.origin,
              );
            }
            return;
          }
        }
      } catch {
        // Fallback to local preview payload if network/mock is unavailable.
      }
      const win = iframeRef.current?.contentWindow;
      if (win && typeof win.postMessage === 'function') {
        win.postMessage(
          {
            type: 'magnm:pitch-preview-update',
            payload: localPayload,
          },
          window.location.origin,
        );
      }
    },
    [catalog, proposalId],
  );

  // Instant price-only live preview push (debounced 150 ms) + scroll-to item:<id> + refresh preview_payload
  const pushPriceOnlyPreview = useCallback(
    (nextDraft: PitchDraftState, itemId: string | null) => {
      const win = iframeRef.current?.contentWindow;
      if (win && typeof win.postMessage === 'function') {
        win.postMessage(
          {
            type: 'magnm:pitch-preview-update',
            payload: {
              prices: buildEffectivePriceMap(nextDraft, catalog),
              custom_items: nextDraft.custom_items,
            },
          },
          window.location.origin,
        );
      }
      if (itemId && followPreview) {
        postScrollToIframe(`item:${itemId}`);
      }
      void pushLivePreview(nextDraft);
    },
    [catalog, followPreview, postScrollToIframe, pushLivePreview],
  );

  useEffect(() => {
    if (!draft || !dirty) return undefined;

    if (priceOnlyEditRef.current) {
      priceOnlyEditRef.current = false;
      const targetItemId = pendingPriceItemRef.current;
      pendingPriceItemRef.current = null;
      if (priceDebounceTimerRef.current) {
        clearTimeout(priceDebounceTimerRef.current);
      }
      priceDebounceTimerRef.current = setTimeout(() => {
        pushPriceOnlyPreview(draft, targetItemId);
      }, 150);
      return () => {
        if (priceDebounceTimerRef.current) {
          clearTimeout(priceDebounceTimerRef.current);
        }
      };
    }

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      void pushLivePreview(draft);
    }, 300);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [draft, dirty, pushLivePreview, pushPriceOnlyPreview]);

  const handleDraftChange = useCallback(
    (updater: (prev: PitchDraftState) => PitchDraftState) => {
      priceOnlyEditRef.current = false;
      setDraft((prev) => (prev ? updater(prev) : prev));
      setDirty(true);
      setNotice(null);
      setDroppedChanges([]);
    },
    [],
  );

  const handlePriceDraftChange = useCallback(
    (itemId: string, updater: (prev: PitchDraftState) => PitchDraftState) => {
      priceOnlyEditRef.current = true;
      pendingPriceItemRef.current = itemId;
      setDraft((prev) => (prev ? updater(prev) : prev));
      setDirty(true);
      setNotice(null);
      setDroppedChanges([]);
    },
    [],
  );

  const handleToggleFollowPreview = (checked: boolean) => {
    setFollowPreview(checked);
    writeStoredFollowPreview(checked);
  };

  const switchTabAndScrollTop = (nextTabId: EditorTabId) => {
    setActiveTab(nextTabId);
    const colEl = formContainerRef.current;
    if (colEl && typeof colEl.scrollIntoView === 'function') {
      try {
        colEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch {
        colEl.scrollIntoView();
      }
    }
    if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') {
      try {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } catch {
        window.scrollTo(0, 0);
      }
    }
  };

  const handleSave = async () => {
    if (!draft || draft.kind === 'default') return;
    setSaving(true);
    setError(null);
    setNotice(null);
    setDroppedChanges([]);
    setViolations([]);
    try {
      const updated = await updateProposal(proposalId, {
        slug: draft.slug,
        kind: draft.kind,
        status: draft.status,
        client_name: draft.client_name,
        business_name: draft.business_name,
        contact: draft.contact,
        preset: {
          ...draft.preset,
          slug: draft.slug,
          name:
            draft.preset.name ||
            draft.business_name ||
            draft.client_name ||
            draft.slug,
        },
        price_overrides: draft.price_overrides,
        custom_items: draft.custom_items,
        hidden_sheets: draft.hidden_sheets,
        hidden_about: draft.hidden_about,
        custom_categories: draft.custom_categories,
        ...(draft.kind === 'final'
          ? {
              payment_plan: draft.payment_plan,
              payment_note: draft.payment_note,
              timeline: draft.timeline,
              service_plans: draft.service_plans,
            }
          : {}),
        notes: draft.notes,
      });
      setProposal(updated);
      setDraft(proposalToDraft(updated));
      setLiveIncludedFromHidden(updated.included_from_hidden);
      setLivePricingSummary(null);
      setDroppedChanges(Array.isArray(updated.changes) ? updated.changes : []);
      setDirty(false);
      setViolations([]);
      setPreviewRev((rev) => rev + 1);
      const dropText = formatDroppedItemsNotice(updated.changes);
      setNotice(dropText ? `Proposal saved. ${dropText}` : 'Proposal saved.');
    } catch (err) {
      const ruleErrors = extractRuleViolations(err);
      setViolations(ruleErrors);
      setError(err instanceof Error ? err.message : 'Failed to save proposal.');
    } finally {
      setSaving(false);
    }
  };

  const guardLeave = (action: () => void) => {
    if (dirty) {
      setPendingLeaveAction(() => action);
      return;
    }
    action();
  };

  const handleDownloadPdf = async () => {
    setError(null);
    const win = iframeRef.current?.contentWindow as { downloadPdf?: () => void } | null;
    if (!win || typeof win.downloadPdf !== 'function') {
      setError('Pitch preview is still loading. Try again in a moment.');
      return;
    }
    win.downloadPdf();
    await refreshPdfs();
  };

  const handleSendWriterBrief = async (brief: string) => {
    setWriterBusy(true);
    try {
      const prompt =
        `Use list_pitch_catalog and draft_proposal to create a proposal draft from this brief. ` +
        `Do not set or suggest prices.\n\nBrief: ${brief}`;
      await postAgentChat('writer', prompt);
      setWriterOpen(false);
      setNotice('Sent brief to Writer. New draft will appear in Pitch list.');
    } finally {
      setWriterBusy(false);
    }
  };

  const hasPairedPhone = (phoneStatus?.devices ?? []).length > 0;

  const handleSendToPhone = async () => {
    if (!proposal || !hasPairedPhone) return;
    setError(null);
    setPhoneBusy(true);
    try {
      await sendToPhone('proposal', proposal.id);
      showToast('Sent to your phone');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send proposal to your phone.');
    } finally {
      setPhoneBusy(false);
    }
  };

  const canShareLinks = Boolean(
    account?.configured && account?.signed_in && !account?.deactivated && account?.user,
  );

  const handleCopyLink = async () => {
    if (!proposal || !canShareLinks) return;
    setError(null);
    setLinkBusy(true);
    try {
      const res = await publishProposal(proposal.id);
      setProposal((prev) =>
        prev ? { ...prev, link_status: 'live', published_at: res.published_at } : prev,
      );
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(res.url);
      }
      showToast('Link copied');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not publish link.');
    } finally {
      setLinkBusy(false);
    }
  };

  const handleConfirmWithdraw = async (id: string) => {
    setError(null);
    setLinkBusy(true);
    try {
      await withdrawProposalLink(id);
      setProposal((prev) => (prev ? { ...prev, link_status: 'withdrawn' } : prev));
      setWithdrawOpen(false);
      setNotice('Link withdrawn.');
    } catch (err) {
      setWithdrawOpen(false);
      setError(err instanceof Error ? err.message : 'Could not withdraw link.');
    } finally {
      setLinkBusy(false);
    }
  };

  const reservedPresets = useMemo(
    () => Object.keys(catalog?.presets || {}),
    [catalog],
  );

  const hasUnsavedPricingChanges = useMemo(() => {
    if (!dirty || !proposal || !draft) return false;
    const savedDraft = proposalToDraft(proposal);
    return (
      JSON.stringify({
        price_overrides: draft.price_overrides,
        initialSelected: draft.preset.initialSelected,
        sections: draft.preset.sections,
        custom_items: draft.custom_items,
        hidden_sheets: draft.hidden_sheets,
        custom_categories: draft.custom_categories,
        service_plans: draft.service_plans,
      }) !==
      JSON.stringify({
        price_overrides: savedDraft.price_overrides,
        initialSelected: savedDraft.preset.initialSelected,
        sections: savedDraft.preset.sections,
        custom_items: savedDraft.custom_items,
        hidden_sheets: savedDraft.hidden_sheets,
        custom_categories: savedDraft.custom_categories,
        service_plans: savedDraft.service_plans,
      })
    );
  }, [dirty, proposal, draft]);

  if (loading) {
    return (
      <div className="studio-stack">
        <SheetHeader page={7} total={10} subtitle="Pitch" title="Loading..." />
        <div className="mg-empty">Loading proposal...</div>
      </div>
    );
  }

  if (!proposal || !draft) {
    return (
      <div className="studio-stack">
        <SheetHeader page={7} total={10} subtitle="Pitch" title="Proposal not found" />
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}
        <div>
          <PillButton variant="outline" size="sm" onClick={onBackToList}>
            Back to Pitch
          </PillButton>
        </div>
      </div>
    );
  }

  const isDefault = draft.kind === 'default';
  const isFinal = draft.kind === 'final';
  const iframeMode = draft.kind === 'quote_form' || isDefault ? 'form' : 'preview';
  const iframeSrc = `/pitch/?proposal=${encodeURIComponent(proposal.id)}&mode=${iframeMode}&rev=${previewRev}`;
  const agentChip = formatAgentChip(proposal.created_by);
  const warnings = proposal.warnings || [];
  const headerTitle = isDefault
    ? 'Default quotation'
    : [draft.business_name, draft.client_name].filter(Boolean).join(' · ') ||
      draft.preset.name ||
      draft.slug;

  const formTabs: TabItem[] = [
    { id: 'details', label: 'Details' },
    { id: 'deliverables', label: isFinal ? 'Services' : 'Deliverables' },
    { id: 'prices', label: 'Prices' },
    ...(isFinal ? [{ id: 'plans', label: 'Plans' }] : []),
  ];
  const tabs: TabItem[] =
    layoutMode === 'wide' ? formTabs : [...formTabs, { id: 'preview', label: 'Preview' }];

  const currentTabIdx = tabs.findIndex((t) => t.id === activeTab);
  const prevTab = currentTabIdx > 0 ? tabs[currentTabIdx - 1] : null;
  const nextTab =
    currentTabIdx >= 0 && currentTabIdx < tabs.length - 1 ? tabs[currentTabIdx + 1] : null;

  const activePricingSummary =
    hasUnsavedPricingChanges && livePricingSummary
      ? livePricingSummary
      : proposal.pricing_summary;
  const recurringLine = formatRecurringTotalsLine(activePricingSummary);
  const bottomBarCaption = hasUnsavedPricingChanges
    ? 'Total (unsaved)'
    : isFinal
      ? 'Total (one-time)'
      : 'Total';

  const formContent = (
    <div ref={formContainerRef} className="studio-stack-sm">
      <div
        className="studio-pitch-tabs-bar studio-pitch-tabs-sticky"
        data-testid="pitch-sticky-tabs"
      >
        <NumberedTabs
          items={tabs}
          current={activeTab}
          onSelect={(id) => setActiveTab(id as EditorTabId)}
          ariaLabel="Proposal editor tabs"
        />
        <Checkbox
          label="Follow in preview"
          checked={followPreview}
          onChange={(e) => handleToggleFollowPreview(e.target.checked)}
        />
      </div>

      {activeTab === 'details' ? (
        <PitchDetailsTab
          draft={draft}
          catalog={catalog}
          reservedPresets={reservedPresets}
          violations={violations}
          onChange={handleDraftChange}
          onScrollTo={handleFocusScrollTo}
        />
      ) : null}

      {activeTab === 'deliverables' ? (
        <PitchDeliverablesTab
          draft={draft}
          catalog={catalog}
          includedFromHidden={liveIncludedFromHidden}
          violations={violations}
          onChange={handleDraftChange}
          onScrollTo={handleFocusScrollTo}
        />
      ) : null}

      {activeTab === 'prices' ? (
        <PitchPricesTab
          draft={draft}
          catalog={catalog}
          pricingSummary={proposal.pricing_summary}
          dirty={dirty}
          violations={violations}
          onChange={handleDraftChange}
          onPriceChange={handlePriceDraftChange}
          onScrollTo={handleFocusScrollTo}
        />
      ) : null}

      {activeTab === 'plans' && isFinal ? (
        <PitchPlansTab
          draft={draft}
          catalog={catalog}
          pricingSummary={proposal.pricing_summary}
          dirty={dirty}
          violations={violations}
          onChange={handleDraftChange}
          onScrollTo={handleFocusScrollTo}
        />
      ) : null}

      {activeTab !== 'preview' ? (
        <nav
          className="studio-pitch-tab-pager"
          aria-label="Section navigation"
        >
          <div>
            {prevTab ? (
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => switchTabAndScrollTop(prevTab.id as EditorTabId)}
              >
                Previous: {prevTab.label}
              </PillButton>
            ) : null}
          </div>
          <div>
            {nextTab ? (
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => switchTabAndScrollTop(nextTab.id as EditorTabId)}
              >
                Next: {nextTab.label}
              </PillButton>
            ) : null}
          </div>
        </nav>
      ) : null}
    </div>
  );

  const previewVisible = isDefault
    ? true
    : layoutMode === 'wide'
      ? true
      : activeTab === 'preview';

  const previewPane = (
    <div
      className="studio-pitch-preview-pane"
      hidden={!previewVisible}
      aria-hidden={!previewVisible}
    >
      <div className="studio-section-bar">
        <span className="mg-label">
          {isDefault ? 'Read-only Pitch preview' : 'Live Pitch preview'}
        </span>
        <span className="studio-mono">
          {isDefault
            ? 'Read-only · Pitch default'
            : dirty
              ? 'Draft preview · save to persist'
              : `Saved · ${iframeMode}`}
        </span>
      </div>
      <div className="studio-pitch-iframe-frame">
        <iframe
          ref={iframeRef}
          title={`Pitch preview for ${draft.slug}`}
          src={iframeSrc}
          className="studio-pitch-iframe"
          onLoad={() => {
            if (!isDefault && dirty && draft) {
              void pushLivePreview(draft);
            }
          }}
        />
      </div>
    </div>
  );

  return (
    <div className="studio-stack studio-pitch-editor-root">
      <div className="studio-section-bar">
        <div className="studio-btn-group">
          <PillButton
            variant="outline"
            size="sm"
            onClick={() => guardLeave(onBackToList)}
          >
            Back to Pitch
          </PillButton>
          <span className="studio-mono">{draft.slug}</span>
          <span className="mg-chip">{formatKindLabel(draft.kind)}</span>
          {agentChip ? <span className="mg-chip">{agentChip}</span> : null}
        </div>

        {!isDefault ? (
          <div className="studio-btn-group">
            {dirty ? <span className="studio-mono">Unsaved changes</span> : null}
            <PillButton
              variant="outline"
              size="sm"
              onClick={() => setWriterOpen(true)}
            >
              Draft with Writer
            </PillButton>
            <PillButton
              size="sm"
              disabled={saving}
              onClick={() => void handleSave()}
            >
              {saving ? 'Saving...' : 'Save'}
            </PillButton>
          </div>
        ) : null}
      </div>

      <SheetHeader
        page={7}
        total={10}
        subtitle={
          <span className="studio-review-header-meta">
            <span>{formatKindLabel(draft.kind)}</span>
            <span>·</span>
            <StatusDot
              state={mapProposalStatusState(draft.status)}
              label={draft.status}
            />
          </span>
        }
        title={headerTitle}
      />

      {isDefault ? (
        <div className="mg-notice" role="note">
          Default quotation (read-only). Duplicate it as a proposal or quote form from the Pitch list to customize.
        </div>
      ) : null}

      {isFinal ? (
        <div className="mg-notice" role="note">
          Final quotation: the client sees only these services, ticked and locked.
        </div>
      ) : null}

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="mg-notice" role="status">
          {notice}
        </div>
      ) : null}

      {droppedChanges.length > 0 ? (
        <div className="mg-notice" role="status" aria-label="Dropped items from hidden sheets">
          <strong>Items dropped from removed sheets:</strong>{' '}
          {droppedChanges.map((c) => c.message).join(' · ')}
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div className="mg-notice" role="status" aria-label="Product rule warnings">
          <strong>Product rule notice:</strong>{' '}
          {warnings.map((w) => w.message).join(' ')}
        </div>
      ) : null}

      {isDefault ? (
        <div className="studio-pitch-full" data-testid="pitch-default-readonly">
          {previewPane}
        </div>
      ) : layoutMode === 'wide' ? (
        <div
          className="studio-pitch-split"
          data-testid="pitch-wide-layout"
        >
          <div className="studio-pitch-form-col">{formContent}</div>
          <div
            className="studio-pitch-preview-col"
            hidden={!previewVisible}
            aria-hidden={!previewVisible}
            data-testid="pitch-side-preview"
          >
            {previewPane}
          </div>
        </div>
      ) : (
        <div className="studio-stack-sm" data-testid="pitch-tabbed-layout">
          {formContent}
          {previewPane}
        </div>
      )}

      {(savedPdfPath || pdfs.length > 0) ? (
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-section-bar">
              <span className="mg-label">Saved PDF exports</span>
              {savedPdfPath ? (
                <span className="studio-mono" role="status">
                  Saved to {savedPdfPath}
                </span>
              ) : null}
            </div>
            {pdfs.length > 0 ? (
              <RowList aria-label="Saved proposal PDFs">
                {pdfs.map((pdf) => (
                  <Row
                    key={pdf.filename}
                    title={pdf.filename}
                    description={<span className="studio-mono">{pdf.path}</span>}
                    meta={
                      <div className="studio-row-actions">
                        <span className="studio-mono">
                          {formatTimestamp(pdf.created_at)}
                        </span>
                        <a
                          className="studio-inline-link"
                          href={pdf.url}
                          download={pdf.filename}
                        >
                          Download
                        </a>
                      </div>
                    }
                  />
                ))}
              </RowList>
            ) : null}
          </div>
        </Card>
      ) : null}

      <BottomBar
        caption={bottomBarCaption}
        value={
          <>
            <span>{formatRupees(activePricingSummary.total)}</span>
            {recurringLine ? (
              <span className="studio-mono studio-pitch-bar-recurring">
                {recurringLine}
              </span>
            ) : null}
          </>
        }
      >
        <PillButton
          variant="outline"
          size="sm"
          onClick={() => guardLeave(() => onPresent(proposal.id))}
        >
          Present
        </PillButton>
        <PillButton
          size="sm"
          onClick={() => void handleDownloadPdf()}
        >
          Download PDF
        </PillButton>
        {proposal.link_status === 'live' ? (
          <span className="mg-chip">Link live</span>
        ) : proposal.link_status === 'withdrawn' ? (
          <span className="mg-chip">Link withdrawn</span>
        ) : null}
        <PillButton
          variant="outline"
          size="sm"
          disabled={!canShareLinks || linkBusy}
          title={canShareLinks ? undefined : 'Sign in under Settings, Account to share links'}
          onClick={() => void handleCopyLink()}
        >
          Copy link
        </PillButton>
        {proposal.link_status === 'live' ? (
          <PillButton
            variant="quiet"
            size="sm"
            disabled={!canShareLinks || linkBusy}
            onClick={() => setWithdrawOpen(true)}
          >
            Withdraw link
          </PillButton>
        ) : null}
        <PillButton
          variant="outline"
          size="sm"
          disabled={!hasPairedPhone || phoneBusy}
          title={hasPairedPhone ? undefined : 'Pair a phone under Settings, Phone first'}
          onClick={() => void handleSendToPhone()}
        >
          {phoneBusy ? 'Sending...' : 'Send to my phone'}
        </PillButton>
      </BottomBar>

      {toast ? (
        <div className="studio-toast" role="status" aria-live="polite">
          {toast}
        </div>
      ) : null}

      <WithdrawLinkDialog
        proposal={withdrawOpen ? proposal : null}
        submitting={linkBusy}
        onClose={() => setWithdrawOpen(false)}
        onConfirmWithdraw={handleConfirmWithdraw}
      />

      <UnsavedChangesDialog
        open={pendingLeaveAction !== null}
        onCancel={() => setPendingLeaveAction(null)}
        onDiscard={() => {
          const fn = pendingLeaveAction;
          setPendingLeaveAction(null);
          setDirty(false);
          fn?.();
        }}
      />

      <DraftWithWriterDialog
        open={writerOpen}
        submitting={writerBusy}
        onClose={() => setWriterOpen(false)}
        onSendBrief={handleSendWriterBrief}
      />
    </div>
  );
}
