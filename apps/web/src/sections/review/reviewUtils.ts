import type { StatusState } from '@magnm/design/react';
import type { RosterAgent } from '../../api/team';
import type {
  ContentItem,
  PlatformLimits,
  ReviewComment,
  ReviewSuggestion,
} from '../../types';

export const TEXT_AI_PRIMARY = [
  { id: 'shorten', label: 'Shorten' },
  { id: 'rephrase', label: 'Rephrase' },
  { id: 'simplify', label: 'Simplify' },
  { id: 'fix_grammar', label: 'Fix grammar' },
  { id: 'stronger_hook', label: 'Stronger hook' },
] as const;

export const TEXT_AI_MORE = [
  { id: 'expand', label: 'Expand' },
  { id: 'formal', label: 'Formal' },
  { id: 'casual', label: 'Casual' },
  { id: 'hashtags', label: 'Hashtags' },
  { id: 'fit_platform', label: 'Fit platform' },
] as const;

export const BLOG_AI_ACTIONS = [
  { id: 'seo_title_meta', label: 'SEO title & meta' },
  { id: 'tldr', label: 'TL;DR' },
  { id: 'outline_check', label: 'Outline check' },
] as const;

export const KNOWN_PLATFORMS: Record<string, PlatformLimits> = {
  x: {
    platform: 'x',
    max_chars: 280,
    max_hashtags: 2,
    threads_numbered: true,
  },
  linkedin: {
    platform: 'linkedin',
    max_chars: 3000,
    hook_chars: 210,
    min_hashtags: 3,
    max_hashtags: 5,
  },
  instagram: {
    platform: 'instagram',
    max_chars: 2200,
    hook_chars: 125,
    max_hashtags: 5,
    alt_text_required: true,
  },
  youtube: {
    platform: 'youtube',
    title_max_chars: 100,
    description_hook_chars: 150,
  },
  blog: {
    platform: 'blog',
    min_words: 1200,
    max_words: 2000,
    requires_headings: true,
    requires_tldr: true,
  },
};

const PLATFORM_ALIASES: Record<string, string> = {
  x: 'x',
  twitter: 'x',
  linkedin: 'linkedin',
  li: 'linkedin',
  instagram: 'instagram',
  ig: 'instagram',
  youtube: 'youtube',
  yt: 'youtube',
  blog: 'blog',
};

/** Normalize a platform alias to a canonical key from engine/platforms.py. */
export function normalizePlatform(raw: string | null | undefined): string {
  const key = (raw || '').trim().toLowerCase();
  return PLATFORM_ALIASES[key] || key || 'linkedin';
}

/** Format seconds into m:ss (for example 12 -> "0:12"). */
export function formatVideoTime(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds >= 0 ? Math.floor(seconds) : 0;
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/** Map a content status string to a monochrome StatusDot state. */
export function mapContentStatusState(status: string): StatusState {
  switch ((status || '').toLowerCase()) {
    case 'ready':
    case 'posted':
      return 'online';
    case 'revision_required':
      return 'error';
    case 'draft':
    default:
      return 'idle';
  }
}

/** Resolve an author id (such as "writer" or "user") to a human-readable mono label. */
export function resolveAuthorName(author: string, roster: RosterAgent[]): string {
  const raw = (author || 'user').trim();
  const low = raw.toLowerCase();
  if (low === 'user' || low === 'ceo') return 'You';
  const match = roster.find((a) => a.id.toLowerCase() === low);
  if (match) return match.name;
  const fallbacks: Record<string, string> = {
    cmo: 'CMO',
    researcher: 'Researcher',
    writer: 'Writer',
    designer: 'Designer',
    video: 'Video Producer',
    reviewer: 'Reviewer',
    analyst: 'Analyst',
    community: 'Community',
  };
  return fallbacks[low] || raw;
}

/** Return true when target is an editable form control or contentEditable element. */
export function isEditableElement(target: EventTarget | null): boolean {
  if (!target || typeof HTMLElement === 'undefined') return false;
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
}

/** Split an X thread body using the exact delimiters from engine/platforms.py. */
export function splitXThread(body: string): string[] {
  const trimmed = (body || '').trim();
  if (!trimmed) return [];
  const parts = trimmed
    .split(/\n\s*---\s*\n|\n\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
  return parts.length > 0 ? parts : [trimmed];
}

/** Extract hashtags from text matching engine/platforms.py. */
export function extractHashtags(body: string): string[] {
  const matches = (body || '').match(/(?<!\w)#[A-Za-z0-9_]+/g);
  return matches ? [...matches] : [];
}

/** Count whitespace-separated words in text. */
export function countWords(body: string): number {
  const trimmed = (body || '').trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

export interface BodySegment {
  key: string;
  start: number;
  end: number;
  text: string;
  commentIds: string[];
  suggestion: ReviewSuggestion | null;
}

/**
 * Partition body text into non-overlapping segments carrying active comment highlights
 * and pending inline tracked-change suggestions.
 */
export function buildBodySegments(
  body: string,
  comments: ReviewComment[],
  suggestions: ReviewSuggestion[],
  currentVersion: number,
): { segments: BodySegment[]; trailingInsertions: ReviewSuggestion[] } {
  const text = body || '';
  const activeTextComments = comments.filter(
    (c) =>
      c.kind === 'text' &&
      c.status === 'open' &&
      !c.anchor?.orphaned &&
      typeof c.anchor?.start === 'number' &&
      typeof c.anchor?.end === 'number' &&
      c.anchor.start >= 0 &&
      c.anchor.end > c.anchor.start &&
      c.anchor.end <= text.length,
  );

  // Locate each pending suggestion on current body without overlapping ranges.
  const activeSugs: Array<{ start: number; end: number; sug: ReviewSuggestion }> = [];
  const trailingInsertions: ReviewSuggestion[] = [];
  const sortedSugs = [...suggestions]
    .filter((s) => s.status === 'pending')
    .sort((a, b) => a.start - b.start || a.end - b.end);

  let lastSugEnd = -1;
  for (const sug of sortedSugs) {
    let start = sug.start;
    let end = sug.end;
    if (sug.base_version !== currentVersion && sug.original) {
      const idx = text.indexOf(sug.original);
      if (idx === -1) continue;
      start = idx;
      end = idx + sug.original.length;
    }
    if (start < 0 || end < start || end > text.length) continue;
    if (sug.original && text.slice(start, end) !== sug.original) continue;
    if (start < lastSugEnd) continue;

    if (start === end && start === text.length) {
      trailingInsertions.push(sug);
    } else {
      activeSugs.push({ start, end, sug });
    }
    lastSugEnd = end;
  }

  const cuts = new Set<number>([0, text.length]);
  for (const c of activeTextComments) {
    cuts.add(c.anchor.start!);
    cuts.add(c.anchor.end!);
  }
  for (const s of activeSugs) {
    cuts.add(s.start);
    cuts.add(s.end);
  }

  const points = Array.from(cuts).sort((a, b) => a - b);
  const segments: BodySegment[] = [];

  for (let i = 0; i < points.length; i += 1) {
    const pos = points[i];

    // Pure insertion suggestion at `pos`
    for (const entry of activeSugs) {
      if (entry.start === pos && entry.end === pos) {
        segments.push({
          key: `ins-${entry.sug.id}`,
          start: pos,
          end: pos,
          text: '',
          commentIds: [],
          suggestion: entry.sug,
        });
      }
    }

    if (i === points.length - 1) break;
    const next = points[i + 1];
    if (next <= pos) continue;

    const matchingComments = activeTextComments
      .filter((c) => c.anchor.start! <= pos && c.anchor.end! >= next)
      .map((c) => c.id);
    const matchingSug =
      activeSugs.find((s) => s.start < s.end && s.start <= pos && s.end >= next)?.sug ?? null;

    segments.push({
      key: `seg-${pos}-${next}`,
      start: pos,
      end: next,
      text: text.slice(pos, next),
      commentIds: matchingComments,
      suggestion: matchingSug,
    });
  }

  return { segments, trailingInsertions };
}

function resolveRawPoint(
  container: HTMLElement,
  node: Node,
  offset: number,
  isEnd: boolean,
): number | null {
  if (node.nodeType === Node.TEXT_NODE) {
    const el = node.parentElement;
    if (!el || el.closest('[data-suggestion-ins="true"]') || el.closest('[data-ignore-offset="true"]')) {
      return null;
    }
    const host = el.closest<HTMLElement>('[data-raw-start]');
    if (!host) return null;
    const base = Number(host.dataset.rawStart);
    if (!Number.isFinite(base)) return null;
    return base + offset;
  }

  if (node instanceof HTMLElement) {
    if (node.dataset.rawStart !== undefined && node.dataset.rawEnd !== undefined) {
      return isEnd ? Number(node.dataset.rawEnd) : Number(node.dataset.rawStart);
    }
    const children = Array.from(node.childNodes);
    if (children.length === 0) return null;
    const idx = Math.max(0, Math.min(offset, children.length - 1));
    const child = children[idx];
    if (child instanceof HTMLElement && child.dataset.rawStart !== undefined) {
      return isEnd && offset > idx ? Number(child.dataset.rawEnd) : Number(child.dataset.rawStart);
    }
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    let current: Node | null;
    while ((current = walker.nextNode())) {
      const parent = current.parentElement;
      if (parent && parent.closest('[data-raw-start]') && !parent.closest('[data-suggestion-ins="true"]')) {
        if (node.contains(current)) {
          const base = Number(parent.closest<HTMLElement>('[data-raw-start]')!.dataset.rawStart);
          return base;
        }
      }
    }
  }
  return null;
}

/**
 * Compute raw body character offsets { start, end, quote } from a DOM Selection inside container.
 * Uses UTF-16 string offsets matching JS string.slice and Python str indexing for BMP characters.
 */
export function computeDomSelectionOffsets(
  container: HTMLElement | null,
  rawBody: string,
  sel: Selection | null,
): { start: number; end: number; quote: string } | null {
  if (!container || !sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) {
    return null;
  }

  const startOffset = resolveRawPoint(container, range.startContainer, range.startOffset, false);
  const endOffset = resolveRawPoint(container, range.endContainer, range.endOffset, true);

  if (
    startOffset !== null &&
    endOffset !== null &&
    startOffset >= 0 &&
    endOffset > startOffset &&
    endOffset <= rawBody.length
  ) {
    return {
      start: startOffset,
      end: endOffset,
      quote: rawBody.slice(startOffset, endOffset),
    };
  }

  const selectedText = sel.toString();
  if (!selectedText) return null;
  const idx = rawBody.indexOf(selectedText);
  if (idx >= 0) {
    return {
      start: idx,
      end: idx + selectedText.length,
      quote: selectedText,
    };
  }
  return null;
}

export interface PostingKitField {
  id: string;
  label: string;
  value: string;
}

/** Extract copyable fields from the posting kit and current content item. */
export function buildCopyableKitFields(kitText: string, item: ContentItem, body: string): PostingKitField[] {
  const fields: PostingKitField[] = [];
  if (item.title) {
    fields.push({ id: 'title', label: 'Title', value: item.title });
  }
  if (body) {
    fields.push({ id: 'body', label: 'Final text', value: body });
  }
  const tags = extractHashtags(body);
  if (tags.length > 0) {
    fields.push({ id: 'hashtags', label: 'Hashtags', value: tags.join(' ') });
  }
  const alts = (item.files || [])
    .filter((f) => f && (f.kind === 'image' || f.kind === 'card') && f.alt_text)
    .map((f) => `${f.path}: ${f.alt_text}`);
  if (alts.length > 0) {
    fields.push({ id: 'alt_texts', label: 'Alt text', value: alts.join('\n') });
  }
  fields.push({ id: 'full_kit', label: 'Full POSTING-KIT.md', value: kitText });
  return fields;
}
