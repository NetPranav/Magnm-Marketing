import { useState } from 'react';
import {
  Card,
  Dialog,
  Field,
  Label,
  PillButton,
  TextArea,
  TextInput,
} from '@magnm/design/react';
import { validateMetricInputs } from '../metrics';
import type { SharedItem } from '../native';
import type { MetricProposal } from '../types';

export interface ShareInModalProps {
  item: SharedItem | null;
  laptopReachable: boolean;
  busy: boolean;
  extractedProposal: MetricProposal | null;
  errorMessage: string | null;
  onDismiss: () => void;
  onSaveAsIdea: (title: string, body: string, url?: string) => Promise<void>;
  onSaveAsNote: (title: string, body: string, url?: string) => Promise<void>;
  onExtractScreenshot: (imageBase64: string, platform?: string) => Promise<void>;
  onConfirmExtracted: (
    proposalId: string,
    metrics: Record<string, number | null>,
    platform: string | null,
    contentId: string | null,
  ) => Promise<void>;
}

const ALLOWED_IMAGE_MIME = new Set(['image/png', 'image/jpeg', 'image/webp']);

function safeDataImageUrl(item: SharedItem): string | null {
  if (!item.imageBase64) return null;
  const raw = item.imageBase64.trim();
  if (raw.startsWith('data:')) {
    const match = raw.match(/^data:(image\/(?:png|jpeg|webp));base64,[A-Za-z0-9+/=]+$/);
    return match ? raw : null;
  }
  const mime = item.mimeType && ALLOWED_IMAGE_MIME.has(item.mimeType) ? item.mimeType : 'image/png';
  if (!/^[A-Za-z0-9+/=]+$/.test(raw)) return null;
  return `data:${mime};base64,${raw}`;
}

function initialMetricInputs(proposal: MetricProposal): Record<string, string> {
  const map: Record<string, string> = {};
  for (const [k, v] of Object.entries(proposal.metrics ?? {})) {
    map[k] = v === null || v === undefined ? '' : String(v);
  }
  return map;
}

function ResultsProposalEditor({
  proposal,
  busy,
  onConfirm,
  onDismiss,
}: {
  proposal: MetricProposal;
  busy: boolean;
  onConfirm: ShareInModalProps['onConfirmExtracted'];
  onDismiss: () => void;
}) {
  const [platform, setPlatform] = useState(proposal.platform ?? '');
  const [contentId, setContentId] = useState(proposal.content_id ?? '');
  const [editedMetrics, setEditedMetrics] = useState<Record<string, string>>(() =>
    initialMetricInputs(proposal),
  );
  const [validationError, setValidationError] = useState<string | null>(null);

  async function handleConfirmResults() {
    setValidationError(null);
    const validated = validateMetricInputs(editedMetrics);
    if (!validated.ok) {
      setValidationError(validated.error);
      return;
    }
    await onConfirm(
      proposal.id,
      validated.metrics,
      platform.trim() || null,
      contentId.trim() || null,
    );
  }

  return (
    <div className="mob-stack-sm">
      <Label>Review extracted results (tap Save results to confirm)</Label>
      {validationError ? (
        <div className="mob-banner-error" role="alert">
          {validationError}
        </div>
      ) : null}
      <Field label="Platform" htmlFor="share-res-platform">
        <TextInput
          id="share-res-platform"
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          placeholder="linkedin"
        />
      </Field>
      <Field label="Draft ID (optional)" htmlFor="share-res-cid">
        <TextInput
          id="share-res-cid"
          value={contentId}
          onChange={(e) => setContentId(e.target.value)}
          placeholder="content_..."
        />
      </Field>
      <div className="mob-metric-grid">
        {Object.keys(editedMetrics).map((metricKey) => (
          <Field
            key={metricKey}
            label={metricKey}
            htmlFor={`share-m-${metricKey}`}
          >
            <TextInput
              id={`share-m-${metricKey}`}
              value={editedMetrics[metricKey]}
              onChange={(e) =>
                setEditedMetrics((prev) => ({
                  ...prev,
                  [metricKey]: e.target.value,
                }))
              }
            />
          </Field>
        ))}
      </div>
      <div className="mob-actions">
        <PillButton
          size="sm"
          disabled={busy}
          onClick={() => void handleConfirmResults()}
        >
          Save results
        </PillButton>
        <PillButton variant="outline" size="sm" onClick={onDismiss}>
          Discard
        </PillButton>
      </div>
    </div>
  );
}

function ShareItemForm({
  item,
  laptopReachable,
  busy,
  onSaveAsIdea,
  onSaveAsNote,
  onExtractScreenshot,
  onDismiss,
}: {
  item: SharedItem;
  laptopReachable: boolean;
  busy: boolean;
  onSaveAsIdea: ShareInModalProps['onSaveAsIdea'];
  onSaveAsNote: ShareInModalProps['onSaveAsNote'];
  onExtractScreenshot: ShareInModalProps['onExtractScreenshot'];
  onDismiss: () => void;
}) {
  const rawText = (item.text ?? '').trim();
  const initialTitle = (rawText.split('\n')[0]?.trim() || item.url || 'Shared item').slice(0, 80);
  const initialBody = [rawText, item.url].filter(Boolean).join('\n\n');

  const [title, setTitle] = useState(initialTitle);
  const [body, setBody] = useState(initialBody);
  const previewImg = safeDataImageUrl(item);

  return (
    <div className="mob-stack-sm">
      <Card outline>
        <div className="mob-stack-sm">
          <Label>Preview</Label>
          {item.url ? <p className="mob-mono">{item.url}</p> : null}
          {item.text ? <p className="mob-body-text">{item.text}</p> : null}
          {previewImg ? (
            <img
              className="mob-share-preview-img"
              src={previewImg}
              alt="Shared screenshot preview"
            />
          ) : null}
        </div>
      </Card>

      <Field label="Title" htmlFor="share-title-input">
        <TextInput
          id="share-title-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </Field>

      <Field label="Notes" htmlFor="share-body-input">
        <TextArea
          id="share-body-input"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </Field>

      <div className="mob-actions">
        <PillButton
          size="sm"
          disabled={!title.trim() || busy}
          onClick={() => void onSaveAsIdea(title.trim(), body, item.url)}
        >
          Idea
        </PillButton>
        <PillButton
          variant="outline"
          size="sm"
          disabled={!title.trim() || busy}
          onClick={() => void onSaveAsNote(title.trim(), body, item.url)}
        >
          Note
        </PillButton>
        {item.imageBase64 ? (
          <PillButton
            variant="outline"
            size="sm"
            disabled={!laptopReachable || busy}
            onClick={() => void onExtractScreenshot(item.imageBase64 as string)}
          >
            Read results screenshot
          </PillButton>
        ) : null}
        <PillButton variant="quiet" size="sm" onClick={onDismiss}>
          Dismiss
        </PillButton>
      </div>
    </div>
  );
}

export function ShareInModal({
  item,
  laptopReachable,
  busy,
  extractedProposal,
  errorMessage,
  onDismiss,
  onSaveAsIdea,
  onSaveAsNote,
  onExtractScreenshot,
  onConfirmExtracted,
}: ShareInModalProps) {
  if (!item) return null;

  return (
    <Dialog open title="Shared into Magnm" onClose={onDismiss}>
      <div className="mob-stack-sm">
        {errorMessage ? (
          <div className="mob-banner-error" role="alert">
            {errorMessage}
          </div>
        ) : null}

        {extractedProposal ? (
          <ResultsProposalEditor
            key={extractedProposal.id}
            proposal={extractedProposal}
            busy={busy}
            onConfirm={onConfirmExtracted}
            onDismiss={onDismiss}
          />
        ) : (
          <ShareItemForm
            key={item.id}
            item={item}
            laptopReachable={laptopReachable}
            busy={busy}
            onSaveAsIdea={onSaveAsIdea}
            onSaveAsNote={onSaveAsNote}
            onExtractScreenshot={onExtractScreenshot}
            onDismiss={onDismiss}
          />
        )}
      </div>
    </Dialog>
  );
}
