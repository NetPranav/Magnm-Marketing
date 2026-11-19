import { useCallback, useEffect, useRef, useState } from 'react';
import {
  PillButton,
  Row,
  RowList,
  Select,
  SheetHeader,
  StatusDot,
} from '@magnm/design/react';
import {
  createProposal,
  deleteProposal,
  duplicateProposal,
  finalizeProposal,
  formatRupees,
  getDefaultProposal,
  getPitchCatalog,
  getPitchVersion,
  listProposals,
  type CreateProposalInput,
  type FinalizeProposalInput,
  type PitchCatalog,
  type PitchProposal,
  type PitchProposalKind,
  type PitchVersionInfo,
} from '../../api/pitch';
import { postAgentChat } from '../../api/team';
import { useStudioEvents } from '../../events';
import { formatTimestamp } from '../../ui/format';
import {
  DeleteProposalDialog,
  DraftWithWriterDialog,
  DuplicateProposalDialog,
  NewProposalDialog,
} from './PitchDialogs';
import {
  formatAgentChip,
  formatKindLabel,
  mapProposalStatusState,
} from './pitchUtils';

export interface PitchListProps {
  onOpenProposal: (proposalId: string) => void;
  onPresentProposal?: (proposalId: string) => void;
}

/** Pitch section list (#/pitch): pinned default quotation, proposals, filters, version note, Writer drafting, and actions. */
export function PitchList({ onOpenProposal, onPresentProposal }: PitchListProps) {
  const [proposals, setProposals] = useState<PitchProposal[]>([]);
  const [defaultProposal, setDefaultProposal] = useState<PitchProposal | null>(null);
  const [catalog, setCatalog] = useState<PitchCatalog | null>(null);
  const [version, setVersion] = useState<PitchVersionInfo | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newKind, setNewKind] = useState<PitchProposalKind | null>(null);
  const [dupTarget, setDupTarget] = useState<PitchProposal | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PitchProposal | null>(null);
  const [writerOpen, setWriterOpen] = useState(false);

  const [defaultPdfRequested, setDefaultPdfRequested] = useState(false);
  const [defaultPdfExportRev, setDefaultPdfExportRev] = useState(0);
  const defaultPdfFrameRef = useRef<HTMLIFrameElement | null>(null);

  const loadList = useCallback(async () => {
    setError(null);
    try {
      const list = await listProposals({
        status: statusFilter || undefined,
        kind: kindFilter || undefined,
      });
      const defaultInList = list.find((p) => p.kind === 'default') || null;
      if (defaultInList) {
        setDefaultProposal((prev) => prev ?? defaultInList);
      }
      setProposals(list.filter((p) => p.kind !== 'default'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load proposals.');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, kindFilter]);

  const loadMeta = useCallback(async () => {
    const [catRes, verRes, defRes] = await Promise.allSettled([
      getPitchCatalog(),
      getPitchVersion(),
      getDefaultProposal(),
    ]);
    if (catRes.status === 'fulfilled') setCatalog(catRes.value);
    if (verRes.status === 'fulfilled') setVersion(verRes.value);
    if (defRes.status === 'fulfilled' && defRes.value && defRes.value.id) {
      setDefaultProposal(defRes.value);
    }
  }, []);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'pitch.updated') {
          void loadList();
          void loadMeta();
        }
      },
      [loadList, loadMeta],
    ),
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const msg = event.data as {
        type?: string;
        proposal_id?: string;
        result?: { path?: string };
      } | null;
      if (
        msg?.type === 'magnm:pitch-pdf-saved' &&
        defaultProposal &&
        msg.proposal_id === defaultProposal.id
      ) {
        setNotice(
          msg.result?.path
            ? `Saved default quotation PDF to ${msg.result.path}.`
            : 'Saved default quotation PDF.',
        );
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [defaultProposal]);

  const handleCreate = async (payload: CreateProposalInput) => {
    setBusy(true);
    try {
      const created = await createProposal(payload);
      setNewKind(null);
      setNotice(`Created ${created.slug}.`);
      onOpenProposal(created.id);
    } finally {
      setBusy(false);
    }
  };

  const handleFinalizeFromDialog = async (
    sourceId: string,
    overrides: FinalizeProposalInput,
  ) => {
    setBusy(true);
    try {
      const finalized = await finalizeProposal(sourceId, overrides);
      setNewKind(null);
      setNotice(`Created final quotation ${finalized.slug}.`);
      onOpenProposal(finalized.id);
    } finally {
      setBusy(false);
    }
  };

  const handleMakeFinal = async (prop: PitchProposal) => {
    setError(null);
    setBusy(true);
    try {
      const finalized = await finalizeProposal(prop.id, {});
      setNotice(`Created final quotation ${finalized.slug}.`);
      onOpenProposal(finalized.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create final quotation.');
    } finally {
      setBusy(false);
    }
  };

  const handleDuplicate = async (
    proposalId: string,
    slug: string,
    kind?: PitchProposalKind,
  ) => {
    setBusy(true);
    try {
      const created = await duplicateProposal(proposalId, slug, kind);
      setDupTarget(null);
      setNotice(`Duplicated to ${created.slug}.`);
      await loadList();
    } finally {
      setBusy(false);
    }
  };

  const handleArchive = async (proposal: PitchProposal) => {
    setError(null);
    setBusy(true);
    try {
      await deleteProposal(proposal.id, false);
      setNotice(`Archived ${proposal.slug}.`);
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not archive proposal.');
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmDelete = async (proposalId: string) => {
    setError(null);
    setBusy(true);
    try {
      await deleteProposal(proposalId, true);
      setDeleteTarget(null);
      setNotice('Proposal deleted.');
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete proposal.');
    } finally {
      setBusy(false);
    }
  };

  const handleSendWriterBrief = async (brief: string) => {
    setBusy(true);
    try {
      const prompt =
        `Use list_pitch_catalog and draft_proposal to create a proposal draft from this brief. ` +
        `Do not set or suggest prices.\n\nBrief: ${brief}`;
      const res = await postAgentChat('writer', prompt);
      setWriterOpen(false);
      setNotice(res.response ? 'Writer drafted a proposal.' : 'Sent brief to Writer.');
      await loadList();
    } finally {
      setBusy(false);
    }
  };

  const triggerDefaultPdf = () => {
    if (!defaultProposal) return;
    setError(null);
    const frameWin = defaultPdfFrameRef.current?.contentWindow as
      | { downloadPdf?: () => void }
      | null
      | undefined;
    if (defaultPdfRequested && frameWin && typeof frameWin.downloadPdf === 'function') {
      frameWin.downloadPdf();
      setNotice('Exported default quotation PDF.');
      return;
    }
    setDefaultPdfRequested(true);
    setDefaultPdfExportRev((rev) => rev + 1);
    setNotice('Preparing default quotation PDF...');
  };

  const shortSha = version?.ref || (version?.commit ? version.commit.slice(0, 7) : '');
  const defaultSha =
    defaultProposal?.pitch_version?.ref ||
    (defaultProposal?.pitch_version?.commit
      ? defaultProposal.pitch_version.commit.slice(0, 7)
      : '') ||
    shortSha;
  const reservedPresets = Object.keys(catalog?.presets || {});
  const showPinnedDefault =
    Boolean(defaultProposal) &&
    !kindFilter &&
    (!statusFilter || statusFilter === 'ready');

  return (
    <div className="studio-stack">
      <SheetHeader
        page={7}
        total={10}
        subtitle="Proposals"
        title="Pitch"
        lead="Create client proposals, quote forms, and final quotations, customize scope and pricing per client, present offline, and export PDFs."
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

      <div className="studio-section-bar">
        <div className="studio-filter-row">
          <Select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="ready">Ready</option>
            <option value="sent">Sent</option>
            <option value="archived">Archived</option>
          </Select>

          <Select
            aria-label="Filter by kind"
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
          >
            <option value="">All kinds</option>
            <option value="proposal">Proposal</option>
            <option value="quote_form">Quote form</option>
            <option value="final">Final quotation</option>
          </Select>
        </div>

        <div className="studio-btn-group">
          <PillButton size="sm" onClick={() => setNewKind('proposal')}>
            New proposal
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => setNewKind('quote_form')}>
            New quote form
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => setNewKind('final')}>
            New final quotation
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => setWriterOpen(true)}>
            Draft with Writer
          </PillButton>
        </div>
      </div>

      {shortSha ? (
        <div className="studio-mono">Pitch version {shortSha}</div>
      ) : null}

      {loading ? (
        <div className="mg-empty">Loading proposals...</div>
      ) : !showPinnedDefault && proposals.length === 0 ? (
        <div className="mg-empty">
          No proposals match the current filter. Create a new proposal, start from a Pitch preset, or ask Writer to draft one.
        </div>
      ) : (
        <RowList aria-label="Pitch proposals">
          {showPinnedDefault && defaultProposal ? (
            <Row
              key={defaultProposal.id}
              data-testid="pitch-default-row"
              title={
                <button
                  type="button"
                  className="studio-pitch-title-btn"
                  onClick={() => onOpenProposal(defaultProposal.id)}
                >
                  Default quotation
                </button>
              }
              description={
                <div className="studio-pitch-row-desc">
                  <span className="mg-chip studio-mono">Default</span>
                  {defaultSha ? (
                    <span className="studio-mono">Pitch version {defaultSha}</span>
                  ) : null}
                </div>
              }
              meta={
                <div className="studio-pitch-row-meta">
                  <StatusDot
                    state={mapProposalStatusState(defaultProposal.status)}
                    label={defaultProposal.status}
                  />
                  <span className="mg-num studio-pitch-total">
                    {formatRupees(defaultProposal.pricing_summary?.total ?? 0)}
                  </span>
                  <div className="studio-row-actions">
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() => onOpenProposal(defaultProposal.id)}
                    >
                      Open
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        onPresentProposal
                          ? onPresentProposal(defaultProposal.id)
                          : onOpenProposal(`${defaultProposal.id}/present`)
                      }
                    >
                      Present
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={triggerDefaultPdf}
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
                      variant="quiet"
                      size="sm"
                      disabled={busy}
                      onClick={() => setDupTarget(defaultProposal)}
                    >
                      Duplicate
                    </PillButton>
                  </div>
                </div>
              }
            />
          ) : null}

          {proposals.map((prop) => {
            const titleText =
              [prop.business_name, prop.client_name].filter(Boolean).join(' · ') ||
              prop.preset?.name ||
              prop.slug;
            const agentChip = formatAgentChip(prop.created_by);
            const totalText = formatRupees(prop.pricing_summary?.total ?? 0);
            const canFinalize = prop.kind === 'proposal' || prop.kind === 'quote_form';

            return (
              <Row
                key={prop.id}
                title={
                  <button
                    type="button"
                    className="studio-pitch-title-btn"
                    onClick={() => onOpenProposal(prop.id)}
                  >
                    {titleText}
                  </button>
                }
                description={
                  <div className="studio-pitch-row-desc">
                    <span className="studio-mono">{prop.slug}</span>
                    <span className="mg-chip">{formatKindLabel(prop.kind)}</span>
                    {agentChip ? <span className="mg-chip">{agentChip}</span> : null}
                    {prop.updated_at ? (
                      <span className="studio-mono">{formatTimestamp(prop.updated_at)}</span>
                    ) : null}
                  </div>
                }
                meta={
                  <div className="studio-pitch-row-meta">
                    <StatusDot
                      state={mapProposalStatusState(prop.status)}
                      label={prop.status}
                    />
                    <span className="mg-num studio-pitch-total">{totalText}</span>
                    <div className="studio-row-actions">
                      <PillButton
                        variant="outline"
                        size="sm"
                        onClick={() => onOpenProposal(prop.id)}
                      >
                        Open
                      </PillButton>
                      {canFinalize ? (
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => void handleMakeFinal(prop)}
                        >
                          Make final quotation
                        </PillButton>
                      ) : null}
                      <PillButton
                        variant="quiet"
                        size="sm"
                        disabled={busy}
                        onClick={() => setDupTarget(prop)}
                      >
                        Duplicate
                      </PillButton>
                      {prop.status !== 'archived' ? (
                        <PillButton
                          variant="quiet"
                          size="sm"
                          disabled={busy}
                          onClick={() => void handleArchive(prop)}
                        >
                          Archive
                        </PillButton>
                      ) : null}
                      <PillButton
                        variant="quiet"
                        size="sm"
                        disabled={busy}
                        onClick={() => setDeleteTarget(prop)}
                      >
                        Delete
                      </PillButton>
                    </div>
                  </div>
                }
              />
            );
          })}
        </RowList>
      )}

      {defaultPdfRequested && defaultProposal ? (
        <iframe
          ref={defaultPdfFrameRef}
          hidden
          title="Default quotation PDF export"
          src={`/pitch/?proposal=${encodeURIComponent(defaultProposal.id)}&mode=form&rev=${defaultPdfExportRev}`}
          onLoad={() => {
            const win = defaultPdfFrameRef.current?.contentWindow as
              | { downloadPdf?: () => void }
              | null
              | undefined;
            if (win && typeof win.downloadPdf === 'function') {
              win.downloadPdf();
              setNotice('Exported default quotation PDF.');
            }
          }}
        />
      ) : null}

      <NewProposalDialog
        open={newKind !== null}
        defaultKind={newKind ?? 'proposal'}
        catalog={catalog}
        proposals={proposals}
        submitting={busy}
        onClose={() => setNewKind(null)}
        onCreate={handleCreate}
        onFinalize={handleFinalizeFromDialog}
      />

      <DuplicateProposalDialog
        proposal={dupTarget}
        reservedPresets={reservedPresets}
        submitting={busy}
        onClose={() => setDupTarget(null)}
        onDuplicate={handleDuplicate}
      />

      <DeleteProposalDialog
        proposal={deleteTarget}
        submitting={busy}
        onClose={() => setDeleteTarget(null)}
        onConfirmDelete={handleConfirmDelete}
      />

      <DraftWithWriterDialog
        open={writerOpen}
        submitting={busy}
        onClose={() => setWriterOpen(false)}
        onSendBrief={handleSendWriterBrief}
      />
    </div>
  );
}
