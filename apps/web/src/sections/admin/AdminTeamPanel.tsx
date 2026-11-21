import { useState } from 'react';
import { Card, Dialog, PillButton, StatusDot } from '@magnm/design/react';
import { setMemberActive, setMemberRole, type AdminMember } from '../../api/admin';
import { isSafeHttpUrl } from '../../ui/format';
import {
  formatRelativeTime,
  memberDisplayName,
  memberInitials,
  presenceLabel,
  presenceStatusState,
} from './adminUtils';

export interface AdminTeamPanelProps {
  members: AdminMember[];
  currentUserId: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
  onOpenWorkspace: (memberId: string) => void;
  onOpenPitch: (memberId: string) => void;
  onOpenSettings: () => void;
}

type ConfirmAction =
  | { kind: 'role'; member: AdminMember; nextRole: 'admin' | 'member' }
  | { kind: 'active'; member: AdminMember; nextActive: boolean };

/** Admin team overview: member status cards, devices, role/active actions, and invites link. */
export function AdminTeamPanel({
  members,
  currentUserId,
  loading,
  onRefresh,
  onOpenWorkspace,
  onOpenPitch,
  onOpenSettings,
}: AdminTeamPanelProps) {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    if (!confirmAction) return;
    setError(null);
    setBusy(true);
    try {
      if (confirmAction.kind === 'role') {
        await setMemberRole(confirmAction.member.id, confirmAction.nextRole);
      } else {
        await setMemberActive(confirmAction.member.id, confirmAction.nextActive);
      }
      setConfirmAction(null);
      await onRefresh();
    } catch (err) {
      setConfirmAction(null);
      setError(err instanceof Error ? err.message : 'Could not update member.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="studio-section" aria-labelledby="admin-team-heading">
      <div className="studio-section-bar">
        <div>
          <h2 id="admin-team-heading" className="studio-section-title">
            Team
          </h2>
          <div className="studio-hint">
            Live device presence, running agents, and member access.
          </div>
        </div>
        <div className="studio-row-actions">
          <a
            href="#/settings"
            className="mg-btn mg-btn--outline mg-btn--sm"
            onClick={(e) => {
              e.preventDefault();
              onOpenSettings();
            }}
          >
            Invite people
          </a>
        </div>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {loading && members.length === 0 ? (
        <div className="mg-empty">Loading team members.</div>
      ) : members.length === 0 ? (
        <div className="mg-empty">No team members found.</div>
      ) : (
        <div className="studio-admin-team-grid">
          {members.map((member) => {
            const name = memberDisplayName(member);
            const isSelf = Boolean(currentUserId) && member.id === currentUserId;
            const roleLabel = member.role === 'admin' ? 'Admin' : 'Member';

            return (
              <Card key={member.id} outline={!member.active}>
                <div className="studio-stack-sm">
                  <div className="studio-card-head">
                    <div className="studio-account-profile">
                      {isSafeHttpUrl(member.avatar_url) ? (
                        <img
                          src={member.avatar_url || undefined}
                          alt={name}
                          className="studio-account-avatar"
                          referrerPolicy="no-referrer"
                        />
                      ) : (
                        <div className="studio-admin-avatar-fallback" aria-hidden="true">
                          {memberInitials(name)}
                        </div>
                      )}
                      <div className="studio-stack-xs">
                        <div className="studio-row-actions">
                          <span className="studio-card-title">{name}</span>
                          <span className="mg-chip">{roleLabel}</span>
                          {!member.active ? <span className="mg-chip">Inactive</span> : null}
                          {isSelf ? <span className="mg-chip">You</span> : null}
                        </div>
                        <span className="studio-mono-wrap">{member.email}</span>
                      </div>
                    </div>

                    <StatusDot
                      state={presenceStatusState(member.presence)}
                      label={presenceLabel(member.presence)}
                      live
                    />
                  </div>

                  <div className="studio-admin-member-meta">
                    <div className="studio-mono">
                      Last active: {formatRelativeTime(member.last_active_at || member.last_seen_at)}
                    </div>
                    <div className="studio-mono">
                      Running agents:{' '}
                      {member.running_agents.length > 0
                        ? member.running_agents.join(', ')
                        : 'None'}
                    </div>
                    {member.devices.length > 0 ? (
                      <div className="studio-admin-device-list">
                        {member.devices.map((dev) => (
                          <div key={dev.device_id} className="studio-mono-wrap">
                            {dev.device_kind} · {dev.status}
                            {dev.app_version ? ` · v${dev.app_version}` : ''}
                            {dev.last_seen_at ? ` · ${formatRelativeTime(dev.last_seen_at)}` : ''}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="studio-mono">No devices reported</div>
                    )}
                  </div>

                  <div className="studio-row-actions">
                    <PillButton
                      size="sm"
                      onClick={() => onOpenWorkspace(member.id)}
                    >
                      Open workspace
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() => onOpenPitch(member.id)}
                    >
                      Pitch
                    </PillButton>
                    {!isSelf ? (
                      <>
                        <PillButton
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            setConfirmAction({
                              kind: 'role',
                              member,
                              nextRole: member.role === 'admin' ? 'member' : 'admin',
                            })
                          }
                        >
                          {member.role === 'admin' ? 'Make member' : 'Make admin'}
                        </PillButton>
                        <PillButton
                          variant="danger"
                          size="sm"
                          onClick={() =>
                            setConfirmAction({
                              kind: 'active',
                              member,
                              nextActive: !member.active,
                            })
                          }
                        >
                          {member.active ? 'Deactivate' : 'Reactivate'}
                        </PillButton>
                      </>
                    ) : null}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog
        open={Boolean(confirmAction)}
        onClose={() => setConfirmAction(null)}
        title={
          confirmAction?.kind === 'role'
            ? confirmAction.nextRole === 'admin'
              ? 'Make admin'
              : 'Make member'
            : confirmAction?.nextActive
              ? 'Reactivate member'
              : 'Deactivate member'
        }
        subtitle="Team access"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmAction(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant={confirmAction?.kind === 'active' && !confirmAction.nextActive ? 'danger' : 'primary'}
              size="sm"
              disabled={busy}
              onClick={() => void handleConfirm()}
            >
              {busy ? 'Saving...' : 'Confirm'}
            </PillButton>
          </>
        }
      >
        {confirmAction ? (
          <p className="mg-sub">
            {confirmAction.kind === 'role'
              ? `Change ${memberDisplayName(confirmAction.member)} to ${confirmAction.nextRole}?`
              : confirmAction.nextActive
                ? `Reactivate ${memberDisplayName(confirmAction.member)} so they can sign in and sync again?`
                : `Deactivate ${memberDisplayName(confirmAction.member)}? They will lose access immediately.`}
          </p>
        ) : null}
      </Dialog>
    </section>
  );
}
