import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Card,
  Dialog,
  NumberedTabs,
  PillButton,
  Row,
  RowList,
  Select,
  TextInput,
} from '@magnm/design/react';
import {
  getMemberPreviews,
  getMemberRecordDetail,
  getMemberRecords,
  type AdminMember,
  type AdminPreview,
  type MirrorRecordDetail,
  type MirrorRecordKind,
  type MirrorRecordSummary,
} from '../../api/admin';
import { formatTimestamp, isSafeHttpUrl } from '../../ui/format';
import { WORKSPACE_KIND_TABS, memberDisplayName } from './adminUtils';

export interface AdminWorkspacePanelProps {
  members: AdminMember[];
  memberId: string;
  onSelectMember: (memberId: string) => void;
  onBackToTeam: () => void;
}

interface PreviewPair {
  sourceId: string;
  thumb: AdminPreview | null;
  full: AdminPreview;
  isVideo: boolean;
}

function SafeExternalLink({ url }: { url: unknown }) {
  const raw = typeof url === 'string' ? url.trim() : '';
  if (!raw) return <span className="studio-hint">No URL</span>;
  if (isSafeHttpUrl(raw)) {
    return (
      <a
        href={raw}
        target="_blank"
        rel="noopener noreferrer"
        className="studio-inline-link studio-mono-wrap"
      >
        {raw}
      </a>
    );
  }
  return <span className="studio-mono-wrap">{raw}</span>;
}

function formatJsonValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function groupPreviewsBySource(previews: AdminPreview[]): PreviewPair[] {
  const bySource = new Map<string, { thumb: AdminPreview | null; image: AdminPreview | null; video: AdminPreview | null }>();
  for (const prev of previews) {
    const key = prev.local_source_id || prev.id;
    const entry = bySource.get(key) ?? { thumb: null, image: null, video: null };
    if (prev.kind === 'thumbnail') entry.thumb = prev;
    else if (prev.kind === 'video_preview') entry.video = prev;
    else if (prev.kind === 'image_preview') entry.image = prev;
    bySource.set(key, entry);
  }
  const pairs: PreviewPair[] = [];
  for (const [sourceId, entry] of bySource.entries()) {
    const full = entry.video || entry.image || entry.thumb;
    if (!full) continue;
    pairs.push({
      sourceId,
      thumb: entry.thumb,
      full,
      isVideo: Boolean(entry.video || full.content_type === 'video/mp4'),
    });
  }
  return pairs;
}

/** Read-only member workspace browser: kind tabs, title search, record detail, and signed previews. */
export function AdminWorkspacePanel({
  members,
  memberId,
  onSelectMember,
  onBackToTeam,
}: AdminWorkspacePanelProps) {
  const [activeKind, setActiveKind] = useState<MirrorRecordKind>('content_item');
  const [searchQuery, setSearchQuery] = useState('');
  const [records, setRecords] = useState<MirrorRecordSummary[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedRef, setSelectedRef] = useState<{ kind: string; localId: string } | null>(null);
  const [detail, setDetail] = useState<MirrorRecordDetail | null>(null);
  const [children, setChildren] = useState<MirrorRecordSummary[]>([]);
  const [childDetails, setChildDetails] = useState<Record<string, MirrorRecordDetail>>({});
  const [previews, setPreviews] = useState<AdminPreview[]>([]);
  const [activePreviewSourceId, setActivePreviewSourceId] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const listReqRef = useRef(0);
  const detailReqRef = useRef(0);
  const previewRetriedRef = useRef(false);

  const loadList = useCallback(
    async (beforeCursor?: string) => {
      if (!memberId) return;
      const reqId = ++listReqRef.current;
      if (beforeCursor) {
        setLoadingMore(true);
      } else {
        setLoadingList(true);
        setListError(null);
      }
      try {
        const res = await getMemberRecords(memberId, {
          kind: activeKind,
          q: searchQuery.trim() || undefined,
          before: beforeCursor || undefined,
          limit: 50,
        });
        if (listReqRef.current !== reqId) return;
        if (beforeCursor) {
          setRecords((prev) => [...prev, ...res.records]);
        } else {
          setRecords(res.records);
        }
        setNextBefore(res.next_before);
      } catch (err) {
        if (listReqRef.current !== reqId) return;
        setListError(err instanceof Error ? err.message : 'Failed to load records.');
      } finally {
        if (listReqRef.current === reqId) {
          setLoadingList(false);
          setLoadingMore(false);
        }
      }
    },
    [activeKind, memberId, searchQuery],
  );

  useEffect(() => {
    setSelectedRef(null);
    setDetail(null);
    setChildren([]);
    setChildDetails({});
    setPreviews([]);
    setActivePreviewSourceId(null);
  }, [memberId, activeKind]);

  useEffect(() => {
    if (!memberId) return undefined;
    const timer = setTimeout(() => {
      void loadList();
    }, searchQuery ? 120 : 0);
    return () => clearTimeout(timer);
  }, [loadList, memberId, searchQuery]);

  const loadPreviewsForParent = useCallback(
    async (parentLocalId: string) => {
      if (!memberId) return;
      try {
        const res = await getMemberPreviews(memberId, parentLocalId);
        setPreviews(res.previews);
      } catch {
        setPreviews([]);
      }
    },
    [memberId],
  );

  const openRecord = useCallback(
    async (kind: string, localId: string) => {
      if (!memberId) return;
      const reqId = ++detailReqRef.current;
      setSelectedRef({ kind, localId });
      setLoadingDetail(true);
      setDetailError(null);
      setDetail(null);
      setChildren([]);
      setChildDetails({});
      setPreviews([]);
      setActivePreviewSourceId(null);
      previewRetriedRef.current = false;

      try {
        const res = await getMemberRecordDetail(memberId, kind, localId);
        if (detailReqRef.current !== reqId) return;
        setDetail(res.record);
        setChildren(res.children);

        const isConf = Boolean(res.record.data?.confidential);
        if (kind === 'content_item' && !isConf) {
          void loadPreviewsForParent(localId);

          const needFetch = res.children.filter((c) => !c.data);
          if (needFetch.length > 0) {
            const settled = await Promise.allSettled(
              needFetch.slice(0, 30).map((c) => getMemberRecordDetail(memberId, c.kind, c.local_id)),
            );
            if (detailReqRef.current !== reqId) return;
            const nextMap: Record<string, MirrorRecordDetail> = {};
            for (const item of settled) {
              if (item.status === 'fulfilled' && item.value?.record) {
                const rec = item.value.record;
                nextMap[`${rec.kind}:${rec.local_id}`] = rec;
              }
            }
            setChildDetails(nextMap);
          }
        }
      } catch (err) {
        if (detailReqRef.current !== reqId) return;
        setDetailError(err instanceof Error ? err.message : 'Failed to load record.');
      } finally {
        if (detailReqRef.current === reqId) {
          setLoadingDetail(false);
        }
      }
    },
    [loadPreviewsForParent, memberId],
  );

  const handlePreviewImageError = useCallback(() => {
    if (!detail || previewRetriedRef.current) return;
    previewRetriedRef.current = true;
    void loadPreviewsForParent(detail.local_id);
  }, [detail, loadPreviewsForParent]);

  const previewPairs = useMemo(() => groupPreviewsBySource(previews), [previews]);
  const activePreviewPair = useMemo(
    () => previewPairs.find((p) => p.sourceId === activePreviewSourceId) ?? null,
    [activePreviewSourceId, previewPairs],
  );

  const resolveChild = (child: MirrorRecordSummary): MirrorRecordSummary => {
    if (child.data) return child;
    return childDetails[`${child.kind}:${child.local_id}`] ?? child;
  };

  const renderConfidentialPlaceholder = (updatedAt: string | null | undefined) => (
    <div className="studio-stack-sm" data-testid="confidential-record">
      <div className="studio-card-title">Confidential</div>
      <div className="studio-mono">{formatTimestamp(updatedAt)}</div>
    </div>
  );

  const renderDetailContent = () => {
    if (!selectedRef) {
      return <div className="mg-empty">Select an item to read it.</div>;
    }
    if (loadingDetail && !detail) {
      return <div className="studio-hint">Loading item...</div>;
    }
    if (detailError) {
      return (
        <div className="mg-notice" role="alert">
          {detailError}
        </div>
      );
    }
    if (!detail) {
      return <div className="mg-empty">Record not found.</div>;
    }

    const data = detail.data || {};
    if (data.confidential) {
      return renderConfidentialPlaceholder(detail.local_updated_at);
    }

    if (detail.kind === 'chat_session') {
      const messages = Array.isArray(data.messages) ? data.messages : [];
      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">
                {String(data.agent_id || 'Agent')} · {String(data.status || 'chat')}
              </div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>

          {messages.length === 0 ? (
            <div className="studio-hint">No messages recorded in this session.</div>
          ) : (
            <div className="studio-messages" aria-label="Chat messages">
              {messages.map((msg, idx) => {
                const m = (msg && typeof msg === 'object' ? msg : {}) as Record<string, unknown>;
                const role = String(m.role || 'assistant');
                const text = typeof m.text === 'string' ? m.text : '';
                const note = typeof m.note === 'string' ? m.note : '';
                return (
                  <Card
                    key={idx}
                    outline={role === 'user'}
                    className={role === 'user' ? 'studio-msg studio-msg--user' : 'studio-msg'}
                  >
                    <div className="studio-msg-head">
                      <span className="mg-label studio-msg-author">
                        {role === 'user' ? 'Member' : String(data.agent_id || 'Assistant')}
                      </span>
                    </div>
                    {text ? <div className="studio-admin-text-body">{text}</div> : null}
                    {note ? <div className="studio-mono">{note}</div> : null}
                  </Card>
                );
              })}
            </div>
          )}

          {children.length > 0 ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Sub-chats</span>
              <RowList aria-label="Sub-chats">
                {children.map((sub) => (
                  <Row
                    key={sub.local_id}
                    title={sub.title || sub.local_id}
                    meta={formatTimestamp(sub.local_updated_at)}
                    onClick={() => void openRecord(sub.kind, sub.local_id)}
                  />
                ))}
              </RowList>
            </div>
          ) : null}
        </div>
      );
    }

    if (detail.kind === 'content_item') {
      const resolvedChildren = children.map(resolveChild);
      const versions = resolvedChildren.filter((c) => c.kind === 'content_version');
      const comments = resolvedChildren.filter((c) => c.kind === 'review_comment');
      const metrics = resolvedChildren.filter((c) => c.kind === 'metric');
      const postedLinks = resolvedChildren.filter((c) => c.kind === 'posted_link');

      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">
                {[data.type, data.platform, data.status].filter(Boolean).join(' · ') || 'Draft'}
              </div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>

          {data.posted_url ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Posted link</span>
              <SafeExternalLink url={data.posted_url} />
            </div>
          ) : null}

          {previewPairs.length > 0 ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Previews</span>
              <div className="studio-asset-grid" aria-label="Draft previews">
                {previewPairs.map((pair) => {
                  const thumbSrc = pair.thumb?.signed_url || (!pair.isVideo ? pair.full.signed_url : '');
                  return (
                    <div key={pair.sourceId} className="studio-asset-card">
                      <button
                        type="button"
                        className="studio-asset-thumb"
                        aria-label={`Open preview for ${pair.sourceId}`}
                        onClick={() => setActivePreviewSourceId(pair.sourceId)}
                      >
                        {thumbSrc ? (
                          <img
                            src={thumbSrc}
                            alt={`Preview ${pair.sourceId}`}
                            onError={handlePreviewImageError}
                          />
                        ) : (
                          <span className="studio-mono">
                            {pair.isVideo ? 'Video preview' : 'Image preview'}
                          </span>
                        )}
                      </button>
                      <span className="studio-mono">
                        {pair.isVideo ? 'Video' : 'Image'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          <div className="studio-stack-xs">
            <span className="mg-label">Versions</span>
            {versions.length === 0 ? (
              <div className="studio-hint">No versions synced for this draft.</div>
            ) : (
              versions.map((ver) => {
                const vdata = ver.data || {};
                if (vdata.confidential) {
                  return (
                    <Card key={ver.local_id} outline>
                      {renderConfidentialPlaceholder(ver.local_updated_at)}
                    </Card>
                  );
                }
                return (
                  <Card key={ver.local_id} outline>
                    <div className="studio-stack-xs">
                      <div className="studio-section-bar">
                        <span className="mg-label">
                          {ver.title || `v${String(vdata.version || '?')}`}
                          {vdata.created_by ? ` · ${String(vdata.created_by)}` : ''}
                        </span>
                        <span className="studio-mono">
                          {formatTimestamp(
                            typeof vdata.created_at === 'string'
                              ? vdata.created_at
                              : ver.local_updated_at,
                          )}
                        </span>
                      </div>
                      <pre className="studio-pre">
                        {typeof vdata.body === 'string' ? vdata.body : ''}
                      </pre>
                      {typeof vdata.notes === 'string' && vdata.notes.trim() ? (
                        <div className="studio-hint">Notes: {vdata.notes}</div>
                      ) : null}
                    </div>
                  </Card>
                );
              })
            )}
          </div>

          {comments.length > 0 ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Review comments</span>
              <RowList aria-label="Review comments">
                {comments.map((cmt) => {
                  const cdata = cmt.data || {};
                  if (cdata.confidential) {
                    return (
                      <Row
                        key={cmt.local_id}
                        title="Confidential"
                        meta={formatTimestamp(cmt.local_updated_at)}
                      />
                    );
                  }
                  return (
                    <Row
                      key={cmt.local_id}
                      title={
                        <span className="studio-mono">
                          {[cdata.author, cdata.version ? `v${cdata.version}` : '', cdata.status]
                            .filter(Boolean)
                            .join(' · ') || 'Comment'}
                        </span>
                      }
                      description={
                        <span className="studio-admin-text-body">
                          {typeof cdata.body === 'string' ? cdata.body : cmt.title || ''}
                        </span>
                      }
                      meta={formatTimestamp(cmt.local_updated_at)}
                    />
                  );
                })}
              </RowList>
            </div>
          ) : null}

          {metrics.length > 0 ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Metrics</span>
              <RowList aria-label="Draft metrics">
                {metrics.map((met) => {
                  const mdata = met.data || {};
                  if (mdata.confidential) {
                    return (
                      <Row
                        key={met.local_id}
                        title="Confidential"
                        meta={formatTimestamp(met.local_updated_at)}
                      />
                    );
                  }
                  return (
                    <Row
                      key={met.local_id}
                      title={met.title || 'Metrics'}
                      description={
                        <pre className="studio-pre">{formatJsonValue(mdata.metrics || {})}</pre>
                      }
                      meta={formatTimestamp(met.local_updated_at)}
                    />
                  );
                })}
              </RowList>
            </div>
          ) : null}

          {postedLinks.length > 0 ? (
            <div className="studio-stack-xs">
              <span className="mg-label">Posted links</span>
              <RowList aria-label="Draft posted links">
                {postedLinks.map((lnk) => {
                  const ldata = lnk.data || {};
                  if (ldata.confidential) {
                    return (
                      <Row
                        key={lnk.local_id}
                        title="Confidential"
                        meta={formatTimestamp(lnk.local_updated_at)}
                      />
                    );
                  }
                  return (
                    <Row
                      key={lnk.local_id}
                      title={lnk.title || String(ldata.platform || 'Posted link')}
                      description={<SafeExternalLink url={ldata.posted_url} />}
                      meta={formatTimestamp(lnk.local_updated_at)}
                    />
                  );
                })}
              </RowList>
            </div>
          ) : null}
        </div>
      );
    }

    if (detail.kind === 'posted_link') {
      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">{String(data.platform || 'Posted link')}</div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>
          <SafeExternalLink url={data.posted_url} />
        </div>
      );
    }

    if (detail.kind === 'research_note') {
      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">
                {[data.source, data.agent_id].filter(Boolean).join(' · ') || 'Research'}
              </div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>
          {data.url ? <SafeExternalLink url={data.url} /> : null}
          <pre className="studio-pre">{typeof data.body === 'string' ? data.body : ''}</pre>
        </div>
      );
    }

    if (detail.kind === 'review_comment') {
      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">
                {[data.author, data.kind, data.status].filter(Boolean).join(' · ') || 'Review'}
              </div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>
          <pre className="studio-pre">{typeof data.body === 'string' ? data.body : ''}</pre>
        </div>
      );
    }

    if (detail.kind === 'team_message') {
      return (
        <div className="studio-stack-sm">
          <div className="studio-card-head">
            <div>
              <div className="mg-label">
                {String(data.sender_name || data.sender || 'Team message')}
              </div>
              <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
            </div>
            <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
          </div>
          <pre className="studio-pre">{typeof data.text === 'string' ? data.text : ''}</pre>
        </div>
      );
    }

    return (
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">{detail.kind}</div>
            <h3 className="studio-card-title">{detail.title || detail.local_id}</h3>
          </div>
          <span className="studio-mono">{formatTimestamp(detail.local_updated_at)}</span>
        </div>
        <pre className="studio-pre">{formatJsonValue(data)}</pre>
      </div>
    );
  };

  return (
    <section className="studio-section" aria-labelledby="admin-workspace-heading">
      <div className="mg-notice" role="status">
        Read-only copy. Ask the member for full quality files.
      </div>

      <div className="studio-section-bar">
        <div className="studio-row-actions">
          <PillButton variant="outline" size="sm" onClick={onBackToTeam}>
            Back to team
          </PillButton>
          <h2 id="admin-workspace-heading" className="studio-section-title">
            Member workspace
          </h2>
        </div>

        <Select
          aria-label="Member workspace selector"
          value={memberId}
          onChange={(e) => onSelectMember(e.target.value)}
        >
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {memberDisplayName(m)}
            </option>
          ))}
        </Select>
      </div>

      <NumberedTabs
        items={WORKSPACE_KIND_TABS}
        current={activeKind}
        onSelect={(id) => setActiveKind(id as MirrorRecordKind)}
        ariaLabel="Workspace record kinds"
      />

      <div className="studio-filter-row">
        <TextInput
          type="search"
          aria-label="Search titles"
          placeholder="Search titles..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>

      {listError ? (
        <div className="mg-notice" role="alert">
          {listError}
        </div>
      ) : null}

      <div className="studio-admin-workspace-grid">
        <div className="studio-stack-sm studio-admin-workspace-list">
          {loadingList && records.length === 0 ? (
            <div className="mg-empty">Loading records.</div>
          ) : records.length === 0 ? (
            <div className="mg-empty">No records found.</div>
          ) : (
            <RowList aria-label="Workspace records">
              {records.map((rec) => {
                const isSelected =
                  selectedRef?.kind === rec.kind && selectedRef?.localId === rec.local_id;
                return (
                  <Row
                    key={`${rec.kind}:${rec.local_id}`}
                    selected={isSelected}
                    title={rec.title || rec.local_id}
                    description={<span className="studio-mono">{rec.kind}</span>}
                    meta={formatTimestamp(rec.local_updated_at)}
                    onClick={() => void openRecord(rec.kind, rec.local_id)}
                  />
                );
              })}
            </RowList>
          )}

          {nextBefore ? (
            <div>
              <PillButton
                variant="outline"
                size="sm"
                disabled={loadingMore}
                onClick={() => void loadList(nextBefore)}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </PillButton>
            </div>
          ) : null}
        </div>

        <div className="studio-admin-workspace-detail">
          <Card outline>{renderDetailContent()}</Card>
        </div>
      </div>

      <Dialog
        open={Boolean(activePreviewPair)}
        onClose={() => setActivePreviewSourceId(null)}
        title={activePreviewPair?.isVideo ? 'Video preview' : 'Image preview'}
        subtitle="Read-only preview"
      >
        {activePreviewPair ? (
          <div className="studio-stack-sm">
            {activePreviewPair.isVideo ? (
              <video
                controls
                preload="none"
                src={activePreviewPair.full.signed_url}
                className="studio-review-video"
                data-testid="admin-preview-video"
              />
            ) : (
              <img
                src={activePreviewPair.full.signed_url}
                alt={`Full preview ${activePreviewPair.sourceId}`}
                className="studio-detail-media"
                onError={handlePreviewImageError}
                data-testid="admin-preview-image"
              />
            )}
            <div className="studio-hint">
              Read-only copy. Ask the member for full quality files.
            </div>
          </div>
        ) : null}
      </Dialog>
    </section>
  );
}
