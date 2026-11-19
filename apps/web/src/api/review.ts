import { api } from '../api';
import type {
  ContentItem,
  JobRecord,
  PostingKitResult,
  ReviewChecklistItem,
  ReviewComment,
  ReviewCommentAnchor,
  ReviewDiffResponse,
  ReviewFinding,
  ReviewPayload,
  ReviewSuggestion,
  ReviewVersionDetail,
} from '../types';

function enc(val: string): string {
  return encodeURIComponent(val);
}

/** Build the Range-capable content file URL for images, videos, and posting kits. */
export function contentFileUrl(contentId: string, filePath: string): string {
  return `/api/content/${enc(contentId)}/file?path=${enc(filePath)}`;
}

/** List content items with open comment and warning counts for Review Studio. */
export function listReviewContent(limit = 100): Promise<ContentItem[]> {
  return api.get<ContentItem[]>(`/api/content?limit=${limit}&review_counts=1`);
}

/** Load the full Review Studio workspace payload for a content item. */
export function getReview(contentId: string): Promise<ReviewPayload> {
  return api.get<ReviewPayload>(`/api/review/${enc(contentId)}`);
}

/** Load a specific version body and metadata. */
export function getReviewVersion(contentId: string, version: number): Promise<ReviewVersionDetail> {
  return api.get<ReviewVersionDetail>(`/api/review/${enc(contentId)}/versions/${version}`);
}

/** Save an edited body as a new content version. */
export function saveReviewVersion(
  contentId: string,
  body: string,
  notes?: string,
): Promise<ContentItem> {
  return api.post<ContentItem>(`/api/review/${enc(contentId)}/versions`, {
    body,
    notes: notes?.trim() || 'Manual edit',
  });
}

/** Restore an earlier version by creating a new version with its body. */
export function restoreReviewVersion(contentId: string, version: number): Promise<ContentItem> {
  return api.post<ContentItem>(`/api/review/${enc(contentId)}/versions/${version}/restore`, {});
}

/** Fetch a word-level diff between two versions. */
export function diffReviewVersions(
  contentId: string,
  fromVersion: number,
  toVersion: number,
): Promise<ReviewDiffResponse> {
  return api.get<ReviewDiffResponse>(
    `/api/review/${enc(contentId)}/diff?from=${fromVersion}&to=${toVersion}`,
  );
}

/** Create a comment (text, image, video, general) or reply, optionally triggering @agent jobs. */
export function createReviewComment(
  contentId: string,
  payload: {
    kind?: 'text' | 'image' | 'video' | 'general';
    version?: number;
    anchor?: ReviewCommentAnchor;
    body: string;
    parent_id?: string | null;
  },
): Promise<ReviewComment> {
  return api.post<ReviewComment>(`/api/review/${enc(contentId)}/comments`, payload);
}

/** Edit a comment body or toggle its resolved/open status. */
export function updateReviewComment(
  contentId: string,
  commentId: string,
  payload: { body?: string; status?: 'open' | 'resolved' | 'applied' },
): Promise<ReviewComment> {
  return api.post<ReviewComment>(
    `/api/review/${enc(contentId)}/comments/${enc(commentId)}`,
    payload,
  );
}

/** Delete the user's own comment and its replies. */
export function deleteReviewComment(
  contentId: string,
  commentId: string,
): Promise<{ deleted: number; id: string }> {
  return api.post<{ deleted: number; id: string }>(
    `/api/review/${enc(contentId)}/comments/${enc(commentId)}/delete`,
    {},
  );
}

/** Enqueue an apply-all job to address open comments as a new version or suggestions. */
export function applyAllComments(
  contentId: string,
  asSuggestions: boolean,
  agent = 'writer',
): Promise<{ job_id: string; job: JobRecord }> {
  return api.post<{ job_id: string; job: JobRecord }>(`/api/review/${enc(contentId)}/apply_all`, {
    as_suggestions: asSuggestions,
    agent,
  });
}

/** Enqueue a text, blog, or image AI action job. */
export function runAiAction(
  contentId: string,
  action: string,
  selection?: { start: number; end: number; quote?: string } | null,
  params?: Record<string, unknown>,
): Promise<{ job_id: string; job: JobRecord }> {
  return api.post<{ job_id: string; job: JobRecord }>(`/api/review/${enc(contentId)}/ai_action`, {
    action,
    selection: selection || undefined,
    params: params || undefined,
  });
}

/** Accept or reject a single pending suggestion. */
export function decideSuggestion(
  contentId: string,
  suggestionId: string,
  decision: 'accept' | 'reject',
): Promise<{
  content_id: string;
  version_created: boolean;
  accepted: ReviewSuggestion[];
  rejected: ReviewSuggestion[];
}> {
  return api.post(`/api/review/${enc(contentId)}/suggestions/${enc(suggestionId)}/decide`, {
    decision,
  });
}

/** Accept or reject all pending suggestions. */
export function decideAllSuggestions(
  contentId: string,
  decision: 'accept' | 'reject',
): Promise<{
  content_id: string;
  version_created: boolean;
  accepted: ReviewSuggestion[];
  rejected: ReviewSuggestion[];
}> {
  return api.post(`/api/review/${enc(contentId)}/suggestions/decide_all`, { decision });
}

/** Toggle a checklist item or add a manual checklist item. */
export function updateChecklistItem(
  contentId: string,
  payload: { item_key?: string; label?: string; checked: boolean },
): Promise<{ content_id: string; item: ReviewChecklistItem; checklist: ReviewChecklistItem[] }> {
  return api.post(`/api/review/${enc(contentId)}/checklist`, payload);
}

/** Mark the content item ready to post (advisory warnings only). */
export function markReviewReady(contentId: string): Promise<{
  item: ContentItem;
  status: string;
  warnings: ReviewFinding[];
  verdict: string;
}> {
  return api.post(`/api/review/${enc(contentId)}/ready`, {});
}

/** Build POSTING-KIT.md and return its text and downloadable file paths. */
export function buildPostingKit(contentId: string): Promise<PostingKitResult> {
  return api.post<PostingKitResult>(`/api/review/${enc(contentId)}/posting_kit`, {});
}

/** Mark the content item as posted with optional URL and platform. */
export function markReviewPosted(
  contentId: string,
  url?: string,
  platform?: string,
): Promise<{
  item: ContentItem;
  status: string;
  posted_url?: string | null;
  posted_at?: string | null;
  platform?: string | null;
}> {
  return api.post(`/api/review/${enc(contentId)}/posted`, {
    url: url?.trim() || undefined,
    platform: platform?.trim() || undefined,
  });
}
