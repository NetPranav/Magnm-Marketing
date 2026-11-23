import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Label, PillButton, Row } from '@magnm/design/react';
import type { ActionInput } from '../actions';
import { catalogFromProposals, linkStatusOf, proposalOfRequest, requestItemNames } from '../pitchPhone';
import type { PhoneAction, PitchProposalItem, QuoteRequestItem } from '../types';
import { PitchEditor } from './PitchEditor';

export const MAX_PITCH_PDF_BYTES = 10 * 1024 * 1024;
const MAX_FILENAME_CHARS = 200;

export interface PitchTabProps {
  proposals: PitchProposalItem[];
  presentingProposal: PitchProposalItem | null;
  laptopReachable: boolean;
  pricesAsOfLabel: string | null;
  errorMessage: string | null;
  onSelectProposal: (proposal: PitchProposalItem) => void;
  onExitPresent: () => void;
  onSharePdf: (base64: string, title?: string) => Promise<void>;
  /** Pitch without the laptop (all optional: without them the tab only lists and presents). */
  signedIn?: boolean;
  phoneActions?: PhoneAction[];
  requests?: QuoteRequestItem[];
  onQueueAction?: (input: ActionInput, okMessage: string) => Promise<boolean>;
  onCopyText?: (text: string) => Promise<void> | void;
}

type PitchView = { kind: 'list' } | { kind: 'edit'; id: string } | { kind: 'new' } | { kind: 'requests' };

function formatPitchKind(kind?: string): string {
  switch (kind) {
    case 'default':
      return 'Default quotation';
    case 'quote_form':
      return 'Quote form';
    case 'final':
      return 'Final quotation';
    default:
      return 'Proposal';
  }
}

function toPdfUint8Array(raw: unknown): Uint8Array | null {
  if (raw instanceof Uint8Array) return raw;
  if (raw instanceof ArrayBuffer) return new Uint8Array(raw);
  if (ArrayBuffer.isView(raw)) {
    return new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
  }
  return null;
}

function hasPdfMagic(bytes: Uint8Array): boolean {
  return (
    bytes.byteLength >= 5 &&
    bytes[0] === 0x25 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x44 &&
    bytes[3] === 0x46 &&
    bytes[4] === 0x2d
  );
}

function uint8ToBase64(bytes: Uint8Array): string {
  const chunkSize = 8192;
  const parts: string[] = [];
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const slice = bytes.subarray(i, i + chunkSize);
    let str = '';
    for (let j = 0; j < slice.length; j += 1) {
      str += String.fromCharCode(slice[j]);
    }
    parts.push(str);
  }
  return btoa(parts.join(''));
}

export function PitchTab({
  proposals,
  presentingProposal,
  laptopReachable,
  pricesAsOfLabel,
  errorMessage,
  onSelectProposal,
  onExitPresent,
  onSharePdf,
  signedIn = false,
  phoneActions = [],
  requests = [],
  onQueueAction,
  onCopyText,
}: PitchTabProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [view, setView] = useState<PitchView>({ kind: 'list' });

  const sendPayloadToFrame = useCallback(() => {
    if (!presentingProposal || presentingProposal.confidential) return;
    const payload = presentingProposal.payload;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return;
    const frameWin = iframeRef.current?.contentWindow;
    if (!frameWin || typeof frameWin.postMessage !== 'function') return;
    frameWin.postMessage({ type: 'magnm:pitch-payload', payload }, '*');
  }, [presentingProposal]);

  useEffect(() => {
    if (!presentingProposal) return undefined;
    const onMessage = (event: MessageEvent) => {
      const frameWin = iframeRef.current?.contentWindow;
      if (!frameWin || event.source !== frameWin) return;
      const msg = event.data as Record<string, unknown> | null;
      if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return;

      if (msg.type === 'magnm:pitch-ready') {
        sendPayloadToFrame();
        return;
      }
      if (msg.type === 'magnm:pitch-exit-present') {
        onExitPresent();
        return;
      }
      if (msg.type === 'magnm:pitch-pdf-bytes') {
        if (
          msg.filename !== undefined &&
          (typeof msg.filename !== 'string' || msg.filename.length > MAX_FILENAME_CHARS)
        ) {
          return;
        }
        const bytes = toPdfUint8Array(msg.bytes);
        if (!bytes || bytes.byteLength === 0 || bytes.byteLength > MAX_PITCH_PDF_BYTES) {
          return;
        }
        if (!hasPdfMagic(bytes)) {
          return;
        }
        const base64 = uint8ToBase64(bytes);
        void onSharePdf(base64, presentingProposal.name || 'Quotation');
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onExitPresent();
      }
    };

    window.addEventListener('message', onMessage);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('message', onMessage);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [presentingProposal, sendPayloadToFrame, onExitPresent, onSharePdf]);

  if (presentingProposal) {
    return (
      <div className="mob-pitch-present" data-testid="pitch-present-screen">
        <iframe
          ref={iframeRef}
          title={`Present ${presentingProposal.name || presentingProposal.id}`}
          src="/pitch/index.html"
          sandbox="allow-scripts"
          className="mob-pitch-present-iframe"
          onLoad={sendPayloadToFrame}
        />
        <button
          type="button"
          className="mob-pitch-present-exit"
          onClick={onExitPresent}
        >
          Exit
        </button>
      </div>
    );
  }

  const manage = Boolean(onQueueAction);
  const catalog = catalogFromProposals(proposals);

  if (manage && onQueueAction && view.kind === 'new') {
    return (
      <PitchEditor
        mode="new"
        catalog={catalog}
        signedIn={signedIn}
        actions={phoneActions}
        onQueue={onQueueAction}
        onBack={() => setView({ kind: 'list' })}
      />
    );
  }

  if (manage && onQueueAction && view.kind === 'edit') {
    const target = proposals.find((p) => p.id === view.id && !p.confidential && p.cloud);
    if (target) {
      return (
        <PitchEditor
          key={target.id}
          mode="edit"
          item={target}
          catalog={target.cloud?.catalog ?? catalog}
          signedIn={signedIn}
          actions={phoneActions}
          onQueue={onQueueAction}
          onCopyText={onCopyText}
          onBack={() => setView({ kind: 'list' })}
        />
      );
    }
  }

  if (manage && view.kind === 'requests') {
    return (
      <section aria-label="Quote requests" className="mob-stack">
        <div className="mob-row-between">
          <Label>Quote requests</Label>
          <PillButton variant="outline" size="sm" onClick={() => setView({ kind: 'list' })}>
            Back
          </PillButton>
        </div>
        {requests.length === 0 ? (
          <Card>
            <p className="mob-body-text">No quote requests yet. Clients send them through a published link.</p>
          </Card>
        ) : (
          requests.map((req) => {
            const owner = proposalOfRequest(req, proposals);
            const items = requestItemNames(req, proposals);
            return (
              <Card key={req.id}>
                <div className="mob-stack-sm mob-wrap-text" data-testid="quote-request">
                  <h2 className="mob-card-title">{[req.name, req.business].filter(Boolean).join(' · ') || 'A client'}</h2>
                  {owner ? <p className="mob-mono">For {owner.name || owner.id}</p> : null}
                  {req.phone ? <p className="mob-body-text">{req.phone}</p> : null}
                  {items.length > 0 ? <p className="mob-body-text">Chose: {items.join(', ')}</p> : null}
                  {req.total_shown !== null ? (
                    <p className="mob-body-text">Total shown to the client: {'\u20b9'}{req.total_shown.toLocaleString('en-IN')}</p>
                  ) : null}
                  {req.notes ? <p className="mob-body-text">{req.notes}</p> : null}
                  <p className="mob-mono">{req.created_at ? new Date(req.created_at).toLocaleString() : ''}{req.seen_at ? ' · seen on the laptop' : ''}</p>
                </div>
              </Card>
            );
          })
        )}
      </section>
    );
  }

  return (
    <section aria-label="Pitch" className="mob-stack">
      <div className="mob-row-between">
        <Label>Pitch proposals</Label>
        {pricesAsOfLabel ? (
          <span className="mob-mono" data-testid="pitch-prices-as-of">
            {pricesAsOfLabel}
          </span>
        ) : null}
      </div>

      {manage ? (
        <div className="mob-actions">
          <PillButton size="sm" onClick={() => setView({ kind: 'new' })}>
            New proposal
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => setView({ kind: 'requests' })}>
            {requests.length > 0 ? `Quote requests (${requests.length})` : 'Quote requests'}
          </PillButton>
        </div>
      ) : null}

      {!laptopReachable && pricesAsOfLabel ? (
        <div className="mg-notice">
          Offline present mode ready · {pricesAsOfLabel}
        </div>
      ) : null}

      {errorMessage ? (
        <div className="mob-banner-error" role="alert">
          {errorMessage}
        </div>
      ) : null}

      {proposals.length === 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <p className="mob-body-text">
              No proposals synced yet. Pair and connect to your laptop to sync proposals for offline present mode.
            </p>
          </div>
        </Card>
      ) : (
        <div className="mg-rows">
          {proposals.map((item) => {
            if (item.confidential) {
              return (
                <Row
                  key={item.id}
                  title="Confidential proposal"
                  description="Open Magnm Studio on your laptop to view or present."
                  meta="Confidential"
                />
              );
            }
            const link = linkStatusOf(item, phoneActions);
            const title = item.name || item.id;
            return (
              <div key={item.id} className="mob-pitch-item">
                <Row
                  title={title}
                  description={formatPitchKind(item.kind)}
                  meta="Present"
                  onClick={() => onSelectProposal(item)}
                />
                {manage ? (
                  <div className="mob-pitch-item-actions">
                    {item.cloud ? (
                      <PillButton
                        variant="outline"
                        size="sm"
                        aria-label={`Manage ${title}`}
                        onClick={() => setView({ kind: 'edit', id: item.id })}
                      >
                        Manage
                      </PillButton>
                    ) : (
                      <span className="mob-mono">Manage opens once the laptop has synced this proposal.</span>
                    )}
                    {link.waiting ? <span className="mg-chip">Link waiting for the laptop</span> : null}
                    {!link.waiting && link.state === 'live' ? <span className="mg-chip">Link live</span> : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
