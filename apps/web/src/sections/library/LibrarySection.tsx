import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import {
  Card,
  Dialog,
  Field,
  PillButton,
  RowList,
  Select,
  SheetHeader,
  TextInput,
} from '@magnm/design/react';
import { deleteAsset, listAssets, listContent, uploadAsset } from '../../api/library';
import type { AssetRecord, ContentItem } from '../../types';
import { formatTimestamp } from '../../ui/format';

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const commaIdx = result.indexOf(',');
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error('Failed to read file.'));
    reader.readAsDataURL(file);
  });
}

function formatAssetTypeLabel(asset: AssetRecord): string {
  const group = (asset.file_group || 'file').toUpperCase();
  return asset.size ? `${group} · ${asset.size}` : group;
}

/** Library section: brand assets grid with upload/preview/delete, and content list linking to Review. */
export function LibrarySection() {
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [contentItems, setContentItems] = useState<ContentItem[]>([]);

  const [typeFilter, setTypeFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [contentFilter, setContentFilter] = useState('all');

  const [selectedAsset, setSelectedAsset] = useState<AssetRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AssetRecord | null>(null);

  const [uploadCategory, setUploadCategory] = useState('cero');
  const [uploadDescription, setUploadDescription] = useState('');
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([listAssets(), listContent(200)]);

    let firstErr: string | null = null;
    const [assetsRes, contentRes] = results;

    if (assetsRes.status === 'fulfilled') {
      const nextAssets = Array.isArray(assetsRes.value) ? assetsRes.value : [];
      setAssets(nextAssets);
      setSelectedAsset((prev) =>
        prev ? nextAssets.find((a) => a.id === prev.id) ?? null : null,
      );
    } else if (assetsRes.reason instanceof Error) {
      firstErr = firstErr ?? assetsRes.reason.message;
    }

    if (contentRes.status === 'fulfilled') {
      setContentItems(Array.isArray(contentRes.value) ? contentRes.value : []);
    } else if (contentRes.reason instanceof Error) {
      firstErr = firstErr ?? contentRes.reason.message;
    }

    setError(firstErr);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const filteredAssets = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return assets.filter((asset) => {
      if (typeFilter !== 'all' && (asset.file_group || 'document') !== typeFilter) {
        return false;
      }
      if (q) {
        const hay = `${asset.name || ''} ${asset.description || ''} ${asset.category || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [assets, typeFilter, searchQuery]);

  const filteredContent = useMemo(() => {
    return contentItems.filter((item) => {
      if (item.type === 'review') return false;
      if (contentFilter !== 'all' && item.status !== contentFilter) return false;
      return true;
    });
  }, [contentItems, contentFilter]);

  const performUpload = async (file: File) => {
    setError(null);
    setNotice(null);
    setUploading(true);
    try {
      const contentBase64 = await readFileAsBase64(file);
      const res = await uploadAsset({
        filename: file.name,
        content_base64: contentBase64,
        category: uploadCategory,
        description: uploadDescription.trim(),
      });
      if (res.asset) {
        setAssets((prev) => [res.asset, ...prev.filter((a) => a.id !== res.asset.id)]);
        setSelectedAsset(res.asset);
      } else {
        await loadAll();
      }
      setStagedFile(null);
      setUploadDescription('');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      setNotice(`Uploaded ${file.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload asset.');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer?.files?.[0];
    if (dropped) {
      void performUpload(dropped);
    }
  };

  const handleConfirmDelete = async () => {
    if (!confirmDelete) return;
    const target = confirmDelete;
    setBusyId(target.id);
    setError(null);
    try {
      await deleteAsset(target.id);
      setAssets((prev) => prev.filter((a) => a.id !== target.id));
      if (selectedAsset?.id === target.id) {
        setSelectedAsset(null);
      }
      setConfirmDelete(null);
      setNotice(`Deleted ${target.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete asset.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="studio-stack">
      <SheetHeader
        page={7}
        total={9}
        subtitle="Media & Drafts"
        title="Library"
        lead="Uploaded screenshots, video recordings, brand assets, and generated content ready for Review."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      <section className="studio-section" aria-labelledby="library-upload-heading">
        <h2 id="library-upload-heading" className="studio-section-title">
          Upload asset
        </h2>
        <div
          className={`studio-dropzone${dragActive ? ' studio-dropzone--active' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          aria-label="Asset upload dropzone"
        >
          <div className="studio-grid-2">
            <Field label="File (drag and drop or pick)" htmlFor="library-file-input">
              <input
                ref={fileInputRef}
                id="library-file-input"
                type="file"
                className="mg-input"
                onChange={(e) => setStagedFile(e.target.files?.[0] ?? null)}
              />
            </Field>
            <Field label="Category" htmlFor="library-upload-category">
              <Select
                id="library-upload-category"
                value={uploadCategory}
                onChange={(e) => setUploadCategory(e.target.value)}
              >
                <option value="cero">Cero product media</option>
                <option value="brand">Brand identity</option>
                <option value="screenshot">Product screenshot</option>
                <option value="recording">Screen recording</option>
                <option value="document">Document</option>
              </Select>
            </Field>
          </div>
          <div className="studio-inline-form">
            <TextInput
              aria-label="Asset description"
              placeholder="Optional usage notes or description"
              value={uploadDescription}
              onChange={(e) => setUploadDescription(e.target.value)}
            />
            <PillButton
              size="sm"
              disabled={uploading || !stagedFile}
              onClick={() => {
                if (stagedFile) {
                  void performUpload(stagedFile);
                }
              }}
            >
              {uploading ? 'Uploading...' : 'Upload asset'}
            </PillButton>
          </div>
          <span className="studio-hint">
            Drop an image, video, audio file, or document here to upload immediately, or choose a file above.
          </span>
        </div>
      </section>

      <section className="studio-section" aria-labelledby="library-assets-heading">
        <div className="studio-section-bar">
          <h2 id="library-assets-heading" className="studio-section-title">
            Assets ({filteredAssets.length})
          </h2>
          <div className="studio-filter-row">
            <Select
              aria-label="Filter assets by type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="all">All types</option>
              <option value="image">Images</option>
              <option value="video">Videos</option>
              <option value="audio">Audio</option>
              <option value="document">Documents</option>
            </Select>
            <TextInput
              aria-label="Search assets by name"
              placeholder="Search by name..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {loading && assets.length === 0 ? (
          <div className="mg-empty">Loading assets.</div>
        ) : filteredAssets.length === 0 ? (
          <div className="mg-empty">
            {assets.length === 0
              ? 'No assets uploaded yet.'
              : 'No assets match the current filter.'}
          </div>
        ) : (
          <div
            className={`studio-library-layout${selectedAsset ? ' studio-library-layout--with-panel' : ''}`}
          >
            <div className="studio-asset-grid">
              {filteredAssets.map((asset) => {
                const isSelected = selectedAsset?.id === asset.id;
                const fileUrl = `/api/asset_file/${encodeURIComponent(asset.id)}`;
                return (
                  <div
                    key={asset.id}
                    className={`studio-asset-card${isSelected ? ' studio-asset-card--selected' : ''}`}
                  >
                    <button
                      type="button"
                      className="studio-asset-thumb"
                      aria-label={`Inspect ${asset.name}`}
                      onClick={() => setSelectedAsset(asset)}
                    >
                      {asset.file_group === 'image' ? (
                        <img src={fileUrl} alt={asset.name} loading="lazy" />
                      ) : asset.file_group === 'video' ? (
                        <span className="studio-mono">VIDEO</span>
                      ) : (
                        <span className="studio-mono">
                          {(asset.file_group || 'FILE').toUpperCase()}
                        </span>
                      )}
                    </button>

                    <button
                      type="button"
                      className="studio-asset-name"
                      title={asset.name}
                      onClick={() => setSelectedAsset(asset)}
                    >
                      {asset.name}
                    </button>

                    <div className="studio-mono">{formatAssetTypeLabel(asset)}</div>

                    <div className="studio-section-bar">
                      <PillButton
                        variant="quiet"
                        size="sm"
                        onClick={() => setSelectedAsset(asset)}
                      >
                        Details
                      </PillButton>
                      <PillButton
                        variant="danger"
                        size="sm"
                        onClick={() => setConfirmDelete(asset)}
                      >
                        Delete
                      </PillButton>
                    </div>
                  </div>
                );
              })}
            </div>

            {selectedAsset ? (
              <aside className="studio-detail-panel" aria-label="Asset details">
                <Card outline>
                  <div className="studio-stack-sm">
                    <div className="studio-card-head">
                      <div>
                        <div className="mg-label">Asset details</div>
                        <div className="studio-card-title">{selectedAsset.name}</div>
                      </div>
                      <PillButton
                        variant="quiet"
                        size="sm"
                        onClick={() => setSelectedAsset(null)}
                      >
                        Close
                      </PillButton>
                    </div>

                    {selectedAsset.file_group === 'image' ? (
                      <img
                        className="studio-detail-media"
                        src={`/api/asset_file/${encodeURIComponent(selectedAsset.id)}`}
                        alt={selectedAsset.name}
                      />
                    ) : selectedAsset.file_group === 'video' ? (
                      <video
                        className="studio-detail-media"
                        controls
                        preload="metadata"
                        src={`/api/asset_file/${encodeURIComponent(selectedAsset.id)}`}
                      />
                    ) : selectedAsset.file_group === 'audio' ? (
                      <audio
                        controls
                        src={`/api/asset_file/${encodeURIComponent(selectedAsset.id)}`}
                      />
                    ) : null}

                    <div className="studio-stack-xs">
                      <span className="studio-mono-wrap">ID: {selectedAsset.id}</span>
                      <span className="studio-mono">
                        Type: {selectedAsset.file_group || 'file'} (
                        {selectedAsset.mime_type || 'application/octet-stream'})
                      </span>
                      {selectedAsset.size ? (
                        <span className="studio-mono">Size: {selectedAsset.size}</span>
                      ) : null}
                      {selectedAsset.category ? (
                        <span className="studio-mono">Category: {selectedAsset.category}</span>
                      ) : null}
                      {selectedAsset.uploaded_at ? (
                        <span className="studio-mono">
                          Uploaded: {formatTimestamp(selectedAsset.uploaded_at)}
                        </span>
                      ) : null}
                      {selectedAsset.description ? (
                        <p className="mg-sub">{selectedAsset.description}</p>
                      ) : null}
                    </div>

                    <div className="studio-btn-group">
                      <a
                        className="mg-btn mg-btn--sm"
                        href={`/api/asset_file/${encodeURIComponent(selectedAsset.id)}`}
                        download={selectedAsset.name}
                      >
                        Download
                      </a>
                      <PillButton
                        variant="danger"
                        size="sm"
                        onClick={() => setConfirmDelete(selectedAsset)}
                      >
                        Delete
                      </PillButton>
                    </div>
                  </div>
                </Card>
              </aside>
            ) : null}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="library-content-heading">
        <div className="studio-section-bar">
          <h2 id="library-content-heading" className="studio-section-title">
            Content ({filteredContent.length})
          </h2>
          <Select
            aria-label="Filter content by status"
            value={contentFilter}
            onChange={(e) => setContentFilter(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="draft">Draft</option>
            <option value="ready">Ready</option>
            <option value="posted">Posted</option>
          </Select>
        </div>

        {loading && contentItems.length === 0 ? (
          <div className="mg-empty">Loading content items.</div>
        ) : filteredContent.length === 0 ? (
          <div className="mg-empty">No content items match this status.</div>
        ) : (
          <RowList aria-label="Content library">
            {filteredContent.map((item) => (
              <a
                key={item.id}
                className="mg-row"
                href={`#/review/${encodeURIComponent(item.id)}`}
              >
                <div className="mg-row-main">
                  <span className="mg-row-title">{item.title || item.id}</span>
                  <span className="mg-row-desc">
                    {item.type}
                    {item.platform ? ` · ${item.platform}` : ''}
                    {item.product_id ? ` · ${item.product_id}` : ''}
                    {item.updated_at ? ` · ${formatTimestamp(item.updated_at)}` : ''}
                  </span>
                </div>
                <span className="mg-row-meta">{item.status}</span>
              </a>
            ))}
          </RowList>
        )}
      </section>

      <Dialog
        open={Boolean(confirmDelete)}
        onClose={() => setConfirmDelete(null)}
        title="Delete asset"
        subtitle="Library"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmDelete(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              disabled={Boolean(confirmDelete && busyId === confirmDelete.id)}
              onClick={() => void handleConfirmDelete()}
            >
              {confirmDelete && busyId === confirmDelete.id ? 'Deleting...' : 'Delete asset'}
            </PillButton>
          </>
        }
      >
        {confirmDelete ? (
          <p className="mg-sub">
            Remove <span className="studio-mono">{confirmDelete.name}</span> from the asset
            library? This deletes the uploaded file.
          </p>
        ) : null}
      </Dialog>
    </div>
  );
}
