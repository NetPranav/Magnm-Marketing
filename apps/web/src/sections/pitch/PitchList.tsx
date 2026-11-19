import { useCallback, useEffect, useState } from 'react';
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
  formatRupees,
  getPitchCatalog,
  getPitchVersion,
  listProposals,
  type CreateProposalInput,
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
}

/** Pitch section list (#/pitch): proposals, filters, version note, Writer drafting, and actions. */
export function PitchList({ onOpenProposal }: PitchListProps) {
  const [proposals, setProposals] = useState<PitchProposal[]>([]);
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

  const loadList = useCallback(async () => {
    setError(null);
    try {
      const list = await listProposals({
        status: statusFilter || undefined,
        kind: kindFilter || undefined,
      });
      setProposals(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load proposals.');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, kindFilter]);

  const loadMeta = useCallback(async () => {
    const [catRes, verRes] = await Promise.allSettled([getPitchCatalog(), getPitchVersion()]);
    if (catRes.status === 'fulfilled') setCatalog(catRes.value);
    if (verRes.status === 'fulfilled') setVersion(verRes.value);
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
        }
      },
      [loadList],
    ),
  );

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

  const handleDuplicate = async (proposalId: string, slug: string) => {
    setBusy(true);
    try {
      const created = await duplicateProposal(proposalId, slug);
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

  const shortSha = version?.ref || (version?.commit ? version.commit.slice(0, 7) : '');
  const reservedPresets = Object.keys(catalog?.presets || {});

  return (
    <div className="studio-stack">
      <SheetHeader
        page={7}
        total={10}
        subtitle="Proposals"
        title="Pitch"
        lead="Create client proposals and quote forms, customize scope and pricing per client, present offline, and export PDFs."
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
        <div className="studio-btn-group">
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
          </Select>
        </div>

        <div className="studio-btn-group">
          <PillButton size="sm" onClick={() => setNewKind('proposal')}>
            New proposal
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => setNewKind('quote_form')}>
            New quote form
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
      ) : proposals.length === 0 ? (
        <div className="mg-empty">
          No proposals match the current filter. Create a new proposal, start from a Pitch preset, or ask Writer to draft one.
        </div>
      ) : (
        <RowList aria-label="Pitch proposals">
          {proposals.map((prop) => {
            const titleText =
              [prop.business_name, prop.client_name].filter(Boolean).join(' · ') ||
              prop.preset?.name ||
              prop.slug;
            const agentChip = formatAgentChip(prop.created_by);
            const totalText = formatRupees(prop.pricing_summary?.total ?? 0);

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

      <NewProposalDialog
        open={newKind !== null}
        defaultKind={newKind ?? 'proposal'}
        catalog={catalog}
        proposals={proposals}
        submitting={busy}
        onClose={() => setNewKind(null)}
        onCreate={handleCreate}
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
