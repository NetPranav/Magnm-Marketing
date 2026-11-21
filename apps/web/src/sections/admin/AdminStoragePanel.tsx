import { useCallback, useEffect, useState } from 'react';
import { Card, Row, RowList } from '@magnm/design/react';
import { getAdminStorage } from '../../api/admin';
import type { StorageUsage } from '../../api/account';
import { formatPreviewKindLabel, formatStorageBytes } from './adminUtils';

/** Admin storage meter: total bytes vs 1 GB limit, breakdown by preview kind, and 80% warning. */
export function AdminStoragePanel() {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadStorage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getAdminStorage();
      setUsage(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load storage usage.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStorage();
  }, [loadStorage]);

  const byKindEntries = usage?.by_kind ? Object.entries(usage.by_kind) : [];
  const pct = usage ? Math.min(100, Math.max(0, Math.round(usage.percent))) : 0;

  return (
    <section className="studio-section" aria-labelledby="admin-storage-heading">
      <div>
        <h2 id="admin-storage-heading" className="studio-section-title">
          Storage
        </h2>
        <div className="studio-hint">
          Shared preview storage across all team members. Video previews expire after 60 days.
        </div>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {loading && !usage ? (
        <div className="mg-empty">Loading storage usage.</div>
      ) : usage ? (
        <div className="studio-stack">
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-section-bar">
                <span className="studio-card-title">
                  {formatStorageBytes(usage.bytes)} of {formatStorageBytes(usage.limit_bytes)}
                </span>
                <span className="studio-mono">
                  {usage.files} files · {Math.round(usage.percent)}%
                </span>
              </div>
              <div
                className="studio-progress-track"
                role="progressbar"
                aria-label="Team storage usage"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct}
              >
                <div
                  className="studio-progress-fill"
                  style={{ width: `${Math.min(100, Math.max(0, usage.percent))}%` }}
                />
              </div>
              {usage.warning || usage.percent >= 80 ? (
                <div className="mg-notice" role="status">
                  Storage is almost full. Video previews will move to Backblaze B2.
                </div>
              ) : null}
            </div>
          </Card>

          <Card outline>
            <div className="studio-stack-sm">
              <span className="mg-label">By kind</span>
              {byKindEntries.length === 0 ? (
                <div className="studio-hint">No preview files stored yet.</div>
              ) : (
                <RowList aria-label="Storage usage by kind">
                  {byKindEntries.map(([kind, stats]) => (
                    <Row
                      key={kind}
                      title={formatPreviewKindLabel(kind)}
                      description={
                        <span className="studio-mono">{stats.files} files</span>
                      }
                      meta={formatStorageBytes(stats.bytes)}
                    />
                  ))}
                </RowList>
              )}
            </div>
          </Card>
        </div>
      ) : null}
    </section>
  );
}
