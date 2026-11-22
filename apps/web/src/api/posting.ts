import { api } from '../api';
import type {
  PostingAttemptRecord,
  PostingConfirmResult,
  PostingPlatform,
  PostingPrepareResult,
  PostingStatusInfo,
} from '../types';

export function getPostingStatus(): Promise<PostingStatusInfo> {
  return api.get<PostingStatusInfo>('/api/posting/status');
}

export async function listPostingAttempts(
  contentId?: string,
  platform?: PostingPlatform,
  limit = 20,
): Promise<PostingAttemptRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (contentId) params.set('content_id', contentId);
  if (platform) params.set('platform', platform);
  const res = await api.get<{ attempts?: PostingAttemptRecord[] }>(
    `/api/posting/attempts?${params.toString()}`,
  );
  return Array.isArray(res?.attempts) ? res.attempts : [];
}

export function connectLinkedIn(token: string): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/posting/connect/linkedin', { token });
}

export function connectInstagram(token: string): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/posting/connect/instagram', { token });
}

export function refreshInstagram(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/posting/refresh/instagram', {});
}

export function startXOAuth(clientId: string): Promise<{
  authorize_url: string;
  redirect_uri: string;
  expires_at: string;
  callback_port: number;
}> {
  return api.post('/api/posting/connect/x/start', { client_id: clientId });
}

export function cancelXOAuth(): Promise<{ ok: boolean; cancelled: boolean }> {
  return api.post('/api/posting/connect/x/cancel', {});
}

export function disconnectPosting(platform: PostingPlatform): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/posting/disconnect', { platform });
}

export function preparePosting(payload: {
  content_id: string;
  platform: PostingPlatform;
  media_url?: string;
  media_paths?: string[];
  ai_generated?: boolean;
  acknowledge_repost?: boolean;
  acknowledge_unknown?: boolean;
}): Promise<PostingPrepareResult> {
  return api.post<PostingPrepareResult>('/api/posting/prepare', payload);
}

export function confirmPosting(payload: {
  confirmation_id: string;
  payload_hash?: string;
  media_url?: string;
  media_paths?: string[];
  ai_generated?: boolean;
  acknowledge_repost?: boolean;
  acknowledge_unknown?: boolean;
}): Promise<PostingConfirmResult> {
  return api.post<PostingConfirmResult>('/api/posting/confirm', payload);
}
