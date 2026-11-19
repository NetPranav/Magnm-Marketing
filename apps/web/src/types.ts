export interface HealthInfo {
  ok: boolean;
  service?: string;
  version?: string;
}

export interface SafeEnvStatus {
  default_provider?: string;
  nvidia_nim_global?: boolean;
  nvidia_nim_roles_configured?: number;
  openai?: boolean;
  anthropic?: boolean;
  gemini?: boolean;
  groq?: boolean;
}

export interface EngineStatusInfo {
  status?: string;
  env_status?: SafeEnvStatus;
  models_matrix?: Array<{ role?: string; has_key?: boolean }>;
  current_job?: {
    running?: boolean;
    job_id?: string | null;
    job_name?: string | null;
    status?: string;
  };
}

export interface JobRecord {
  id: string;
  name: string;
  status: string;
  progress: number;
  message: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  actor?: string | null;
}
