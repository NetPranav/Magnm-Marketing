import type { StatusState } from '@magnm/design/react';
import type { AdminMember, AdminPresence, MirrorRecordKind } from '../../api/admin';

export interface WorkspaceKindTab {
  id: MirrorRecordKind;
  label: string;
}

export const WORKSPACE_KIND_TABS: WorkspaceKindTab[] = [
  { id: 'chat_session', label: 'Chats' },
  { id: 'content_item', label: 'Drafts' },
  { id: 'research_note', label: 'Research' },
  { id: 'review_comment', label: 'Reviews' },
  { id: 'brief', label: 'Briefs' },
  { id: 'task', label: 'Tasks' },
  { id: 'posted_link', label: 'Posted links' },
  { id: 'metric', label: 'Results' },
  { id: 'team_message', label: 'Team room' },
  { id: 'pitch_proposal', label: 'Proposals' },
];

/** Map derived member presence string to StatusDot state. */
export function presenceStatusState(presence: AdminPresence | string): StatusState {
  switch (presence) {
    case 'working':
      return 'working';
    case 'online':
      return 'online';
    case 'idle':
      return 'idle';
    default:
      return 'off';
  }
}

/** Sentence-case display label for member presence. */
export function presenceLabel(presence: AdminPresence | string): string {
  switch (presence) {
    case 'working':
      return 'Working';
    case 'online':
      return 'Online';
    case 'idle':
      return 'Idle';
    default:
      return 'Offline';
  }
}

/** Format an ISO timestamp relative to now (e.g. "5 minutes ago"). */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'Never';
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return 'just now';
  const mins = Math.floor(diffSec / 60);
  if (mins < 60) return mins === 1 ? '1 minute ago' : `${mins} minutes ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours === 1 ? '1 hour ago' : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? '1 day ago' : `${days} days ago`;
}

/** Format byte counts into human-readable MB/GB strings matching AccountPanel. */
export function formatStorageBytes(bytes: number): string {
  const safe = Math.max(0, Number(bytes || 0));
  const gbUnit = 1024 * 1024 * 1024;
  const mbUnit = 1024 * 1024;
  if (safe >= gbUnit) {
    const gb = safe / gbUnit;
    return Number.isInteger(gb) ? `${gb} GB` : `${gb.toFixed(1)} GB`;
  }
  if (safe >= mbUnit || safe === 0) {
    return `${Math.round(safe / mbUnit)} MB`;
  }
  if (safe >= 1024) {
    return `${Math.round(safe / 1024)} KB`;
  }
  return `${Math.round(safe)} B`;
}

/** Return the display label for a team member. */
export function memberDisplayName(member: Pick<AdminMember, 'display_name' | 'email' | 'id'>): string {
  const name = (member.display_name || '').trim();
  return name || member.email || member.id;
}

/** Format preview kind keys into sentence-case labels. */
export function formatPreviewKindLabel(kind: string): string {
  switch (kind) {
    case 'thumbnail':
      return 'Thumbnails';
    case 'image_preview':
      return 'Image previews';
    case 'video_preview':
      return 'Video previews';
    default:
      return kind.replace(/_/g, ' ');
  }
}

/** Format member initials when no avatar URL is available. */
export function memberInitials(nameOrEmail: string): string {
  const cleaned = (nameOrEmail || '?').trim();
  if (!cleaned) return '?';
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return cleaned.slice(0, 2).toUpperCase();
}
