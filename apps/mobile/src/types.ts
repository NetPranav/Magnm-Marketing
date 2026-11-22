export type MobileTabId = 'today' | 'drafts' | 'pitch' | 'chat' | 'more';

export type QueueItemKind = 'draft' | 'note' | 'idea';

export interface PairingConfig {
  host: string;
  port: number;
  deviceId: string;
  deviceName: string;
  ownerUid: string;
  pairedAt: string;
}

export interface ParsedQrPayload {
  v: 1;
  host: string;
  port: number;
  code: string;
}

export interface AuthSession {
  access_token: string;
  refresh_token: string;
  expires_at: number;
  user_id: string;
  email: string;
}

export interface AccountProfile {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  role: 'admin' | 'member';
  active: boolean;
}

export interface ProductRuleEntry {
  id: string;
  name: string;
  status: 'released' | 'in_development' | 'secret';
  show: 'everything' | 'text_only' | 'teasers_only' | 'nothing';
  facts_file?: string | null;
  blocked_terms?: string[];
  confidential?: boolean;
  notes?: string | null;
}

export interface ProductRulesConfig {
  version: number;
  show_levels: Record<string, string>;
  products: ProductRuleEntry[];
}

export interface LaptopStatus {
  ok: boolean;
  version?: string;
  running_agents: string[];
  running_jobs: string[];
  unread_notifications: number;
  device?: { id: string; name: string };
}

export interface DraftVersion {
  id?: string;
  version: number;
  number?: number;
  body?: string;
  notes?: string | null;
  created_by?: string | null;
  created_at?: string | null;
}

export interface DraftComment {
  id: string;
  body: string;
  author?: string | null;
  version?: number | null;
  parent_id?: string | null;
  status?: string | null;
  created_at?: string | null;
  confidential?: boolean;
  replies?: DraftComment[];
}

export type DraftFileEntry =
  | {
      kind?: string;
      path: string;
      alt_text?: string;
    }
  | string;

export interface DraftItem {
  id: string;
  type?: string | null;
  platform?: string | null;
  status?: string | null;
  title?: string | null;
  body?: string | null;
  notes?: string | null;
  posted_url?: string | null;
  posted_at?: string | null;
  current_version?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  confidential?: boolean;
  files?: DraftFileEntry[];
  versions?: DraftVersion[];
  comments?: DraftComment[];
  warnings?: Array<{ severity?: string; message?: string; product?: string } | string>;
}

export interface PitchProposalItem {
  id: string;
  name?: string;
  kind?: string;
  updated_at?: string;
  payload?: Record<string, unknown>;
  confidential?: boolean;
}

export interface ReminderItem {
  id: string;
  kind?: string | null;
  title: string;
  note?: string | null;
  due_at?: string | null;
  done_at?: string | null;
  snoozed_until?: string | null;
  created_at?: string | null;
  confidential?: boolean;
}

export interface PhoneNotification {
  id: string;
  kind?: string | null;
  title: string;
  body?: string | null;
  route?: string | null;
  created_at?: string | null;
  read_at?: string | null;
  confidential?: boolean;
}

export interface WeeklySummary {
  id: string;
  week_start?: string | null;
  status?: string | null;
  narrative?: string | null;
  data?: Record<string, unknown>;
  highlights?: Record<string, unknown>;
  created_at?: string | null;
  confidential?: boolean;
}

export interface MetricProposal {
  id: string;
  content_id?: string | null;
  platform?: string | null;
  status?: string | null;
  metrics?: Record<string, number | null>;
  evidence?: Record<string, string>;
  created_at?: string | null;
  decided_at?: string | null;
  confidential?: boolean;
}

export interface PhoneAgentInfo {
  id: string;
  name: string;
  code?: string;
  role?: string;
  description?: string;
  status?: string;
  message_count?: number;
}

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant' | 'system';
  agentId: string;
  text: string;
  mode: 'laptop' | 'phone-lite';
  ownerUid?: string | null;
  failed?: boolean;
  confidential?: boolean;
  waitingUser?: boolean;
  question?: string;
  options?: string[];
  createdAt: string;
}

export interface MirrorRecord {
  id: string;
  owner_id: string;
  kind: string;
  local_id: string;
  parent_local_id?: string | null;
  title?: string | null;
  data: Record<string, unknown>;
  local_updated_at: string;
  synced_at?: string;
  cached_at: number;
}

export interface QueuedInboxItem {
  client_id: string;
  kind: QueueItemKind;
  title: string;
  body: string;
  platform?: string;
  url?: string;
  created_at: string;
  owner_uid: string;
  laptop_device_id: string;
  last_error?: string | null;
}

export interface InboxAckItem {
  client_id: string;
  kind: string;
  id: string;
  deduplicated?: boolean;
}
