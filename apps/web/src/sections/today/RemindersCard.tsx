import { useMemo, useState, type FormEvent } from 'react';
import { Card, Field, PillButton, TextInput } from '@magnm/design/react';
import {
  createReminder,
  deleteReminder,
  markReminderDone,
  snoozeReminder,
} from '../../api/reminders';
import type { ReminderRecord } from '../../types';
import { formatTimestamp } from '../../ui/format';

export interface RemindersCardProps {
  reminders: ReminderRecord[];
  loading: boolean;
  onRefresh: () => Promise<void> | void;
  onAddResults: (contentId: string) => void;
  onError: (message: string) => void;
}

function parseTimeMs(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

function isReminderOverdue(rem: ReminderRecord, nowMs: number): boolean {
  if (rem.done_at) return false;
  const snoozeMs = parseTimeMs(rem.snoozed_until);
  if (snoozeMs !== null && snoozeMs > nowMs) {
    return false;
  }
  const dueMs = parseTimeMs(rem.due_at);
  return dueMs !== null && dueMs <= nowMs;
}

function effectiveDueMs(rem: ReminderRecord, nowMs: number): number {
  const snoozeMs = parseTimeMs(rem.snoozed_until);
  if (snoozeMs !== null && snoozeMs > nowMs) {
    return snoozeMs;
  }
  return parseTimeMs(rem.due_at) ?? nowMs;
}

/** Today card listing open reminders (overdue first, highlighted) with Done/Snooze/Delete and Add form. */
export function RemindersCard({
  reminders,
  loading,
  onRefresh,
  onAddResults,
  onError,
}: RemindersCardProps) {
  const [title, setTitle] = useState('');
  const [dueAt, setDueAt] = useState('');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [snoozeOpenId, setSnoozeOpenId] = useState<string | null>(null);
  const [customSnoozeAt, setCustomSnoozeAt] = useState('');

  const nowMs = Date.now();
  const sortedReminders = useMemo(() => {
    const now = Date.now();
    return [...reminders].sort((a, b) => {
      const aOver = isReminderOverdue(a, now) ? 0 : 1;
      const bOver = isReminderOverdue(b, now) ? 0 : 1;
      if (aOver !== bOver) return aOver - bOver;
      return effectiveDueMs(a, now) - effectiveDueMs(b, now);
    });
  }, [reminders]);

  const handleAdd = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !dueAt.trim()) {
      onError('Reminder title and date-time are required.');
      return;
    }
    setSubmitting(true);
    try {
      await createReminder({
        title: title.trim(),
        due_at: dueAt.trim(),
        note: note.trim(),
      });
      setTitle('');
      setDueAt('');
      setNote('');
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to create reminder.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDone = async (id: string) => {
    setBusyId(id);
    try {
      await markReminderDone(id);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to mark reminder done.');
    } finally {
      setBusyId(null);
    }
  };

  const handleSnooze = async (id: string, payload: { minutes?: number; until?: string }) => {
    setBusyId(id);
    try {
      await snoozeReminder(id, payload);
      setSnoozeOpenId(null);
      setCustomSnoozeAt('');
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to snooze reminder.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string) => {
    setBusyId(id);
    try {
      await deleteReminder(id);
      await onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to delete reminder.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card outline aria-label="Reminders">
      <div className="studio-stack-sm">
        <div className="studio-card-head">
          <div>
            <div className="mg-label">Schedule</div>
            <div className="studio-card-title">Reminders ({sortedReminders.length})</div>
          </div>
        </div>

        {loading && sortedReminders.length === 0 ? (
          <div className="mg-empty">Loading reminders.</div>
        ) : sortedReminders.length === 0 ? (
          <div className="mg-empty">No open reminders.</div>
        ) : (
          <div className="mg-rows" role="list" aria-label="Open reminders">
            {sortedReminders.map((rem) => {
              const overdue = isReminderOverdue(rem, nowMs);
              const snoozeMs = parseTimeMs(rem.snoozed_until);
              const isSnoozed = snoozeMs !== null && snoozeMs > nowMs;
              const isBusy = busyId === rem.id;
              return (
                <div
                  key={rem.id}
                  role="listitem"
                  data-overdue={overdue ? 'true' : 'false'}
                  className={`studio-reminder-item${overdue ? ' studio-reminder--overdue' : ''}`}
                >
                  <div className="studio-reminder-top">
                    <div className="mg-row-main">
                      <div className="studio-btn-group">
                        <span className="mg-row-title">{rem.title}</span>
                        {overdue ? <span className="mg-chip">Overdue</span> : null}
                        {isSnoozed ? (
                          <span className="mg-chip">
                            Snoozed until {formatTimestamp(rem.snoozed_until)}
                          </span>
                        ) : null}
                      </div>
                      <span className="mg-row-desc">
                        Due {formatTimestamp(rem.due_at)}
                        {rem.note ? ` · ${rem.note}` : ''}
                      </span>
                    </div>
                    <div className="studio-row-actions">
                      {rem.kind === 'results_due' && rem.content_id ? (
                        <PillButton
                          size="sm"
                          disabled={isBusy}
                          onClick={() => onAddResults(rem.content_id as string)}
                        >
                          Add results
                        </PillButton>
                      ) : null}
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleDone(rem.id)}
                      >
                        Done
                      </PillButton>
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={isBusy}
                        onClick={() =>
                          setSnoozeOpenId((prev) => (prev === rem.id ? null : rem.id))
                        }
                      >
                        Snooze
                      </PillButton>
                      <PillButton
                        variant="danger"
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleDelete(rem.id)}
                      >
                        Delete
                      </PillButton>
                    </div>
                  </div>

                  {snoozeOpenId === rem.id ? (
                    <div className="studio-reminder-snooze" aria-label={`Snooze options for ${rem.title}`}>
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleSnooze(rem.id, { minutes: 60 })}
                      >
                        1 hour
                      </PillButton>
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={isBusy}
                        onClick={() => void handleSnooze(rem.id, { until: 'tomorrow 9am' })}
                      >
                        Tomorrow 9am
                      </PillButton>
                      <input
                        type="datetime-local"
                        aria-label={`Snooze until for ${rem.title}`}
                        className="mg-input"
                        value={customSnoozeAt}
                        onChange={(e) => setCustomSnoozeAt(e.target.value)}
                      />
                      <PillButton
                        size="sm"
                        disabled={isBusy || !customSnoozeAt.trim()}
                        onClick={() => void handleSnooze(rem.id, { until: customSnoozeAt })}
                      >
                        Set time
                      </PillButton>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        <form className="studio-reminder-form" onSubmit={(e) => void handleAdd(e)}>
          <div className="mg-label">Add reminder</div>
          <div className="studio-grid-2">
            <Field label="Title" htmlFor="new-reminder-title">
              <TextInput
                id="new-reminder-title"
                aria-label="Reminder title"
                placeholder="Check post replies..."
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <Field label="Date and time (IST)" htmlFor="new-reminder-due">
              <input
                id="new-reminder-due"
                aria-label="Reminder due time"
                type="datetime-local"
                className="mg-input"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
            </Field>
          </div>
          <div className="studio-inline-form">
            <TextInput
              aria-label="Reminder note"
              placeholder="Optional note..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <PillButton
              type="submit"
              size="sm"
              disabled={submitting || !title.trim() || !dueAt.trim()}
            >
              {submitting ? 'Adding...' : 'Add reminder'}
            </PillButton>
          </div>
        </form>
      </div>
    </Card>
  );
}
