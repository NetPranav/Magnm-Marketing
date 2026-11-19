import { api } from '../api';
import type { AssetRecord, ContentItem } from '../types';

/** Load registered brand and media assets. */
export function listAssets(): Promise<AssetRecord[]> {
  return api.get<AssetRecord[]>('/api/assets');
}

/** Upload a base64-encoded file to the assets library. */
export function uploadAsset(payload: {
  filename: string;
  content_base64: string;
  category: string;
  description?: string;
}): Promise<{ success: boolean; asset: AssetRecord }> {
  return api.post<{ success: boolean; asset: AssetRecord }>('/api/assets/upload', payload);
}

/** Delete an asset by ID. */
export function deleteAsset(id: string): Promise<{ success: boolean }> {
  return api.post<{ success: boolean }>('/api/assets/delete', { id });
}

/** List generated and draft content items, optionally filtered by status. */
export function listContent(limit = 200, status?: string): Promise<ContentItem[]> {
  const qs =
    status && status !== 'all'
      ? `?limit=${limit}&status=${encodeURIComponent(status)}`
      : `?limit=${limit}`;
  return api.get<ContentItem[]>(`/api/content${qs}`);
}
