import { useCallback, useEffect, useState } from 'react';
import { PillButton } from '@magnm/design/react';
import { getAdminAudit, type AdminAuditEntry } from '../../api/admin';
import { formatTimestamp } from '../../ui/format';

/** Admin audit log table showing time, actor, action, and target. */
export function AdminAuditPanel() {
  const [entries, setEntries] = useState<AdminAuditEntry[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAudit = useCallback(async (beforeCursor?: string) => {
    if (beforeCursor) {
      setLoadingMore(true);
    } else {
      setLoading(true);
      setError(null);
    }
    try {
      const res = await getAdminAudit({
        before: beforeCursor || undefined,
        limit: 50,
      });
      if (beforeCursor) {
        setEntries((prev) => [...prev, ...res.entries]);
      } else {
        setEntries(res.entries);
      }
      setNextBefore(res.next_before);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log.');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void loadAudit();
  }, [loadAudit]);

  return (
    <section className="studio-section" aria-labelledby="admin-audit-heading">
      <div>
        <h2 id="admin-audit-heading" className="studio-section-title">
          Audit log
        </h2>
        <div className="studio-hint">
          Append-only record of invites, role and active changes, shared knowledge, and base prices.
        </div>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {loading && entries.length === 0 ? (
        <div className="mg-empty">Loading audit log.</div>
      ) : entries.length === 0 ? (
        <div className="mg-empty">No audit log entries yet.</div>
      ) : (
        <div className="studio-stack-sm">
          <div className="studio-table-wrap">
            <table className="studio-table" aria-label="Admin audit log">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Who</th>
                  <th>Action</th>
                  <th>Target</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={String(entry.id)}>
                    <td className="studio-mono">{formatTimestamp(entry.created_at)}</td>
                    <td>{entry.actor_name}</td>
                    <td className="studio-mono">{entry.action}</td>
                    <td className="studio-mono-wrap">{entry.target_label || entry.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {nextBefore ? (
            <div>
              <PillButton
                variant="outline"
                size="sm"
                disabled={loadingMore}
                onClick={() => void loadAudit(nextBefore)}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </PillButton>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
