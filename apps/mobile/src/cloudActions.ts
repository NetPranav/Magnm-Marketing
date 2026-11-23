// Sends queued phone actions to the cloud and reads back what the laptop did with them.
// Only the signed-in account's own actions travel, only through /rest/v1/phone_actions (already inside the
// Supabase allowlist), and the cloud is never allowed to rewrite what the phone queued: it can only say
// waiting, applied or rejected, with a short reason.

import { cleanReply, loadActions, MAX_RESULT_CHARS, patchAction } from './actions';
import { getStoredSession, getValidAccessToken } from './auth';
import { getNativeBridge, type NativeBridge } from './native';
import { getPublicSupabaseConfig, HttpStatusError, netJson, type NetworkCredentials } from './net';
import { getStateGeneration } from './storage';

// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x1f\x7f]/g;
const REFUSED_STATUSES = new Set([400, 403, 409, 422]);

function shortReason(raw: unknown, fallback: string): string {
  if (typeof raw !== 'string') return fallback;
  const text = raw.replace(CONTROL_RE, ' ').replace(/\s+/g, ' ').trim().slice(0, MAX_RESULT_CHARS);
  return text || fallback;
}

async function cloudSession(
  ownerUid: string,
  bridge: NativeBridge,
): Promise<{ creds: NetworkCredentials; url: string } | null> {
  const uid = ownerUid.trim();
  if (!uid) return null;
  const session = await getStoredSession(bridge);
  if (!session || session.user_id !== uid) return null;
  const token = await getValidAccessToken(bridge);
  if (!token) return null;
  // The refresh may have changed the account (or signed out) while it ran.
  const again = await getStoredSession(bridge);
  if (!again || again.user_id !== uid) return null;
  const cfg = getPublicSupabaseConfig();
  return {
    url: cfg.url,
    creds: { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token },
  };
}

/** Send every waiting, unsent action of this account. Offline or server trouble leaves them for the next try. */
export async function sendWaitingActions(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ sent: number }> {
  const uid = (ownerUid ?? '').trim();
  const out = { sent: 0 };
  if (!uid) return out;
  const gen = getStateGeneration();
  try {
    if (loadActions(uid).every((a) => a.sent || a.state !== 'waiting')) return out;
    const cloud = await cloudSession(uid, bridge);
    if (!cloud || gen !== getStateGeneration()) return out;

    for (const action of loadActions(uid)) {
      if (action.sent || action.state !== 'waiting') continue;
      if (gen !== getStateGeneration()) return out;
      try {
        await netJson(
          {
            destination: 'supabase',
            method: 'POST',
            url: `${cloud.url}/rest/v1/phone_actions?on_conflict=id`,
            headers: { Prefer: 'resolution=ignore-duplicates,return=minimal' },
            body: { id: action.id, kind: action.kind, target_id: action.target_id, payload: action.payload },
          },
          cloud.creds,
          bridge,
        );
        if (gen !== getStateGeneration()) return out;
        patchAction(uid, action.id, { sent: true });
        out.sent += 1;
      } catch (err) {
        if (gen !== getStateGeneration()) return out;
        if (err instanceof HttpStatusError && REFUSED_STATUSES.has(err.status)) {
          patchAction(uid, action.id, {
            state: 'rejected',
            result: shortReason(err.message, 'The cloud refused this action.'),
          });
          continue;
        }
        // Offline, signed out (401), throttled or a server problem: keep it, try again later.
        break;
      }
    }
  } catch {
    // Nothing here may break the app: the actions stay on the phone.
  }
  return out;
}

/** Ask the cloud what happened to this account's sent actions and show it. Never changes what was queued. */
export async function refreshActionStatuses(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const uid = (ownerUid ?? '').trim();
  if (!uid) return;
  const gen = getStateGeneration();
  try {
    const open = new Set(loadActions(uid).filter((a) => a.sent && a.state === 'waiting').map((a) => a.id));
    if (open.size > 0) {
      const cloud = await cloudSession(uid, bridge);
      if (!cloud || gen !== getStateGeneration()) return;
      const rows = await netJson<unknown>(
        {
          destination: 'supabase',
          method: 'GET',
          url: `${cloud.url}/rest/v1/phone_actions?owner_id=eq.${encodeURIComponent(uid)}&select=id,owner_id,status,result,expires_at&order=created_at.desc&limit=200`,
        },
        cloud.creds,
        bridge,
      );
      if (gen !== getStateGeneration() || !Array.isArray(rows)) return;
      const now = Date.now();
      for (const raw of rows) {
        if (!raw || typeof raw !== 'object') continue;
        const row = raw as { id?: unknown; owner_id?: unknown; status?: unknown; result?: unknown; expires_at?: unknown };
        if (row.owner_id !== uid || typeof row.id !== 'string' || !open.has(row.id)) continue;
        if (row.status === 'applied') {
          patchAction(uid, row.id, { state: 'applied', result: shortReason(row.result, 'Applied by your laptop.') });
        } else if (row.status === 'rejected') {
          patchAction(uid, row.id, { state: 'rejected', result: shortReason(row.result, 'Not applied.') });
        } else if (row.status === 'queued' || row.status === 'applying') {
          const expires = typeof row.expires_at === 'string' ? Date.parse(row.expires_at) : NaN;
          if (Number.isFinite(expires) && expires <= now) {
            patchAction(uid, row.id, { state: 'rejected', result: 'Expired before the laptop applied it.' });
          }
        }
      }
    }
    await fetchAgentReplies(uid, bridge);
  } catch {
    // Offline: the next refresh tries again.
  }
}

const SESSION_TOKEN_RE = /session:([A-Za-z0-9_-]{1,100})\b/;
const LAPTOP_ONLY_RESULT = 'Replied on the laptop only.';
const MAX_REPLY_FETCHES = 5;

/**
 * Bring home the replies of agent requests the laptop answered. The laptop's result names the chat session; the
 * reply itself is read from this account's own cloud mirror (one record per request) and kept with the action.
 * A confidential session is never read: the phone only learns that the reply stays on the laptop. A record that is
 * not this account's, not a chat session, not that session or not that agent's is ignored.
 */
export async function fetchAgentReplies(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const uid = (ownerUid ?? '').trim();
  if (!uid) return;
  const gen = getStateGeneration();
  const wanted: Array<{ id: string; target: string; sessionId: string }> = [];
  for (const a of loadActions(uid)) {
    if (a.kind !== 'agent_task' || a.state !== 'applied' || a.reply || a.laptop_only) continue;
    const result = a.result ?? '';
    if (result === LAPTOP_ONLY_RESULT) {
      patchAction(uid, a.id, { laptop_only: true });
      continue;
    }
    const sessionId = SESSION_TOKEN_RE.exec(result)?.[1];
    if (sessionId) wanted.push({ id: a.id, target: a.target_id, sessionId });
  }
  if (wanted.length === 0) return;
  const cloud = await cloudSession(uid, bridge);
  if (!cloud || gen !== getStateGeneration()) return;
  for (const want of wanted.slice(0, MAX_REPLY_FETCHES)) {
    if (gen !== getStateGeneration()) return;
    let rows: unknown;
    try {
      rows = await netJson<unknown>(
        {
          destination: 'supabase',
          method: 'GET',
          url:
            `${cloud.url}/rest/v1/mirror_records?owner_id=eq.${encodeURIComponent(uid)}&kind=eq.chat_session` +
            `&local_id=eq.${encodeURIComponent(want.sessionId)}&deleted_at=is.null` +
            `&select=id,owner_id,kind,local_id,data&limit=1`,
        },
        cloud.creds,
        bridge,
      );
    } catch {
      continue;
    }
    if (gen !== getStateGeneration() || !Array.isArray(rows)) continue;
    const rec = rows.find(
      (r): r is { owner_id: string; kind: string; local_id: string; data: Record<string, unknown> } =>
        Boolean(r) && typeof r === 'object' &&
        (r as { owner_id?: unknown }).owner_id === uid &&
        (r as { kind?: unknown }).kind === 'chat_session' &&
        (r as { local_id?: unknown }).local_id === want.sessionId &&
        Boolean((r as { data?: unknown }).data) && typeof (r as { data?: unknown }).data === 'object',
    );
    if (!rec) continue;
    if (rec.data.confidential === true) {
      patchAction(uid, want.id, { laptop_only: true, reply: null });
      continue;
    }
    if (rec.data.agent_id !== want.target || !Array.isArray(rec.data.messages)) continue;
    let reply: string | null = null;
    for (const m of rec.data.messages as unknown[]) {
      if (m && typeof m === 'object' && (m as { role?: unknown }).role === 'assistant' && typeof (m as { text?: unknown }).text === 'string') {
        reply = cleanReply((m as { text: string }).text) ?? reply;
      }
    }
    if (reply) patchAction(uid, want.id, { reply });
  }
}

/** Send what is waiting, then read back the laptop's answers. */
export async function syncPhoneActions(ownerUid: string, bridge: NativeBridge = getNativeBridge()): Promise<void> {
  await sendWaitingActions(ownerUid, bridge);
  await refreshActionStatuses(ownerUid, bridge);
}
