import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Field, Label, PillButton, Select, TextInput } from '@magnm/design/react';
import { stateLabel } from '../actions';
import type { ContextCacheState } from '../cache';
import {
  assetsFromMirror,
  fetchLaptopLibrary,
  fetchPhoneUploads,
  shareLaptopAsset,
  uploadFromPhone,
  type LibraryAsset,
  type PhoneUpload,
} from '../libraryPhone';
import type { NativeBridge } from '../native';
import type { PairingConfig, PhoneAction, ProductRulesConfig } from '../types';
import { LIBRARY_CATEGORIES } from '../workRules';

const CATEGORY_LABELS: Record<string, string> = {
  cero: 'Cero product media',
  brand: 'Brand identity',
  screenshot: 'Product screenshot',
  recording: 'Screen recording',
  document: 'Document',
};

export interface LibraryTabProps {
  ownerUid: string | null;
  signedIn: boolean;
  laptopReachable: boolean;
  pairing: PairingConfig | null;
  cache: ContextCacheState;
  phoneActions: PhoneAction[];
  bridge: NativeBridge;
  savedCopyLabel: string | null;
  rules: ProductRulesConfig | null;
  onActionsChanged: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function daysLeft(expires: string): string {
  const days = Math.max(0, Math.ceil((Date.parse(expires) - Date.now()) / 86_400_000));
  return days === 1 ? '1 day left' : `${days} days left`;
}

/** Library: upload from the phone, see the cloud copy of what is waiting, and the laptop's assets. */
export function LibraryTab({ ownerUid, signedIn, laptopReachable, pairing, cache, phoneActions, bridge, savedCopyLabel, rules, onActionsChanged }: LibraryTabProps) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState('brand');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [uploads, setUploads] = useState<PhoneUpload[]>([]);
  const [live, setLive] = useState<LibraryAsset[] | null>(null);
  const [getting, setGetting] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refreshUploads = useCallback(async () => {
    if (!ownerUid) return;
    try {
      const list = await fetchPhoneUploads(ownerUid, bridge);
      if (mounted.current) setUploads(list);
    } catch {
      // Offline: the list simply keeps what it had. The queue below still shows every upload action.
    }
  }, [ownerUid, bridge]);

  useEffect(() => {
    void refreshUploads();
  }, [refreshUploads, phoneActions]);

  // The live list from the laptop while it is reachable; the saved cloud copy otherwise.
  useEffect(() => {
    let cancelled = false;
    if (!laptopReachable || !pairing) {
      setLive(null);
      return undefined;
    }
    fetchLaptopLibrary(pairing, bridge)
      .then((list) => {
        if (!cancelled) setLive(list);
      })
      .catch(() => {
        if (!cancelled) setLive(null);
      });
    return () => {
      cancelled = true;
    };
  }, [laptopReachable, pairing, bridge]);

  const assets = live ?? assetsFromMirror(cache);
  const uploadActions = phoneActions.filter((a) => a.kind === 'library_upload');

  async function handleUpload() {
    if (!file || !ownerUid || busy) return;
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      await uploadFromPhone({ ownerUid, file, category, description: note, rules, bridge });
      if (!mounted.current) return;
      setMessage('Uploaded. Your laptop adds it to its Library when it is back.');
      setFile(null);
      setNote('');
      if (fileInput.current) fileInput.current.value = '';
      onActionsChanged();
      void refreshUploads();
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : 'The upload failed.');
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function handleGet(asset: LibraryAsset) {
    if (!pairing) return;
    setError(null);
    setGetting(asset.id);
    try {
      await shareLaptopAsset(pairing, asset.id, asset.name, bridge);
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : 'Could not get the file.');
    } finally {
      if (mounted.current) setGetting(null);
    }
  }

  return (
    <div className="mob-stack">
      <Card>
        <div className="mob-stack-sm">
          <Label>Add to your Library</Label>
          <div className="mob-body-text">
            Images, videos and PDFs up to 50 MB. They go to your private cloud and your laptop adds them to its Library when it is back. They are only stored, never opened or run.
          </div>
          <Field label="Choose a file" htmlFor="lib-file">
            <input
              id="lib-file"
              ref={fileInput}
              type="file"
              className="mg-input"
              accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,application/pdf"
              disabled={!signedIn || busy}
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setError(null);
                setMessage(null);
              }}
            />
          </Field>
          <Field label="Category" htmlFor="lib-category">
            <Select id="lib-category" value={category} onChange={(e) => setCategory(e.target.value)} disabled={!signedIn || busy}>
              {LIBRARY_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABELS[c]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Note (optional)" htmlFor="lib-note">
            <TextInput id="lib-note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} disabled={!signedIn || busy} />
          </Field>
          {error ? (
            <div className="mob-banner-error" role="alert">
              {error}
            </div>
          ) : null}
          {message ? <div className="mg-notice">{message}</div> : null}
          <div className="mob-actions">
            <PillButton size="sm" disabled={!signedIn || !file || busy} onClick={() => void handleUpload()}>
              Upload to my Library
            </PillButton>
          </div>
        </div>
      </Card>

      {uploads.length > 0 || uploadActions.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Waiting for the laptop</Label>
            {uploads.map((u) => {
              const act = uploadActions.find((a) => a.target_id === u.id);
              return (
                <div key={u.id} className="mob-asset">
                  <div className="mob-wrap-text">{u.file_name}</div>
                  <div className="mob-asset-meta">
                    {u.kind} · {formatSize(u.bytes)} · {act ? stateLabel(act) : 'Waiting for laptop'} · {daysLeft(u.expires_at)}
                  </div>
                </div>
              );
            })}
            {uploadActions
              .filter((a) => !uploads.some((u) => u.id === a.target_id))
              .slice(-8)
              .reverse()
              .map((a) => (
                <div key={a.id} className="mob-asset">
                  <div className="mob-wrap-text">{a.label ?? 'Upload'}</div>
                  <div className="mob-asset-meta">
                    {stateLabel(a)}
                    {a.result ? ` · ${a.result}` : ''}
                  </div>
                </div>
              ))}
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="mob-stack-sm">
          <Label>On the laptop</Label>
          {live === null ? (
            <div className="mob-body-text">
              {laptopReachable ? 'Loading the laptop Library.' : 'The laptop is not reachable.'} {savedCopyLabel ? `${savedCopyLabel}.` : ''}
            </div>
          ) : null}
          {assets.length === 0 ? <div className="mob-body-text">No assets in your saved copy yet.</div> : null}
          {assets.map((asset) => {
            const reachable = live !== null && laptopReachable;
            const canGet = reachable && asset.fetchable && !asset.confidential;
            return (
              <div key={asset.id} className="mob-asset" aria-disabled={reachable ? undefined : 'true'} data-grey={reachable ? undefined : 'true'}>
                <div className="mob-wrap-text">{asset.confidential ? 'Confidential item' : asset.name}</div>
                <div className="mob-asset-meta">
                  {asset.confidential
                    ? 'Only on the laptop'
                    : [asset.fileGroup, formatSize(asset.sizeBytes), CATEGORY_LABELS[asset.category] ?? asset.category].filter(Boolean).join(' · ')}
                  {reachable ? '' : ' · On the laptop'}
                </div>
                {!asset.confidential ? (
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={!canGet || getting === asset.id}
                    aria-label={`Get on phone: ${asset.name}`}
                    title={canGet ? undefined : reachable ? 'Only images, videos and PDFs can be sent to the phone' : 'Connect to your laptop to get this file'}
                    onClick={() => void handleGet(asset)}
                  >
                    Get on phone
                  </PillButton>
                ) : null}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
