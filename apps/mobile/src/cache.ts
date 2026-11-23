import { validateProductRules } from './productRules';
import {
  bumpStateGeneration,
  readNonSecretJson,
  removeNonSecretKey,
  writeNonSecretJson,
} from './storage';
import type {
  DraftItem,
  LaptopStatus,
  MetricProposal,
  MirrorRecord,
  PhoneNotification,
  PitchProposalItem,
  ProductRulesConfig,
  ReminderItem,
  WeeklySummary,
} from './types';

export const CACHE_STORAGE_KEY = 'magnm.mobile.contextCache.v1';
export const MAX_MIRROR_RECORDS = 400;
export const CACHE_TTL_MS = 14 * 24 * 60 * 60 * 1000; // 14 days

export interface LaptopCachedSnapshot {
  deviceId: string;
  status: LaptopStatus | null;
  drafts: DraftItem[];
  draftDetails: Record<string, DraftItem>;
  reminders: ReminderItem[];
  notifications: PhoneNotification[];
  unreadCount: number;
  summary: WeeklySummary | null;
  pendingResults: MetricProposal[];
  pitchProposals?: PitchProposalItem[];
  pitchPricesAsOf?: number | null;
  cachedAt: number;
}

export interface CachedProductRulesEntry {
  ownerUid: string;
  rules: ProductRulesConfig;
  updatedAt: string | null;
  cachedAt: number;
}

export interface ContextCacheState {
  ownerUid: string | null;
  mirrorRecords: MirrorRecord[];
  laptop: LaptopCachedSnapshot | null;
  productRules: CachedProductRulesEntry | null;
}

function emptyCache(ownerUid: string | null = null): ContextCacheState {
  return {
    ownerUid,
    mirrorRecords: [],
    laptop: null,
    productRules: null,
  };
}

/** Ensure confidential drafts always stay minimal metadata stubs in cache. */
export function sanitizeDraftForCache(item: DraftItem): DraftItem {
  if (!item.confidential) {
    return item;
  }
  return {
    id: item.id,
    type: item.type ?? null,
    platform: item.platform ?? null,
    status: item.status ?? null,
    title: 'Confidential item',
    confidential: true,
    current_version: item.current_version ?? null,
    created_at: item.created_at ?? null,
    updated_at: item.updated_at ?? null,
    versions: [],
    comments: [],
    warnings: [],
  };
}

export function sanitizeReminderForCache(rem: ReminderItem): ReminderItem {
  if (!rem.confidential) return rem;
  return {
    id: rem.id,
    kind: rem.kind ?? null,
    title: 'Confidential reminder',
    note: '',
    confidential: true,
    due_at: rem.due_at ?? null,
    done_at: rem.done_at ?? null,
    snoozed_until: rem.snoozed_until ?? null,
    created_at: rem.created_at ?? null,
  };
}

export function sanitizeNotificationForCache(notif: PhoneNotification): PhoneNotification {
  if (!notif.confidential) return notif;
  return {
    id: notif.id,
    kind: notif.kind ?? null,
    title: 'Confidential notification',
    body: '',
    confidential: true,
    created_at: notif.created_at ?? null,
    read_at: notif.read_at ?? null,
  };
}

export function sanitizeSummaryForCache(summary: WeeklySummary | null): WeeklySummary | null {
  if (!summary) return null;
  if (!summary.confidential) return summary;
  return {
    id: summary.id,
    week_start: summary.week_start ?? null,
    status: summary.status ?? null,
    confidential: true,
    created_at: summary.created_at ?? null,
  };
}

export function sanitizeProposalForCache(prop: MetricProposal): MetricProposal {
  if (!prop.confidential) return prop;
  return {
    id: prop.id,
    status: prop.status ?? null,
    confidential: true,
    created_at: prop.created_at ?? null,
    decided_at: prop.decided_at ?? null,
  };
}

export function sanitizePitchProposalForCache(item: PitchProposalItem): PitchProposalItem {
  if (!item.confidential) return item;
  return {
    id: item.id,
    confidential: true,
  };
}

/** Ensure confidential mirror_records keep only stub metadata and never unstub. */
export function sanitizeMirrorRecordForCache(rec: MirrorRecord): MirrorRecord {
  const dataObj = rec.data && typeof rec.data === 'object' ? rec.data : {};
  const isConf = Boolean(dataObj.confidential);
  if (!isConf) {
    return rec;
  }
  const stubTitle = rec.kind === 'chat_session' ? 'Confidential chat' : 'Confidential item';
  return {
    id: rec.id,
    owner_id: rec.owner_id,
    kind: rec.kind,
    local_id: rec.local_id,
    parent_local_id: rec.parent_local_id ?? null,
    title: stubTitle,
    data: {
      confidential: true,
      agent_id: typeof dataObj.agent_id === 'string' ? dataObj.agent_id : undefined,
      type: typeof dataObj.type === 'string' ? dataObj.type : undefined,
      platform: typeof dataObj.platform === 'string' ? dataObj.platform : undefined,
      status: typeof dataObj.status === 'string' ? dataObj.status : undefined,
      updated_at: typeof dataObj.updated_at === 'string' ? dataObj.updated_at : undefined,
      // A confidential reminder stays a stub, but the phone still needs to know it is an open reminder.
      kind: dataObj.kind === 'reminder' ? 'reminder' : undefined,
      due_at: dataObj.kind === 'reminder' && typeof dataObj.due_at === 'string' ? dataObj.due_at : undefined,
      done_at: dataObj.kind === 'reminder' && typeof dataObj.done_at === 'string' ? dataObj.done_at : undefined,
    },
    local_updated_at: rec.local_updated_at,
    synced_at: rec.synced_at,
    cached_at: rec.cached_at,
  };
}

function cleanOwnerId(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed : null;
}

function pruneCache(raw: ContextCacheState, nowMs: number): ContextCacheState {
  const cutoff = nowMs - CACHE_TTL_MS;
  const ownerUid = cleanOwnerId(raw.ownerUid);

  // Account-scoped data of unknown owner is never adopted: discard it.
  const freshMirror = ownerUid
    ? (raw.mirrorRecords ?? [])
        .filter((rec) => rec && typeof rec.cached_at === 'number' && rec.cached_at >= cutoff)
        .filter((rec) => rec.owner_id === ownerUid)
        .map(sanitizeMirrorRecordForCache)
        .sort((a, b) => String(b.local_updated_at).localeCompare(String(a.local_updated_at)))
        .slice(0, MAX_MIRROR_RECORDS)
    : [];

  const rawRules = raw.productRules;
  const rulesOwner = cleanOwnerId(rawRules?.ownerUid);
  const productRules =
    ownerUid &&
    rawRules &&
    rulesOwner === ownerUid &&
    typeof rawRules.cachedAt === 'number' &&
    rawRules.cachedAt >= cutoff
      ? {
          ownerUid,
          rules: rawRules.rules,
          updatedAt: rawRules.updatedAt ?? null,
          cachedAt: rawRules.cachedAt,
        }
      : null;

  // Laptop snapshot is bound to pairing.deviceId; unknown owner is discarded.
  const laptopDeviceId = cleanOwnerId(raw.laptop?.deviceId);
  const laptop =
    laptopDeviceId &&
    raw.laptop &&
    typeof raw.laptop.cachedAt === 'number' &&
    raw.laptop.cachedAt >= cutoff
      ? {
          ...raw.laptop,
          deviceId: laptopDeviceId,
          drafts: (raw.laptop.drafts ?? []).slice(0, MAX_MIRROR_RECORDS).map(sanitizeDraftForCache),
          draftDetails: Object.fromEntries(
            Object.entries(raw.laptop.draftDetails ?? {}).map(([id, item]) => [
              id,
              sanitizeDraftForCache(item),
            ]),
          ),
          reminders: (raw.laptop.reminders ?? []).map(sanitizeReminderForCache),
          notifications: (raw.laptop.notifications ?? []).map(sanitizeNotificationForCache),
          summary: sanitizeSummaryForCache(raw.laptop.summary),
          pendingResults: (raw.laptop.pendingResults ?? []).map(sanitizeProposalForCache),
          pitchProposals: (raw.laptop.pitchProposals ?? []).map(sanitizePitchProposalForCache),
          pitchPricesAsOf:
            typeof raw.laptop.pitchPricesAsOf === 'number' && Number.isFinite(raw.laptop.pitchPricesAsOf)
              ? raw.laptop.pitchPricesAsOf
              : null,
        }
      : null;

  return {
    ownerUid,
    mirrorRecords: freshMirror,
    laptop,
    productRules,
  };
}

/** Load context cache, dropping expired or ownerless entries and enforcing optional owner/device match. */
export function loadCache(
  nowMs: number = Date.now(),
  expected?: { ownerUid?: string | null; laptopDeviceId?: string | null },
): ContextCacheState {
  const loaded = readNonSecretJson<ContextCacheState>(CACHE_STORAGE_KEY);
  if (!loaded || typeof loaded !== 'object') {
    return emptyCache();
  }
  const pruned = pruneCache(loaded, nowMs);
  const changed =
    pruned.mirrorRecords.length !== (loaded.mirrorRecords ?? []).length ||
    Boolean(loaded.laptop && !pruned.laptop) ||
    Boolean(loaded.productRules && !pruned.productRules) ||
    (loaded.ownerUid ?? null) !== pruned.ownerUid;
  if (changed) {
    try {
      writeNonSecretJson(CACHE_STORAGE_KEY, pruned);
    } catch {
      removeNonSecretKey(CACHE_STORAGE_KEY);
    }
  }

  let result = pruned;
  if (expected?.ownerUid !== undefined) {
    const expUid = cleanOwnerId(expected.ownerUid);
    if (!expUid || result.ownerUid !== expUid) {
      result = {
        ...result,
        ownerUid: expUid,
        mirrorRecords: [],
        productRules: null,
      };
    }
  }
  if (expected?.laptopDeviceId !== undefined) {
    const expDev = cleanOwnerId(expected.laptopDeviceId);
    if (!expDev || result.laptop?.deviceId !== expDev) {
      result = {
        ...result,
        laptop: null,
      };
    }
  }
  return result;
}

function saveCacheState(state: ContextCacheState, nowMs: number = Date.now()): ContextCacheState {
  const pruned = pruneCache(state, nowMs);
  writeNonSecretJson(CACHE_STORAGE_KEY, pruned);
  return pruned;
}

/** Clear all cached mirror records, laptop snapshots, and cached rules. */
export function wipeCache(): void {
  removeNonSecretKey(CACHE_STORAGE_KEY);
  bumpStateGeneration();
}

/** Clear only the laptop snapshot from cache (used on unpair while preserving signed-in account data). */
export function wipeLaptopSnapshot(nowMs: number = Date.now()): void {
  const current = loadCache(nowMs);
  bumpStateGeneration();
  if (!current.laptop) return;
  const next = { ...current, laptop: null };
  if (!next.ownerUid && next.mirrorRecords.length === 0 && !next.productRules) {
    removeNonSecretKey(CACHE_STORAGE_KEY);
    return;
  }
  try {
    writeNonSecretJson(CACHE_STORAGE_KEY, next);
  } catch {
    removeNonSecretKey(CACHE_STORAGE_KEY);
  }
}

/** Ensure account-scoped cache belongs strictly to uid (never adopting unknown-owner data). */
export function ensureCacheOwner(uid: string | null, nowMs: number = Date.now()): ContextCacheState {
  const cleanUid = cleanOwnerId(uid);
  const current = loadCache(nowMs);
  if (!cleanUid) {
    if (current.ownerUid || current.mirrorRecords.length > 0 || current.productRules) {
      const next = { ...current, ownerUid: null, mirrorRecords: [], productRules: null };
      if (!next.laptop) {
        removeNonSecretKey(CACHE_STORAGE_KEY);
        return next;
      }
      return saveCacheState(next, nowMs);
    }
    return { ...current, ownerUid: null, mirrorRecords: [], productRules: null };
  }
  if (current.ownerUid && current.ownerUid !== cleanUid) {
    wipeCache();
    return saveCacheState(emptyCache(cleanUid), nowMs);
  }
  if (!current.ownerUid) {
    // Never adopt prior unknown-owner account data; start clean for cleanUid while preserving device-bound laptop snapshot.
    return saveCacheState(
      {
        ownerUid: cleanUid,
        mirrorRecords: [],
        laptop: current.laptop,
        productRules: null,
      },
      nowMs,
    );
  }
  return current;
}

/** Save fetched mirror_records for ownerUid, enforcing owner filter, 200 cap, 14-day TTL, and stubs. */
export function saveMirrorRecords(
  ownerUid: string,
  records: Array<Omit<MirrorRecord, 'cached_at'>>,
  nowMs: number = Date.now(),
  kept: MirrorRecord[] = [],
): ContextCacheState {
  const cleanUid = ownerUid.trim();
  if (!cleanUid) {
    throw new Error('ownerUid is required to cache mirror_records.');
  }
  const base = ensureCacheOwner(cleanUid, nowMs);
  const normalized: MirrorRecord[] = records
    .filter((rec) => rec && rec.owner_id === cleanUid)
    .map((rec) =>
      sanitizeMirrorRecordForCache({
        ...rec,
        cached_at: nowMs,
      }),
    );
  return saveCacheState(
    {
      ...base,
      ownerUid: cleanUid,
      // Saved records of a kind that could not be fetched this time keep their own age (so the 14-day limit holds).
      mirrorRecords: [
        ...normalized,
        ...kept.filter((rec) => rec && rec.owner_id === cleanUid).map(sanitizeMirrorRecordForCache),
      ],
    },
    nowMs,
  );
}

/** Save the latest laptop response snapshot bound to pairing.deviceId. */
export function saveLaptopSnapshot(
  deviceId: string,
  partial: Partial<Omit<LaptopCachedSnapshot, 'cachedAt' | 'deviceId'>>,
  nowMs: number = Date.now(),
): ContextCacheState {
  const cleanDeviceId = (deviceId ?? '').trim();
  if (!cleanDeviceId) {
    throw new Error('deviceId is required to cache laptop snapshot.');
  }
  const current = loadCache(nowMs);
  const prev =
    current.laptop && current.laptop.deviceId === cleanDeviceId
      ? current.laptop
      : {
          deviceId: cleanDeviceId,
          status: null,
          drafts: [],
          draftDetails: {},
          reminders: [],
          notifications: [],
          unreadCount: 0,
          summary: null,
          pendingResults: [],
          pitchProposals: [],
          pitchPricesAsOf: null,
          cachedAt: nowMs,
        };
  const nextLaptop: LaptopCachedSnapshot = {
    deviceId: cleanDeviceId,
    status: partial.status !== undefined ? partial.status : prev.status,
    drafts: partial.drafts !== undefined ? partial.drafts : prev.drafts,
    draftDetails: partial.draftDetails !== undefined ? partial.draftDetails : prev.draftDetails,
    reminders: partial.reminders !== undefined ? partial.reminders : prev.reminders,
    notifications: partial.notifications !== undefined ? partial.notifications : prev.notifications,
    unreadCount: partial.unreadCount !== undefined ? partial.unreadCount : prev.unreadCount,
    summary: partial.summary !== undefined ? partial.summary : prev.summary,
    pendingResults: partial.pendingResults !== undefined ? partial.pendingResults : prev.pendingResults,
    pitchProposals: partial.pitchProposals !== undefined ? partial.pitchProposals : prev.pitchProposals ?? [],
    pitchPricesAsOf:
      partial.pitchPricesAsOf !== undefined ? partial.pitchPricesAsOf : prev.pitchPricesAsOf ?? null,
    cachedAt: nowMs,
  };
  const next = { ...current, laptop: nextLaptop };
  try {
    return saveCacheState(next, nowMs);
  } catch {
    // The snapshot is only a convenience copy: a full store must not break the live view.
    return current;
  }
}

/** Save validated product_rules bound to the signed-in ownerUid. */
export function saveCachedProductRules(
  ownerUid: string | null,
  rawRules: unknown,
  updatedAt: string | null = null,
  nowMs: number = Date.now(),
): ProductRulesConfig {
  const cleanUid = cleanOwnerId(ownerUid);
  if (!cleanUid) {
    throw new Error('ownerUid is required to cache product rules.');
  }
  const validated = validateProductRules(rawRules);
  try {
    const current = ensureCacheOwner(cleanUid, nowMs);
    saveCacheState(
      {
        ...current,
        ownerUid: cleanUid,
        productRules: {
          ownerUid: cleanUid,
          rules: validated,
          updatedAt,
          cachedAt: nowMs,
        },
      },
      nowMs,
    );
  } catch {
    // Can't save: use the fresh rules now, and drop the stale copy so it can't be used offline later.
    removeNonSecretKey(CACHE_STORAGE_KEY);
  }
  return validated;
}

export function getCachedProductRules(
  expectedOwnerUid?: string | null,
  nowMs: number = Date.now(),
): ProductRulesConfig | null {
  const current = loadCache(nowMs);
  if (!current.ownerUid || !current.productRules) return null;
  if (current.productRules.ownerUid !== current.ownerUid) return null;
  if (expectedOwnerUid !== undefined) {
    const cleanExpected = cleanOwnerId(expectedOwnerUid);
    if (!cleanExpected || current.ownerUid !== cleanExpected) return null;
  }
  return current.productRules.rules;
}

/** Format "Saved copy from <time>" label for offline views. */
export function formatSavedCopyLabel(timestampMs: number | null | undefined): string | null {
  if (!timestampMs || !Number.isFinite(timestampMs)) return null;
  const formatted = new Date(timestampMs).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
  return `Saved copy from ${formatted}`;
}

/** Format "Prices as of <time>" label for Pitch proposals. */
export function formatPricesAsOfLabel(timestampMs: number | null | undefined): string | null {
  if (!timestampMs || !Number.isFinite(timestampMs)) return null;
  const formatted = new Date(timestampMs).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
  return `Prices as of ${formatted}`;
}
