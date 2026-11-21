import { useMemo } from 'react';
import { Card, PillButton } from '@magnm/design/react';
import { proposalImageUrl } from '../../api/results';
import type { ContentItem, MetricProposal } from '../../types';
import { formatTimestamp } from '../../ui/format';

export interface ResultsCardProps {
  proposals: MetricProposal[];
  contentItems: ContentItem[];
  loading: boolean;
  onOpenNew: () => void;
  onReviewProposal: (proposal: MetricProposal) => void;
  onDiscardProposal: (proposalId: string) => void;
}

function summarizeMetrics(metrics: Record<string, number>): string {
  const entries = Object.entries(metrics || {});
  if (entries.length === 0) return 'No numbers extracted';
  return entries
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
}

/** Today card listing pending analytics screenshot proposals awaiting user confirmation. */
export function ResultsCard({
  proposals,
  contentItems,
  loading,
  onOpenNew,
  onReviewProposal,
  onDiscardProposal,
}: ResultsCardProps) {
  const titlesById = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of contentItems) {
      map.set(item.id, item.title || item.id);
    }
    return map;
  }, [contentItems]);

  return (
    <Card outline aria-label="Results">
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">Analyst</div>
            <div className="studio-card-title">Results ({proposals.length})</div>
          </div>
          <PillButton variant="outline" size="sm" onClick={onOpenNew}>
            Add results
          </PillButton>
        </div>

        {loading && proposals.length === 0 ? (
          <div className="mg-empty">Checking pending results.</div>
        ) : proposals.length === 0 ? (
          <div className="mg-empty">No screenshot results waiting to confirm.</div>
        ) : (
          <div className="mg-rows" role="list" aria-label="Pending results">
            {proposals.map((prop) => {
              const postTitle = prop.content_id
                ? titlesById.get(prop.content_id) || prop.content_id
                : 'Unlinked post';
              return (
                <div key={prop.id} role="listitem" className="studio-results-proposal-row">
                  <div className="studio-results-proposal-main">
                    {prop.image_path ? (
                      <img
                        src={proposalImageUrl(prop.id)}
                        alt="Screenshot preview"
                        className="studio-results-row-thumb"
                      />
                    ) : null}
                    <div className="mg-row-main">
                      <span className="mg-row-title">
                        {postTitle}
                        {prop.platform ? ` · ${prop.platform}` : ''}
                      </span>
                      <span className="mg-row-desc">
                        {summarizeMetrics(prop.metrics)}
                        {prop.created_at ? ` · ${formatTimestamp(prop.created_at)}` : ''}
                      </span>
                    </div>
                  </div>
                  <div className="studio-row-actions">
                    <PillButton size="sm" onClick={() => onReviewProposal(prop)}>
                      Review
                    </PillButton>
                    <PillButton
                      variant="danger"
                      size="sm"
                      onClick={() => onDiscardProposal(prop.id)}
                    >
                      Discard
                    </PillButton>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
