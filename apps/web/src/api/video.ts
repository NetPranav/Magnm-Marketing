import { ApiError, api } from '../api';
import type { JobRecord } from '../types';

export interface VoiceInfo {
  id: string;
  label: string;
  lang: string;
}

export interface VoiceStatus {
  installed: boolean;
  missing: string[];
  dir: string;
  size_mb: number;
  library: boolean;
  voices: VoiceInfo[];
  default_voice: string;
  default_speed: number;
  min_speed: number;
  max_speed: number;
}

export interface VideoSettings {
  voice: string;
  speed: number;
  captions: boolean;
  flux: boolean;
}

export interface VideoStatus {
  voice: VoiceStatus;
  settings: VideoSettings;
  ffmpeg: boolean;
  browser: boolean;
  rendering: string[];
}

export type RenderState = 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface RenderStatus {
  content_id: string;
  state: RenderState;
  percent?: number;
  message?: string;
  error?: string | null;
  error_code?: string | null;
  job_id?: string;
}

/** A video problem the engine explained in plain words, with the code that decides which button to offer. */
export interface VideoProblem {
  message: string;
  code: string;
}

/** Turn a failed request into a plain message and code (no_voice shows the Download voice button). */
export function videoProblem(err: unknown): VideoProblem {
  if (err instanceof ApiError) {
    const data = err.data as { code?: unknown } | undefined;
    return { message: err.message, code: typeof data?.code === 'string' ? data.code : '' };
  }
  return { message: err instanceof Error ? err.message : 'Something went wrong. Try again.', code: '' };
}

/** Voice, settings, ffmpeg and browser readiness for Settings > Video. */
export function getVideoStatus(): Promise<VideoStatus> {
  return api.get<VideoStatus>('/api/video/status');
}

/** Save voice, speed, captions or FLUX illustrations. */
export function saveVideoSettings(changes: Partial<VideoSettings>): Promise<{ settings: VideoSettings }> {
  return api.post<{ settings: VideoSettings }>('/api/video/settings', changes);
}

/** Download the local voice (about 120 MB). Returns the job to follow, or installed: true if it is already there. */
export function downloadVoice(): Promise<{ success: boolean; installed: boolean; job?: JobRecord }> {
  return api.post<{ success: boolean; installed: boolean; job?: JobRecord }>('/api/video/voice/download', {});
}

/** URL of a short audio sample of a voice. */
export function voiceSampleUrl(voice: string, speed: number): string {
  return `/api/video/voice/sample?voice=${encodeURIComponent(voice)}&speed=${encodeURIComponent(String(speed))}`;
}

/** Fetch a short audio sample of a voice for the Play sample button. A failure is explained in plain words. */
export async function fetchVoiceSample(voice: string, speed: number): Promise<Blob> {
  const response = await fetch(voiceSampleUrl(voice, speed), { credentials: 'same-origin' });
  if (!response.ok) {
    let data: unknown;
    let message = 'The sample could not be played.';
    try {
      data = await response.json();
      const text = (data as { error?: unknown })?.error;
      if (typeof text === 'string' && text.trim()) message = text;
    } catch {
      // Not JSON: keep the plain fallback message.
    }
    throw new ApiError(response.status, message, data);
  }
  return response.blob();
}

/** The state of the latest render of a script. */
export function getRenderStatus(contentId: string): Promise<RenderStatus> {
  return api.get<RenderStatus>(`/api/video/render/${encodeURIComponent(contentId)}`);
}

/** Queue a render of a script (the laptop renders one video at a time). */
export function startRender(
  contentId: string,
  options: { voice?: boolean; aspects?: string[] } = {},
): Promise<{ success: boolean; job: JobRecord; status: RenderStatus }> {
  return api.post<{ success: boolean; job: JobRecord; status: RenderStatus }>('/api/video/render', {
    content_id: contentId,
    voice: options.voice ?? true,
    ...(options.aspects ? { aspects: options.aspects } : {}),
  });
}

/** Cancel a queued or running render. */
export function cancelRender(contentId: string): Promise<{ success: boolean; status: RenderStatus }> {
  return api.post<{ success: boolean; status: RenderStatus }>('/api/video/render/cancel', { content_id: contentId });
}

/** Wait helper: poll a job until it is done, failed or cancelled. */
export async function waitForJob(
  jobId: string,
  onTick: (job: JobRecord) => void,
  options: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<JobRecord | null> {
  const intervalMs = options.intervalMs ?? 1000;
  const deadline = Date.now() + (options.timeoutMs ?? 15 * 60_000);
  for (;;) {
    const jobs = await api.get<JobRecord[]>('/api/jobs?limit=20');
    const job = jobs.find((j) => j.id === jobId);
    if (job) {
      onTick(job);
      if (job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') return job;
    }
    if (Date.now() >= deadline) return job ?? null;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
