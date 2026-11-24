import { useState } from 'react';
import {
  Card,
  Field,
  Label,
  PillButton,
  Row,
  RowList,
  TextInput,
  ThemeToggle,
} from '@magnm/design/react';
import { kindLabel, stateLabel } from '../actions';
import type { NativeBridge } from '../native';
import { PersonalKeysPanel } from './PersonalKeysPanel';
import { ReposPanel } from './ReposPanel';
import { UpdatesPanel } from './UpdatesPanel';
import type {
  AccountProfile,
  AuthSession,
  PairingConfig,
  ParsedQrPayload,
  PhoneAction,
  QueuedInboxItem,
} from '../types';

export interface MoreTabProps {
  pairing: PairingConfig | null;
  pairingStep: 'idle' | 'confirm' | 'waiting';
  pendingPairPayload: ParsedQrPayload | null;
  pairingError: string | null;
  session: AuthSession | null;
  profile: AccountProfile | null;
  authError: string | null;
  moreError: string | null;
  bridge: NativeBridge;
  /** Called when the personal keys on this phone changed, with whether one is left. */
  onPersonalKeysChanged?: (hasKey: boolean) => void;
  queueItems: QueuedInboxItem[];
  /** Actions queued on this phone for the laptop (this account's only). */
  phoneActions?: PhoneAction[];
  onRemovePhoneAction?: (id: string) => void;
  laptopReachable: boolean;
  mirrorRecordCount: number;
  savedCopyLabel: string | null;
  busyAction: string | null;
  onScanQr: () => Promise<void>;
  onParsePairCode: (raw: string) => void;
  onConfirmPairing: (deviceName: string) => Promise<void>;
  onCancelPairing: () => void;
  onUnpair: () => void;
  onGoogleSignIn: () => Promise<void>;
  onSignOut: () => void;
  onFlushQueue: () => Promise<void>;
  onRemoveQueuedItem: (clientId: string) => void;
  onSyncMirror: () => Promise<void>;
}

export function MoreTab({
  pairing,
  pairingStep,
  pendingPairPayload,
  pairingError,
  session,
  profile,
  authError,
  moreError,
  bridge,
  onPersonalKeysChanged,
  queueItems,
  phoneActions = [],
  onRemovePhoneAction,
  laptopReachable,
  mirrorRecordCount,
  savedCopyLabel,
  busyAction,
  onScanQr,
  onParsePairCode,
  onConfirmPairing,
  onCancelPairing,
  onUnpair,
  onGoogleSignIn,
  onSignOut,
  onFlushQueue,
  onRemoveQueuedItem,
  onSyncMirror,
}: MoreTabProps) {
  const [qrInput, setQrInput] = useState('');
  const [phoneName, setPhoneName] = useState('My Android Phone');

  return (
    <section aria-label="More" className="mob-stack">
      {moreError ? (
        <div className="mob-banner-error" role="alert">
          {moreError}
        </div>
      ) : null}

      <Card>
        <div className="mob-stack-sm">
          <Label>Laptop pairing</Label>
          {pairingError ? (
            <div className="mob-banner-error" role="alert">
              {pairingError}
            </div>
          ) : null}

          {pairing ? (
            <div className="mob-stack-sm">
              <div className="mob-body-text">
                Paired as {pairing.deviceName} ({pairing.host}:{pairing.port})
              </div>
              <span className="mob-mono">Device ID: {pairing.deviceId}</span>
              <div className="mob-actions">
                <PillButton
                  variant="danger"
                  size="sm"
                  disabled={busyAction === 'unpair'}
                  onClick={onUnpair}
                >
                  Unpair laptop
                </PillButton>
              </div>
            </div>
          ) : pairingStep === 'confirm' && pendingPairPayload ? (
            <div className="mob-stack-sm">
              <div className="mg-notice">
                Pair with this laptop? {pendingPairPayload.host}:{pendingPairPayload.port}
              </div>
              <Field label="Name this phone" htmlFor="pair-device-name">
                <TextInput
                  id="pair-device-name"
                  value={phoneName}
                  onChange={(e) => setPhoneName(e.target.value)}
                  placeholder="Pixel 9"
                />
              </Field>
              <div className="mob-actions">
                <PillButton
                  size="sm"
                  disabled={!phoneName.trim() || busyAction === 'pair'}
                  onClick={() => void onConfirmPairing(phoneName)}
                >
                  Pair with this laptop
                </PillButton>
                <PillButton variant="outline" size="sm" onClick={onCancelPairing}>
                  Cancel
                </PillButton>
              </div>
            </div>
          ) : pairingStep === 'waiting' ? (
            <div className="mob-stack-sm">
              <div className="mg-notice">
                Waiting for approval on your laptop ({pendingPairPayload?.host}:{pendingPairPayload?.port})...
              </div>
              <div className="mob-actions">
                <PillButton variant="outline" size="sm" onClick={onCancelPairing}>
                  Cancel
                </PillButton>
              </div>
            </div>
          ) : (
            <div className="mob-stack-sm">
              <Field label="Pairing code (magnm-pair:...)" htmlFor="pair-code-input">
                <TextInput
                  id="pair-code-input"
                  value={qrInput}
                  onChange={(e) => setQrInput(e.target.value)}
                  placeholder="magnm-pair:..."
                />
              </Field>
              <div className="mob-actions">
                <PillButton
                  size="sm"
                  disabled={!qrInput.trim()}
                  onClick={() => onParsePairCode(qrInput)}
                >
                  Continue
                </PillButton>
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={() => void onScanQr()}
                >
                  Scan QR
                </PillButton>
              </div>
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <Label>Account</Label>
          {authError ? (
            <div className="mob-banner-error" role="alert">
              {authError}
            </div>
          ) : null}
          {session ? (
            <div className="mob-stack-sm">
              <div className="mob-body-text">
                {profile?.display_name || session.email || 'Signed in'}
              </div>
              <span className="mob-mono">
                {session.email}
                {profile?.role ? ` · ${profile.role}` : ''}
              </span>
              <div className="mob-actions">
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={busyAction === 'signout'}
                  onClick={onSignOut}
                >
                  Sign out
                </PillButton>
              </div>
            </div>
          ) : (
            <div className="mob-stack-sm">
              <p className="mob-body-text">
                Sign in with your invited Google account to sync context and use shared team AI in Phone Lite.
              </p>
              <div className="mob-actions">
                <PillButton
                  size="sm"
                  disabled={busyAction === 'signin'}
                  onClick={() => void onGoogleSignIn()}
                >
                  Sign in with Google
                </PillButton>
              </div>
            </div>
          )}
        </div>
      </Card>

      <PersonalKeysPanel bridge={bridge} signedIn={Boolean(session)} onChanged={onPersonalKeysChanged} />

      <ReposPanel bridge={bridge} userId={session?.user_id ?? null} />

      <UpdatesPanel bridge={bridge} />

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Offline queue</Label>
            <span className="mob-mono">{queueItems.length} pending</span>
          </div>
          {queueItems.length === 0 ? (
            <p className="mob-mono">No queued offline drafts, notes, or ideas.</p>
          ) : (
            <RowList>
              {queueItems.map((item) => (
                <Row
                  key={item.client_id}
                  title={`[${item.kind}] ${item.title}`}
                  description={item.last_error || item.body.slice(0, 120) || 'Ready to sync'}
                  meta={
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() => onRemoveQueuedItem(item.client_id)}
                    >
                      Remove
                    </PillButton>
                  }
                />
              ))}
            </RowList>
          )}
          {queueItems.length > 0 ? (
            <div className="mob-actions">
              <PillButton
                size="sm"
                disabled={!laptopReachable || busyAction === 'flush-queue'}
                onClick={() => void onFlushQueue()}
              >
                Sync queue now
              </PillButton>
            </div>
          ) : null}
        </div>
      </Card>

      {session ? (
        <Card>
          <div className="mob-stack-sm">
            <div className="mob-row-between">
              <Label>Phone actions</Label>
              <span className="mob-mono">
                {phoneActions.filter((a) => a.state === 'waiting').length} waiting
              </span>
            </div>
            {phoneActions.length === 0 ? (
              <p className="mob-mono">
                Comments, posted marks, finished reminders and agent requests made while your laptop is away show here.
              </p>
            ) : (
              <RowList>
                {[...phoneActions].reverse().map((action) => (
                  <Row
                    key={action.id}
                    title={`${kindLabel(action.kind)}${action.label ? `: ${action.label}` : ''}`}
                    description={
                      action.state === 'waiting'
                        ? action.sent
                          ? 'The laptop will apply this when it is back.'
                          : 'Will be sent as soon as the phone is online.'
                        : action.result || undefined
                    }
                    meta={
                      <>
                        <span className="mg-chip">{stateLabel(action)}</span>
                        {onRemovePhoneAction && (action.state !== 'waiting' || !action.sent) ? (
                          <PillButton variant="outline" size="sm" onClick={() => onRemovePhoneAction(action.id)}>
                            Remove
                          </PillButton>
                        ) : null}
                      </>
                    }
                  />
                ))}
              </RowList>
            )}
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>Context cache</Label>
            <span className="mob-mono">{mirrorRecordCount} / 200 records</span>
          </div>
          {savedCopyLabel ? <div className="mob-mono">{savedCopyLabel}</div> : null}
          <div className="mob-actions">
            <PillButton
              variant="outline"
              size="sm"
              disabled={!session || busyAction === 'sync-mirror'}
              onClick={() => void onSyncMirror()}
            >
              Sync context cache
            </PillButton>
          </div>
        </div>
      </Card>

      <Card>
        <div className="mob-stack-sm">
          <Label>Appearance</Label>
          <ThemeToggle />
          <hr className="mg-divider" />
          <Label>Privacy note</Label>
          <p className="mob-body-text">
            Laptop calls go strictly over Tailscale (100.64.0.0/10) with your device token. Sign-in tokens and API keys stay in the device secure keystore and are never written to web storage. Confidential work stays on your laptop and is never sent to cloud models in Phone Lite. Non-confidential mirrored records remain visible to workspace admins.
          </p>
        </div>
      </Card>
    </section>
  );
}
