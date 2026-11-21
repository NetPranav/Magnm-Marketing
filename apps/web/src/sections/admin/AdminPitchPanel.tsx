import { useCallback, useEffect, useState } from 'react';
import { Card, Row, RowList, Select } from '@magnm/design/react';
import { getMemberPitch, type AdminMember, type AdminPitchResponse } from '../../api/admin';
import { formatRupees } from '../../api/pitch';
import { formatTimestamp } from '../../ui/format';
import { memberDisplayName } from './adminUtils';

export interface AdminPitchPanelProps {
  members: AdminMember[];
  memberId: string;
  onSelectMember: (memberId: string) => void;
}

/** Read-only Pitch view for a member: published links (short share IDs) and quote requests. */
export function AdminPitchPanel({ members, memberId, onSelectMember }: AdminPitchPanelProps) {
  const [data, setData] = useState<AdminPitchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPitch = useCallback(async (mid: string) => {
    if (!mid) return;
    setLoading(true);
    setError(null);
    try {
      const res = await getMemberPitch(mid);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load Pitch data.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (memberId) {
      void loadPitch(memberId);
    }
  }, [memberId, loadPitch]);

  const proposals = data?.proposals || [];
  const submissions = data?.submissions || [];

  return (
    <section className="studio-section" aria-labelledby="admin-pitch-heading">
      <div className="studio-section-bar">
        <div>
          <h2 id="admin-pitch-heading" className="studio-section-title">
            Pitch (read-only)
          </h2>
          <div className="studio-hint">
            Published client links and incoming quote requests for the selected member.
          </div>
        </div>
        <Select
          aria-label="Member for Pitch"
          value={memberId}
          onChange={(e) => onSelectMember(e.target.value)}
        >
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {memberDisplayName(m)}
            </option>
          ))}
        </Select>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {!memberId ? (
        <div className="mg-empty">Select a team member to inspect their Pitch activity.</div>
      ) : loading && !data ? (
        <div className="mg-empty">Loading Pitch links and quote requests.</div>
      ) : (
        <div className="studio-stack">
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-section-bar">
                <span className="mg-label">Published links</span>
                <span className="studio-mono">{proposals.length} links</span>
              </div>

              {proposals.length === 0 ? (
                <div className="studio-hint">No published links for this member.</div>
              ) : (
                <RowList aria-label="Published Pitch links">
                  {proposals.map((prop, idx) => (
                    <Row
                      key={`${prop.share_id}-${idx}`}
                      title={
                        [prop.business_name, prop.client_name].filter(Boolean).join(' · ') ||
                        prop.kind
                      }
                      description={
                        <div className="studio-row-actions">
                          <span className="mg-chip">{prop.kind}</span>
                          <span className="mg-chip">{prop.status}</span>
                          <span className="studio-mono">ID: {prop.share_id}</span>
                        </div>
                      }
                      meta={formatTimestamp(prop.updated_at)}
                    />
                  ))}
                </RowList>
              )}
            </div>
          </Card>

          <Card outline>
            <div className="studio-stack-sm">
              <div className="studio-section-bar">
                <span className="mg-label">Quote requests</span>
                <span className="studio-mono">{submissions.length} requests</span>
              </div>

              {submissions.length === 0 ? (
                <div className="studio-hint">No quote requests for this member.</div>
              ) : (
                <RowList aria-label="Member quote requests">
                  {submissions.map((sub) => (
                    <Row
                      key={sub.id}
                      title={
                        [sub.business, sub.name].filter(Boolean).join(' · ') || 'Client inquiry'
                      }
                      description={
                        <div className="studio-stack-xs">
                          <div className="studio-row-actions">
                            {sub.phone ? <span className="studio-mono">{sub.phone}</span> : null}
                            <span className="studio-mono">Link: {sub.share_id}</span>
                            {sub.seen_at ? (
                              <span className="mg-chip">Seen</span>
                            ) : (
                              <span className="mg-chip">New</span>
                            )}
                          </div>
                          {sub.items.length > 0 ? (
                            <div className="studio-hint">Items: {sub.items.join(', ')}</div>
                          ) : null}
                          {sub.notes ? <div className="studio-hint">Notes: {sub.notes}</div> : null}
                        </div>
                      }
                      meta={
                        <div className="studio-quote-request-meta">
                          {typeof sub.total_shown === 'number' ? (
                            <span className="studio-pitch-total">
                              {formatRupees(sub.total_shown)}
                            </span>
                          ) : null}
                          <span>{formatTimestamp(sub.created_at)}</span>
                        </div>
                      }
                    />
                  ))}
                </RowList>
              )}
            </div>
          </Card>
        </div>
      )}
    </section>
  );
}
