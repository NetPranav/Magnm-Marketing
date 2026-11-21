import { useMemo, useState } from 'react';
import { Card, PillButton, StatusDot } from '@magnm/design/react';
import type {
  WeeklySummaryPlatformRow,
  WeeklySummaryPostHighlight,
  WeeklySummaryRecord,
} from '../../types';
import { formatTimestamp, isSafeHttpUrl } from '../../ui/format';

export interface WeeklySummaryCardProps {
  summaries: WeeklySummaryRecord[];
  loading: boolean;
  generating: boolean;
  selectedSummaryId?: string | null;
  onSelectSummary: (id: string) => void;
  onGenerateNow: () => void;
  onAddResults: (contentId: string, platform?: string | null) => void;
}

function formatWow(val: number | 'n/a' | undefined): string {
  if (val === undefined || val === 'n/a') return 'n/a';
  if (!Number.isFinite(val)) return 'n/a';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val}%`;
}

function formatMetricCell(val: number | undefined): string {
  if (val === undefined || !Number.isFinite(val)) return '0';
  return String(val);
}

function formatPostMetricsSummary(post: WeeklySummaryPostHighlight): string {
  const m = post.metrics || {};
  const parts: string[] = [];
  if (typeof m.views === 'number') parts.push(`views: ${m.views}`);
  if (typeof m.likes === 'number') parts.push(`likes: ${m.likes}`);
  if (typeof m.comments === 'number') parts.push(`comments: ${m.comments}`);
  if (typeof m.shares === 'number') parts.push(`shares: ${m.shares}`);
  if (typeof m.saves === 'number') parts.push(`saves: ${m.saves}`);
  if (typeof post.engagement === 'number') parts.push(`engagement: ${post.engagement}`);
  return parts.join(' · ') || 'No metric breakdown';
}

function renderPostHighlight(label: string, post: WeeklySummaryPostHighlight) {
  const safeExternal = isSafeHttpUrl(post.posted_url);
  const href = safeExternal ? post.posted_url! : `#/review/${encodeURIComponent(post.content_id)}`;
  return (
    <Card outline key={label}>
      <div className="studio-stack-xs">
        <div className="mg-label">{label}</div>
        <div className="studio-card-title">
          {safeExternal ? (
            <a className="studio-inline-link" href={href} target="_blank" rel="noreferrer">
              {post.title}
            </a>
          ) : (
            <a className="studio-inline-link" href={href}>
              {post.title}
            </a>
          )}
        </div>
        <div className="mg-row-desc">
          {post.platform}
          {typeof post.score === 'number'
            ? ` · score ${post.score}${post.score_unit === 'per_view' ? ' /view' : ''}`
            : ''}
        </div>
        <div className="studio-mono">{formatPostMetricsSummary(post)}</div>
      </div>
    </Card>
  );
}

/** Today card showing the latest weekly Analyst summary, tables, top/bottom posts, missing results, and narrative. */
export function WeeklySummaryCard({
  summaries,
  loading,
  generating,
  selectedSummaryId,
  onSelectSummary,
  onGenerateNow,
  onAddResults,
}: WeeklySummaryCardProps) {
  const [showOlder, setShowOlder] = useState(false);

  const activeSummary = useMemo(() => {
    if (summaries.length === 0) return null;
    if (selectedSummaryId) {
      const matched = summaries.find((s) => s.id === selectedSummaryId);
      if (matched) return matched;
    }
    return summaries[0];
  }, [summaries, selectedSummaryId]);

  const platformRows: WeeklySummaryPlatformRow[] = useMemo(() => {
    if (!activeSummary) return [];
    const hl = activeSummary.highlights || {};
    if (Array.isArray(hl.platform_rows) && hl.platform_rows.length > 0) {
      return hl.platform_rows;
    }
    if (hl.platforms && typeof hl.platforms === 'object') {
      return Object.values(hl.platforms);
    }
    return [];
  }, [activeSummary]);

  const missingResults = activeSummary?.highlights?.missing_results ?? [];
  const topPost = activeSummary?.highlights?.top_post ?? null;
  const bottomPost = activeSummary?.highlights?.bottom_post ?? null;
  const counts = activeSummary?.highlights?.counts ?? {};
  const weekLabel = activeSummary?.data?.week_label || activeSummary?.week_start || '';
  const weekRangeFull =
    activeSummary?.data?.week_start && activeSummary?.data?.week_end
      ? `${activeSummary.data.week_start} to ${activeSummary.data.week_end}`
      : weekLabel;

  return (
    <Card outline aria-label="Weekly summary">
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">Analyst</div>
            <div className="studio-card-title">
              Weekly summary{weekLabel ? ` · ${weekLabel}` : ''}
            </div>
            {activeSummary ? (
              <div className="mg-row-desc">
                IST week {weekRangeFull}
                {activeSummary.created_at ? ` · generated ${formatTimestamp(activeSummary.created_at)}` : ''}
              </div>
            ) : null}
          </div>
          <div className="studio-row-actions">
            {summaries.length > 1 ? (
              <PillButton
                variant="outline"
                size="sm"
                onClick={() => setShowOlder((prev) => !prev)}
              >
                {showOlder ? 'Hide older' : `Older summaries (${summaries.length - 1})`}
              </PillButton>
            ) : null}
            <PillButton size="sm" disabled={generating} onClick={onGenerateNow}>
              {generating ? 'Generating...' : 'Generate now'}
            </PillButton>
          </div>
        </div>

        {showOlder && summaries.length > 1 ? (
          <div className="studio-older-summaries" role="list" aria-label="Older summaries">
            {summaries.map((item) => {
              const isSelected = activeSummary?.id === item.id;
              const label = item.data?.week_label || item.week_start;
              return (
                <PillButton
                  key={item.id}
                  variant={isSelected ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => {
                    onSelectSummary(item.id);
                    setShowOlder(false);
                  }}
                >
                  {label}
                </PillButton>
              );
            })}
          </div>
        ) : null}

        {loading && !activeSummary ? (
          <div className="mg-empty">Loading weekly summary.</div>
        ) : !activeSummary ? (
          <div className="mg-empty">
            No weekly summary generated yet. Click Generate now to build one for last week.
          </div>
        ) : (
          <div className="studio-stack-sm">
            <div className="studio-provider-list" aria-label="Weekly summary counts">
              <span className="mg-chip">Posted: {counts.posts_posted ?? 0}</span>
              <span className="mg-chip">With results: {counts.posts_with_results ?? 0}</span>
              <span className="mg-chip">Missing results: {counts.posts_missing_results ?? 0}</span>
              <span className="mg-chip">Drafts: {counts.drafts_created ?? 0}</span>
              <span className="mg-chip">Notes: {counts.research_notes_added ?? 0}</span>
              <span className="mg-chip">
                Reminders done/missed: {counts.reminders_done ?? 0}/{counts.reminders_missed ?? 0}
              </span>
            </div>

            {platformRows.length === 0 ? (
              <div className="mg-empty">No posts marked posted in this week.</div>
            ) : (
              <div className="studio-table-wrap">
                <table className="studio-summary-table" aria-label="Per-platform performance">
                  <thead>
                    <tr>
                      <th>Platform</th>
                      <th>Posts</th>
                      <th>Views</th>
                      <th>Likes</th>
                      <th>Comments</th>
                      <th>Shares</th>
                      <th>Saves</th>
                      <th>Engagement</th>
                      <th>Avg eng/post</th>
                      <th>WoW eng</th>
                    </tr>
                  </thead>
                  <tbody>
                    {platformRows.map((row) => (
                      <tr key={row.platform}>
                        <td>{row.platform}</td>
                        <td>{row.posts_count}</td>
                        <td>{formatMetricCell(row.totals?.views)}</td>
                        <td>{formatMetricCell(row.totals?.likes)}</td>
                        <td>{formatMetricCell(row.totals?.comments)}</td>
                        <td>{formatMetricCell(row.totals?.shares)}</td>
                        <td>{formatMetricCell(row.totals?.saves)}</td>
                        <td>{formatMetricCell(row.totals?.engagement)}</td>
                        <td>
                          {row.with_results_count > 0
                            ? formatMetricCell(row.averages?.engagement)
                            : 'n/a'}
                        </td>
                        <td>{formatWow(row.wow_change?.engagement)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {topPost || bottomPost ? (
              <div className="studio-grid-2">
                {topPost ? renderPostHighlight('Top post', topPost) : null}
                {bottomPost ? renderPostHighlight('Bottom post', bottomPost) : null}
              </div>
            ) : null}

            {missingResults.length > 0 ? (
              <div className="studio-stack-xs">
                <div className="mg-label">Missing results ({missingResults.length})</div>
                <div className="mg-rows" role="list" aria-label="Posts missing results">
                  {missingResults.map((item) => (
                    <div key={item.content_id} role="listitem" className="studio-missing-result-row">
                      <div className="mg-row-main">
                        <span className="mg-row-title">{item.title}</span>
                        <span className="mg-row-desc">
                          {item.platform}
                          {item.posted_at ? ` · posted ${formatTimestamp(item.posted_at)}` : ''}
                        </span>
                      </div>
                      <PillButton
                        size="sm"
                        onClick={() => onAddResults(item.content_id, item.platform)}
                      >
                        Add results
                      </PillButton>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="studio-summary-narrative">
              <div className="studio-card-head">
                <span className="mg-label">Narrative & recommendations</span>
                <StatusDot
                  state={
                    activeSummary.status === 'ready'
                      ? 'online'
                      : activeSummary.status === 'narrative_skipped'
                        ? 'idle'
                        : 'off'
                  }
                  label={
                    activeSummary.status === 'ready'
                      ? 'Grounded'
                      : activeSummary.status === 'narrative_skipped'
                        ? 'Narrative skipped'
                        : 'Tables only'
                  }
                />
              </div>
              {activeSummary.status === 'narrative_skipped' ? (
                <div className="mg-notice">
                  {activeSummary.narrative ||
                    'Narrative skipped: it mentioned numbers that are not in the data.'}
                </div>
              ) : activeSummary.narrative ? (
                <div className="studio-summary-narrative-text">
                  {activeSummary.narrative.split(/\n+/).map((para, idx) => (
                    <p key={idx}>{para}</p>
                  ))}
                </div>
              ) : (
                <div className="studio-hint">
                  AI narrative unavailable; showing verified tables only.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
