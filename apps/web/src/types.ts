export interface HealthInfo {
  ok: boolean;
  service?: string;
  version?: string;
  data_root?: string;
}

export interface SafeEnvStatus {
  has_env_file?: boolean;
  default_provider?: string;
  nvidia_nim_base_url?: string;
  nvidia_nim_global?: boolean;
  nvidia_nim_fallback_model?: string;
  nvidia_nim_roles_configured?: number;
  team_ai?: boolean;
  openai?: boolean;
  anthropic?: boolean;
  gemini?: boolean;
  groq?: boolean;
  ollama_url?: string;
  chatgpt_cli?: boolean;
}

export interface RoleModelEntry {
  role_id: string;
  name?: string;
  code?: string;
  model_env?: string;
  key_env?: string;
  model?: string;
  default_model?: string;
  primary?: string;
  backup?: string | null;
  warnings?: string[];
  tier?: string;
  timeout_s?: number;
  max_tokens?: number;
  specialization?: string;
  has_key?: boolean;
  key_source?: string;
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
  models_matrix?: RoleModelEntry[];
  nim_catalog_suggestions?: string[];
  crisis_playbook?: string;
  product_facts?: string;
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

export interface ContentFileEntry {
  kind?: string;
  path: string;
  alt_text?: string;
  verdict?: string;
  review_id?: string;
  notes?: string;
}

export interface ReviewVersionSummary {
  number: number;
  version: number;
  created_by?: string | null;
  notes?: string | null;
  created_at?: string | null;
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
  version?: number;
  created_at: string;
  updated_at: string;
  posted_url?: string | null;
  posted_at?: string | null;
  body?: string;
  notes?: string | null;
  files?: ContentFileEntry[];
  versions?: ReviewVersionSummary[];
  open_comments_count?: number;
  warnings_count?: number;
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

export interface AssetRecord {
  id: string;
  name: string;
  saved_filename?: string;
  relative_path?: string;
  mime_type?: string;
  file_group?: string;
  size?: string;
  size_bytes?: number;
  category?: string;
  description?: string;
  tags?: string[];
  uploaded_at?: string;
}

export interface ProductRule {
  id: string;
  name: string;
  status: string;
  show: string;
  facts_file?: string | null;
  blocked_terms?: string[];
  confidential?: boolean;
  notes?: string;
}

export interface ProductRulesConfig {
  version: number;
  show_levels: Record<string, string>;
  products: ProductRule[];
}

export interface RepoCommit {
  sha?: string;
  hash?: string;
  date?: string;
  author?: string;
  message: string;
  is_merge?: boolean;
}

export interface RepoRecord {
  id: string;
  name: string;
  product_id?: string;
  kind: string;
  confidential?: boolean;
  status?: string;
  local_path?: string;
  github?: string;
  branch?: string;
  version?: string;
  stars?: number | null;
  marketing_angle?: string;
  last_sync?: string | null;
  recent_commits?: RepoCommit[];
  rate_limited?: boolean;
  rate_limit_message?: string | null;
}

export interface ShippedItem {
  repo_id: string;
  kind: string;
  title: string;
  url?: string | null;
  date: string;
}

export interface KeyPoolEntry {
  label: string;
  healthy: boolean;
  cooling_down_s: number;
  disabled_reason?: string | null;
  requests_last_min: number;
  ok: number;
  errors: number;
}

export interface SetupCheckItem {
  id: string;
  label: string;
  status: string;
  detail: string;
  fix?: string;
}

export interface SetupCheckResult {
  checks: SetupCheckItem[];
  ok: boolean;
}

export interface BakeoffModelStat {
  model: string;
  count: number;
  median_latency_ms: number;
  auto_review_warns: number;
  warn_briefs?: number;
  errors: number;
}

export interface BakeoffResult {
  timestamp?: string;
  role?: string;
  models?: string[];
  summary?: Record<string, BakeoffModelStat>;
  briefs?: Array<{ id: string; title?: string; platform?: string; type?: string }>;
  outputs?: Record<
    string,
    Array<{
      label: string;
      latency_ms?: number;
      chars?: number;
      error?: string | null;
      auto_review?: { verdict?: string; findings?: unknown[] };
    }>
  >;
}

export interface CheckTextWarning {
  product?: string;
  severity: string;
  message: string;
}

export interface ReviewVersionDetail {
  content_id: string;
  version: number;
  number: number;
  body: string;
  notes?: string | null;
  created_by?: string | null;
  created_at?: string | null;
}

export interface ReviewDiffOp {
  op: 'equal' | 'insert' | 'delete';
  text: string;
}

export interface CodingAgentInfo {
  id: string;
  name: string;
  command: string;
  vendor?: string | null;
  installed: boolean;
  path?: string | null;
  version?: string | null;
  enabled?: boolean;
  confined?: boolean;
  reason?: string | null;
  acp?: boolean;
  install_command?: string;
}

export interface McpClientConfig {
  id: string;
  name: string;
  format: 'cli' | 'json' | 'toml' | string;
  config_hint: string;
  snippet: string;
}

export interface McpSettingsInfo {
  writes_enabled: boolean;
  calls_today: number;
  mcp_server_path: string;
  data_dir: string;
  clients: McpClientConfig[];
}

export interface CodingOutFile {
  name: string;
  size: number;
}

export interface CodingRunRecord {
  id: string;
  engine: string;
  mode: 'read_only' | 'write_out' | string;
  prompt: string;
  repo_ids?: string[];
  status: 'awaiting_approval' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | string;
  workspace?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  summary?: string | null;
  log_path?: string | null;
  out_files?: CodingOutFile[];
  error?: string | null;
  transport?: string | null;
  log_tail?: string;
  approval_hash?: string | null;
  approved_at?: string | null;
  approval_consumed?: boolean;
  confidential?: boolean;
  requested_by?: string | null;
  session_id?: string | null;
}

export interface CodingRunLogResponse {
  run_id: string;
  content: string;
  from: number;
  next_offset: number;
  status: string;
}

export interface ReviewDiffResponse {
  content_id: string;
  from_version: number;
  to_version: number;
  ops: ReviewDiffOp[];
}

export interface ReviewCommentAnchor {
  start?: number;
  end?: number;
  quote?: string;
  orphaned?: boolean;
  file?: string;
  x?: number;
  y?: number;
  t?: number;
  ai_action?: string;
}

export interface ReviewComment {
  id: string;
  content_id: string;
  version: number;
  kind: 'text' | 'image' | 'video' | 'general';
  anchor: ReviewCommentAnchor;
  body: string;
  author: string;
  parent_id?: string | null;
  status: 'open' | 'resolved' | 'applied';
  mentions?: string[];
  created_at: string;
  updated_at?: string;
  replies?: ReviewComment[];
  job_id?: string;
  job_ids?: string[];
}

export interface ReviewSuggestion {
  id: string;
  content_id: string;
  base_version: number;
  start: number;
  end: number;
  original: string;
  replacement: string;
  author: string;
  reason?: string | null;
  status: string;
  created_at: string;
  decided_at?: string | null;
}

export interface ReviewChecklistItem {
  id?: string;
  content_id?: string;
  item_key: string;
  label: string;
  source: 'auto' | 'platform' | 'manual';
  checked: boolean;
  updated_at?: string;
}

export interface ReviewFinding {
  code?: string;
  product?: string;
  severity: string;
  message: string;
}

export interface PlatformLimits {
  platform: string;
  max_chars?: number;
  hook_chars?: number;
  min_hashtags?: number;
  max_hashtags?: number;
  threads_numbered?: boolean;
  alt_text_required?: boolean;
  title_max_chars?: number;
  description_hook_chars?: number;
  min_words?: number;
  max_words?: number;
  requires_headings?: boolean;
  requires_tldr?: boolean;
}

export interface PlatformCheckResult {
  platform: string;
  limits: PlatformLimits;
  warnings: ReviewFinding[];
  ok: boolean;
  char_count: number;
  word_count: number;
  hashtag_count: number;
  see_more_cutoff?: string | null;
}

export interface ReviewPayload {
  item: ContentItem;
  body: string;
  current_version: number;
  versions: ReviewVersionSummary[];
  comments: ReviewComment[];
  suggestions: ReviewSuggestion[];
  checklist: ReviewChecklistItem[];
  warnings: ReviewFinding[];
  auto_review?: {
    verdict?: string;
    findings?: ReviewFinding[];
  };
  platform_check: PlatformCheckResult;
  product?: ProductRule | null;
  confidential?: boolean;
}

export interface PostingKitResult {
  content_id: string;
  path: string;
  text: string;
  files: string[];
  attachments?: ContentFileEntry[];
}

export interface ReminderRecord {
  id: string;
  title: string;
  note: string;
  due_at: string;
  kind: 'custom' | 'results_due' | 'post_due' | string;
  content_id?: string | null;
  created_by: string;
  created_at: string;
  done_at?: string | null;
  snoozed_until?: string | null;
  fired_at?: string | null;
}

export interface MetricProposal {
  id: string;
  content_id?: string | null;
  platform?: string | null;
  metrics: Record<string, number>;
  evidence: Record<string, string>;
  image_path?: string | null;
  status: 'pending' | 'saved' | 'discarded' | string;
  created_at: string;
  decided_at?: string | null;
}

export interface MetricRecord {
  id: string;
  content_id?: string | null;
  platform: string;
  metrics: Record<string, number>;
  source: 'screenshot' | 'manual' | string;
  captured_at: string;
  created_by?: string | null;
  created_at: string;
}

export interface WeeklySummaryPostHighlight {
  content_id: string;
  metric_id?: string | null;
  title: string;
  platform: string;
  posted_at?: string | null;
  posted_url?: string;
  metrics?: Record<string, number>;
  engagement?: number;
  views?: number | null;
  score?: number;
  score_unit?: 'per_view' | 'engagement' | string;
}

export interface WeeklySummaryMissingResult {
  content_id: string;
  title: string;
  platform: string;
  posted_at?: string | null;
  posted_url?: string;
}

export interface WeeklySummaryPlatformRow {
  platform: string;
  posts_count: number;
  with_results_count: number;
  totals: Record<string, number>;
  averages: Record<string, number>;
  prev_posts_count: number;
  prev_totals: Record<string, number>;
  wow_change: Record<string, number | 'n/a'>;
}

export interface WeeklySummaryHighlights {
  platforms?: Record<string, WeeklySummaryPlatformRow>;
  platform_rows?: WeeklySummaryPlatformRow[];
  overall?: Omit<WeeklySummaryPlatformRow, 'platform'>;
  top_post?: WeeklySummaryPostHighlight | null;
  bottom_post?: WeeklySummaryPostHighlight | null;
  missing_results?: WeeklySummaryMissingResult[];
  counts?: Record<string, number>;
}

export interface WeeklySummaryData {
  week_start: string;
  week_end: string;
  week_label?: string;
  posts?: Array<{
    content_id: string;
    title: string;
    platform: string;
    posted_at?: string;
    posted_url?: string;
  }>;
  drafts_created?: Array<{ content_id: string; title: string }>;
  research_notes?: Array<{ id: string; title: string }>;
  reminders?: { done?: unknown[]; missed?: unknown[] };
}

export interface WeeklySummaryRecord {
  id: string;
  week_start: string;
  created_at: string;
  data: WeeklySummaryData;
  highlights: WeeklySummaryHighlights;
  narrative: string;
  status: 'ready' | 'tables_only' | 'narrative_skipped' | string;
}

export interface LearningEvidence {
  content_id: string;
  metric_id?: string | null;
  title?: string;
  platform?: string;
  metrics?: Record<string, number>;
}

export interface LearningRecord {
  id: string;
  text: string;
  evidence: LearningEvidence[];
  applies_to: string[];
  status: 'pending' | 'approved' | 'rejected' | 'retired' | string;
  source_summary_id?: string | null;
  created_at: string;
  decided_at?: string | null;
}

export type SocialPlatform = 'instagram' | 'x' | 'linkedin';
export type SocialRecipe = 'profile_posts' | 'search' | 'post_replies' | 'trending';

export interface SocialPlatformStatus {
  platform: SocialPlatform;
  enabled: boolean;
  acknowledged_at?: string | null;
  has_session: boolean;
  paused: boolean;
  paused_until?: string | null;
  pause_reason?: string | null;
  pages_today: number;
  daily_limit: number;
  challenges_24h: number;
}

export interface SocialRunResult {
  platform?: string;
  recipe?: string;
  url?: string;
  text?: string;
  pages_used?: number;
  untrusted?: boolean;
  note?: string;
  fetched_at?: string;
  ai_summary?: string | null;
  ai_summary_model?: string | null;
  ai_summary_untrusted?: boolean;
  ai_summarised_at?: string | null;
}

export interface SocialRunRecord {
  id: string;
  platform: SocialPlatform | string;
  recipe: SocialRecipe | string;
  argument: string;
  status:
    | 'awaiting_start'
    | 'running'
    | 'waiting_user'
    | 'done'
    | 'incomplete'
    | 'cancelled'
    | 'failed'
    | string;
  approval_hash?: string | null;
  approved_at?: string | null;
  approval_consumed?: boolean;
  requested_by?: string | null;
  session_id?: string | null;
  pages_used?: number;
  challenge_kind?: string | null;
  challenge_detected_at?: string | null;
  challenge_notif_id?: string | null;
  result?: SocialRunResult | null;
  summary?: string | null;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string;
}

export interface SocialStatusInfo {
  platforms: Record<SocialPlatform, SocialPlatformStatus>;
  active_run: SocialRunRecord | null;
  login: {
    active: boolean;
    platform: SocialPlatform | string | null;
    started_at?: string | null;
  };
  acknowledgement_summary: string;
  limits: {
    pages_per_run: number;
    pages_per_day: number;
    nav_cooldown_s: number;
    active_time_s: number;
    challenge_timeout_s: number;
  };
}
