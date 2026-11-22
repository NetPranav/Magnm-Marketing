import { ApiError, api } from '../api';
import type {
  SocialPlatform,
  SocialRecipe,
  SocialRunRecord,
  SocialStatusInfo,
} from '../types';

export type SocialLiveInputPayload =
  | { action: 'click'; x: number; y: number; button?: 'left' | 'right' }
  | { action: 'scroll'; delta_x: number; delta_y: number }
  | { action: 'text'; text: string }
  | { action: 'key'; key: string };

export function getSocialStatus(): Promise<SocialStatusInfo> {
  return api.get<SocialStatusInfo>('/api/social/status');
}

export function enableSocialPlatform(
  platform: SocialPlatform,
  acknowledged: boolean,
  acknowledgementText?: string,
): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/social/enable', {
    platform,
    acknowledged,
    acknowledgement_text: acknowledgementText,
  });
}

export function disableSocialPlatform(platform: SocialPlatform): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/social/disable', { platform });
}

export function removeSocialPlatform(platform: SocialPlatform): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/social/remove', { platform });
}

export function startSocialLogin(
  platform: SocialPlatform,
): Promise<{ ok: boolean; platform: string; status: string }> {
  return api.post<{ ok: boolean; platform: string; status: string }>('/api/social/login/start', {
    platform,
  });
}

export function doneSocialLogin(
  platform?: SocialPlatform,
): Promise<{ ok: boolean; platform: string; saved: boolean }> {
  return api.post<{ ok: boolean; platform: string; saved: boolean }>('/api/social/login/done', {
    platform,
  });
}

export function cancelSocialLogin(): Promise<{ ok: boolean; cancelled: boolean }> {
  return api.post<{ ok: boolean; cancelled: boolean }>('/api/social/login/cancel', {});
}

export async function listSocialRuns(
  limit = 50,
  platform?: SocialPlatform,
  status?: string,
): Promise<SocialRunRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (platform) params.set('platform', platform);
  if (status) params.set('status', status);
  const res = await api.get<{ runs: SocialRunRecord[] }>(`/api/social/runs?${params.toString()}`);
  return Array.isArray(res?.runs) ? res.runs : [];
}

export function getSocialRun(runId: string): Promise<SocialRunRecord> {
  return api.get<SocialRunRecord>(`/api/social/runs/${encodeURIComponent(runId)}`);
}

export function createSocialRun(payload: {
  platform: SocialPlatform;
  recipe: SocialRecipe;
  argument: string;
  requested_by?: string;
  session_id?: string;
}): Promise<{ success: boolean; run: SocialRunRecord }> {
  return api.post<{ success: boolean; run: SocialRunRecord }>('/api/social/runs', payload);
}

export function startSocialRun(
  runId: string,
  approvalHash: string,
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return api.post<{ success: boolean; run: SocialRunRecord }>(
    `/api/social/runs/${encodeURIComponent(runId)}/start`,
    { approval_hash: approvalHash },
  );
}

export function cancelSocialRun(
  runId: string,
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return api.post<{ success: boolean; run: SocialRunRecord }>(
    `/api/social/runs/${encodeURIComponent(runId)}/cancel`,
    {},
  );
}

export function summariseSocialRun(
  runId: string,
): Promise<{ success: boolean; run: SocialRunRecord }> {
  return api.post<{ success: boolean; run: SocialRunRecord }>(
    `/api/social/runs/${encodeURIComponent(runId)}/summarise`,
    {},
  );
}

export function socialChallengeLater(
  runId?: string,
): Promise<{ ok: boolean; run: SocialRunRecord }> {
  return api.post<{ ok: boolean; run: SocialRunRecord }>('/api/social/challenge/later', {
    run_id: runId,
  });
}

export function socialChallengeSkip(
  runId?: string,
): Promise<{ ok: boolean; run: SocialRunRecord }> {
  return api.post<{ ok: boolean; run: SocialRunRecord }>('/api/social/challenge/skip', {
    run_id: runId,
  });
}

export async function socialChallengeContinue(
  runId?: string,
): Promise<{ ok: boolean; cleared: boolean; challenge_kind?: string; run?: SocialRunRecord }> {
  const response = await fetch('/api/social/challenge/continue', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ run_id: runId }),
  });
  const data = (await response.json().catch(() => ({}))) as {
    ok?: boolean;
    cleared?: boolean;
    challenge_kind?: string;
    run?: SocialRunRecord;
    error?: string;
  };
  if (response.status === 409 && data && data.ok === false) {
    return {
      ok: false,
      cleared: false,
      challenge_kind: data.challenge_kind,
      run: data.run,
    };
  }
  if (!response.ok) {
    throw new ApiError(response.status, data?.error || `Request failed (${response.status})`, data);
  }
  return {
    ok: Boolean(data?.ok),
    cleared: Boolean(data?.cleared),
    challenge_kind: data?.challenge_kind,
    run: data?.run,
  };
}

export async function fetchSocialLiveFrameBlob(signal?: AbortSignal): Promise<Blob> {
  const response = await fetch('/api/social/live/frame', {
    method: 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    signal,
  });
  if (!response.ok) {
    let message = `Frame unavailable (${response.status})`;
    try {
      const data = (await response.json()) as { error?: string };
      if (data?.error) message = data.error;
    } catch {
      // Ignore parse error
    }
    throw new ApiError(response.status, message);
  }
  return response.blob();
}

export function postSocialLiveInput(
  payload: SocialLiveInputPayload,
): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/api/social/live/input', payload);
}

export function openSocialLiveFullWindow(): Promise<{ ok: boolean; headless?: boolean }> {
  return api.post<{ ok: boolean; headless?: boolean }>('/api/social/live/open_window', {});
}
