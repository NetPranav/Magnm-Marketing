import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BottomBar,
  Card,
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
  getProposal,
  listProposalPdfs,
  updateProposal,
  type PitchCatalog,
  type PitchPdfRecord,
  type PitchProposal,
  type PitchRuleViolation,
} from '../../api/pitch';
import { postAgentChat } from '../../api/team';
import { useStudioEvents } from '../../events';
import { formatTimestamp } from '../../ui/format';
import { PitchDeliverablesTab } from './PitchDeliverablesTab';
import { PitchDetailsTab } from './PitchDetailsTab';
import { DraftWithWriterDialog, UnsavedChangesDialog } from './PitchDialogs';
import { PitchPricesTab } from './PitchPricesTab';
import {
  buildLivePreviewPayload,
  extractRuleViolations,
  formatAgentChip,
  formatKindLabel,
  mapProposalStatusState,
  proposalToDraft,
  type PitchDraftState,
} from './pitchUtils';

type EditorTabId = 'details' | 'deliverables' | 'prices' | 'preview';
type LayoutMode = 'wide' | 'medium' | 'narrow';

const FORM_TABS: TabItem[] = [
  { id: 'details', label: 'Details' },
  { id: 'deliverables', label: 'Deliverables' },
  { id: 'prices', label: 'Prices' },
];

const ALL_TABS: TabItem[] = [
  ...FORM_TABS,
  { id: 'preview', label: 'Preview' },
];

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
  const [showPricesPreview, setShowPricesPreview] = useState(false);

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
  const [violations, setViolations] = useState<PitchRuleViolation[]>([]);

  const [pendingLeaveAction, setPendingLeaveAction] = useState<(() => void) | null>(null);
  const [writerOpen, setWriterOpen] = useState(false);
  const [writerBusy, setWriterBusy] = useState(false);

  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  useEffect(() => {
    if (layoutMode === 'wide' && activeTab === 'preview') {
      setActiveTab('details');
    }
  }, [layoutMode, activeTab]);

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
        if (eventName !== 'pitch.updated' || !data || typeof data !== 'object') return;
        const payload = data as { proposal_id?: string; what?: string; path?: string };
        if (payload.proposal_id === proposalId && payload.what === 'pdf_saved') {
          if (payload.path) setSavedPdfPath(payload.path);
          void refreshPdfs();
        }
      },
      [proposalId, refreshPdfs],
    ),
  );

  // Debounced live preview push (at most once per second while typing).
  const pushLivePreview = useCallback(
    (nextDraft: PitchDraftState) => {
      const win = iframeRef.current?.contentWindow;
      if (!win || typeof win.postMessage !== 'function') return;
      win.postMessage(
        {
          type: 'magnm:pitch-preview-update',
          payload: buildLivePreviewPayload(nextDraft, catalog),
        },
        window.location.origin,
      );
    },
    [catalog],
  );

  useEffect(() => {
    if (!draft || !dirty) return undefined;
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      pushLivePreview(draft);
    }, 1000);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [draft, dirty, pushLivePreview]);

  const handleDraftChange = useCallback(
    (updater: (prev: PitchDraftState) => PitchDraftState) => {
      setDraft((prev) => (prev ? updater(prev) : prev));
      setDirty(true);
      setNotice(null);
    },
    [],
  );

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
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
        notes: draft.notes,
      });
      setProposal(updated);
      setDraft(proposalToDraft(updated));
      setDirty(false);
      setViolations([]);
      setPreviewRev((rev) => rev + 1);
      setNotice('Proposal saved.');
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

  const reservedPresets = useMemo(
    () => Object.keys(catalog?.presets || {}),
    [catalog],
  );

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

  const iframeMode = draft.kind === 'quote_form' ? 'form' : 'preview';
  const iframeSrc = `/pitch/?proposal=${encodeURIComponent(proposal.id)}&mode=${iframeMode}&rev=${previewRev}`;
  const agentChip = formatAgentChip(proposal.created_by);
  const warnings = proposal.warnings || [];
  const headerTitle =
    [draft.business_name, draft.client_name].filter(Boolean).join(' · ') ||
    draft.preset.name ||
    draft.slug;

  const tabs = layoutMode === 'wide' ? FORM_TABS : ALL_TABS;
  const isPricesFullWidth = layoutMode === 'wide' && activeTab === 'prices';

  const formContent = (
    <div className="studio-stack-sm">
      <div className="studio-pitch-tabs-bar">
        <NumberedTabs
          items={tabs}
          current={activeTab}
          onSelect={(id) => setActiveTab(id as EditorTabId)}
          ariaLabel="Proposal editor tabs"
        />
        {isPricesFullWidth ? (
          <PillButton
            variant="outline"
            size="sm"
            aria-pressed={showPricesPreview}
            onClick={() => setShowPricesPreview((prev) => !prev)}
          >
            {showPricesPreview ? 'Hide preview' : 'Show preview'}
          </PillButton>
        ) : null}
      </div>

      {activeTab === 'details' ? (
        <PitchDetailsTab
          draft={draft}
          reservedPresets={reservedPresets}
          violations={violations}
          onChange={handleDraftChange}
        />
      ) : null}

      {activeTab === 'deliverables' ? (
        <PitchDeliverablesTab
          draft={draft}
          catalog={catalog}
          violations={violations}
          onChange={handleDraftChange}
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
        />
      ) : null}
    </div>
  );

  const previewVisible =
    layoutMode === 'wide'
      ? activeTab !== 'prices' || showPricesPreview
      : activeTab === 'preview';
  const previewPane = (
    <div
      className="studio-pitch-preview-pane"
      hidden={!previewVisible}
      aria-hidden={!previewVisible}
    >
      <div className="studio-section-bar">
        <span className="mg-label">Live Pitch preview</span>
        <span className="studio-mono">
          {dirty ? 'Draft preview · save to persist' : `Saved · ${iframeMode}`}
        </span>
      </div>
      <div className="studio-pitch-iframe-frame">
        <iframe
          ref={iframeRef}
          title={`Pitch preview for ${draft.slug}`}
          src={iframeSrc}
          className="studio-pitch-iframe"
          onLoad={() => {
            if (dirty && draft) {
              pushLivePreview(draft);
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

      {warnings.length > 0 ? (
        <div className="mg-notice" role="status" aria-label="Product rule warnings">
          <strong>Product rule notice:</strong>{' '}
          {warnings.map((w) => w.message).join(' ')}
        </div>
      ) : null}

      {layoutMode === 'wide' ? (
        <div
          className={isPricesFullWidth ? 'studio-pitch-full' : 'studio-pitch-split'}
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
        caption="Total"
        value={formatRupees(proposal.pricing_summary.total)}
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
        <PillButton
          variant="outline"
          size="sm"
          disabled
          title="Client links need the team connection (Phase 4)"
        >
          Copy link
        </PillButton>
        <PillButton
          variant="outline"
          size="sm"
          disabled
          title="Available after phone pairing (Phase 4)"
        >
          Send to my phone
        </PillButton>
      </BottomBar>

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
