import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { Card, Dialog, Field, PillButton, Select, TextInput } from '@magnm/design/react';
import { api } from '../api';
import {
  confirmResultsProposal,
  discardResultsProposal,
  extractResultsScreenshot,
  proposalImageUrl,
} from '../api/results';
import type { ContentItem, MetricProposal, MetricRecord } from '../types';

const METRIC_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'views', label: 'Views' },
  { key: 'impressions', label: 'Impressions' },
  { key: 'reach', label: 'Reach' },
  { key: 'likes', label: 'Likes' },
  { key: 'comments', label: 'Comments' },
  { key: 'shares', label: 'Shares' },
  { key: 'reposts', label: 'Reposts' },
  { key: 'saves', label: 'Saves' },
  { key: 'bookmarks', label: 'Bookmarks' },
  { key: 'clicks', label: 'Clicks' },
  { key: 'profile_visits', label: 'Profile visits' },
  { key: 'followers_gained', label: 'Followers gained' },
  { key: 'watch_time_seconds', label: 'Watch time (s)' },
  { key: 'engagement_rate', label: 'Engagement rate (%)' },
  { key: 'ctr', label: 'CTR (%)' },
];

const PLATFORMS = ['linkedin', 'x', 'instagram', 'youtube', 'blog'] as const;

function buildFieldValues(metrics: Record<string, number> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of METRIC_FIELDS) {
    const val = metrics?.[field.key];
    out[field.key] = typeof val === 'number' && Number.isFinite(val) ? String(val) : '';
  }
  return out;
}

export interface AddResultsDialogProps {
  open: boolean;
  onClose: () => void;
  initialContentId?: string | null;
  initialPlatform?: string | null;
  initialProposal?: MetricProposal | null;
  contentItems?: ContentItem[];
  onSaved?: (result: { proposal: MetricProposal; metric: MetricRecord }) => void;
  onDiscarded?: (proposalId: string) => void;
}

/** Modal dialog to extract analytics metrics from a screenshot and confirm before saving. */
export function AddResultsDialog({
  open,
  onClose,
  initialContentId,
  initialPlatform,
  initialProposal,
  contentItems: providedContentItems,
  onSaved,
  onDiscarded,
}: AddResultsDialogProps) {
  const [fetchedItems, setFetchedItems] = useState<ContentItem[]>([]);
  const [selectedContentId, setSelectedContentId] = useState('');
  const [selectedPlatform, setSelectedPlatform] = useState('linkedin');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [localPreviewUrl, setLocalPreviewUrl] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const [proposal, setProposal] = useState<MetricProposal | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const items = providedContentItems && providedContentItems.length > 0 ? providedContentItems : fetchedItems;

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSelectedFile(null);
    setLocalPreviewUrl(null);
    setDragActive(false);

    if (initialProposal) {
      setProposal(initialProposal);
      setSelectedContentId(initialProposal.content_id || initialContentId || '');
      setSelectedPlatform(initialProposal.platform || initialPlatform || 'linkedin');
      setFieldValues(buildFieldValues(initialProposal.metrics));
    } else {
      setProposal(null);
      const cid = initialContentId || '';
      setSelectedContentId(cid);
      const matched = items.find((item) => item.id === cid);
      setSelectedPlatform(initialPlatform || matched?.platform || 'linkedin');
      setFieldValues({});
    }
  }, [open, initialProposal, initialContentId, initialPlatform]);

  useEffect(() => {
    if (!open || (providedContentItems && providedContentItems.length > 0)) return;
    let active = true;
    void api
      .get<ContentItem[]>('/api/content?limit=200')
      .then((res) => {
        if (active && Array.isArray(res)) {
          setFetchedItems(res);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [open, providedContentItems]);

  useEffect(() => {
    if (!selectedFile || typeof URL.createObjectURL !== 'function') {
      setLocalPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(selectedFile);
    setLocalPreviewUrl(url);
    return () => {
      if (typeof URL.revokeObjectURL === 'function') {
        URL.revokeObjectURL(url);
      }
    };
  }, [selectedFile]);

  const selectablePosts = useMemo(() => {
    const filtered = items.filter((item) => item.type !== 'review' && item.type !== 'plan');
    return [...filtered].sort((a, b) => {
      const aPosted = a.status === 'posted' ? 0 : 1;
      const bPosted = b.status === 'posted' ? 0 : 1;
      return aPosted - bPosted;
    });
  }, [items]);

  const handleSelectPost = (nextCid: string) => {
    setSelectedContentId(nextCid);
    const matched = items.find((item) => item.id === nextCid);
    if (matched?.platform && PLATFORMS.includes(matched.platform as (typeof PLATFORMS)[number])) {
      setSelectedPlatform(matched.platform);
    }
  };

  const handleFilePicked = (file: File | null) => {
    setError(null);
    setSelectedFile(file);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer?.files?.[0] ?? null;
    if (dropped) {
      handleFilePicked(dropped);
    }
  };

  const handleReadScreenshot = async () => {
    if (!selectedFile) {
      setError('Please choose a PNG, JPEG, or WebP screenshot first.');
      return;
    }
    setError(null);
    setReading(true);
    try {
      const res = await extractResultsScreenshot(selectedFile, {
        content_id: selectedContentId || null,
        platform: selectedPlatform,
      });
      setProposal(res.proposal);
      setSelectedContentId(res.proposal.content_id || selectedContentId);
      if (res.proposal.platform) {
        setSelectedPlatform(res.proposal.platform);
      }
      setFieldValues(buildFieldValues(res.proposal.metrics));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read screenshot.');
    } finally {
      setReading(false);
    }
  };

  const handleConfirmSave = async () => {
    if (!proposal) return;
    setError(null);

    const parsedMetrics: Record<string, number> = {};
    for (const field of METRIC_FIELDS) {
      const raw = (fieldValues[field.key] ?? '').trim();
      if (!raw) continue;
      const num = Number(raw);
      if (!Number.isFinite(num) || num < 0) {
        setError(`${field.label} must be a non-negative number.`);
        return;
      }
      parsedMetrics[field.key] = num;
    }
    if (Object.keys(parsedMetrics).length === 0) {
      setError('Enter at least one metric value before saving.');
      return;
    }

    setSaving(true);
    try {
      const res = await confirmResultsProposal(proposal.id, {
        metrics: parsedMetrics,
        content_id: selectedContentId || null,
        platform: selectedPlatform,
      });
      onSaved?.(res);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save results.');
    } finally {
      setSaving(false);
    }
  };

  const handleDiscard = async () => {
    if (!proposal) {
      onClose();
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await discardResultsProposal(proposal.id);
      onDiscarded?.(proposal.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to discard results.');
    } finally {
      setSaving(false);
    }
  };

  const thumbnailSrc =
    localPreviewUrl || (proposal?.image_path ? proposalImageUrl(proposal.id) : null);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      subtitle="Analyst"
      title="Add results"
      className="studio-results-dialog"
      actions={
        proposal ? (
          <>
            <PillButton
              variant="danger"
              size="sm"
              disabled={saving}
              onClick={() => void handleDiscard()}
            >
              Discard
            </PillButton>
            <PillButton
              variant="quiet"
              size="sm"
              disabled={saving}
              onClick={onClose}
            >
              Later
            </PillButton>
            <PillButton
              size="sm"
              disabled={saving}
              onClick={() => void handleConfirmSave()}
            >
              {saving ? 'Saving...' : 'Save results'}
            </PillButton>
          </>
        ) : (
          <>
            <PillButton variant="quiet" size="sm" onClick={onClose}>
              Cancel
            </PillButton>
            <PillButton
              size="sm"
              disabled={reading || !selectedFile}
              onClick={() => void handleReadScreenshot()}
            >
              {reading ? 'Reading...' : 'Read screenshot'}
            </PillButton>
          </>
        )
      }
    >
      <div className="studio-stack-sm">
        {error ? (
          <div className="mg-notice" role="alert">
            {error}
          </div>
        ) : null}

        {!proposal ? (
          <>
            <div className="studio-grid-2">
              <Field label="Post" htmlFor="results-post-select">
                <Select
                  id="results-post-select"
                  value={selectedContentId}
                  onChange={(e) => handleSelectPost(e.target.value)}
                >
                  <option value="">Unlinked post</option>
                  {selectablePosts.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title || item.id} ({item.status})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Platform" htmlFor="results-platform-select">
                <Select
                  id="results-platform-select"
                  value={selectedPlatform}
                  onChange={(e) => setSelectedPlatform(e.target.value)}
                >
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div
              className={`studio-dropzone${dragActive ? ' studio-dropzone--active' : ''}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
              aria-label="Analytics screenshot dropzone"
            >
              <Field label="Analytics screenshot (PNG, JPEG, WebP, max 10 MB)" htmlFor="results-file-input">
                <input
                  ref={fileInputRef}
                  id="results-file-input"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="mg-input"
                  onChange={(e) => handleFilePicked(e.target.files?.[0] ?? null)}
                />
              </Field>
              {selectedFile ? (
                <div className="studio-stack-xs">
                  <span className="studio-mono">{selectedFile.name}</span>
                  {localPreviewUrl ? (
                    <img
                      src={localPreviewUrl}
                      alt="Selected analytics screenshot"
                      className="studio-results-thumb"
                    />
                  ) : null}
                </div>
              ) : (
                <span className="studio-hint">
                  Drop an analytics screenshot here or choose a file, then click Read screenshot to extract numbers for review.
                </span>
              )}
            </div>
          </>
        ) : (
          <div className="studio-results-review">
            <div className="studio-stack-sm">
              {thumbnailSrc ? (
                <img
                  src={thumbnailSrc}
                  alt="Analytics screenshot"
                  className="studio-results-thumb"
                />
              ) : (
                <Card outline>
                  <span className="studio-hint">No screenshot attached.</span>
                </Card>
              )}

              <Field label="Post" htmlFor="results-confirm-post">
                <Select
                  id="results-confirm-post"
                  value={selectedContentId}
                  onChange={(e) => handleSelectPost(e.target.value)}
                >
                  <option value="">Unlinked post</option>
                  {selectablePosts.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title || item.id} ({item.status})
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Platform" htmlFor="results-confirm-platform">
                <Select
                  id="results-confirm-platform"
                  value={selectedPlatform}
                  onChange={(e) => setSelectedPlatform(e.target.value)}
                >
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="studio-stack-xs">
              <div className="studio-hint">
                Review and edit extracted metrics before saving. Empty fields are omitted.
              </div>
              <div className="studio-results-fields">
                {METRIC_FIELDS.map((field) => {
                  const evText = proposal.evidence?.[field.key];
                  return (
                    <div key={field.key} className="studio-results-field-row">
                      <label className="mg-label" htmlFor={`metric-field-${field.key}`}>
                        {field.label}
                      </label>
                      <TextInput
                        id={`metric-field-${field.key}`}
                        aria-label={field.label}
                        type="number"
                        min="0"
                        step="any"
                        placeholder="—"
                        value={fieldValues[field.key] ?? ''}
                        onChange={(e) =>
                          setFieldValues((prev) => ({
                            ...prev,
                            [field.key]: e.target.value,
                          }))
                        }
                      />
                      <span className="studio-results-evidence">
                        {evText ? `Seen: ${evText}` : 'Not found'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}
