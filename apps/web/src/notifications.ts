import type { NotificationRecord } from './api/notifications';
import {
  getDesktopNotificationPermission,
  isDesktop,
  requestDesktopNotificationPermission,
  sendDesktopNotification,
} from './desktop';

export type SystemNotificationPermission = 'granted' | 'denied' | 'default' | 'unsupported';

/** Return current system notification permission without prompting the user. */
export async function getSystemNotificationPermission(): Promise<SystemNotificationPermission> {
  if (isDesktop()) {
    return getDesktopNotificationPermission();
  }
  if (typeof Notification === 'undefined') {
    return 'unsupported';
  }
  return (Notification.permission as SystemNotificationPermission) || 'default';
}

/** Prompt the user for system notification permission (only call from a user button click). */
export async function requestSystemNotificationPermission(): Promise<SystemNotificationPermission> {
  if (isDesktop()) {
    return requestDesktopNotificationPermission();
  }
  if (typeof Notification === 'undefined') {
    return 'unsupported';
  }
  const result = await Notification.requestPermission();
  return (result as SystemNotificationPermission) || 'default';
}

/** Return true when the app window is hidden or does not currently have focus. */
export function isAppUnfocused(): boolean {
  if (typeof document === 'undefined') return false;
  if (document.visibilityState === 'hidden') return true;
  if (typeof document.hasFocus === 'function') {
    return !document.hasFocus();
  }
  return false;
}

/** Display a desktop or browser notification when unfocused and permission is granted. */
export async function showSystemNotification(
  notif: NotificationRecord,
  onClickRoute: (route: string) => void,
): Promise<boolean> {
  // Normal notifications only pop up when the app is in the background; a test always does, so people can check.
  if (notif.kind !== 'test' && !isAppUnfocused()) {
    return false;
  }

  if (isDesktop()) {
    const perm = await getDesktopNotificationPermission();
    if (perm !== 'granted') return false;
    return sendDesktopNotification({
      title: notif.title,
      body: notif.body || undefined,
      route: notif.route,
    });
  }

  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') {
    return false;
  }

  const instance = new Notification(notif.title, {
    body: notif.body || undefined,
    tag: notif.id,
  });
  instance.onclick = () => {
    if (typeof window !== 'undefined' && typeof window.focus === 'function') {
      window.focus();
    }
    onClickRoute(notif.route);
    if (typeof instance.close === 'function') {
      instance.close();
    }
  };
  return true;
}
