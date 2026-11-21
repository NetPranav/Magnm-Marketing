import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, PillButton, Row, RowList, Select } from '@magnm/design/react';
import {
  getAdminActivity,
  getAdminDailyActivity,
  type AdminActivityEvent,
  type AdminDailyActivityResponse,
  type AdminMember,
} from '../../api/admin';
import { formatTimestamp } from '../../ui/format';
import { formatRelativeTime, memberDisplayName } from './adminUtils';

export interface AdminActivityPanelProps {
  members: AdminMember[];
}

function shortDateLabel(isoDate: string): string {
  const parts = isoDate.split('-');
  if (parts.length === 3) {
    return `${parts[1]}/${parts[2]}`;
  }
  return isoDate;
}

/** Team activity overview: 14-day CSS/SVG bar chart with member filter and paginated timeline. */
export function AdminActivityPanel({ members }: AdminActivityPanelProps) {
  const [daily, setDaily] = useState<AdminDailyActivityResponse | null>(null);
  const [events, setEvents] = useState<AdminActivityEvent[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [memberFilter, setMemberFilter] = useState<string>('');
  const [kindFilter, setKindFilter] = useState<string>('');
  const [loadingDaily, setLoadingDaily] = useState(true);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDaily = useCallback(async () => {
    setLoadingDaily(true);
    try {
      const res = await getAdminDailyActivity(14);
      setDaily(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load activity chart.');
    } finally {
      setLoadingDaily(false);
    }
  }, []);

  const loadTimeline = useCallback(
    async (beforeCursor?: string) => {
      if (beforeCursor) {
        setLoadingMore(true);
      } else {
        setLoadingEvents(true);
        setError(null);
      }
      try {
        const res = await getAdminActivity({
          member: memberFilter || undefined,
          kind: kindFilter || undefined,
          before: beforeCursor || undefined,
          limit: 50,
        });
        if (beforeCursor) {
          setEvents((prev) => [...prev, ...res.events]);
        } else {
          setEvents(res.events);
        }
        setNextBefore(res.next_before);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load activity timeline.');
      } finally {
        setLoadingEvents(false);
        setLoadingMore(false);
      }
    },
    [kindFilter, memberFilter],
  );

  useEffect(() => {
    void loadDaily();
  }, [loadDaily]);

  useEffect(() => {
    void loadTimeline();
  }, [loadTimeline]);

  const chartBars = useMemo(() => {
    const series = daily?.series || [];
    const counts = series.map((day) => {
      const count = memberFilter
        ? Number(day.by_member?.[memberFilter] ?? 0)
        : Number(day.total ?? 0);
      return { date: day.date, count };
    });
    const maxCount = Math.max(1, ...counts.map((c) => c.count));
    return counts.map((item) => ({
      ...item,
      heightPct: item.count > 0 ? Math.max(6, Math.round((item.count / maxCount) * 100)) : 0,
    }));
  }, [daily, memberFilter]);

  const kindOptions = useMemo(() => {
    const set = new Set<string>(['activity']);
    for (const ev of events) {
      if (ev.kind) set.add(ev.kind);
    }
    return Array.from(set).sort();
  }, [events]);

  return (
    <section className="studio-section" aria-labelledby="admin-activity-heading">
      <div className="studio-section-bar">
        <div>
          <h2 id="admin-activity-heading" className="studio-section-title">
            Activity
          </h2>
          <div className="studio-hint">Last 14 days and live team timeline.</div>
        </div>
        <div className="studio-filter-row">
          <Select
            aria-label="Filter by member"
            value={memberFilter}
            onChange={(e) => setMemberFilter(e.target.value)}
          >
            <option value="">All members</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {memberDisplayName(m)}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Filter by kind"
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
          >
            <option value="">All kinds</option>
            {kindOptions.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <span className="mg-label">Events per day (14 days)</span>
            <span className="studio-mono">
              Total: {chartBars.reduce((sum, b) => sum + b.count, 0)}
            </span>
          </div>

          {loadingDaily && !daily ? (
            <div className="studio-hint">Loading activity chart...</div>
          ) : (
            <div
              className="studio-admin-chart"
              role="img"
              aria-label="14-day activity bar chart"
              data-testid="admin-activity-chart"
            >
              {chartBars.map((bar) => (
                <div
                  key={bar.date}
                  className="studio-admin-chart-col"
                  title={`${bar.date}: ${bar.count} events`}
                  data-testid="admin-activity-bar"
                  data-date={bar.date}
                  data-count={bar.count}
                >
                  <span className="studio-admin-chart-count studio-mono">{bar.count}</span>
                  <div className="studio-admin-chart-track">
                    <div
                      className="studio-admin-chart-bar"
                      style={{ height: `${bar.heightPct}%` }}
                    />
                  </div>
                  <span className="studio-admin-chart-date studio-mono">
                    {shortDateLabel(bar.date)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {loadingEvents && events.length === 0 ? (
        <div className="mg-empty">Loading activity timeline.</div>
      ) : events.length === 0 ? (
        <div className="mg-empty">No activity events match the current filter.</div>
      ) : (
        <div className="studio-stack-sm">
          <RowList aria-label="Team activity timeline">
            {events.map((ev) => (
              <Row
                key={String(ev.id)}
                title={ev.summary || ev.kind}
                description={
                  <div className="studio-row-actions">
                    <span className="studio-mono">{ev.member_name}</span>
                    <span className="mg-chip">{ev.kind}</span>
                    {ev.agent_id ? <span className="mg-chip">{ev.agent_id}</span> : null}
                  </div>
                }
                meta={
                  <span title={formatTimestamp(ev.ts)}>
                    {formatRelativeTime(ev.ts)}
                  </span>
                }
              />
            ))}
          </RowList>

          {nextBefore ? (
            <div>
              <PillButton
                variant="outline"
                size="sm"
                disabled={loadingMore}
                onClick={() => void loadTimeline(nextBefore)}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </PillButton>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
