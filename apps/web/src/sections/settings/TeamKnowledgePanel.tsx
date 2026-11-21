import { useCallback, useEffect, useState } from 'react';
import { Card, Dialog, PillButton, Row, RowList } from '@magnm/design/react';
import { getAccount, type AccountState } from '../../api/account';
import {
  applyTeamKnowledge,
  getTeamKnowledge,
  publishTeamKnowledge,
  type TeamKnowledgeItem,
  type TeamKnowledgeKey,
  type TeamKnowledgeStatus,
} from '../../api/teamKnowledge';
import { useStudioEvents } from '../../events';

function formatRelativeTime(iso: string): string {
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

/** Settings card (signed in only): publish/apply shared brand kit, product rules, agent instructions, templates, and base prices. */
export function TeamKnowledgePanel() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [knowledge, setKnowledge] = useState<TeamKnowledgeStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [itemErrors, setItemErrors] = useState<Record<string, string>>({});
  const [confirmApplyItem, setConfirmApplyItem] = useState<TeamKnowledgeItem | null>(null);

  const loadKnowledge = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await getTeamKnowledge();
      setKnowledge(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load team knowledge status.');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAccountAndKnowledge = useCallback(async () => {
    try {
      const acct = await getAccount();
      setAccount(acct);
      const activeSignedIn = Boolean(acct?.configured && acct?.signed_in && !acct?.deactivated && acct?.user);
      if (activeSignedIn) {
        await loadKnowledge();
      } else {
        setKnowledge(null);
        setItemErrors({});
        setError(null);
      }
    } catch {
      setAccount(null);
      setKnowledge(null);
    }
  }, [loadKnowledge]);

  useEffect(() => {
    void loadAccountAndKnowledge();
    const onAccountChanged = () => {
      void loadAccountAndKnowledge();
    };
    window.addEventListener('magnm:account-changed', onAccountChanged);
    return () => {
      window.removeEventListener('magnm:account-changed', onAccountChanged);
    };
  }, [loadAccountAndKnowledge]);

  const isSignedIn = Boolean(
    account?.configured && account?.signed_in && !account?.deactivated && account?.user,
  );

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'team_knowledge.updated' && isSignedIn) {
          void loadKnowledge();
        }
      },
      [isSignedIn, loadKnowledge],
    ),
  );

  if (!isSignedIn) {
    return null;
  }

  const role = knowledge?.role ?? account?.user?.role ?? 'member';
  const isAdmin = role === 'admin';
  const items = Array.isArray(knowledge?.items) ? knowledge.items : [];

  const handlePublish = async (key: TeamKnowledgeKey) => {
    setBusyKey(key);
    setError(null);
    setItemErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    try {
      const res = await publishTeamKnowledge(key);
      if (res.status) {
        setKnowledge(res.status);
      } else {
        await loadKnowledge();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to publish to team.';
      setItemErrors((prev) => ({ ...prev, [key]: msg }));
    } finally {
      setBusyKey(null);
    }
  };

  const handleConfirmApply = async () => {
    if (!confirmApplyItem) return;
    const target = confirmApplyItem;
    setBusyKey(target.key);
    setError(null);
    setItemErrors((prev) => {
      const next = { ...prev };
      delete next[target.key];
      return next;
    });
    try {
      const res = await applyTeamKnowledge(target.key);
      setConfirmApplyItem(null);
      if (res.status) {
        setKnowledge(res.status);
      } else {
        await loadKnowledge();
      }
    } catch (err) {
      setConfirmApplyItem(null);
      const msg = err instanceof Error ? err.message : 'Failed to apply team version.';
      setItemErrors((prev) => ({ ...prev, [target.key]: msg }));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className="studio-section" aria-labelledby="settings-team-knowledge-heading">
      <div className="studio-section-bar">
        <h2 id="settings-team-knowledge-heading" className="studio-section-title">
          Team knowledge
        </h2>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        {loading && items.length === 0 ? (
          <div className="studio-hint">Checking team knowledge...</div>
        ) : items.length === 0 ? (
          <div className="studio-hint">No team knowledge items reported.</div>
        ) : (
          <RowList aria-label="Team knowledge">
            {items.map((item) => {
              const itemErr = itemErrors[item.key] || item.last_error;
              let statusText: string;
              if (isAdmin) {
                if (item.published_at) {
                  statusText = `Published ${formatRelativeTime(item.published_at)}`;
                } else if (item.remote_updated_at) {
                  statusText = `Updated ${formatRelativeTime(item.remote_updated_at)}${item.remote_updated_by ? ` by ${item.remote_updated_by}` : ''}`;
                } else {
                  statusText = 'Not published yet';
                }
              } else {
                statusText = item.remote_updated_at
                  ? `Managed by your admin · updated ${formatRelativeTime(item.remote_updated_at)}${item.remote_updated_by ? ` by ${item.remote_updated_by}` : ''}`
                  : 'Managed by your admin · not published yet';
              }

              return (
                <Row
                  key={item.key}
                  title={item.label}
                  description={
                    <div className="studio-stack-xs">
                      <div className="studio-chip-row">
                        <span className="studio-mono">{statusText}</span>
                        {isAdmin && item.local_changed_since_publish ? (
                          <span className="mg-chip">Changed since last publish</span>
                        ) : null}
                        {isAdmin && item.remote_newer ? (
                          <span className="mg-chip">Team version is newer</span>
                        ) : null}
                      </div>
                      {itemErr ? (
                        <div className="mg-notice" role="alert">
                          {itemErr}
                        </div>
                      ) : null}
                    </div>
                  }
                  meta={
                    isAdmin ? (
                      <div className="studio-row-actions">
                        <PillButton
                          size="sm"
                          disabled={busyKey === item.key}
                          aria-label={`Publish ${item.label} to team`}
                          onClick={() => void handlePublish(item.key)}
                        >
                          {busyKey === item.key ? 'Publishing...' : 'Publish to team'}
                        </PillButton>
                        {item.remote_newer ? (
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busyKey === item.key}
                            aria-label={`Apply team version for ${item.label}`}
                            onClick={() => setConfirmApplyItem(item)}
                          >
                            Apply team version
                          </PillButton>
                        ) : null}
                      </div>
                    ) : undefined
                  }
                />
              );
            })}
          </RowList>
        )}
      </Card>

      <Dialog
        open={Boolean(confirmApplyItem)}
        onClose={() => setConfirmApplyItem(null)}
        title="Apply team version"
        subtitle={confirmApplyItem?.label || 'Team knowledge'}
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmApplyItem(null)}>
              Cancel
            </PillButton>
            <PillButton
              size="sm"
              disabled={Boolean(confirmApplyItem && busyKey === confirmApplyItem.key)}
              onClick={() => void handleConfirmApply()}
            >
              {confirmApplyItem && busyKey === confirmApplyItem.key
                ? 'Applying...'
                : 'Apply team version'}
            </PillButton>
          </>
        }
      >
        {confirmApplyItem ? (
          <p className="mg-sub">
            Apply the team version of <span className="studio-mono">{confirmApplyItem.label}</span>?
            The current local version is backed up first before overwriting.
          </p>
        ) : null}
      </Dialog>
    </section>
  );
}
