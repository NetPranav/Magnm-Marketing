import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Card,
  Dialog,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  TextInput,
} from '@magnm/design/react';
import {
  cancelLogin,
  createInvite,
  getAccount,
  listInvites,
  logoutAccount,
  revokeInvite,
  startLogin,
  type AccountRole,
  type AccountState,
  type InviteRecord,
} from '../../api/account';

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function formatInviteStatus(invite: InviteRecord): string {
  if (invite.revoked_at) return 'Revoked';
  if (invite.accepted_at) return 'Joined';
  return 'Invited';
}

function formatRoleLabel(role: string): string {
  return role === 'admin' ? 'Admin' : 'Member';
}

/** Settings sub-section: Google sign-in for Magnm Studio accounts and admin invite management. */
export function AccountPanel() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);

  const [invites, setInvites] = useState<InviteRecord[]>([]);
  const [invitesError, setInvitesError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<AccountRole>('member');
  const [confirmRevokeEmail, setConfirmRevokeEmail] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollStartedAtRef = useRef<number>(0);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const loadInvites = useCallback(async () => {
    setInvitesError(null);
    try {
      const rows = await listInvites();
      setInvites(rows);
    } catch (err) {
      setInvitesError(err instanceof Error ? err.message : 'Failed to load invites.');
    }
  }, []);

  const loadAccount = useCallback(async () => {
    try {
      const next = await getAccount();
      setAccount(next);
      if (next.signed_in && !next.deactivated && next.user?.role === 'admin') {
        await loadInvites();
      } else {
        setInvites([]);
      }
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load account status.');
      return null;
    } finally {
      setLoading(false);
    }
  }, [loadInvites]);

  useEffect(() => {
    void loadAccount();
    return () => {
      stopPolling();
    };
  }, [loadAccount, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollStartedAtRef.current = Date.now();
    pollTimerRef.current = setInterval(() => {
      if (Date.now() - pollStartedAtRef.current >= POLL_TIMEOUT_MS) {
        stopPolling();
        setAuthorizeUrl(null);
        setError('Google sign-in timed out. Please try again.');
        return;
      }

      void (async () => {
        try {
          const next = await getAccount();
          if (next.signed_in) {
            stopPolling();
            setAuthorizeUrl(null);
            setAccount(next);
            if (!next.deactivated && next.user?.role === 'admin') {
              await loadInvites();
            } else {
              setInvites([]);
            }
          } else if (next.last_error) {
            stopPolling();
            setAuthorizeUrl(null);
            setAccount(next);
          }
        } catch (err) {
          stopPolling();
          setAuthorizeUrl(null);
          setError(err instanceof Error ? err.message : 'Failed to check sign-in status.');
        }
      })();
    }, POLL_INTERVAL_MS);
  }, [loadInvites, stopPolling]);

  const handleStartLogin = async () => {
    setError(null);
    setBusy('login');
    try {
      const res = await startLogin(true);
      setAuthorizeUrl(res.authorize_url);
      setAccount((prev) => (prev ? { ...prev, last_error: null } : prev));
      startPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start Google sign-in.');
    } finally {
      setBusy(null);
    }
  };

  const handleCancelLogin = async () => {
    stopPolling();
    setAuthorizeUrl(null);
    setBusy('cancel');
    try {
      await cancelLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel sign-in.');
    } finally {
      setBusy(null);
    }
  };

  const handleLogout = async () => {
    stopPolling();
    setAuthorizeUrl(null);
    setError(null);
    setBusy('logout');
    try {
      await logoutAccount();
      setInvites([]);
      await loadAccount();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign out.');
    } finally {
      setBusy(null);
    }
  };

  const handleCreateInvite = async (e: FormEvent) => {
    e.preventDefault();
    setInvitesError(null);
    const cleaned = inviteEmail.trim().toLowerCase();
    if (!EMAIL_PATTERN.test(cleaned)) {
      setInvitesError('Enter a valid email address.');
      return;
    }

    setBusy('invite');
    try {
      await createInvite(cleaned, inviteRole);
      setInviteEmail('');
      setInviteRole('member');
      await loadInvites();
    } catch (err) {
      setInvitesError(err instanceof Error ? err.message : 'Could not send invite.');
    } finally {
      setBusy(null);
    }
  };

  const handleConfirmRevoke = async () => {
    if (!confirmRevokeEmail) return;
    const target = confirmRevokeEmail;
    setInvitesError(null);
    setBusy(`revoke-${target}`);
    try {
      await revokeInvite(target);
      setConfirmRevokeEmail(null);
      await loadInvites();
    } catch (err) {
      setConfirmRevokeEmail(null);
      setInvitesError(err instanceof Error ? err.message : 'Could not revoke invite.');
    } finally {
      setBusy(null);
    }
  };

  const isConfigured = Boolean(account?.configured);
  const isSignedIn = Boolean(account?.signed_in);
  const isDeactivated = Boolean(account?.deactivated);
  const user = account?.user ?? null;
  const visibleError = error || account?.last_error || null;
  const isAdmin = isSignedIn && !isDeactivated && user?.role === 'admin';

  return (
    <section className="studio-section" aria-labelledby="settings-account-heading">
      <div className="studio-settings-head">
        <span className="studio-settings-num">01</span>
        <h2 id="settings-account-heading" className="studio-section-title">
          Account
        </h2>
      </div>

      {visibleError ? (
        <div className="mg-notice" role="alert">
          {visibleError}
        </div>
      ) : null}

      <Card>
        {loading && !account ? (
          <div className="studio-hint">Checking account status...</div>
        ) : !isConfigured ? (
          <p className="studio-hint">
            Team sign-in is not set up on this device yet.
          </p>
        ) : !isSignedIn ? (
          authorizeUrl ? (
            <div className="studio-stack-sm">
              <div className="studio-card-title">Waiting for Google sign-in in your browser...</div>
              <div className="studio-row-actions">
                <a
                  className="studio-inline-link"
                  href={authorizeUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  Open the sign-in page again
                </a>
                <PillButton
                  variant="quiet"
                  size="sm"
                  disabled={busy === 'cancel'}
                  onClick={() => void handleCancelLogin()}
                >
                  Cancel
                </PillButton>
              </div>
            </div>
          ) : (
            <div className="studio-section-bar">
              <div>
                <div className="studio-card-title">Not signed in</div>
                <div className="studio-hint">
                  Sign in with an invited Google account to sync with your team.
                </div>
              </div>
              <PillButton
                size="sm"
                disabled={busy === 'login'}
                onClick={() => void handleStartLogin()}
              >
                {busy === 'login' ? 'Starting...' : 'Sign in with Google'}
              </PillButton>
            </div>
          )
        ) : isDeactivated ? (
          <div className="studio-section-bar">
            <div className="studio-stack-xs">
              <div className="studio-card-title">Account deactivated</div>
              <div className="studio-hint">
                Your account has been deactivated. Ask your admin.
              </div>
            </div>
            <PillButton
              variant="outline"
              size="sm"
              disabled={busy === 'logout'}
              onClick={() => void handleLogout()}
            >
              {busy === 'logout' ? 'Signing out...' : 'Sign out'}
            </PillButton>
          </div>
        ) : user ? (
          <div className="studio-section-bar">
            <div className="studio-account-profile">
              {user.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.display_name || user.email}
                  className="studio-account-avatar"
                  referrerPolicy="no-referrer"
                />
              ) : null}
              <div className="studio-stack-xs">
                <div className="studio-row-actions">
                  <span className="studio-card-title">{user.display_name || user.email}</span>
                  <span className="mg-chip">{formatRoleLabel(user.role)}</span>
                </div>
                <span className="studio-mono">{user.email}</span>
              </div>
            </div>
            <PillButton
              variant="outline"
              size="sm"
              disabled={busy === 'logout'}
              onClick={() => void handleLogout()}
            >
              {busy === 'logout' ? 'Signing out...' : 'Sign out'}
            </PillButton>
          </div>
        ) : null}
      </Card>

      {isAdmin ? (
        <Card outline>
          <div className="studio-stack-sm">
            <div className="mg-label">Invite people</div>

            {invitesError ? (
              <div className="mg-notice" role="alert">
                {invitesError}
              </div>
            ) : null}

            <form className="studio-inline-form studio-account-invite-form" onSubmit={(e) => void handleCreateInvite(e)} noValidate>
              <Field label="Email" htmlFor="account-invite-email" className="studio-account-invite-email">
                <TextInput
                  id="account-invite-email"
                  type="email"
                  aria-label="Invite email"
                  placeholder="teammate@example.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                />
              </Field>
              <Field label="Role" htmlFor="account-invite-role" className="studio-account-invite-role">
                <Select
                  id="account-invite-role"
                  aria-label="Invite role"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as AccountRole)}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </Select>
              </Field>
              <PillButton type="submit" disabled={busy === 'invite'}>
                {busy === 'invite' ? 'Inviting...' : 'Invite'}
              </PillButton>
            </form>

            {invites.length === 0 ? (
              <div className="studio-hint">No invites yet.</div>
            ) : (
              <RowList aria-label="Team invites">
                {invites.map((invite) => {
                  const statusLabel = formatInviteStatus(invite);
                  const isOpen = !invite.revoked_at && !invite.accepted_at;
                  return (
                    <Row
                      key={invite.email}
                      title={invite.email}
                      description={
                        <span className="studio-mono">
                          {formatRoleLabel(invite.role)} · {statusLabel}
                        </span>
                      }
                      meta={
                        <div className="studio-row-actions">
                          <span className="mg-chip">{statusLabel}</span>
                          {isOpen ? (
                            <PillButton
                              variant="quiet"
                              size="sm"
                              disabled={busy === `revoke-${invite.email}`}
                              aria-label={`Revoke invite for ${invite.email}`}
                              onClick={() => setConfirmRevokeEmail(invite.email)}
                            >
                              Revoke
                            </PillButton>
                          ) : null}
                        </div>
                      }
                    />
                  );
                })}
              </RowList>
            )}
          </div>
        </Card>
      ) : null}

      <Dialog
        open={Boolean(confirmRevokeEmail)}
        onClose={() => setConfirmRevokeEmail(null)}
        title="Revoke invite"
        subtitle="Team access"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmRevokeEmail(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              disabled={Boolean(confirmRevokeEmail && busy === `revoke-${confirmRevokeEmail}`)}
              onClick={() => void handleConfirmRevoke()}
            >
              {confirmRevokeEmail && busy === `revoke-${confirmRevokeEmail}`
                ? 'Revoking...'
                : 'Revoke invite'}
            </PillButton>
          </>
        }
      >
        {confirmRevokeEmail ? (
          <p className="mg-sub">
            Revoke the open invite for <span className="studio-mono">{confirmRevokeEmail}</span>?
          </p>
        ) : null}
      </Dialog>
    </section>
  );
}
