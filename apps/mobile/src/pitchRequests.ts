// Quote requests clients sent through a published Pitch link. They are already in the cloud (quote_submissions),
// so the phone reads this account's own straight from there; the laptop marks them seen when it is back. Rows of
// another owner are never used, and nothing here writes to the cloud.

import { getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, netJson } from './net';
import { cleanQuoteRequests } from './pitchPhone';
import { getStateGeneration } from './storage';
import type { QuoteRequestItem } from './types';

const MAX_REQUESTS = 50;

/** This account's newest quote requests. An older cloud without the table gives none. */
export async function fetchQuoteRequests(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<QuoteRequestItem[]> {
  const uid = (ownerUid ?? '').trim();
  if (!uid) return [];
  const gen = getStateGeneration();
  const session = await getStoredSession(bridge);
  if (!session || session.user_id !== uid) return [];
  const token = await getValidAccessToken(bridge);
  if (!token) return [];
  const cfg = getPublicSupabaseConfig();
  let rows: unknown;
  try {
    rows = await netJson<unknown>(
      {
        destination: 'supabase',
        method: 'GET',
        url:
          `${cfg.url}/rest/v1/quote_submissions?owner_id=eq.${encodeURIComponent(uid)}` +
          `&select=id,share_id,owner_id,choices,contact,created_at,seen_at&order=created_at.desc&limit=${MAX_REQUESTS}`,
      },
      { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token },
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && err.status === 404) return [];
    throw err;
  }
  if (gen !== getStateGeneration()) return [];
  return cleanQuoteRequests(rows, uid);
}
