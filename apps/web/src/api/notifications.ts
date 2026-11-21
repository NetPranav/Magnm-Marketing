import { api } from '../api';

export interface NotificationRecord {
  id: string;
  kind: string;
  title: string;
  body: string;
  route: string;
  created_at: string;
  read_at: string | null;
}

export interface NotificationsListResponse {
  notifications: NotificationRecord[];
  unread_count: number;
}

export interface NotificationSettingItem {
  kind: string;
  label: string;
  description: string;
  admin_only: boolean;
  default: boolean;
  enabled: boolean;
}

export interface NotificationSettingsResponse {
  settings: NotificationSettingItem[];
  is_admin: boolean;
}

/** Fetch newest-first notifications and unread count from the local engine. */
export function listNotifications(limit = 50): Promise<NotificationsListResponse> {
  return api.get<NotificationsListResponse>(`/api/notifications?limit=${limit}`);
}

/** Mark specific notification IDs or all notifications as read. */
export function markNotificationsRead(options: {
  ids?: string[];
  all?: boolean;
}): Promise<{ ok: boolean; unread_count: number }> {
  return api.post<{ ok: boolean; unread_count: number }>('/api/notifications/read', options);
}

/** Fetch per-event notification settings (admin kinds filtered by engine for members). */
export function getNotificationSettings(): Promise<NotificationSettingsResponse> {
  return api.get<NotificationSettingsResponse>('/api/notifications/settings');
}

/** Save a per-event notification toggle. */
export function updateNotificationSetting(
  kind: string,
  enabled: boolean,
): Promise<{
  ok: boolean;
  kind: string;
  enabled: boolean;
  settings?: NotificationSettingItem[];
  is_admin?: boolean;
}> {
  return api.post('/api/notifications/settings', { kind, enabled });
}

/** Ask the engine to send one test notification (in-app and, if allowed, a system notification). */
export function sendTestNotification(): Promise<{ notification: NotificationRecord | null }> {
  return api.post<{ notification: NotificationRecord | null }>('/api/notifications/test', {});
}
