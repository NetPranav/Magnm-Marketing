import { useCallback, useEffect, useRef } from 'react';
import { Card, Label, Row, RowList } from '@magnm/design/react';
import type { PitchProposalItem } from '../types';

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
}

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
}: PitchTabProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

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
        <RowList>
          {proposals.map((item) =>
            item.confidential ? (
              <Row
                key={item.id}
                title="Confidential proposal"
                description="Open Magnm Studio on your laptop to view or present."
                meta="Confidential"
              />
            ) : (
              <Row
                key={item.id}
                title={item.name || item.id}
                description={formatPitchKind(item.kind)}
                meta="Present"
                onClick={() => onSelectProposal(item)}
              />
            ),
          )}
        </RowList>
      )}
    </section>
  );
}
