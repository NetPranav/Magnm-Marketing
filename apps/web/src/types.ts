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

export interface ScheduleRecord {
  id: string;
  name: string;
  command: string;
  description?: string;
  interval_hours?: number | null;
  cron?: string | null;
  enabled: boolean;
  last_run?: string | null;
  last_status?: string | null;
}

export interface EditorialPlanRow {
  slot: string;
  platforms: string;
  topic: string;
  status: string;
}

export interface ResearchFindingItem {
  source: string;
  title: string;
  url: string;
  created_at?: string;
  snippet?: string;
  engagement?: string;
  query?: string;
  scoring?: {
    score?: number;
    max_score?: number;
  };
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
  activity_log?: string[];
  schedules?: ScheduleRecord[];
  editorial_plan?: EditorialPlanRow[];
  research_findings?: {
    file?: string | null;
    items?: ResearchFindingItem[];
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

export interface PendingQuestion {
  question: string;
  options?: string[];
  tool_call_id?: string;
}

export interface AgentSession {
  id: string;
  agent_id: string;
  status: string;
  pending_question?: PendingQuestion | null;
  parent_session_id?: string | null;
  depth?: number;
  title?: string | null;
  result_text?: string | null;
  error?: string | null;
  steps?: number;
  model_used?: string | null;
  provider?: string | null;
  fallback_used?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowGate {
  id: string;
  run_id: string;
  step: number;
  question: string;
  options: string[];
  answer?: string | null;
  feedback?: string | null;
  status: string;
  created_at?: string;
  answered_at?: string | null;
}

export interface WorkflowRun {
  id: string;
  kind: string;
  status: string;
  current_step: number;
  inputs: Record<string, unknown>;
  state: Record<string, unknown>;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  steps?: Array<{ index: number; name: string; status: string }>;
  gates?: WorkflowGate[];
  content_ids?: string[];
}

export interface ContentItem {
  id: string;
  type: string;
  platform?: string | null;
  title: string;
  status: string;
  product_id?: string | null;
  created_by?: string | null;
  session_id?: string | null;
  current_version?: number;
  created_at: string;
  updated_at: string;
  posted_url?: string | null;
  posted_at?: string | null;
  body?: string;
  notes?: string | null;
}

export interface ProductFact {
  id: string;
  product_id: string;
  text: string;
  source_url?: string | null;
  source_kind: string;
  status: string;
  created_by?: string | null;
  created_at?: string;
}
