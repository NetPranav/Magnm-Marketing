import { api } from '../api';
import type { ReminderRecord } from '../types';

/** Fetch reminders filtered by open, done, or all. */
export async function listReminders(status: 'open' | 'done' | 'all' = 'open'): Promise<ReminderRecord[]> {
  const res = await api.get<{ reminders?: ReminderRecord[] }>(
    `/api/reminders?status=${encodeURIComponent(status)}`,
  );
  return Array.isArray(res?.reminders) ? res.reminders : [];
}

/** Create a new custom reminder. */
export async function createReminder(payload: {
  title: string;
  due_at: string;
  note?: string;
  content_id?: string | null;
}): Promise<ReminderRecord> {
  const res = await api.post<{ reminder: ReminderRecord }>('/api/reminders', payload);
  return res.reminder;
}

/** Mark a reminder as done. */
export async function markReminderDone(id: string): Promise<ReminderRecord> {
  const res = await api.post<{ reminder: ReminderRecord }>(
    `/api/reminders/${encodeURIComponent(id)}/done`,
    {},
  );
  return res.reminder;
}

/** Snooze a reminder by minutes or until an ISO/IST timestamp or phrase. */
export async function snoozeReminder(
  id: string,
  payload: { minutes?: number; until?: string },
): Promise<ReminderRecord> {
  const res = await api.post<{ reminder: ReminderRecord }>(
    `/api/reminders/${encodeURIComponent(id)}/snooze`,
    payload,
  );
  return res.reminder;
}

/** Delete a reminder permanently. */
export function deleteReminder(id: string): Promise<{ success: boolean; id: string }> {
  return api.delete<{ success: boolean; id: string }>(`/api/reminders/${encodeURIComponent(id)}`);
}
