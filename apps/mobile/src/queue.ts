import { HttpStatusError } from './net';
import {
  bumpStateGeneration,
  getStateGeneration,
  readNonSecretJson,
  removeNonSecretKey,
  writeNonSecretJson,
} from './storage';
import type { InboxAckItem, QueueItemKind, QueuedInboxItem } from './types';

export const QUEUE_STORAGE_KEY = 'magnm.mobile.inboxQueue.v1';
export const MAX_QUEUE_BATCH = 50;

let activeFlushPromise: Promise<unknown> = Promise.resolve();

function generateClientId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return `mob_${globalThis.crypto.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `mob_${hex}`;
}

function isValidQueueItem(item: unknown): item is QueuedInboxItem {
  if (!item || typeof item !== 'object') return false;
  const obj = item as Partial<QueuedInboxItem>;
  return Boolean(
    typeof obj.client_id === 'string' &&
      obj.client_id.trim().length > 0 &&
      (obj.kind === 'draft' || obj.kind === 'note' || obj.kind === 'idea') &&
      typeof obj.title === 'string' &&
      typeof obj.body === 'string' &&
      typeof obj.laptop_device_id === 'string' &&
      obj.laptop_device_id.trim().length > 0,
  );
}

export function loadQueue(): QueuedInboxItem[] {
  const raw = readNonSecretJson<unknown[]>(QUEUE_STORAGE_KEY);
  if (!Array.isArray(raw)) return [];
  const valid = raw.filter(isValidQueueItem);
  // Never adopt queued items of unknown owner (missing laptop_device_id).
  if (valid.length !== raw.length) {
    try {
      if (valid.length === 0) {
        removeNonSecretKey(QUEUE_STORAGE_KEY);
      } else {
        writeNonSecretJson(QUEUE_STORAGE_KEY, valid);
      }
    } catch {
      // Best-effort cleanup of invalid entries during read.
    }
  }
  return valid;
}

function saveQueue(items: QueuedInboxItem[]): void {
  writeNonSecretJson(QUEUE_STORAGE_KEY, items);
}

export function wipeQueue(): void {
  removeNonSecretKey(QUEUE_STORAGE_KEY);
  bumpStateGeneration();
}

/** Return queued items belonging to the paired laptop. */
export function getBoundQueueItems(deviceId: string | null): QueuedInboxItem[] {
  const laptopDeviceId = (deviceId ?? '').trim();
  if (!laptopDeviceId) return [];
  return loadQueue().filter((item) => item.laptop_device_id === laptopDeviceId);
}

export interface EnqueueInput {
  kind: QueueItemKind;
  title: string;
  body: string;
  platform?: string;
  url?: string;
  ownerUid?: string;
  laptopDeviceId: string;
  clientId?: string;
}

/** Queue a draft, note, or idea locally, bound to paired laptop (sign-in is not required). */
export function enqueueInboxItem(input: EnqueueInput): QueuedInboxItem {
  const title = input.title.trim();
  if (!title) {
    throw new Error('Title is required to queue an item.');
  }
  const laptopDeviceId = (input.laptopDeviceId ?? '').trim();
  if (!laptopDeviceId) {
    throw new Error('Pair with your laptop before saving inbox items.');
  }
  if (input.url && input.url.trim()) {
    let parsed: URL;
    try {
      parsed = new URL(input.url.trim());
    } catch {
      throw new Error('URL must be a valid http or https link.');
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error('URL must use http or https.');
    }
  }

  const item: QueuedInboxItem = {
    client_id: (input.clientId ?? '').trim() || generateClientId(),
    kind: input.kind,
    title,
    body: input.body,
    platform: input.platform?.trim() || undefined,
    url: input.url?.trim() || undefined,
    created_at: new Date().toISOString(),
    owner_uid: (input.ownerUid ?? '').trim(),
    laptop_device_id: laptopDeviceId,
    last_error: null,
  };

  const existing = loadQueue();
  if (existing.some((entry) => entry.client_id === item.client_id)) {
    return item;
  }
  saveQueue([...existing, item]);
  return item;
}

/** Remove a single queued item by client_id. */
export function removeQueuedItem(clientId: string): QueuedInboxItem[] {
  const next = loadQueue().filter((item) => item.client_id !== clientId);
  saveQueue(next);
  return next;
}

function isBadRequest400(err: unknown): boolean {
  if (err instanceof HttpStatusError && err.status === 400) return true;
  if (err && typeof err === 'object' && 'status' in err && (err as { status: unknown }).status === 400) {
    return true;
  }
  return false;
}

function markItemsError(clientIds: Set<string>, message: string): void {
  const updated = loadQueue().map((item) =>
    clientIds.has(item.client_id) ? { ...item, last_error: message } : item,
  );
  saveQueue(updated);
}

function removeAckedItems(ackedIds: Set<string>): void {
  if (ackedIds.size === 0) return;
  const next = loadQueue().filter((item) => !ackedIds.has(item.client_id));
  saveQueue(next);
}

export interface FlushQueueResult {
  acknowledgedIds: string[];
  failedIds: string[];
  remaining: QueuedInboxItem[];
}

export type SendInboxBatchFn = (items: QueuedInboxItem[]) => Promise<{ items: InboxAckItem[] }>;

/**
 * Flush bound queue items to POST /phone/v1/inbox.
 * Serialized one flush at a time, drains > 50 items, and splits 400 batch errors item-by-item.
 */
export function flushBoundQueue(deviceId: string, sendBatch: SendInboxBatchFn): Promise<FlushQueueResult> {
  const laptopDeviceId = deviceId.trim();
  const run = activeFlushPromise
    .catch(() => undefined)
    .then(() => executeFlush(laptopDeviceId, sendBatch));
  activeFlushPromise = run.catch(() => undefined);
  return run;
}

async function executeFlush(
  laptopDeviceId: string,
  sendBatch: SendInboxBatchFn,
): Promise<FlushQueueResult> {
  const gen = getStateGeneration();
  const acknowledgedIds: string[] = [];
  const failedIds: string[] = [];
  const attemptedIds = new Set<string>();

  if (!laptopDeviceId) {
    return { acknowledgedIds, failedIds, remaining: loadQueue() };
  }

  while (true) {
    if (gen !== getStateGeneration()) {
      return { acknowledgedIds, failedIds, remaining: loadQueue() };
    }
    const pending = getBoundQueueItems(laptopDeviceId).filter(
      (item) => !attemptedIds.has(item.client_id),
    );
    if (pending.length === 0) {
      break;
    }

    const batch = pending.slice(0, MAX_QUEUE_BATCH);
    for (const item of batch) {
      attemptedIds.add(item.client_id);
    }

    try {
      const res = await sendBatch(batch);
      if (gen !== getStateGeneration()) {
        return { acknowledgedIds, failedIds, remaining: loadQueue() };
      }
      const ackedList = Array.isArray(res?.items) ? res.items : [];
      const sentIds = new Set(batch.map((b) => b.client_id));
      const batchAcked = new Set<string>();
      for (const ack of ackedList) {
        if (ack && typeof ack.client_id === 'string' && sentIds.has(ack.client_id)) {
          batchAcked.add(ack.client_id);
          acknowledgedIds.push(ack.client_id);
        }
      }
      removeAckedItems(batchAcked);
    } catch (err) {
      if (gen !== getStateGeneration()) {
        return { acknowledgedIds, failedIds, remaining: loadQueue() };
      }
      const message = err instanceof Error ? err.message : 'Sync failed';

      if (isBadRequest400(err)) {
        if (batch.length === 1) {
          const single = batch[0];
          markItemsError(new Set([single.client_id]), message);
          failedIds.push(single.client_id);
          continue;
        }

        // Batch returned 400: retry items one by one so one bad item does not block the rest.
        for (let idx = 0; idx < batch.length; idx += 1) {
          const single = batch[idx];
          if (gen !== getStateGeneration()) {
            return { acknowledgedIds, failedIds, remaining: loadQueue() };
          }
          try {
            const singleRes = await sendBatch([single]);
            if (gen !== getStateGeneration()) {
              return { acknowledgedIds, failedIds, remaining: loadQueue() };
            }
            const ackedList = Array.isArray(singleRes?.items) ? singleRes.items : [];
            const wasAcked = ackedList.some((a) => a && a.client_id === single.client_id);
            if (wasAcked) {
              removeAckedItems(new Set([single.client_id]));
              acknowledgedIds.push(single.client_id);
            }
          } catch (singleErr) {
            if (gen !== getStateGeneration()) {
              return { acknowledgedIds, failedIds, remaining: loadQueue() };
            }
            const singleMsg = singleErr instanceof Error ? singleErr.message : 'Sync failed';
            if (isBadRequest400(singleErr)) {
              markItemsError(new Set([single.client_id]), singleMsg);
              failedIds.push(single.client_id);
            } else {
              const remainingBatchIds = new Set(batch.slice(idx).map((b) => b.client_id));
              markItemsError(remainingBatchIds, singleMsg);
              throw singleErr;
            }
          }
        }
        continue;
      }

      markItemsError(new Set(batch.map((b) => b.client_id)), message);
      throw err;
    }
  }

  return {
    acknowledgedIds,
    failedIds,
    remaining: loadQueue(),
  };
}
