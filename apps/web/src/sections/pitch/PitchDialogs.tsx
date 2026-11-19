import { useEffect, useState } from 'react';
import {
  Checkbox,
  Dialog,
  Field,
  PillButton,
  Select,
  TextArea,
  TextInput,
} from '@magnm/design/react';
import type {
  CreateProposalInput,
  FinalizeProposalInput,
  PitchCatalog,
  PitchProposal,
  PitchProposalKind,
  PitchRuleViolation,
} from '../../api/pitch';
import { extractRuleViolations, validatePitchText, validateSlug } from './pitchUtils';

export type NewProposalSource = 'blank' | 'preset' | 'duplicate' | 'finalize';

export interface NewProposalDialogProps {
  open: boolean;
  defaultKind: PitchProposalKind;
  catalog: PitchCatalog | null;
  proposals: PitchProposal[];
  submitting: boolean;
  onClose: () => void;
  onCreate: (payload: CreateProposalInput) => Promise<void>;
  onFinalize?: (sourceId: string, overrides: FinalizeProposalInput) => Promise<void>;
}

/** Modal dialog for creating a blank proposal, quote form, final quotation, preset-based, or duplicate/finalize. */
export function NewProposalDialog({
  open,
  defaultKind,
  catalog,
  proposals,
  submitting,
  onClose,
  onCreate,
  onFinalize,
}: NewProposalDialogProps) {
  const presetEntries = Object.entries(catalog?.presets || {});
  const catalogEntries = Object.entries(catalog?.items || {});
  const finalizableProposals = proposals.filter(
    (p) => p.kind === 'proposal' || p.kind === 'quote_form',
  );
  const [source, setSource] = useState<NewProposalSource>('blank');
  const [presetSlug, setPresetSlug] = useState<string>('urban-company');
  const [duplicateId, setDuplicateId] = useState<string>('');
  const [initialServices, setInitialServices] = useState<string[]>([]);
  const [slug, setSlug] = useState('');
  const [clientName, setClientName] = useState('');
  const [businessName, setBusinessName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [violations, setViolations] = useState<PitchRuleViolation[]>([]);

  useEffect(() => {
    if (!open) return;
    setSource('blank');
    const nonGeneral = presetEntries.find(([k]) => k !== 'general')?.[0] || presetEntries[0]?.[0] || 'general';
    setPresetSlug(nonGeneral);
    setDuplicateId(
      defaultKind === 'final'
        ? finalizableProposals[0]?.id || ''
        : proposals[0]?.id || '',
    );
    setInitialServices([]);
    setSlug('');
    setClientName('');
    setBusinessName('');
    setError(null);
    setViolations([]);
  }, [open, defaultKind]);

  const reservedPresets = Object.keys(catalog?.presets || {});
  const liveSlugError = slug ? validateSlug(slug, reservedPresets) : null;
  const serverSlugViolation = violations.find((v) => v.field === 'slug')?.message;
  const clientNameError =
    violations.find((v) => v.field === 'client_name')?.message || validatePitchText(clientName);
  const businessNameError =
    violations.find((v) => v.field === 'business_name')?.message || validatePitchText(businessName);

  const toggleInitialService = (itemId: string, checked: boolean) => {
    setInitialServices((prev) =>
      checked ? (prev.includes(itemId) ? prev : [...prev, itemId]) : prev.filter((id) => id !== itemId),
    );
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const slugCheck = validateSlug(slug, reservedPresets);
    if (slugCheck) {
      setError(slugCheck);
      return;
    }

    if (
      defaultKind === 'final' &&
      source === 'blank' &&
      catalogEntries.length > 0 &&
      initialServices.length === 0
    ) {
      setError('Select at least one agreed service for a final quotation.');
      return;
    }

    setError(null);
    setViolations([]);
    try {
      if (defaultKind === 'final' && source === 'finalize' && duplicateId && onFinalize) {
        await onFinalize(duplicateId, {
          slug: slug.trim(),
          client_name: clientName.trim() || undefined,
          business_name: businessName.trim() || undefined,
        });
        return;
      }

      const payload: CreateProposalInput = {
        slug: slug.trim(),
        kind: defaultKind,
        client_name: clientName.trim() || undefined,
        business_name: businessName.trim() || undefined,
      };

      if (defaultKind === 'final' && source === 'blank') {
        payload.preset = { initialSelected: initialServices };
      } else if ((defaultKind === 'proposal' || defaultKind === 'final') && source === 'preset' && presetSlug) {
        payload.from_preset = presetSlug;
      } else if (defaultKind === 'proposal' && source === 'duplicate' && duplicateId) {
        payload.duplicate_of = duplicateId;
      }

      await onCreate(payload);
    } catch (err) {
      const list = extractRuleViolations(err);
      setViolations(list);
      setError(err instanceof Error ? err.message : 'Could not create proposal.');
    }
  };

  const title =
    defaultKind === 'quote_form'
      ? 'New quote form'
      : defaultKind === 'final'
        ? 'New final quotation'
        : 'New proposal';
  const subtitle =
    defaultKind === 'quote_form'
      ? "Starts with Pitch's default selection and layout"
      : defaultKind === 'final'
        ? 'Start blank, from a Pitch preset, or finalize an existing proposal or quote form'
        : 'Start blank, from a Pitch preset, or duplicate an existing proposal';
  const submitLabel =
    defaultKind === 'quote_form'
      ? 'Create quote form'
      : defaultKind === 'final'
        ? 'Create final quotation'
        : 'Create proposal';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      subtitle={subtitle}
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            type="submit"
            form="new-pitch-proposal-form"
            disabled={submitting || !slug.trim()}
          >
            {submitting ? 'Creating...' : submitLabel}
          </PillButton>
        </>
      }
    >
      <form id="new-pitch-proposal-form" className="studio-stack-sm" onSubmit={(e) => void handleSubmit(e)}>
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}

        {defaultKind === 'proposal' || defaultKind === 'final' ? (
          <Field label="Start from" htmlFor="new-pitch-source">
            <Select
              id="new-pitch-source"
              value={source}
              onChange={(e) => setSource(e.target.value as NewProposalSource)}
            >
              <option value="blank">
                {defaultKind === 'final' ? 'Blank final quotation' : 'Blank proposal'}
              </option>
              <option value="preset" disabled={presetEntries.length === 0}>
                From a Pitch preset
              </option>
              {defaultKind === 'proposal' ? (
                <option value="duplicate" disabled={proposals.length === 0}>
                  Duplicate existing proposal
                </option>
              ) : (
                <option value="finalize" disabled={finalizableProposals.length === 0}>
                  From an existing proposal or quote form
                </option>
              )}
            </Select>
          </Field>
        ) : null}

        {(defaultKind === 'proposal' || defaultKind === 'final') && source === 'preset' ? (
          <Field label="Pitch preset" htmlFor="new-pitch-preset">
            <Select
              id="new-pitch-preset"
              value={presetSlug}
              onChange={(e) => setPresetSlug(e.target.value)}
            >
              {presetEntries.map(([key, preset]) => (
                <option key={key} value={key}>
                  {preset.name || key} ({key})
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {defaultKind === 'proposal' && source === 'duplicate' ? (
          <Field label="Source proposal" htmlFor="new-pitch-dup">
            <Select
              id="new-pitch-dup"
              value={duplicateId}
              onChange={(e) => setDuplicateId(e.target.value)}
            >
              {proposals.map((prop) => (
                <option key={prop.id} value={prop.id}>
                  {prop.business_name || prop.client_name || prop.slug} ({prop.slug})
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {defaultKind === 'final' && source === 'finalize' ? (
          <Field label="Source proposal or quote form" htmlFor="new-pitch-finalize-src">
            <Select
              id="new-pitch-finalize-src"
              value={duplicateId}
              onChange={(e) => setDuplicateId(e.target.value)}
            >
              {finalizableProposals.map((prop) => (
                <option key={prop.id} value={prop.id}>
                  {prop.business_name || prop.client_name || prop.slug} ({prop.slug})
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {defaultKind === 'final' && source === 'blank' && catalogEntries.length > 0 ? (
          <Field
            label="Initial agreed services"
            hint="Select at least one service to start. You can refine services in the editor."
          >
            <div className="studio-pitch-dialog-checklist" role="group" aria-label="Initial agreed services">
              {catalogEntries.map(([itemId, item]) => (
                <Checkbox
                  key={itemId}
                  label={item.n}
                  checked={initialServices.includes(itemId)}
                  onChange={(e) => toggleInitialService(itemId, e.target.checked)}
                />
              ))}
            </div>
          </Field>
        ) : null}

        <Field
          label="Slug"
          htmlFor="new-pitch-slug"
          hint={
            serverSlugViolation ||
            liveSlugError ||
            'Lowercase letters, digits, and hyphens (2-63 chars).'
          }
        >
          <TextInput
            id="new-pitch-slug"
            placeholder="e.g. acme-telemed"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase())}
          />
        </Field>

        <div className="studio-grid-2">
          <Field
            label="Client name"
            htmlFor="new-pitch-client"
            hint={
              clientNameError ? (
                <span className="studio-field-error" role="alert">
                  {clientNameError}
                </span>
              ) : undefined
            }
          >
            <TextInput
              id="new-pitch-client"
              aria-invalid={Boolean(clientNameError)}
              placeholder="e.g. Dr. Mehta"
              value={clientName}
              onChange={(e) => setClientName(e.target.value)}
            />
          </Field>
          <Field
            label="Business name"
            htmlFor="new-pitch-biz"
            hint={
              businessNameError ? (
                <span className="studio-field-error" role="alert">
                  {businessNameError}
                </span>
              ) : undefined
            }
          >
            <TextInput
              id="new-pitch-biz"
              aria-invalid={Boolean(businessNameError)}
              placeholder="e.g. TeleMed India"
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
            />
          </Field>
        </div>
      </form>
    </Dialog>
  );
}

export interface DuplicateProposalDialogProps {
  proposal: PitchProposal | null;
  reservedPresets: string[];
  submitting: boolean;
  onClose: () => void;
  onDuplicate: (proposalId: string, slug: string, kind?: PitchProposalKind) => Promise<void>;
}

/** Modal dialog to duplicate a proposal onto a new slug. */
export function DuplicateProposalDialog({
  proposal,
  reservedPresets,
  submitting,
  onClose,
  onDuplicate,
}: DuplicateProposalDialogProps) {
  const [slug, setSlug] = useState('');
  const [targetKind, setTargetKind] = useState<'proposal' | 'quote_form'>('proposal');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!proposal) return;
    setSlug(`${proposal.slug}-copy`.slice(0, 62));
    setTargetKind('proposal');
    setError(null);
  }, [proposal]);

  const liveError = slug ? validateSlug(slug, reservedPresets) : null;
  const isDefault = proposal?.kind === 'default';

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!proposal) return;
    const slugErr = validateSlug(slug, reservedPresets);
    if (slugErr) {
      setError(slugErr);
      return;
    }
    setError(null);
    try {
      await onDuplicate(proposal.id, slug.trim(), isDefault ? targetKind : undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not duplicate proposal.');
    }
  };

  return (
    <Dialog
      open={Boolean(proposal)}
      onClose={onClose}
      title={isDefault ? 'Duplicate default quotation' : 'Duplicate proposal'}
      subtitle={proposal ? `Copy ${proposal.slug}` : undefined}
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            type="submit"
            form="duplicate-pitch-form"
            disabled={submitting || !slug.trim()}
          >
            {submitting ? 'Duplicating...' : 'Duplicate'}
          </PillButton>
        </>
      }
    >
      <form id="duplicate-pitch-form" className="studio-stack-sm" onSubmit={(e) => void handleSubmit(e)}>
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}
        {isDefault ? (
          <Field label="Duplicate as" htmlFor="dup-pitch-kind">
            <Select
              id="dup-pitch-kind"
              value={targetKind}
              onChange={(e) => setTargetKind(e.target.value as 'proposal' | 'quote_form')}
            >
              <option value="proposal">Proposal</option>
              <option value="quote_form">Quote form</option>
            </Select>
          </Field>
        ) : null}
        <Field label="New slug" htmlFor="dup-pitch-slug" hint={liveError || undefined}>
          <TextInput
            id="dup-pitch-slug"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase())}
          />
        </Field>
      </form>
    </Dialog>
  );
}

export interface DeleteProposalDialogProps {
  proposal: PitchProposal | null;
  submitting: boolean;
  onClose: () => void;
  onConfirmDelete: (proposalId: string) => Promise<void>;
}

/** Confirmation dialog before permanently deleting a proposal. */
export function DeleteProposalDialog({
  proposal,
  submitting,
  onClose,
  onConfirmDelete,
}: DeleteProposalDialogProps) {
  return (
    <Dialog
      open={Boolean(proposal)}
      onClose={onClose}
      title="Delete proposal"
      subtitle={proposal?.slug}
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </PillButton>
          <PillButton
            variant="danger"
            size="sm"
            disabled={submitting || !proposal}
            onClick={() => {
              if (proposal) void onConfirmDelete(proposal.id);
            }}
          >
            {submitting ? 'Deleting...' : 'Delete permanently'}
          </PillButton>
        </>
      }
    >
      {proposal ? (
        <p className="mg-sub">
          Permanently delete{' '}
          <span className="studio-mono">{proposal.slug}</span> (
          {proposal.business_name || proposal.client_name || 'Untitled'})? This cannot be undone.
        </p>
      ) : null}
    </Dialog>
  );
}

export interface DraftWithWriterDialogProps {
  open: boolean;
  submitting: boolean;
  onClose: () => void;
  onSendBrief: (brief: string) => Promise<void>;
}

/** Dialog asking Writer to draft a proposal using draft_proposal (no price fields). */
export function DraftWithWriterDialog({
  open,
  submitting,
  onClose,
  onSendBrief,
}: DraftWithWriterDialogProps) {
  const [brief, setBrief] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setBrief('');
    setError(null);
  }, [open]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = brief.trim();
    if (!trimmed) {
      setError('Enter a short brief describing the client and scope.');
      return;
    }
    setError(null);
    try {
      await onSendBrief(trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach Writer.');
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Draft with Writer"
      subtitle="Agents select deliverables and write copy; prices stay unchanged"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            type="submit"
            form="draft-with-writer-form"
            disabled={submitting || !brief.trim()}
          >
            {submitting ? 'Sending to Writer...' : 'Ask Writer to draft'}
          </PillButton>
        </>
      }
    >
      <form id="draft-with-writer-form" className="studio-stack-sm" onSubmit={(e) => void handleSubmit(e)}>
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}
        <Field
          label="Client brief"
          htmlFor="writer-proposal-brief"
          hint="Describe the client, business, goals, and features needed. Writer uses list_pitch_catalog and draft_proposal."
        >
          <TextArea
            id="writer-proposal-brief"
            rows={4}
            placeholder="e.g. TeleMed India (Dr. Mehta) wants a patient booking web app and Android app with WhatsApp notifications."
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
          />
        </Field>
      </form>
    </Dialog>
  );
}

export interface UnsavedChangesDialogProps {
  open: boolean;
  onCancel: () => void;
  onDiscard: () => void;
}

/** Unsaved-changes guard modal when leaving the proposal editor with dirty fields. */
export function UnsavedChangesDialog({ open, onCancel, onDiscard }: UnsavedChangesDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title="Unsaved changes"
      subtitle="Leave proposal editor"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onCancel}>
            Stay
          </PillButton>
          <PillButton variant="danger" size="sm" onClick={onDiscard}>
            Discard changes
          </PillButton>
        </>
      }
    >
      <p className="mg-sub">
        You have unsaved edits on this proposal. Leaving now will discard them.
      </p>
    </Dialog>
  );
}
