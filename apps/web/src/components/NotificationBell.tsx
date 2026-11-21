import { useEffect, useId, useRef, useState } from 'react';
import { PillButton } from '@magnm/design/react';
import type { NotificationRecord } from '../api/notifications';

export interface NotificationBellProps {
  notifications: NotificationRecord[];
  unreadCount: number;
  onSelectNotification: (item: NotificationRecord) => void;
  onMarkAllRead: () => void;
}

function formatRelativeTime(iso: string): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return '';
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return 'just now';
  const mins = Math.floor(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/** Bell button with unread badge and popover list of in-app notifications. */
export function NotificationBell({
  notifications,
  unreadCount,
  onSelectNotification,
  onMarkAllRead,
}: NotificationBellProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (ev: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(ev.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const badgeText = unreadCount > 99 ? '99+' : String(unreadCount);
  const buttonLabel = unreadCount > 0 ? `Notifications (${unreadCount} unread)` : 'Notifications';

  return (
    <div className="studio-notif-wrap" ref={wrapRef}>
      <button
        type="button"
        className="studio-notif-btn"
        aria-label={buttonLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unreadCount > 0 ? (
          <span className="studio-notif-badge" data-testid="notification-badge">
            {badgeText}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          id={panelId}
          className="studio-notif-panel"
          role="dialog"
          aria-label="Notifications"
        >
          <div className="studio-notif-panel-head">
            <span className="mg-label">Notifications</span>
            <PillButton
              variant="quiet"
              size="sm"
              disabled={unreadCount === 0}
              onClick={onMarkAllRead}
            >
              Mark all as read
            </PillButton>
          </div>

          {notifications.length === 0 ? (
            <div className="studio-notif-empty">No notifications yet.</div>
          ) : (
            <div className="studio-notif-list" role="list">
              {notifications.map((item) => {
                const isUnread = !item.read_at;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="listitem"
                    className={`studio-notif-item${isUnread ? ' studio-notif-item--unread' : ''}`}
                    onClick={() => {
                      setOpen(false);
                      onSelectNotification(item);
                    }}
                  >
                    <span className="studio-notif-dot-col" aria-hidden="true">
                      {isUnread ? <span className="studio-notif-dot" /> : null}
                    </span>
                    <span className="studio-notif-item-main">
                      <span className="studio-notif-item-title">{item.title}</span>
                      {item.body ? (
                        <span className="studio-notif-item-body">{item.body}</span>
                      ) : null}
                      <span className="studio-notif-item-time">
                        {formatRelativeTime(item.created_at)}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
