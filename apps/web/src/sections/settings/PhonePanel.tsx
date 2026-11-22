import { useCallback, useEffect, useMemo, useState } from 'react';
import qrcode from 'qrcode-generator';
import { Card, Checkbox, PillButton, Row, RowList, StatusDot } from '@magnm/design/react';
import {
  approvePhoneRequest,
  cancelPhonePairing,
  denyPhoneRequest,
  disablePhone,
  enablePhone,
  getPhoneStatus,
  isTailscaleMissingError,
  revokePhoneDevice,
  startPhonePairing,
  type PhoneStatus,
} from '../../api/phone';
import { useFallbackPoll, useStudioEvents } from '../../events';

const TAILSCALE_MISSING_TEXT = 'Install and connect Tailscale on this laptop first.';
const QR_MARGIN = 2;

function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${mins}:${secs < 10 ? '0' : ''}${secs}`;
}

function formatLastSeen(iso?: string | null): string {
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

function buildQrSvgModel(payload: string): { size: number; pathData: string } | null {
  try {
    const qr = qrcode(0, 'M');
    qr.addData(payload);
    qr.make();
    const count = qr.getModuleCount();
    const parts: string[] = [];
    for (let r = 0; r < count; r += 1) {
      for (let c = 0; c < count; c += 1) {
        if (qr.isDark(r, c)) {
          parts.push(`M${c + QR_MARGIN} ${r + QR_MARGIN}h1v1h-1z`);
        }
      }
    }
    return {
      size: count + QR_MARGIN * 2,
      pathData: parts.join(''),
    };
  } catch {
    return null;
  }
}

/** Settings card: Tailscale phone connection toggle, QR pairing with approval, and device revocation. */
export function PhonePanel() {
  const [phone, setPhone] = useState<PhoneStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tailscaleMissing, setTailscaleMissing] = useState(false);
  const [countdownSec, setCountdownSec] = useState(0);

  const applyStatus = useCallback((next: PhoneStatus) => {
    setPhone(next);
    setTailscaleMissing(Boolean(next.tailscale_missing));
    if (next.error && next.tailscale_missing) {
      setError(next.error);
    }
    if (next.pairing?.active && next.pairing.expires_at) {
      const ms = new Date(next.pairing.expires_at).getTime() - Date.now();
      setCountdownSec(Math.max(0, Math.ceil(ms / 1000)));
    } else if (next.pairing?.active && typeof next.pairing.expires_in === 'number') {
      setCountdownSec(Math.max(0, next.pairing.expires_in));
    } else {
      setCountdownSec(0);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const res = await getPhoneStatus();
      if (res && typeof res === 'object' && 'enabled' in res) {
        applyStatus(res);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load phone connection status.');
    } finally {
      setLoading(false);
    }
  }, [applyStatus]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const eventsState = useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'phone.updated') {
          void loadStatus();
        }
      },
      [loadStatus],
    ),
  );

  const pairingActive = Boolean(phone?.pairing?.active || (phone?.pairing?.pending_requests?.length ?? 0) > 0);

  useFallbackPoll(
    useCallback(() => {
      void loadStatus();
    }, [loadStatus]),
    pairingActive,
    eventsState,
    2000,
  );

  useEffect(() => {
    if (!phone?.pairing?.active) {
      return undefined;
    }
    const timer = setInterval(() => {
      setCountdownSec((prev) => {
        if (prev <= 1) {
          void loadStatus();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [loadStatus, phone?.pairing?.active, phone?.pairing?.expires_at]);

  const handleToggle = async (nextEnabled: boolean) => {
    setError(null);
    setTailscaleMissing(false);
    setBusy('toggle');
    try {
      const res = nextEnabled ? await enablePhone() : await disablePhone();
      applyStatus(res);
    } catch (err) {
      const missing = isTailscaleMissingError(err);
      setTailscaleMissing(missing);
      setError(
        missing
          ? TAILSCALE_MISSING_TEXT
          : err instanceof Error
            ? err.message
            : 'Failed to update phone connections.',
      );
      setPhone((cur) => (cur ? { ...cur, enabled: false, running: false } : cur));
    } finally {
      setBusy(null);
    }
  };

  const handleStartPairing = async () => {
    setError(null);
    setBusy('pair-start');
    try {
      const res = await startPhonePairing();
      if (res.status) {
        applyStatus(res.status);
      } else {
        setPhone((cur) =>
          cur
            ? {
                ...cur,
                pairing: {
                  active: Boolean(res.active),
                  qr_payload: res.qr_payload,
                  expires_at: res.expires_at,
                  expires_in: res.expires_in,
                  pending_requests: cur.pairing?.pending_requests ?? [],
                },
              }
            : cur,
        );
        setCountdownSec(Math.max(0, res.expires_in || 300));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start phone pairing.');
    } finally {
      setBusy(null);
    }
  };

  const handleCancelPairing = async () => {
    setError(null);
    setBusy('pair-cancel');
    try {
      const res = await cancelPhonePairing();
      applyStatus(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel pairing.');
    } finally {
      setBusy(null);
    }
  };

  const handleApprove = async (requestId: string) => {
    setError(null);
    setBusy(`approve-${requestId}`);
    try {
      const res = await approvePhoneRequest(requestId);
      if (res.status) {
        applyStatus(res.status);
      } else {
        await loadStatus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not approve pairing request.');
    } finally {
      setBusy(null);
    }
  };

  const handleDeny = async (requestId: string) => {
    setError(null);
    setBusy(`deny-${requestId}`);
    try {
      const res = await denyPhoneRequest(requestId);
      if (res.status) {
        applyStatus(res.status);
      } else {
        await loadStatus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not deny pairing request.');
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async (deviceId: string) => {
    setError(null);
    setBusy(`revoke-${deviceId}`);
    try {
      const res = await revokePhoneDevice(deviceId);
      if (res.status) {
        applyStatus(res.status);
      } else {
        await loadStatus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke phone device.');
    } finally {
      setBusy(null);
    }
  };

  const qrPayload = phone?.pairing?.qr_payload || '';
  const qrModel = useMemo(() => (qrPayload ? buildQrSvgModel(qrPayload) : null), [qrPayload]);
  const pendingRequests = phone?.pairing?.pending_requests || [];
  const devices = phone?.devices || [];
  const enabled = Boolean(phone?.enabled);
  const running = Boolean(phone?.running);

  return (
    <section className="studio-section" aria-labelledby="settings-phone-heading">
      <div className="studio-section-bar">
        <div className="studio-settings-head">
          <span className="studio-settings-num">10</span>
          <h2 id="settings-phone-heading" className="studio-section-title">
            Phone
          </h2>
        </div>
        {phone ? (
          <StatusDot
            state={running ? 'online' : tailscaleMissing ? 'error' : 'off'}
            label={running ? `Listening on ${phone.host}:${phone.port}` : enabled ? 'Stopped' : 'Off'}
          />
        ) : null}
      </div>

      {tailscaleMissing ? (
        <div className="mg-notice" role="alert">
          {TAILSCALE_MISSING_TEXT}
        </div>
      ) : error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          {loading && !phone ? (
            <div className="studio-hint">Checking phone connection status...</div>
          ) : (
            <>
              <div className="studio-section-bar">
                <div className="studio-stack-xs">
                  <Checkbox
                    role="switch"
                    aria-checked={enabled}
                    label="Allow phone connections"
                    checked={enabled}
                    disabled={busy === 'toggle'}
                    onChange={(e) => void handleToggle(e.target.checked)}
                  />
                  <div className="studio-hint">
                    Connects your paired Android phone to this laptop strictly over Tailscale.
                  </div>
                </div>
                {enabled && running && !phone?.pairing?.active ? (
                  <PillButton
                    size="sm"
                    disabled={busy === 'pair-start'}
                    onClick={() => void handleStartPairing()}
                  >
                    {busy === 'pair-start' ? 'Starting...' : 'Pair a phone'}
                  </PillButton>
                ) : null}
              </div>

              {phone?.pairing?.active && qrModel ? (
                <div className="studio-phone-pair-box" aria-label="Phone pairing QR code">
                  <div className="studio-phone-qr">
                    <svg
                      viewBox={`0 0 ${qrModel.size} ${qrModel.size}`}
                      width={180}
                      height={180}
                      role="img"
                      aria-label="Pairing QR code"
                      shapeRendering="crispEdges"
                    >
                      <rect width={qrModel.size} height={qrModel.size} fill="#ffffff" />
                      <path d={qrModel.pathData} fill="#0a0a0a" />
                    </svg>
                  </div>
                  <div className="studio-stack-xs">
                    <div className="studio-card-title">Scan with Magnm Mobile</div>
                    <div className="studio-hint">
                      Open Magnm on your phone (on the same Tailscale account) and scan this code.
                    </div>
                    <div className="studio-mono" data-testid="phone-pair-countdown">
                      Expires in {formatCountdown(countdownSec)}
                    </div>
                    <div>
                      <PillButton
                        variant="quiet"
                        size="sm"
                        disabled={busy === 'pair-cancel'}
                        onClick={() => void handleCancelPairing()}
                      >
                        Cancel
                      </PillButton>
                    </div>
                  </div>
                </div>
              ) : null}

              {pendingRequests.length > 0 ? (
                <div className="studio-stack-xs">
                  <div className="mg-label">Pending approval</div>
                  <RowList aria-label="Pending phone pairing requests">
                    {pendingRequests.map((req) => (
                      <Row
                        key={req.request_id}
                        title={req.device_name}
                        description={
                          <span className="studio-mono">Source IP: {req.source_ip}</span>
                        }
                        meta={
                          <div className="studio-row-actions">
                            <PillButton
                              size="sm"
                              disabled={busy === `approve-${req.request_id}`}
                              onClick={() => void handleApprove(req.request_id)}
                            >
                              Approve
                            </PillButton>
                            <PillButton
                              variant="outline"
                              size="sm"
                              disabled={busy === `deny-${req.request_id}`}
                              onClick={() => void handleDeny(req.request_id)}
                            >
                              Deny
                            </PillButton>
                          </div>
                        }
                      />
                    ))}
                  </RowList>
                </div>
              ) : null}
            </>
          )}
        </div>
      </Card>

      <Card outline>
        <div className="studio-stack-sm">
          <div className="mg-label">Paired devices</div>
          {devices.length === 0 ? (
            <div className="studio-hint">No phones paired yet.</div>
          ) : (
            <RowList aria-label="Paired phones">
              {devices.map((dev) => (
                <Row
                  key={dev.id}
                  title={dev.name}
                  description={
                    <span className="studio-mono">
                      Last seen: {formatLastSeen(dev.last_seen_at)}
                      {dev.last_ip ? ` · ${dev.last_ip}` : ''}
                    </span>
                  }
                  meta={
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busy === `revoke-${dev.id}`}
                      aria-label={`Revoke ${dev.name}`}
                      onClick={() => void handleRevoke(dev.id)}
                    >
                      Revoke
                    </PillButton>
                  }
                />
              ))}
            </RowList>
          )}
        </div>
      </Card>
    </section>
  );
}
