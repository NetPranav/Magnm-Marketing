import { useMemo, useState } from 'react';
import { Card, Checkbox, Field, Label, PillButton, Select, TextArea, TextInput } from '@magnm/design/react';
import { PhoneActionError, validateActionInput, type ActionInput } from '../actions';
import {
  buildCreateInput,
  buildFinalizeInput,
  buildLinkInput,
  buildUpdateInput,
  changedFields,
  editValuesOf,
  hasWaitingEdit,
  linkStatusOf,
  sheetOfGroup,
  type PitchEditValues,
} from '../pitchPhone';
import { MAX_PRICE, SHEET_KEYS, isValidPrice } from '../pitchRules';
import type { PhoneAction, PitchCatalog, PitchFields, PitchProposalItem } from '../types';

const SHEET_LABELS: Record<(typeof SHEET_KEYS)[number], string> = {
  p2: 'Web & SaaS',
  p3: 'Mobile app',
  p4: 'Backend',
  p5: 'AI & DevOps',
};

const ABOUT_LABELS: Array<[string, string]> = [
  ['lead', 'Introduction'],
  ['what_we_do', 'What we do'],
  ['portfolio', 'Portfolio'],
  ['this_plan', 'This plan'],
  ['architecture', 'Architecture'],
  ['timeline', 'Timeline'],
  ['payment', 'Payment'],
];

const PRICE_ERROR = `A price must be a whole number from 0 to ${MAX_PRICE.toLocaleString('en-US')}.`;

export interface PitchEditorProps {
  mode: 'edit' | 'new';
  item?: PitchProposalItem;
  catalog: PitchCatalog | null;
  signedIn: boolean;
  actions: PhoneAction[];
  onQueue: (input: ActionInput, okMessage: string) => Promise<boolean>;
  onCopyText?: (text: string) => Promise<void> | void;
  onBack: () => void;
}

function toggle(list: string[], id: string, on: boolean): string[] {
  const without = list.filter((x) => x !== id);
  return on ? [...without, id] : without;
}

function emptyValues(): PitchEditValues {
  return {
    client_name: '', business_name: '', contact: {}, notes: '', title: '', lead: '', whatWeDo: '', portfolio: '',
    thisPlan: '', initialSelected: [], sections: {}, hidden_sheets: [], hidden_about: [], price_overrides: {},
  };
}

/** The proposal editor (and the new-proposal form). Edits are queued as phone actions; nothing is applied here. */
export function PitchEditor({ mode, item, catalog, signedIn, actions, onQueue, onCopyText, onBack }: PitchEditorProps) {
  const cloud = item?.cloud;
  const initial = useMemo(() => (cloud ? editValuesOf(cloud) : emptyValues()), [cloud]);
  const initialPrices = useMemo(
    () => Object.fromEntries(Object.entries(initial.price_overrides).map(([id, p]) => [id, String(p)])),
    [initial],
  );
  const [values, setValues] = useState<PitchEditValues>(initial);
  const [priceText, setPriceText] = useState<Record<string, string>>(initialPrices);
  const [preset, setPreset] = useState('');
  const [newKind, setNewKind] = useState<'proposal' | 'quote_form'>('proposal');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isNew = mode === 'new';
  const kind = item?.kind ?? 'proposal';
  const readOnlyReason =
    !isNew && cloud
      ? kind === 'default'
        ? 'The default quotation cannot be edited.'
        : cloud.truncated
          ? 'This proposal is too large to edit on the phone. Edit it on the laptop.'
          : !cloud.editable
            ? 'This proposal cannot be edited on the phone.'
            : null
      : null;
  const waitingEdit = item ? hasWaitingEdit(item, actions) : false;
  const link = item ? linkStatusOf(item, actions) : null;
  // What the laptop said about the last change to this proposal (for example that it kept both copies).
  const lastAnswer = item
    ? actions.filter((a) => a.target_id === item.id && a.kind.startsWith('pitch_') && a.state !== 'waiting' && a.result).slice(-1)[0]
    : undefined;

  function set<K extends keyof PitchEditValues>(key: K, value: PitchEditValues[K]) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  function setContact(key: 'name' | 'business' | 'email' | 'phone' | 'notes', value: string) {
    setValues((v) => ({ ...v, contact: { ...v.contact, [key]: value } }));
  }

  /** The prices typed so far, or throws the reason one of them is not allowed. */
  function pricesNow(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [id, text] of Object.entries(priceText)) {
      const t = text.trim();
      if (!t) continue;
      const n = /^\d{1,8}$/.test(t) ? Number(t) : NaN;
      if (!isValidPrice(n)) throw new PhoneActionError(PRICE_ERROR);
      out[id] = n;
    }
    return out;
  }

  const priceTextChanged = JSON.stringify(priceText) !== JSON.stringify(initialPrices);
  const dirty = isNew || priceTextChanged || (cloud ? Object.keys(changedFields(cloud, values)).length > 0 : false);

  async function submit(build: () => ActionInput, okMessage: string, andClose = true) {
    setError(null);
    let input: ActionInput;
    try {
      input = build();
      validateActionInput(input);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That could not be queued.');
      return;
    }
    setBusy(true);
    try {
      const ok = await onQueue(input, okMessage);
      if (ok && andClose) onBack();
    } finally {
      setBusy(false);
    }
  }

  function save() {
    void submit(() => {
      if (!item) throw new PhoneActionError('Choose a proposal.');
      const fields: PitchFields = cloud ? changedFields(cloud, { ...values, price_overrides: pricesNow() }) : {};
      return buildUpdateInput(item, fields);
    }, 'Edit saved. Waiting for the laptop.');
  }

  function create() {
    void submit(() => {
      const fields: PitchFields & { kind?: 'proposal' | 'quote_form'; from_preset?: string } = { kind: newKind };
      if (preset) fields.from_preset = preset;
      if (values.client_name.trim()) fields.client_name = values.client_name;
      if (values.business_name.trim()) fields.business_name = values.business_name;
      if (values.notes.trim()) fields.notes = values.notes;
      const contact = Object.fromEntries(Object.entries(values.contact).filter(([, v]) => (v ?? '').trim()));
      if (Object.keys(contact).length > 0) fields.contact = contact;
      return buildCreateInput(fields);
    }, 'Proposal queued. Waiting for the laptop.');
  }

  const items = Object.entries(catalog?.items ?? {});
  const inProposal = items.filter(
    ([id]) =>
      values.initialSelected.includes(id) ||
      SHEET_KEYS.some((s) => (values.sections[s] ?? []).includes(id)) ||
      Boolean(priceText[id]),
  );

  return (
    <section aria-label={isNew ? 'New proposal' : 'Edit proposal'} className="mob-pitch-editor">
      <div className="mob-row-between">
        <Label>{isNew ? 'New proposal' : item?.name || 'Proposal'}</Label>
        <PillButton variant="outline" size="sm" onClick={onBack}>
          Back
        </PillButton>
      </div>

      {error ? (
        <div className="mob-banner-error mob-wrap-text" role="alert">
          {error}
        </div>
      ) : null}
      {!signedIn ? <div className="mg-notice">Sign in under More to change proposals while the laptop is away.</div> : null}
      {readOnlyReason ? <div className="mg-notice">{readOnlyReason}</div> : null}
      {lastAnswer ? (
        <div className="mg-notice mob-wrap-text">
          Last change: {lastAnswer.state === 'rejected' ? 'not applied. ' : ''}
          {lastAnswer.result}
        </div>
      ) : null}
      {waitingEdit ? (
        <div className="mg-notice">An earlier edit is waiting for the laptop. Save again when it has been applied.</div>
      ) : null}
      {!isNew && !readOnlyReason ? (
        <div className="mg-notice">
          If the laptop changed this proposal first, your edit is kept as a copy marked "(phone)". Nothing is overwritten.
        </div>
      ) : null}

      {isNew ? (
        <Card>
          <div className="mob-stack-sm">
            <Field label="Start from" htmlFor="pitch-new-preset">
              <Select id="pitch-new-preset" value={preset} onChange={(e) => setPreset(e.target.value)}>
                <option value="">Blank proposal</option>
                {(catalog?.presets ?? []).map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Kind" htmlFor="pitch-new-kind">
              <Select id="pitch-new-kind" value={newKind} onChange={(e) => setNewKind(e.target.value as 'proposal' | 'quote_form')}>
                <option value="proposal">Proposal</option>
                <option value="quote_form">Quote form</option>
              </Select>
            </Field>
          </div>
        </Card>
      ) : null}

      {!readOnlyReason ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Client details</Label>
            <Field label="Client name" htmlFor="pitch-client-name">
              <TextInput id="pitch-client-name" value={values.client_name} maxLength={200} onChange={(e) => set('client_name', e.target.value)} />
            </Field>
            <Field label="Business name" htmlFor="pitch-business-name">
              <TextInput id="pitch-business-name" value={values.business_name} maxLength={200} onChange={(e) => set('business_name', e.target.value)} />
            </Field>
            <Field label="Contact name" htmlFor="pitch-contact-name">
              <TextInput id="pitch-contact-name" value={values.contact.name ?? ''} maxLength={200} onChange={(e) => setContact('name', e.target.value)} />
            </Field>
            <Field label="Contact email" htmlFor="pitch-contact-email">
              <TextInput id="pitch-contact-email" inputMode="email" value={values.contact.email ?? ''} maxLength={200} onChange={(e) => setContact('email', e.target.value)} />
            </Field>
            <Field label="Contact phone" htmlFor="pitch-contact-phone">
              <TextInput id="pitch-contact-phone" inputMode="tel" value={values.contact.phone ?? ''} maxLength={200} onChange={(e) => setContact('phone', e.target.value)} />
            </Field>
            <Field label="Contact notes" htmlFor="pitch-contact-notes">
              <TextArea id="pitch-contact-notes" value={values.contact.notes ?? ''} maxLength={1000} onChange={(e) => setContact('notes', e.target.value)} />
            </Field>
            <Field label="Notes (private)" htmlFor="pitch-notes">
              <TextArea id="pitch-notes" value={values.notes} maxLength={4000} onChange={(e) => set('notes', e.target.value)} />
            </Field>
          </div>
        </Card>
      ) : null}

      {!readOnlyReason && !isNew ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Texts the client sees</Label>
            <Field label="Title" htmlFor="pitch-title">
              <TextInput id="pitch-title" value={values.title} maxLength={200} onChange={(e) => set('title', e.target.value)} />
            </Field>
            <Field label="Lead" htmlFor="pitch-lead">
              <TextArea id="pitch-lead" value={values.lead} maxLength={1000} onChange={(e) => set('lead', e.target.value)} />
            </Field>
            <Field label="What we do" htmlFor="pitch-what">
              <TextArea id="pitch-what" value={values.whatWeDo} maxLength={1000} onChange={(e) => set('whatWeDo', e.target.value)} />
            </Field>
            <Field label="Portfolio" htmlFor="pitch-portfolio">
              <TextInput id="pitch-portfolio" value={values.portfolio} maxLength={200} onChange={(e) => set('portfolio', e.target.value)} />
            </Field>
            <Field label="This plan" htmlFor="pitch-this-plan">
              <TextArea id="pitch-this-plan" value={values.thisPlan} maxLength={1000} onChange={(e) => set('thisPlan', e.target.value)} />
            </Field>
            <Label>Sections shown</Label>
            <div className="mob-stack-sm">
              {ABOUT_LABELS.map(([key, label]) => (
                <Checkbox
                  key={key}
                  label={`Show section: ${label}`}
                  checked={!values.hidden_about.includes(key)}
                  onChange={(e) => set('hidden_about', toggle(values.hidden_about, key, !e.target.checked))}
                />
              ))}
            </div>
          </div>
        </Card>
      ) : null}

      {!readOnlyReason && !isNew && kind !== 'final' && catalog ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Services on each sheet</Label>
            {SHEET_KEYS.map((sheet) => {
              const sheetItems = items.filter(([, it]) => sheetOfGroup(it.g) === sheet);
              if (sheetItems.length === 0) return null;
              return (
                <div key={sheet} className="mob-stack-sm">
                  <Checkbox
                    label={`Show sheet: ${SHEET_LABELS[sheet]}`}
                    checked={!values.hidden_sheets.includes(sheet)}
                    onChange={(e) => set('hidden_sheets', toggle(values.hidden_sheets, sheet, !e.target.checked))}
                  />
                  {sheetItems.map(([id, it]) => (
                    <div key={id} className="mob-service-row">
                      <span className="mob-service-name">{it.n}</span>
                      <Checkbox
                        label={`Shown on its sheet: ${it.n}`}
                        checked={(values.sections[sheet] ?? []).includes(id)}
                        onChange={(e) => set('sections', { ...values.sections, [sheet]: toggle(values.sections[sheet] ?? [], id, e.target.checked) })}
                      />
                      <Checkbox
                        label={`In the plan: ${it.n}`}
                        checked={values.initialSelected.includes(id)}
                        onChange={(e) => set('initialSelected', toggle(values.initialSelected, id, e.target.checked))}
                      />
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </Card>
      ) : null}

      {!readOnlyReason && !isNew && inProposal.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Prices for this proposal</Label>
            <p className="mob-mono">Leave a price empty to use the base price.</p>
            {inProposal.map(([id, it]) => (
              <Field key={id} label={`Price for ${it.n}`} htmlFor={`pitch-price-${id}`}>
                <TextInput
                  id={`pitch-price-${id}`}
                  inputMode="numeric"
                  placeholder={String(it.p)}
                  value={priceText[id] ?? ''}
                  onChange={(e) => setPriceText((t) => ({ ...t, [id]: e.target.value }))}
                />
              </Field>
            ))}
          </div>
        </Card>
      ) : null}

      <div className="mob-actions">
        {isNew ? (
          <PillButton disabled={!signedIn || busy} onClick={create}>
            Create proposal
          </PillButton>
        ) : !readOnlyReason ? (
          <PillButton disabled={!signedIn || busy || waitingEdit || !dirty} onClick={save}>
            Save changes
          </PillButton>
        ) : null}
      </div>

      {!isNew && item && link ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Client link</Label>
            <span className="mg-chip">
              {link.waiting
                ? 'Waiting for the laptop'
                : link.state === 'live'
                  ? 'Link is live'
                  : link.state === 'withdrawn'
                    ? 'Link was withdrawn'
                    : 'No client link yet'}
            </span>
            {link.url ? <p className="mob-mono mob-wrap-text">{link.url}</p> : null}
            <div className="mob-actions">
              <PillButton
                variant="outline"
                size="sm"
                disabled={!signedIn || busy || link.waiting !== null}
                onClick={() => void submit(() => buildLinkInput(item, 'publish'), 'Link queued. Waiting for the laptop.', false)}
              >
                Publish link
              </PillButton>
              {link.url ? (
                <PillButton variant="outline" size="sm" onClick={() => void onCopyText?.(link.url as string)}>
                  Copy link
                </PillButton>
              ) : null}
              {link.state === 'live' ? (
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={!signedIn || busy || link.waiting !== null}
                  onClick={() => void submit(() => buildLinkInput(item, 'withdraw'), 'Withdraw queued. Waiting for the laptop.', false)}
                >
                  Withdraw link
                </PillButton>
              ) : null}
            </div>
            <p className="mob-mono">The laptop builds and publishes the link when it is back online.</p>
          </div>
        </Card>
      ) : null}

      {!isNew && item && (kind === 'proposal' || kind === 'quote_form') && cloud?.editable ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Final quotation</Label>
            <p className="mob-mono">Makes a new final quotation from this proposal. The proposal is kept.</p>
            <div className="mob-actions">
              <PillButton
                variant="outline"
                size="sm"
                disabled={!signedIn || busy}
                onClick={() => void submit(() => buildFinalizeInput(item), 'Final quotation queued. Waiting for the laptop.', false)}
              >
                Make final quotation
              </PillButton>
            </div>
          </div>
        </Card>
      ) : null}
    </section>
  );
}
