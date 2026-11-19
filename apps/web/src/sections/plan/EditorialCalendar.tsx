import { useMemo, useState } from 'react';
import { PillButton } from '@magnm/design/react';
import type { ContentItem } from '../../types';

export interface CalendarSlotEvent {
  id: string;
  date: string; // YYYY-MM-DD
  title: string;
  platform?: string | null;
  status: string;
  href?: string;
}

export interface EditorialCalendarProps {
  items: ContentItem[];
  plannedSlots?: Array<{ date: string; platform: string; topic: string }>;
  loading: boolean;
}

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const;

function toDateKey(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  const isoMatch = /^(\d{4}-\d{2}-\d{2})/.exec(trimmed);
  if (isoMatch) return isoMatch[1];
  const dt = new Date(trimmed);
  if (Number.isNaN(dt.getTime())) return null;
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const d = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function buildMonthCells(year: number, month: number): Array<{ dateKey: string; dayNum: number; inMonth: boolean }> {
  const firstDay = new Date(year, month, 1);
  const mondayOffset = (firstDay.getDay() + 6) % 7; // Monday = 0
  const start = new Date(year, month, 1 - mondayOffset);
  const cells: Array<{ dateKey: string; dayNum: number; inMonth: boolean }> = [];

  for (let i = 0; i < 42; i += 1) {
    const dt = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const d = String(dt.getDate()).padStart(2, '0');
    cells.push({
      dateKey: `${y}-${m}-${d}`,
      dayNum: dt.getDate(),
      inMonth: dt.getMonth() === month,
    });
  }
  return cells;
}

/** Month-view editorial calendar for scheduled and posted content with mono weekday labels. */
export function EditorialCalendar({ items, plannedSlots = [], loading }: EditorialCalendarProps) {
  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());

  const eventsByDate = useMemo(() => {
    const map = new Map<string, CalendarSlotEvent[]>();
    const push = (ev: CalendarSlotEvent) => {
      const list = map.get(ev.date) ?? [];
      list.push(ev);
      map.set(ev.date, list);
    };

    for (const item of items) {
      if (item.type === 'review' || item.type === 'plan') continue;
      const dateKey = toDateKey(item.posted_at || item.updated_at || item.created_at);
      if (!dateKey) continue;
      push({
        id: item.id,
        date: dateKey,
        title: item.title || item.id,
        platform: item.platform,
        status: item.status,
        href: `#/review/${encodeURIComponent(item.id)}`,
      });
    }

    plannedSlots.forEach((slot, idx) => {
      const dateKey = toDateKey(slot.date);
      if (!dateKey) return;
      push({
        id: `slot-${idx}-${dateKey}`,
        date: dateKey,
        title: slot.topic || 'Planned slot',
        platform: slot.platform,
        status: 'planned',
      });
    });

    return map;
  }, [items, plannedSlots]);

  const cells = useMemo(() => buildMonthCells(viewYear, viewMonth), [viewYear, viewMonth]);
  const monthLabel = new Date(viewYear, viewMonth, 1).toLocaleString(undefined, {
    month: 'long',
    year: 'numeric',
  });
  const monthPrefix = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}`;
  const monthEventCount = Array.from(eventsByDate.entries()).reduce(
    (sum, [dateKey, list]) => (dateKey.startsWith(monthPrefix) ? sum + list.length : sum),
    0,
  );

  const shiftMonth = (delta: number) => {
    const next = new Date(viewYear, viewMonth + delta, 1);
    setViewYear(next.getFullYear());
    setViewMonth(next.getMonth());
  };

  return (
    <div className="studio-stack-sm">
      <div className="studio-section-bar">
        <div>
          <span className="studio-cal-month">{monthLabel}</span>
          <span className="studio-mono studio-hint"> ({monthEventCount} items)</span>
        </div>
        <div className="studio-btn-group">
          <PillButton variant="outline" size="sm" onClick={() => shiftMonth(-1)}>
            Prev
          </PillButton>
          <PillButton
            variant="quiet"
            size="sm"
            onClick={() => {
              const cur = new Date();
              setViewYear(cur.getFullYear());
              setViewMonth(cur.getMonth());
            }}
          >
            Today
          </PillButton>
          <PillButton variant="outline" size="sm" onClick={() => shiftMonth(1)}>
            Next
          </PillButton>
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="mg-empty">Loading editorial calendar.</div>
      ) : (
        <div className="studio-calendar" role="grid" aria-label={`Editorial calendar for ${monthLabel}`}>
          <div className="studio-cal-weekdays" role="row">
            {WEEKDAYS.map((day) => (
              <div key={day} className="mg-label studio-cal-weekday" role="columnheader">
                {day}
              </div>
            ))}
          </div>

          <div className="studio-cal-grid">
            {cells.map((cell) => {
              const dayEvents = eventsByDate.get(cell.dateKey) ?? [];
              return (
                <div
                  key={cell.dateKey}
                  role="gridcell"
                  className={
                    cell.inMonth ? 'studio-cal-cell' : 'studio-cal-cell studio-cal-cell--muted'
                  }
                >
                  <div className="studio-mono studio-cal-day">{cell.dayNum}</div>
                  <div className="studio-cal-events">
                    {dayEvents.map((ev) =>
                      ev.href ? (
                        <a
                          key={ev.id}
                          href={ev.href}
                          className="studio-cal-event"
                          title={`${ev.title} (${ev.status})`}
                        >
                          <span className="studio-cal-event-title">{ev.title}</span>
                          <span className="studio-mono studio-cal-event-meta">
                            {ev.platform ? `${ev.platform} · ` : ''}
                            {ev.status}
                          </span>
                        </a>
                      ) : (
                        <div
                          key={ev.id}
                          className="studio-cal-event"
                          title={`${ev.title} (${ev.status})`}
                        >
                          <span className="studio-cal-event-title">{ev.title}</span>
                          <span className="studio-mono studio-cal-event-meta">
                            {ev.platform ? `${ev.platform} · ` : ''}
                            {ev.status}
                          </span>
                        </div>
                      ),
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
