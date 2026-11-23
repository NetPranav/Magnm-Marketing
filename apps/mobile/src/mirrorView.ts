// What the phone shows from the cloud mirror when the laptop is not reachable (or never was): drafts with their
// comments, and open reminders. Confidential records stay stubs and never gain text here.

import type { ContextCacheState } from './cache';
import type { DraftComment, DraftItem, ReminderItem } from './types';

const MIRROR_KINDS = new Set(['content_item', 'content_version', 'review_comment', 'task']);

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function isReminderRecord(rec: { kind: string; data?: Record<string, unknown> }): boolean {
  return rec.kind === 'task' && rec.data?.kind === 'reminder';
}

/** True when the cloud copy is fresher than the last live copy from the laptop (or there is no live copy). */
export function mirrorIsNewer(cache: ContextCacheState): boolean {
  const laptopAt = cache.laptop?.cachedAt ?? 0;
  let newest = 0;
  for (const rec of cache.mirrorRecords) {
    if (!MIRROR_KINDS.has(rec.kind)) continue;
    const at = rec.synced_at ? Date.parse(rec.synced_at) : NaN;
    if (Number.isFinite(at) && at > newest) newest = at;
  }
  return newest > laptopAt;
}

function mirrorComments(cache: ContextCacheState): Map<string, DraftComment[]> {
  const flat = new Map<string, DraftComment[]>();
  for (const rec of cache.mirrorRecords) {
    if (rec.kind !== 'review_comment' || !rec.parent_local_id) continue;
    const conf = Boolean(rec.data?.confidential);
    const comment: DraftComment = {
      id: rec.local_id,
      body: conf ? '' : str(rec.data?.body) ?? '',
      author: conf ? null : str(rec.data?.author),
      version: typeof rec.data?.version === 'number' ? rec.data.version : null,
      parent_id: str(rec.data?.parent_id),
      status: str(rec.data?.status),
      created_at: str(rec.data?.created_at) ?? rec.local_updated_at,
      confidential: conf,
      replies: [],
    };
    const list = flat.get(rec.parent_local_id) ?? [];
    list.push(comment);
    flat.set(rec.parent_local_id, list);
  }
  const threads = new Map<string, DraftComment[]>();
  for (const [draftId, list] of flat) {
    const byId = new Map(list.map((c) => [c.id, c]));
    const roots: DraftComment[] = [];
    for (const c of list.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))) {
      const parent = c.parent_id ? byId.get(c.parent_id) : undefined;
      if (parent && parent !== c) parent.replies = [...(parent.replies ?? []), c];
      else roots.push(c);
    }
    threads.set(draftId, roots);
  }
  return threads;
}

/** Drafts built from mirror records only. */
export function draftsFromMirror(cache: ContextCacheState): DraftItem[] {
  const versionsByParent = new Map<string, Array<{ version: number; body: string }>>();
  for (const rec of cache.mirrorRecords) {
    if (rec.kind === 'content_version' && rec.parent_local_id && !rec.data?.confidential) {
      const body = typeof rec.data?.body === 'string' ? rec.data.body : '';
      const verNum = typeof rec.data?.version === 'number' ? rec.data.version : 0;
      if (body) {
        const list = versionsByParent.get(rec.parent_local_id) ?? [];
        list.push({ version: verNum, body });
        versionsByParent.set(rec.parent_local_id, list);
      }
    }
  }
  const comments = mirrorComments(cache);

  return cache.mirrorRecords
    .filter((rec) => rec.kind === 'content_item')
    .map((rec) => {
      const isConf = Boolean(rec.data?.confidential);
      const currentVer = typeof rec.data?.current_version === 'number' ? rec.data.current_version : null;
      const verList = versionsByParent.get(rec.local_id) ?? [];
      let chosenBody: string | null = null;
      if (!isConf && verList.length > 0) {
        const byCurrent = currentVer !== null ? verList.find((v) => v.version === currentVer) : undefined;
        if (byCurrent) {
          chosenBody = byCurrent.body;
        } else {
          chosenBody = verList.reduce((best, cur) => (cur.version >= best.version ? cur : best)).body;
        }
      }
      return {
        id: rec.local_id,
        title: isConf ? 'Confidential item' : rec.title || 'Untitled draft',
        platform: typeof rec.data?.platform === 'string' ? rec.data.platform : null,
        status: typeof rec.data?.status === 'string' ? rec.data.status : null,
        confidential: isConf,
        body: isConf ? null : chosenBody,
        current_version: currentVer,
        versions: isConf ? [] : verList.map((v) => ({ version: v.version, body: v.body })),
        comments: isConf ? [] : comments.get(rec.local_id) ?? [],
        posted_url: isConf ? null : str(rec.data?.posted_url),
        updated_at: rec.local_updated_at,
      };
    });
}

/** Open reminders built from mirror records only. A confidential reminder is a stub. */
export function remindersFromMirror(cache: ContextCacheState): ReminderItem[] {
  return cache.mirrorRecords
    .filter((rec) => isReminderRecord(rec) && !rec.data?.done_at)
    .map((rec) => {
      const conf = Boolean(rec.data?.confidential);
      return {
        id: rec.local_id,
        kind: str(rec.data?.reminder_kind),
        title: conf ? 'Confidential reminder' : rec.title || 'Reminder',
        note: conf ? '' : str(rec.data?.note) ?? '',
        due_at: str(rec.data?.due_at),
        done_at: null,
        snoozed_until: str(rec.data?.snoozed_until),
        created_at: str(rec.data?.created_at),
        confidential: conf,
      };
    })
    .sort((a, b) => String(a.due_at ?? '').localeCompare(String(b.due_at ?? '')));
}

/** The drafts to show: the live copy from the laptop unless the cloud copy is newer or there is no live copy. */
export function draftsFromCache(cache: ContextCacheState): DraftItem[] {
  const live = cache.laptop?.drafts ?? [];
  const cloud = draftsFromMirror(cache);
  if (live.length > 0 && !(cloud.length > 0 && mirrorIsNewer(cache))) return live;
  return cloud.length > 0 ? cloud : live;
}

/** The open reminders to show, chosen the same way. */
export function remindersFromCache(cache: ContextCacheState): ReminderItem[] {
  const cloud = remindersFromMirror(cache);
  if (!cache.laptop || mirrorIsNewer(cache)) return cloud;
  return cache.laptop.reminders ?? [];
}
