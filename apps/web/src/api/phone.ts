import { ApiError, api } from '../api';

export interface PhonePendingRequest {
  request_id: string;
  device_name: string;
  source_ip: string;
  created_at: string;
  expires_at: string;
}

export interface PhoneDevice {
  id: string;
  name: string;
  created_at: string;
  last_seen_at?: string | null;
  last_ip?: string | null;
  revoked_at?: string | null;
  owner_user_id?: string | null;
}

export interface PhonePairingState {
  active: boolean;
  qr_payload: string | null;
  expires_at: string | null;
  expires_in: number;
  pending_requests: PhonePendingRequest[];
}

export interface PhoneStatus {
  enabled: boolean;
  running: boolean;
  host: string | null;
  port: number;
  tailscale_missing?: boolean;
  error?: string | null;
  pairing: PhonePairingState;
  devices: PhoneDevice[];
}

export interface PhonePairingStartResult {
  active: boolean;
  qr_payload: string;
  expires_at: string;
  expires_in: number;
  status?: PhoneStatus;
}

export function isTailscaleMissingError(err: unknown): boolean {
  if (err instanceof ApiError && err.data && typeof err.data === 'object') {
    if ((err.data as { tailscale_missing?: unknown }).tailscale_missing === true) {
      return true;
    }
  }
  if (err instanceof Error && err.message.toLowerCase().includes('tailscale')) {
    return true;
  }
  return false;
}

/** Fetch current phone listener status, pairing state, and paired devices. */
export function getPhoneStatus(): Promise<PhoneStatus> {
  return api.get<PhoneStatus>('/api/phone');
}

/** Enable the Tailscale phone listener. */
export function enablePhone(): Promise<PhoneStatus> {
  return api.post<PhoneStatus>('/api/phone/enable', {});
}

/** Disable and stop the Tailscale phone listener immediately. */
export function disablePhone(): Promise<PhoneStatus> {
  return api.post<PhoneStatus>('/api/phone/disable', {});
}

/** Start a 5-minute phone pairing session and return the QR payload. */
export function startPhonePairing(): Promise<PhonePairingStartResult> {
  return api.post<PhonePairingStartResult>('/api/phone/pair/start', {});
}

/** Cancel the active phone pairing code and pending requests. */
export function cancelPhonePairing(): Promise<PhoneStatus> {
  return api.post<PhoneStatus>('/api/phone/pair/cancel', {});
}

/** Approve a pending phone pairing request by request_id. */
export function approvePhoneRequest(
  requestId: string,
): Promise<{ device: PhoneDevice; status: PhoneStatus }> {
  return api.post<{ device: PhoneDevice; status: PhoneStatus }>('/api/phone/pair/approve', {
    request_id: requestId,
  });
}

/** Deny a pending phone pairing request by request_id. */
export function denyPhoneRequest(
  requestId: string,
): Promise<{ ok: boolean; status: PhoneStatus }> {
  return api.post<{ ok: boolean; status: PhoneStatus }>('/api/phone/pair/deny', {
    request_id: requestId,
  });
}

/** Revoke a paired phone device so its token is rejected on the next request. */
export function revokePhoneDevice(
  deviceId: string,
): Promise<{ device: PhoneDevice; status: PhoneStatus }> {
  return api.post<{ device: PhoneDevice; status: PhoneStatus }>('/api/phone/devices/revoke', {
    device_id: deviceId,
  });
}

/** Send a non-confidential proposal or content item to the paired phone(s). */
export function sendToPhone(
  kind: 'proposal' | 'content',
  id: string,
): Promise<{ ok: boolean; route: string }> {
  return api.post<{ ok: boolean; route: string }>('/api/phone/send', { kind, id });
}
