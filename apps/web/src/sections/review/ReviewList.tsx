import { useCallback, useEffect, useMemo, useState } from 'react';
import { Checkbox, PillButton, Row, RowList, SheetHeader, StatusDot } from '@magnm/design/react';
import { listReviewContent } from '../../api/review';
import { useStudioEvents } from '../../events';
import type { ContentItem } from '../../types';
import { formatTimestamp } from '../../ui/format';
import { mapContentStatusState } from './reviewUtils';

export interface ReviewListProps {
  onOpenItem: (contentId: string) => void;
}

interface StatusGroup {
  id: string;
  label: string;
  items: ContentItem[];
}

/** Review list screen (#/review): drafts and ready items grouped by status. */
export function ReviewList({ onOpenItem }: ReviewListProps) {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [showPosted, setShowPosted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setError(null);
    try {
      const data = await listReviewContent(100);
      setItems(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load content items.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'review.updated' || eventName === 'content_status') {
          void loadList();
        }
      },
      [loadList],
    ),
  );

  const groups = useMemo<StatusGroup[]>(() => {
    const drafts: ContentItem[] = [];
    const ready: ContentItem[] = [];
    const posted: ContentItem[] = [];

    for (const item of items) {
      const st = (item.status || 'draft').toLowerCase();
      if (st === 'ready') {
        ready.push(item);
      } else if (st === 'posted') {
        posted.push(item);
      } else {
        drafts.push(item);
      }
    }

    const result: StatusGroup[] = [
      { id: 'draft', label: 'Drafts', items: drafts },
      { id: 'ready', label: 'Ready', items: ready },
    ];
    if (showPosted && posted.length > 0) {
      result.push({ id: 'posted', label: 'Posted', items: posted });
    }
    return result;
  }, [items, showPosted]);

  const postedCount = useMemo(
    () => items.filter((item) => (item.status || '').toLowerCase() === 'posted').length,
    [items],
  );

  return (
    <div className="studio-stack">
      <SheetHeader
        page={6}
        total={10}
        subtitle="Editorial"
        title="Review"
        lead="Inspect drafts and ready items, resolve comments and suggestions, check platform previews, and build posting kits."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <div className="studio-section-bar">
        <span className="studio-mono">
          {loading ? 'Loading items...' : `${items.length} total items`}
        </span>
        <div className="studio-btn-group">
          {postedCount > 0 ? (
            <Checkbox
              label={`Show posted (${postedCount})`}
              checked={showPosted}
              onChange={(e) => setShowPosted(e.target.checked)}
            />
          ) : null}
          <PillButton variant="outline" size="sm" onClick={() => void loadList()}>
            Refresh
          </PillButton>
        </div>
      </div>

      {!loading && items.length === 0 ? (
        <div className="mg-empty">
          No drafts or ready items yet. Start a workflow in Create to stage content for review.
        </div>
      ) : (
        groups.map((group) => (
          <section
            key={group.id}
            className="studio-section"
            aria-label={group.label}
          >
            <div className="studio-section-bar">
              <h2 className="studio-section-title">
                {group.label} ({group.items.length})
              </h2>
              <StatusDot state={mapContentStatusState(group.id)} label={group.id} />
            </div>

            {group.items.length === 0 ? (
              <div className="mg-empty">No {group.label.toLowerCase()} items.</div>
            ) : (
              <RowList aria-label={`${group.label} items`}>
                {group.items.map((item) => {
                  const openCount = item.open_comments_count ?? 0;
                  const warnCount = item.warnings_count ?? 0;
                  const platformLabel = item.platform || 'general';
                  return (
                    <Row
                      key={item.id}
                      title={item.title || item.id}
                      description={
                        <span className="studio-review-row-meta">
                          <span className="studio-mono">
                            {item.type} · {platformLabel}
                          </span>
                          <span className="mg-chip">
                            {openCount} open comment{openCount === 1 ? '' : 's'}
                          </span>
                          <span className="mg-chip">
                            {warnCount} warning{warnCount === 1 ? '' : 's'}
                          </span>
                        </span>
                      }
                      meta={formatTimestamp(item.updated_at)}
                      onClick={() => onOpenItem(item.id)}
                    />
                  );
                })}
              </RowList>
            )}
          </section>
        ))
      )}
    </div>
  );
}
