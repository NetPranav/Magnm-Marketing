import { useState } from 'react';
import {
  Card,
  Field,
  Label,
  PillButton,
  Row,
  RowList,
  StatusDot,
  TextInput,
  type StatusState,
} from '@magnm/design/react';
import { validateMetricInputs } from '../metrics';
import type {
  LaptopStatus,
  MetricProposal,
  PairingConfig,
  PhoneNotification,
  ReminderItem,
  WeeklySummary,
} from '../types';

export interface TodayTabProps {
  pairing: PairingConfig | null;
  laptopReachable: boolean;
  laptopAuthError: string | null;
  laptopStatus: LaptopStatus | null;
  savedCopyLabel: string | null;
  reminders: ReminderItem[];
  notifications: PhoneNotification[];
  unreadCount: number;
  summary: WeeklySummary | null;
  pendingResults: MetricProposal[];
  busyAction: string | null;
  errorMessage: string | null;
  onRefresh: () => void;
  onGoToPairing: () => void;
  onMarkReminderDone: (id: string) => void;
  onMarkAllNotificationsRead: () => void;
  onOpenNotification?: (notif: PhoneNotification) => void;
  onConfirmResult: (
    proposalId: string,
    metrics: Record<string, number | null>,
    platform: string | null,
    contentId: string | null,
  ) => Promise<boolean>;
}

function formatMetricInput(metrics: Record<string, number | null> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(metrics ?? {})) {
    out[k] = v === null || v === undefined ? '' : String(v);
  }
  return out;
}

export function TodayTab({
  pairing,
  laptopReachable,
  laptopAuthError,
  laptopStatus,
  savedCopyLabel,
  reminders,
  notifications,
  unreadCount,
  summary,
  pendingResults,
  busyAction,
  errorMessage,
  onRefresh,
  onGoToPairing,
  onMarkReminderDone,
  onMarkAllNotificationsRead,
  onOpenNotification,
  onConfirmResult,
}: TodayTabProps) {
  const [editingProposalId, setEditingProposalId] = useState<string | null>(null);
  const [editedMetrics, setEditedMetrics] = useState<Record<string, string>>({});
  const [editedPlatform, setEditedPlatform] = useState('');
  const [editedContentId, setEditedContentId] = useState('');
  const [metricError, setMetricError] = useState<string | null>(null);

  let statusState: StatusState = 'off';
  let statusText = 'Not paired';
  if (laptopAuthError) {
    statusState = 'error';
    statusText = 'Authorization failed (401)';
  } else if (pairing && laptopReachable) {
    statusState = (laptopStatus?.running_jobs?.length || laptopStatus?.running_agents?.length)
      ? 'working'
      : 'online';
    statusText = 'Laptop connected';
  } else if (pairing) {
    statusState = 'idle';
    statusText = 'Laptop unreachable';
  }

  function startEditingProposal(prop: MetricProposal) {
    setEditingProposalId(prop.id);
    setEditedMetrics(formatMetricInput(prop.metrics));
    setEditedPlatform(prop.platform ?? '');
    setEditedContentId(prop.content_id ?? '');
    setMetricError(null);
  }

  async function submitConfirmedResult(propId: string) {
    setMetricError(null);
    const validated = validateMetricInputs(editedMetrics);
    if (!validated.ok) {
      setMetricError(validated.error);
      return;
    }
    const ok = await onConfirmResult(
      propId,
      validated.metrics,
      editedPlatform.trim() || null,
      editedContentId.trim() || null,
    );
    if (ok) {
      setEditingProposalId(null);
    }
  }

  const summaryHighlights =
    summary?.highlights && typeof summary.highlights === 'object'
      ? (summary.highlights as Record<string, unknown>)
      : null;

  return (
    <section aria-label="Today" className="mob-stack">
      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Laptop status</Label>
            <StatusDot state={statusState} label={statusText} live />
          </div>

          {laptopAuthError ? (
            <div className="mob-banner-error" role="alert">
              <span>{laptopAuthError}</span>
              <PillButton variant="outline" size="sm" onClick={onGoToPairing}>
                Open pairing
              </PillButton>
            </div>
          ) : null}

          {errorMessage ? (
            <div className="mob-banner-error" role="alert">
              {errorMessage}
            </div>
          ) : null}

          {!pairing ? (
            <div className="mob-stack-sm">
              <p className="mob-body-text">
                Pair this phone with your laptop over Tailscale to view live status, drafts, and reminders.
              </p>
              <div className="mob-actions">
                <PillButton size="sm" onClick={onGoToPairing}>
                  Pair laptop
                </PillButton>
              </div>
            </div>
          ) : (
            <div className="mob-stack-sm">
              <div className="mob-mono">
                {pairing.deviceName} · {pairing.host}:{pairing.port}
                {laptopStatus?.version ? ` · engine ${laptopStatus.version}` : ''}
              </div>
              {!laptopReachable && savedCopyLabel ? (
                <div className="mg-notice" data-testid="offline-saved-label">
                  {savedCopyLabel}
                </div>
              ) : null}
              {laptopReachable && laptopStatus ? (
                <div className="mob-mono">
                  Agents running: {laptopStatus.running_agents.length > 0 ? laptopStatus.running_agents.join(', ') : 'None'}
                  {' · '}
                  Jobs: {laptopStatus.running_jobs.length > 0 ? laptopStatus.running_jobs.join(', ') : 'Idle'}
                </div>
              ) : null}
              <div className="mob-actions">
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={busyAction === 'refresh'}
                  onClick={onRefresh}
                >
                  {busyAction === 'refresh' ? 'Refreshing...' : 'Refresh'}
                </PillButton>
              </div>
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Pending results</Label>
            <span className="mob-mono">{pendingResults.length}</span>
          </div>
          {pendingResults.length === 0 ? (
            <p className="mob-mono">No pending screenshot results to confirm.</p>
          ) : (
            <div className="mob-stack-sm">
              {pendingResults.map((prop) => {
                if (prop.confidential) {
                  return (
                    <div key={prop.id} className="mg-notice">
                      Confidential result — open Magnm Studio on your laptop to review.
                    </div>
                  );
                }
                const isEditing = editingProposalId === prop.id;
                const metricEntries = Object.entries(prop.metrics ?? {}).filter(([, v]) => v !== null);
                return (
                  <Card key={prop.id} outline>
                    <div className="mob-stack-sm">
                      <div className="mob-row-between">
                        <strong>{prop.platform || 'Post'} results</strong>
                        <span className="mob-mono">{prop.id}</span>
                      </div>
                      {!isEditing ? (
                        <>
                          <div className="mob-mono">
                            {metricEntries.length > 0
                              ? metricEntries.map(([k, v]) => `${k}: ${String(v)}`).join(' · ')
                              : 'No metrics parsed yet'}
                          </div>
                          <div className="mob-actions">
                            <PillButton
                              size="sm"
                              disabled={!laptopReachable}
                              onClick={() => startEditingProposal(prop)}
                            >
                              Review &amp; save
                            </PillButton>
                          </div>
                        </>
                      ) : (
                        <div className="mob-stack-sm">
                          {metricError ? (
                            <div className="mob-banner-error" role="alert">
                              {metricError}
                            </div>
                          ) : null}
                          <Field label="Platform" htmlFor={`res-plat-${prop.id}`}>
                            <TextInput
                              id={`res-plat-${prop.id}`}
                              value={editedPlatform}
                              onChange={(e) => setEditedPlatform(e.target.value)}
                              placeholder="linkedin"
                            />
                          </Field>
                          <Field label="Draft ID (optional)" htmlFor={`res-cid-${prop.id}`}>
                            <TextInput
                              id={`res-cid-${prop.id}`}
                              value={editedContentId}
                              onChange={(e) => setEditedContentId(e.target.value)}
                              placeholder="content_..."
                            />
                          </Field>
                          <div className="mob-metric-grid">
                            {Object.keys(editedMetrics).map((metricKey) => (
                              <Field
                                key={metricKey}
                                label={metricKey}
                                htmlFor={`res-m-${prop.id}-${metricKey}`}
                              >
                                <TextInput
                                  id={`res-m-${prop.id}-${metricKey}`}
                                  value={editedMetrics[metricKey]}
                                  onChange={(e) =>
                                    setEditedMetrics((prev) => ({
                                      ...prev,
                                      [metricKey]: e.target.value,
                                    }))
                                  }
                                />
                              </Field>
                            ))}
                          </div>
                          <div className="mob-actions">
                            <PillButton
                              size="sm"
                              disabled={!laptopReachable || busyAction === `confirm-${prop.id}`}
                              onClick={() => void submitConfirmedResult(prop.id)}
                            >
                              Save results
                            </PillButton>
                            <PillButton
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setMetricError(null);
                                setEditingProposalId(null);
                              }}
                            >
                              Cancel
                            </PillButton>
                          </div>
                        </div>
                      )}
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Reminders</Label>
            <span className="mob-mono">{reminders.length} open</span>
          </div>
          {reminders.length === 0 ? (
            <p className="mob-mono">No open reminders.</p>
          ) : (
            <RowList>
              {reminders.map((rem) => (
                <Row
                  key={rem.id}
                  title={rem.confidential ? 'Confidential reminder' : rem.title}
                  description={rem.confidential ? 'Open on laptop to view details.' : rem.note || undefined}
                  meta={
                    rem.confidential ? (
                      'Confidential'
                    ) : (
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={!laptopReachable || busyAction === `rem-${rem.id}`}
                        onClick={() => onMarkReminderDone(rem.id)}
                      >
                        Done
                      </PillButton>
                    )
                  }
                />
              ))}
            </RowList>
          )}
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Notifications</Label>
            <div className="mob-actions">
              <span className="mob-mono">{unreadCount} unread</span>
              {unreadCount > 0 ? (
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={!laptopReachable || busyAction === 'notif-read'}
                  onClick={onMarkAllNotificationsRead}
                >
                  Mark read
                </PillButton>
              ) : null}
            </div>
          </div>
          {notifications.length === 0 ? (
            <p className="mob-mono">No notifications yet.</p>
          ) : (
            <RowList>
              {notifications.slice(0, 10).map((notif) => {
                const route = notif.route || '';
                const canOpen =
                  !notif.confidential &&
                  Boolean(onOpenNotification) &&
                  (route.startsWith('#/pitch/') || route.startsWith('#/review/'));
                return (
                  <Row
                    key={notif.id}
                    title={notif.confidential ? 'Confidential notification' : notif.title}
                    description={notif.confidential ? undefined : notif.body || undefined}
                    meta={notif.read_at ? 'Read' : 'New'}
                    onClick={canOpen ? () => onOpenNotification?.(notif) : undefined}
                  />
                );
              })}
            </RowList>
          )}
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Weekly summary</Label>
            {summary?.week_start ? <span className="mob-mono">{summary.week_start}</span> : null}
          </div>
          {!summary ? (
            <p className="mob-mono">No weekly summary available yet.</p>
          ) : summary.confidential ? (
            <div className="mg-notice">
              Confidential summary — open Magnm Studio on your laptop to read.
            </div>
          ) : (
            <div className="mob-stack-sm">
              <p className="mob-body-text">
                {summary.narrative || 'Summary generated. Open desktop for full breakdown.'}
              </p>
              {summaryHighlights ? (
                <div className="mob-mono">
                  Status: {summary.status || 'ready'}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </Card>
    </section>
  );
}
