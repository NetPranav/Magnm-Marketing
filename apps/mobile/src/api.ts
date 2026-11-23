import modelsConfig from '@magnm/config/models.json';
import { getConfiguredAgent, type ConfiguredAgent } from './agentTools';
import { getPersonalNimKey, getStoredSession, getValidAccessToken } from './auth';
import {
  getCachedProductRules,
  loadCache,
  saveCachedProductRules,
  saveMirrorRecords,
} from './cache';
import { getNativeBridge, type NativeBridge } from './native';
import {
  getPublicSupabaseConfig,
  HttpStatusError,
  netJson,
  netShareLaptopFile,
  NIM_BASE_URL,
  type NetworkCredentials,
} from './net';
import { getLaptopToken } from './pairing';
import { findConfidentialMention, formatProductRulesPrompt, unwrapSharedProductRules } from './productRules';
import { rulesForQueue } from './queueRules';
import { getStateGeneration } from './storage';
import type {
  DraftComment,
  DraftItem,
  DraftVersion,
  InboxAckItem,
  LaptopStatus,
  MetricProposal,
  MirrorRecord,
  PairingConfig,
  PhoneAgentInfo,
  PhoneNotification,
  PitchProposalItem,
  ProductRulesConfig,
  QueuedInboxItem,
  ReminderItem,
  SocialLiveFrameResponse,
  SocialLiveInputPayload,
  SocialPlatform,
  SocialRecipe,
  SocialRunRecord,
  SocialStatusInfo,
  WeeklySummary,
} from './types';

export const AGENT_MESSAGE_TIMEOUT_MS = 180_000;
export const SCREENSHOT_EXTRACT_TIMEOUT_MS = 120_000;
export const PHONE_LITE_TIMEOUT_MS = 120_000;

export class LaptopAuthError extends Error {
  readonly status = 401;

  constructor(message = 'Laptop rejected authorization (401). This phone may be unpaired or the laptop account changed.') {
    super(message);
    this.name = 'LaptopAuthError';
  }
}

export class ConfidentialRefusalError extends Error {
  readonly productName: string;

  constructor(productName: string) {
    super(
      `Cannot send a message naming confidential product "${productName}" to a cloud model in Phone Lite. Open Magnm Studio on your laptop (local AI) instead.`,
    );
    this.name = 'ConfidentialRefusalError';
    this.productName = productName;
  }
}

export class ProductRulesUnavailableError extends Error {
  constructor() {
    super(
      'The bundled product rules could not be read and no shared rules are cached. Phone Lite is paused to protect product rules.',
    );
    this.name = 'ProductRulesUnavailableError';
  }
}

async function laptopCreds(
  pairing: PairingConfig,
  bridge: NativeBridge,
): Promise<NetworkCredentials> {
  const laptopToken = await getLaptopToken(bridge);
  return {
    laptop: { host: pairing.host, port: pairing.port },
    laptopToken,
  };
}

async function laptopCall<T>(
  pairing: PairingConfig,
  method: 'GET' | 'POST',
  relPath: string,
  body?: unknown,
  bridge: NativeBridge = getNativeBridge(),
  timeoutMs?: number,
): Promise<T> {
  const cleanRel = relPath.replace(/^\/+/, '');
  const creds = await laptopCreds(pairing, bridge);
  try {
    return await netJson<T>(
      {
        destination: 'laptop',
        method,
        url: `http://${pairing.host}:${pairing.port}/phone/v1/${cleanRel}`,
        body,
        timeoutMs,
      },
      creds,
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && err.status === 401) {
      throw new LaptopAuthError();
    }
    throw err;
  }
}

export async function fetchLaptopStatus(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<LaptopStatus> {
  return laptopCall<LaptopStatus>(pairing, 'GET', 'status', undefined, bridge, 5000);
}

export async function fetchLaptopDrafts(
  pairing: PairingConfig,
  limit = 30,
  bridge: NativeBridge = getNativeBridge(),
): Promise<DraftItem[]> {
  const res = await laptopCall<{ drafts: DraftItem[] }>(
    pairing,
    'GET',
    `drafts?limit=${limit}`,
    undefined,
    bridge,
  );
  return Array.isArray(res?.drafts) ? res.drafts : [];
}

interface RawReviewDetailPayload {
  item?: DraftItem & { versions?: DraftVersion[] };
  body?: string | null;
  current_version?: number | null;
  versions?: DraftVersion[];
  comments?: DraftComment[];
  warnings?: DraftItem['warnings'];
  confidential?: boolean;
}

function normalizeDraftDetailResponse(raw: DraftItem | RawReviewDetailPayload): DraftItem {
  if (raw && typeof raw === 'object' && 'item' in raw && raw.item && typeof raw.item === 'object') {
    const inner = raw.item;
    const itemVersions = Array.isArray(inner.versions) ? inner.versions : [];
    const summaryVersions = Array.isArray(raw.versions) ? raw.versions : [];
    const versions = itemVersions.length > 0 ? itemVersions : summaryVersions;
    return {
      ...inner,
      body: typeof raw.body === 'string' ? raw.body : inner.body ?? null,
      current_version:
        typeof raw.current_version === 'number'
          ? raw.current_version
          : inner.current_version ?? null,
      versions,
      comments: Array.isArray(raw.comments) ? raw.comments : [],
      warnings: Array.isArray(raw.warnings) ? raw.warnings : [],
      confidential: Boolean(raw.confidential),
    };
  }
  const flat = raw as DraftItem;
  return {
    ...flat,
    versions: Array.isArray(flat.versions) ? flat.versions : [],
    comments: Array.isArray(flat.comments) ? flat.comments : [],
    warnings: Array.isArray(flat.warnings) ? flat.warnings : [],
  };
}

export async function fetchLaptopDraftDetail(
  pairing: PairingConfig,
  contentId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<DraftItem> {
  const raw = await laptopCall<DraftItem | RawReviewDetailPayload>(
    pairing,
    'GET',
    `drafts/${encodeURIComponent(contentId)}`,
    undefined,
    bridge,
  );
  return normalizeDraftDetailResponse(raw);
}

export async function postLaptopDraftComment(
  pairing: PairingConfig,
  contentId: string,
  body: string,
  version?: number | null,
  bridge: NativeBridge = getNativeBridge(),
): Promise<DraftComment> {
  const res = await laptopCall<{ comment: DraftComment }>(
    pairing,
    'POST',
    `drafts/${encodeURIComponent(contentId)}/comments`,
    { body, version: version ?? undefined },
    bridge,
  );
  return {
    ...res.comment,
    replies: Array.isArray(res.comment.replies) ? res.comment.replies : [],
  };
}

interface RawPostedInner {
  item?: DraftItem;
  status?: string | null;
  posted_url?: string | null;
  posted_at?: string | null;
  platform?: string | null;
}

export async function postLaptopDraftPosted(
  pairing: PairingConfig,
  contentId: string,
  url?: string | null,
  bridge: NativeBridge = getNativeBridge(),
): Promise<DraftItem> {
  const res = await laptopCall<{ item: DraftItem | RawPostedInner }>(
    pairing,
    'POST',
    `drafts/${encodeURIComponent(contentId)}/posted`,
    url && url.trim() ? { url: url.trim() } : {},
    bridge,
  );
  const outer = res.item;
  if (outer && typeof outer === 'object' && 'item' in outer && outer.item && typeof outer.item === 'object') {
    const innerItem = outer.item;
    return {
      ...innerItem,
      status: outer.status ?? innerItem.status,
      posted_url: outer.posted_url !== undefined ? outer.posted_url : innerItem.posted_url,
      posted_at: outer.posted_at !== undefined ? outer.posted_at : innerItem.posted_at,
      platform: outer.platform !== undefined ? outer.platform : innerItem.platform,
    };
  }
  return outer as DraftItem;
}

export async function fetchLaptopAgents(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<PhoneAgentInfo[]> {
  const res = await laptopCall<{ agents: PhoneAgentInfo[] }>(pairing, 'GET', 'agents', undefined, bridge);
  return Array.isArray(res?.agents) ? res.agents : [];
}

export async function postLaptopAgentMessage(
  pairing: PairingConfig,
  agentId: string,
  text: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{
  success: boolean;
  confidential?: boolean;
  waiting_user?: boolean;
  session_id?: string;
  agent?: { id: string; name: string };
  response?: string;
  question?: string;
  options?: string[];
  error?: string;
}> {
  return laptopCall(
    pairing,
    'POST',
    `agents/${encodeURIComponent(agentId)}/message`,
    { text },
    bridge,
    AGENT_MESSAGE_TIMEOUT_MS,
  );
}

export async function fetchLaptopSummary(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<WeeklySummary | null> {
  const res = await laptopCall<{ summary: WeeklySummary | null }>(pairing, 'GET', 'summary', undefined, bridge);
  return res?.summary ?? null;
}

export async function fetchLaptopReminders(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<ReminderItem[]> {
  const res = await laptopCall<{ reminders: ReminderItem[] }>(
    pairing,
    'GET',
    'reminders?status=open',
    undefined,
    bridge,
  );
  return Array.isArray(res?.reminders) ? res.reminders : [];
}

export async function postLaptopReminderDone(
  pairing: PairingConfig,
  reminderId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<ReminderItem> {
  const res = await laptopCall<{ reminder: ReminderItem }>(
    pairing,
    'POST',
    `reminders/${encodeURIComponent(reminderId)}/done`,
    {},
    bridge,
  );
  return res.reminder;
}

export async function fetchLaptopNotifications(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ notifications: PhoneNotification[]; unread_count: number }> {
  const res = await laptopCall<{ notifications: PhoneNotification[]; unread_count: number }>(
    pairing,
    'GET',
    'notifications?limit=50',
    undefined,
    bridge,
  );
  return {
    notifications: Array.isArray(res?.notifications) ? res.notifications : [],
    unread_count: Number(res?.unread_count ?? 0),
  };
}

export async function postLaptopNotificationsRead(
  pairing: PairingConfig,
  options: { all?: boolean; ids?: string[] },
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ ok: boolean; unread_count: number }> {
  return laptopCall<{ ok: boolean; unread_count: number }>(
    pairing,
    'POST',
    'notifications/read',
    options,
    bridge,
  );
}

export async function fetchLaptopPendingResults(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<MetricProposal[]> {
  const res = await laptopCall<{ proposals: MetricProposal[] }>(
    pairing,
    'GET',
    'results/pending',
    undefined,
    bridge,
  );
  return Array.isArray(res?.proposals) ? res.proposals : [];
}

export async function extractLaptopResults(
  pairing: PairingConfig,
  payload: { image_base64: string; platform?: string; content_id?: string },
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ proposal: MetricProposal; period?: string | null; notes?: string | null }> {
  return laptopCall(
    pairing,
    'POST',
    'results/extract',
    payload,
    bridge,
    SCREENSHOT_EXTRACT_TIMEOUT_MS,
  );
}

export async function confirmLaptopResults(
  pairing: PairingConfig,
  proposalId: string,
  payload: { metrics: Record<string, number | null>; content_id?: string | null; platform?: string | null },
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ proposal: MetricProposal }> {
  return laptopCall(
    pairing,
    'POST',
    `results/${encodeURIComponent(proposalId)}/confirm`,
    payload,
    bridge,
  );
}

export async function fetchLaptopPitchProposals(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<PitchProposalItem[]> {
  const res = await laptopCall<{ proposals: PitchProposalItem[] }>(
    pairing,
    'GET',
    'pitch',
    undefined,
    bridge,
  );
  return Array.isArray(res?.proposals) ? res.proposals : [];
}

export async function shareLaptopDraftFile(
  pairing: PairingConfig,
  contentId: string,
  filePath: string,
  title: string | undefined,
  bridge: NativeBridge = getNativeBridge(),
): Promise<void> {
  const creds = await laptopCreds(pairing, bridge);
  const url = `http://${pairing.host}:${pairing.port}/phone/v1/files?content_id=${encodeURIComponent(contentId)}&path=${encodeURIComponent(filePath)}`;
  await netShareLaptopFile(url, title, creds, bridge);
}

export async function postLaptopInboxBatch(
  pairing: PairingConfig,
  items: QueuedInboxItem[],
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ items: InboxAckItem[] }> {
  return laptopCall<{ items: InboxAckItem[] }>(
    pairing,
    'POST',
    'inbox',
    {
      items: items.map((item) =>
        item.kind === 'social_request'
          ? {
              client_id: item.client_id,
              kind: 'social_request',
              platform: item.platform,
              recipe: item.recipe,
              argument: item.argument,
              created_at: item.created_at,
            }
          : {
              client_id: item.client_id,
              kind: item.kind,
              title: item.title,
              body: item.body,
              platform: item.platform,
              url: item.url,
              created_at: item.created_at,
            },
      ),
    },
    bridge,
  );
}

export async function fetchLaptopSocialStatus(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<SocialStatusInfo> {
  return laptopCall<SocialStatusInfo>(pairing, 'GET', 'social/status', undefined, bridge, 5000);
}

export async function fetchLaptopSocialRuns(
  pairing: PairingConfig,
  limit = 30,
  bridge: NativeBridge = getNativeBridge(),
): Promise<SocialRunRecord[]> {
  const res = await laptopCall<{ runs: SocialRunRecord[] }>(
    pairing,
    'GET',
    `social/runs?limit=${limit}`,
    undefined,
    bridge,
  );
  return Array.isArray(res?.runs) ? res.runs : [];
}

export async function fetchLaptopSocialRun(
  pairing: PairingConfig,
  runId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<SocialRunRecord> {
  return laptopCall<SocialRunRecord>(
    pairing,
    'GET',
    `social/runs/${encodeURIComponent(runId)}`,
    undefined,
    bridge,
  );
}

export async function createLaptopSocialRun(
  pairing: PairingConfig,
  payload: { platform: SocialPlatform; recipe: SocialRecipe; argument: string },
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return laptopCall<{ success: boolean; run: SocialRunRecord }>(
    pairing,
    'POST',
    'social/runs',
    payload,
    bridge,
  );
}

export async function startLaptopSocialRun(
  pairing: PairingConfig,
  runId: string,
  approvalHash: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return laptopCall<{ success: boolean; run: SocialRunRecord }>(
    pairing,
    'POST',
    `social/runs/${encodeURIComponent(runId)}/start`,
    { approval_hash: approvalHash },
    bridge,
  );
}

export async function cancelLaptopSocialRun(
  pairing: PairingConfig,
  runId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return laptopCall<{ success: boolean; run: SocialRunRecord }>(
    pairing,
    'POST',
    `social/runs/${encodeURIComponent(runId)}/cancel`,
    {},
    bridge,
  );
}

export async function summariseLaptopSocialRun(
  pairing: PairingConfig,
  runId: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return laptopCall<{ success: boolean; run: SocialRunRecord }>(
    pairing,
    'POST',
    `social/runs/${encodeURIComponent(runId)}/summarise`,
    {},
    bridge,
    AGENT_MESSAGE_TIMEOUT_MS,
  );
}

export async function postLaptopSocialChallenge(
  pairing: PairingConfig,
  action: 'later' | 'skip' | 'continue',
  runId?: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ ok: boolean; cleared?: boolean; challenge_kind?: string; run?: SocialRunRecord }> {
  const cleanCreds = await laptopCreds(pairing, bridge);
  try {
    return await netJson<{
      ok: boolean;
      cleared?: boolean;
      challenge_kind?: string;
      run?: SocialRunRecord;
    }>(
      {
        destination: 'laptop',
        method: 'POST',
        url: `http://${pairing.host}:${pairing.port}/phone/v1/social/challenge/${action}`,
        body: runId ? { run_id: runId } : {},
      },
      cleanCreds,
      bridge,
    );
  } catch (err) {
    if (err instanceof HttpStatusError && err.status === 401) {
      throw new LaptopAuthError();
    }
    if (action === 'continue' && err instanceof HttpStatusError && err.status === 409) {
      try {
        const parsed = JSON.parse(err.body) as {
          ok?: boolean;
          cleared?: boolean;
          challenge_kind?: string;
          run?: SocialRunRecord;
        };
        if (parsed && parsed.ok === false) {
          return {
            ok: false,
            cleared: false,
            challenge_kind: parsed.challenge_kind,
            run: parsed.run,
          };
        }
      } catch {
        // Fall through to throw original error
      }
    }
    throw err;
  }
}

export async function fetchLaptopSocialLiveFrame(
  pairing: PairingConfig,
  bridge: NativeBridge = getNativeBridge(),
): Promise<SocialLiveFrameResponse> {
  return laptopCall<SocialLiveFrameResponse>(
    pairing,
    'GET',
    'social/live/frame',
    undefined,
    bridge,
    5000,
  );
}

export async function postLaptopSocialLiveInput(
  pairing: PairingConfig,
  payload: SocialLiveInputPayload,
  bridge: NativeBridge = getNativeBridge(),
): Promise<{ ok: boolean }> {
  return laptopCall<{ ok: boolean }>(pairing, 'POST', 'social/live/input', payload, bridge, 5000);
}

// What the phone needs from the mirror, one small query per kind so none crowds the others out of the 200 cap:
// drafts, their versions and comments, and open reminders (which the laptop mirrors as tasks).
const MIRROR_SELECT = 'id,owner_id,kind,local_id,parent_local_id,title,data,local_updated_at,synced_at';
const MIRROR_QUERIES: ReadonlyArray<{ kind: string; filter: string; limit: number }> = [
  { kind: 'content_item', filter: 'kind=eq.content_item', limit: 60 },
  { kind: 'content_version', filter: 'kind=eq.content_version', limit: 60 },
  { kind: 'review_comment', filter: 'kind=eq.review_comment', limit: 40 },
  { kind: 'task', filter: 'kind=eq.task&data-%3E%3Ekind=eq.reminder&data-%3E%3Edone_at=is.null', limit: 40 },
];

/** Fetch ONLY the signed-in user's own mirror_records (always filtered owner_id=eq.<uid>). */
export async function fetchOwnMirrorRecords(
  ownerUid: string,
  bridge: NativeBridge = getNativeBridge(),
): Promise<MirrorRecord[]> {
  const uid = ownerUid.trim();
  if (!uid) {
    throw new Error('Cannot fetch mirror_records without a signed-in user ID.');
  }
  const gen = getStateGeneration();
  const token = await getValidAccessToken(bridge);
  if (!token) {
    throw new Error('Sign in required to sync mirror_records.');
  }
  const cfg = getPublicSupabaseConfig();
  const creds = { supabaseUrl: cfg.url, supabaseAnonKey: cfg.publishableKey, supabaseAccessToken: token };
  const settled = await Promise.allSettled(
    MIRROR_QUERIES.map((q) =>
      netJson<Array<Omit<MirrorRecord, 'cached_at'>>>(
        {
          destination: 'supabase',
          method: 'GET',
          url: `${cfg.url}/rest/v1/mirror_records?owner_id=eq.${encodeURIComponent(uid)}&deleted_at=is.null&${q.filter}&select=${MIRROR_SELECT}&order=local_updated_at.desc&limit=${q.limit}`,
        },
        creds,
        bridge,
      ),
    ),
  );
  if (gen !== getStateGeneration()) {
    return [];
  }
  const failed = settled.find((r): r is PromiseRejectedResult => r.status === 'rejected');
  if (failed && settled.every((r) => r.status === 'rejected')) {
    throw failed.reason;
  }
  const byId = new Map<string, Omit<MirrorRecord, 'cached_at'>>();
  for (const result of settled) {
    if (result.status !== 'fulfilled' || !Array.isArray(result.value)) continue;
    for (const rec of result.value) {
      if (rec && typeof rec.id === 'string' && !byId.has(rec.id)) byId.set(rec.id, rec);
    }
  }
  const clean = Array.from(byId.values());
  // A kind whose request failed keeps what was saved (this account's only, with its original age). A request that
  // answered, even with nothing, replaces that kind.
  const failedKinds = new Set(MIRROR_QUERIES.filter((_, i) => settled[i].status === 'rejected').map((q) => q.kind));
  const kept =
    failedKinds.size > 0
      ? loadCache(Date.now(), { ownerUid: uid }).mirrorRecords.filter(
          (r) => r.owner_id === uid && failedKinds.has(r.kind) && !byId.has(r.id),
        )
      : [];
  saveMirrorRecords(uid, clean, Date.now(), kept);
  return clean.filter((r) => r.owner_id === uid).map((r) => ({ ...r, cached_at: Date.now() }));
}

/** Fetch product_rules from Supabase shared_knowledge and cache them locally. */
export async function fetchSharedProductRules(
  ownerUid: string | null,
  bridge: NativeBridge = getNativeBridge(),
): Promise<ProductRulesConfig> {
  const cleanUid = (ownerUid ?? '').trim();
  if (!cleanUid) {
    throw new Error('Sign in required to fetch product rules.');
  }
  const gen = getStateGeneration();
  const token = await getValidAccessToken(bridge);
  if (!token) {
    throw new Error('Sign in required to fetch product rules.');
  }
  const cfg = getPublicSupabaseConfig();
  const rows = await netJson<Array<{ key: string; data: unknown; updated_at?: string | null }>>(
    {
      destination: 'supabase',
      method: 'GET',
      url: `${cfg.url}/rest/v1/shared_knowledge?key=eq.product_rules&select=key,data,updated_at`,
    },
    {
      supabaseUrl: cfg.url,
      supabaseAnonKey: cfg.publishableKey,
      supabaseAccessToken: token,
    },
    bridge,
  );
  if (!Array.isArray(rows) || rows.length === 0 || !rows[0]?.data) {
    throw new Error('Product rules were not found in shared_knowledge.');
  }
  const rules = unwrapSharedProductRules(rows[0].data);
  if (gen !== getStateGeneration()) {
    throw new Error('Account state changed while fetching product rules.');
  }
  return saveCachedProductRules(cleanUid, rules, rows[0].updated_at ?? null);
}

/**
 * The rules Phone Lite uses, the same as the phone queue: the bundled baseline plus the shared or cached rules, which
 * can only add confidential products. Nothing published by the team is never a reason to pause. It only fails
 * closed if the bundled file itself is unusable and there are no shared or cached rules either.
 */
export async function resolveProductRulesForPhoneLite(
  bridge: NativeBridge = getNativeBridge(),
): Promise<ProductRulesConfig> {
  let shared: ProductRulesConfig | null = null;
  // Fresh rules first: a product may have become confidential since they were cached.
  const session = await getStoredSession(bridge);
  if (session?.user_id) {
    try {
      shared = await fetchSharedProductRules(session.user_id, bridge);
    } catch {
      // Offline or nothing published: fall back to the saved copy, then to the bundled baseline.
      shared = getCachedProductRules(session.user_id);
    }
  } else {
    shared = getCachedProductRules();
  }
  const rules = rulesForQueue(shared);
  if (!rules) throw new ProductRulesUnavailableError();
  return rules;
}

function selectPhoneLiteModel(agentId: string): string {
  const roles = modelsConfig.roles as Record<string, string>;
  const tiers = modelsConfig.tiers as Record<string, { primary: string }>;
  const tierName = roles[agentId] || 'writing';
  return tiers[tierName]?.primary || 'minimaxai/minimax-m3.1-flash';
}

function buildPhoneLiteSystemPrompt(agent: ConfiguredAgent, rules: ProductRulesConfig): string {
  return [
    `You are Magnm's ${agent.name} running in Phone Lite mode. Your role: ${agent.role}`,
    'Answer with what you can do in text: writing, rewriting, planning, reviewing text and explaining.',
    'You have NO tools, NO web browsing, NO repo access, and NO media rendering on the phone.',
    'If the request needs a tool, say plainly that it needs the laptop.',
    'Never invent unverified metrics or product features.',
    '',
    formatProductRulesPrompt(rules),
  ].join('\n');
}

export interface PhoneLiteResult {
  reply: string;
  provider: 'personal-nim' | 'ai-proxy';
  model: string;
}

/**
 * Send a Phone Lite request when the laptop is away. Any of the configured agents may answer, with text only and
 * no tools; product_rules must be present and a confidential product is refused.
 */
export async function sendPhoneLiteMessage(params: {
  agentId: string;
  text: string;
  history?: Array<{ role: 'user' | 'assistant'; content: string }>;
  bridge?: NativeBridge;
}): Promise<PhoneLiteResult> {
  const bridge = params.bridge ?? getNativeBridge();
  const agent = getConfiguredAgent(typeof params.agentId === 'string' ? params.agentId : '');
  if (!agent) {
    throw new Error(`Phone Lite can only talk to a configured agent (got "${String(params.agentId).slice(0, 40)}").`);
  }
  const cleanAgent = agent.id;

  const text = params.text.trim();
  if (!text) {
    throw new Error('Message text is required.');
  }

  const rules = await resolveProductRulesForPhoneLite(bridge);

  const mentionedInPrompt = findConfidentialMention(text, rules);
  if (mentionedInPrompt) {
    throw new ConfidentialRefusalError(mentionedInPrompt.name || mentionedInPrompt.id);
  }

  const safeHistory = (params.history ?? []).filter(
    (m): m is { role: 'user' | 'assistant'; content: string } =>
      Boolean(m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string'),
  );

  for (const turn of safeHistory) {
    const mentionedInHistory = findConfidentialMention(turn.content, rules);
    if (mentionedInHistory) {
      throw new ConfidentialRefusalError(mentionedInHistory.name || mentionedInHistory.id);
    }
  }

  const personalNimKey = await getPersonalNimKey(bridge);
  const model = selectPhoneLiteModel(cleanAgent);
  const systemPrompt = buildPhoneLiteSystemPrompt(agent, rules);

  const messages = [
    { role: 'system', content: systemPrompt },
    ...safeHistory.slice(-6).map((m) => ({ role: m.role, content: m.content })),
    { role: 'user', content: text },
  ];

  // Explicitly no tools in Phone Lite.
  const payload = {
    model,
    messages,
    max_tokens: 1200,
    temperature: 0.6,
    stream: false,
  };

  interface ChatCompletionResponse {
    choices?: Array<{ message?: { content?: string } }>;
  }

  if (personalNimKey) {
    const data = await netJson<ChatCompletionResponse>(
      {
        destination: 'nim',
        method: 'POST',
        url: `${NIM_BASE_URL}/chat/completions`,
        body: payload,
        timeoutMs: PHONE_LITE_TIMEOUT_MS,
      },
      { nimPersonalKey: personalNimKey },
      bridge,
    );
    const reply = data?.choices?.[0]?.message?.content?.trim() || '';
    if (!reply) throw new Error('NIM returned an empty response.');
    return { reply, provider: 'personal-nim', model };
  }

  const accessToken = await getValidAccessToken(bridge);
  if (!accessToken) {
    throw new Error(
      'Phone Lite needs either a personal NVIDIA NIM key or a Google sign-in (shared team keys via ai-proxy).',
    );
  }
  const cfg = getPublicSupabaseConfig();
  const data = await netJson<ChatCompletionResponse>(
    {
      destination: 'supabase',
      method: 'POST',
      url: `${cfg.url}/functions/v1/ai-proxy`,
      body: payload,
      timeoutMs: PHONE_LITE_TIMEOUT_MS,
    },
    {
      supabaseUrl: cfg.url,
      supabaseAnonKey: cfg.publishableKey,
      supabaseAccessToken: accessToken,
    },
    bridge,
  );
  const reply = data?.choices?.[0]?.message?.content?.trim() || '';
  if (!reply) throw new Error('ai-proxy returned an empty response.');
  return { reply, provider: 'ai-proxy', model };
}
