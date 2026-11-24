import { api } from '../api';

export interface PipelineStep {
  name: string;
  status: string;
  detail?: string;
}

export interface PipelineRun {
  id: string;
  started_at: string;
  finished_at?: string | null;
  trigger: 'manual' | 'schedule';
  status: string;
  repo: { id: string; name: string; scope: 'personal' | 'team'; confidential: boolean } | null;
  why: string;
  steps: PipelineStep[];
  runs: Array<{ id: string; kind: string; status: string }>;
  error?: string | null;
}

export interface MissedRun {
  id: string;
  name: string;
  due_at: string;
}

export interface PipelineSettings {
  kinds: string[];
  platforms: string[];
  skip_recent_days: number;
}

export interface AutomationState {
  paused: boolean;
  paused_since: string | null;
  paused_by: string | null;
  missed: MissedRun[];
  settings: PipelineSettings;
  history: PipelineRun[];
  running: boolean;
  allowed_kinds: string[];
  allowed_platforms: string[];
  schedule: { id: string; enabled: boolean; cron?: string | null; interval_hours?: number | null; last_run?: string | null } | null;
  local_ai: boolean;
}

/** Everything the automatic-work card needs: the switch, missed runs, the pipeline settings and its history. */
export function getAutomation(): Promise<AutomationState> {
  return api.get<AutomationState>('/api/automation');
}

/** Turn "Pause automatic work" on or off. Resuming starts nothing. */
export function setAutomationPaused(paused: boolean): Promise<AutomationState> {
  return api.post<AutomationState>('/api/automation/pause', { paused });
}

export function saveAutomationSettings(settings: Partial<PipelineSettings>): Promise<{ settings: PipelineSettings }> {
  return api.post<{ settings: PipelineSettings }>('/api/automation/settings', settings);
}

/** Queue one pipeline run now (a manual action: it also works while automatic work is paused). */
export function runAutomationNow(): Promise<{ success: boolean }> {
  return api.post<{ success: boolean }>('/api/automation/run', {});
}

export function actOnMissed(id: string, action: 'run' | 'dismiss'): Promise<{ missed: MissedRun[] }> {
  return api.post<{ missed: MissedRun[] }>('/api/automation/missed', { id, action });
}
