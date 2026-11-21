import { api } from '../api';
import type { MetricProposal, MetricRecord } from '../types';

/** Build the URL for a proposal's uploaded screenshot image. */
export function proposalImageUrl(proposalId: string): string {
  return `/api/results/proposals/${encodeURIComponent(proposalId)}/image`;
}

/** List screenshot metric proposals filtered by status. */
export async function listResultsProposals(
  status: 'pending' | 'saved' | 'discarded' | 'all' = 'pending',
): Promise<MetricProposal[]> {
  const res = await api.get<{ proposals?: MetricProposal[] }>(
    `/api/results/proposals?status=${encodeURIComponent(status)}`,
  );
  return Array.isArray(res?.proposals) ? res.proposals : [];
}

/** Upload an analytics screenshot to extract a pending proposal without saving metrics. */
export function extractResultsScreenshot(
  file: File,
  options?: { content_id?: string | null; platform?: string | null },
): Promise<{ proposal: MetricProposal; period?: string | null; notes?: string | null }> {
  const form = new FormData();
  form.append('file', file);
  if (options?.content_id) {
    form.append('content_id', options.content_id);
  }
  if (options?.platform) {
    form.append('platform', options.platform);
  }
  return api.postForm<{ proposal: MetricProposal; period?: string | null; notes?: string | null }>(
    '/api/results/extract',
    form,
  );
}

/** Confirm and save a metric proposal (with optional edited metrics, post, and platform). */
export function confirmResultsProposal(
  proposalId: string,
  payload: {
    metrics: Record<string, number>;
    content_id?: string | null;
    platform: string;
  },
): Promise<{ proposal: MetricProposal; metric: MetricRecord }> {
  return api.post<{ proposal: MetricProposal; metric: MetricRecord }>(
    `/api/results/proposals/${encodeURIComponent(proposalId)}/confirm`,
    payload,
  );
}

/** Discard a pending metric proposal without saving any metrics. */
export function discardResultsProposal(
  proposalId: string,
): Promise<{ proposal: MetricProposal }> {
  return api.post<{ proposal: MetricProposal }>(
    `/api/results/proposals/${encodeURIComponent(proposalId)}/discard`,
    {},
  );
}
