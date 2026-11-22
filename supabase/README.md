# Magnm Studio — Supabase Schema & Privacy Rules

This directory holds the Postgres 17 migrations and row-level security (RLS) privacy tests for **Magnm Studio** (Phase 4).

## Environments

- **`magnm-dev` (the live project for now):** since 2026-11-21 it holds the team's real data (decided by the team; no client data yet). Never reset it: apply only forward migrations with `supabase/dev.sh push`. SQL tests run inside transactions that roll back. Any live test data must be clearly labelled and deleted right after the test.
- **Production:** a separate Supabase project that holds real team data and is **never reset**. Only forward migrations (`supabase db push`) are ever applied to production.

## Tables and Views (`public`)

Every table in `public` has RLS enabled, and the `anon` role has zero privileges on any table or view. Deactivated accounts (`profiles.active = false`) lose all read and write access immediately.

| Object | Purpose | Who can read | Who can write |
|---|---|---|---|
| `profiles` | One row per team member (`id` linked to `auth.users`), lower-case `email`, `display_name`, `avatar_url`, `role` (`admin` or `member`), `active` flag, timestamps. Created automatically by a trigger on `auth.users` insert when an open invite exists. | Members read their own row; admins read all rows. | Members may update only their own `display_name`, `avatar_url`, and `last_seen_at`. Admins may change `role` and `active`, except nobody can demote or deactivate the last active admin. |
| `team_directory` (view) | Safe teammate directory exposing only `id`, `display_name`, and `avatar_url` of active members (never emails). | Any active member or admin. | Read-only view. |
| `invites` | Allowlist for Google sign-in (`email`, `role`, `invited_by`, `created_at`, `accepted_at`, `revoked_at`). Sign-up without an open, unrevoked invite raises an exception and aborts user creation. | Admins only. | Admins create, revoke, and delete invites. The `auth.users` trigger stamps `accepted_at`. |
| `activity_events` | Append-only timeline of member and agent events (`owner_id`, `ts`, `kind`, `agent_id`, `summary`, `data` up to 32 KB). | Members read their own rows; admins read all rows. | Members insert their own rows. No updates or deletes (except cascade on user delete). |
| `device_status` | Live status heartbeat per device (`(owner_id, device_id)` primary key, `device_kind` in `desktop`/`android`, `status` in `online`/`idle`/`working`/`offline`, `running_agents`, `app_version`, `last_seen_at`). | Members read their own rows; admins read all rows. | Members insert, update, and delete their own rows. |
| `mirror_records` | Read-only workspace mirror for the admin (`chat_session`, `team_message`, `chat_turn`, `content_item`, `content_version`, `research_note`, `review_comment`, `metric`, `posted_link`, `brief`, `task`; `data` up to 256 KB, engine caps at 200 KB). | Members read their own rows; admins read all rows. | Members insert, update, and delete only their own rows (and cannot change `owner_id`). Admins cannot insert, update, or delete other members' rows. |
| `shared_knowledge` | Team-wide documents (`brand_kit`, `product_rules`, `agent_instructions`, `templates`). | All active members and admins. | Admins only (`updated_by` and `updated_at` stamped by trigger). |
| `pitch_base_prices` | Base prices (`item_id`, `price >= 0`) for Magnm-Pitch services. | All active members and admins. | Admins only (`updated_by` and `updated_at` stamped by trigger). |
| `team_repos` | Global GitHub repositories an admin adds for the whole team (`id` stable slug, `github` strict `owner/repo` unique ignoring case, `name`, `product_id`, `branch`, `confidential`, `status`, `created_by`, `updated_by`, timestamps; every text column is length-capped and character-checked). Each member's engine pulls them on its normal sync cycle. | All active members and admins. | Admins only (stamped by trigger, audited as `team_repo.*`). |
| `published_proposals` | Published Pitch links (`share_id`, `owner_id`, `local_proposal_id`, `kind` in `proposal`/`final`/`default_quote`, `payload` up to 512 KB, `status` in `live`/`withdrawn`, `expires_at`). | Owner reads their own rows; admins read all rows. | Owner inserts, updates, and deletes their own rows. Clients read only through `public.get_published_proposal(share_id)`. |
| `quote_submissions` | Client quote responses (`id`, `share_id`, `owner_id` copied from the proposal, `choices` up to 64 KB, `contact` up to 8 KB, `created_at`, `seen_at`). | Proposal owner reads their own rows; admins read all rows. | Inserted only via `public.submit_quote(share_id, choices, contact)` (rate-limited to 20 per `share_id` per 24 hours). Proposal owner may update only `seen_at`. |
| `media_previews` | Metadata for compressed thumbnails, image previews, and video previews stored in the private `previews` bucket (`owner_id`, `local_source_id`, `source_kind` in `content_file`/`asset`, `parent_local_id`, `kind` in `thumbnail`/`image_preview`/`video_preview`, `storage_path` prefixed by `<owner_id>/`, `content_type`, `bytes`, dimensions/duration, `expires_at`, `deleted_at`). | Owner reads their own rows; admins read all rows. | Owner inserts, updates, and deletes their own rows (cannot change `owner_id`). Admins cannot modify other members' rows. |
| `ai_allowed_models` | Allowed NVIDIA NIM model IDs, `enabled` flag, and `max_tokens_cap` (`1..32768`), seeded from `config/models.json`. | All active members and admins. | Admins only. |
| `ai_limits` | Daily AI request/token caps (`daily_requests` default `200`, `daily_tokens` default `400000`). One `owner_id is null` team default row plus optional per-member override rows. | Active members read the team default and their own row; admins read all rows. | Admins only (default row cannot be deleted). |
| `ai_usage_daily` | Per-member UTC day counters (`requests`, `prompt_tokens`, `completion_tokens`, `rejected`). | Members read their own rows; admins read all rows. | Service-role functions only (`ai_check_and_count`, `ai_record_usage`); nobody writes directly. |
| `ai_key_health` | Per-key status, last OK timestamp, truncated error (`<= 300` chars), and `cooldown_until` keyed by masked `label`. | Admins only (and via `admin_ai_key_labels()`). | Service-role / admin functions only (`ai_report_key`, `admin_set_ai_keys`). |
| `audit_log` | Append-only audit log (`actor_id`, `action`, `target`, `data`, `created_at`) written by security-definer triggers on `invites`, `profiles`, `shared_knowledge`, `pitch_base_prices`, and `team_repos`, plus `admin_set_ai_keys`. | Admins only. | Triggers / security-definer RPCs only; nobody can insert directly, update, or delete rows. |

## Private Media Previews Storage (`previews` bucket)

- **Bucket:** `previews` (`public = false`, 15 MB per-object limit, `image/webp` and `video/mp4` only).
- **Storage policies (`storage.objects`):** Active members may `SELECT`, `INSERT`, `UPDATE`, and `DELETE` only objects whose first path segment is their own user ID (`(storage.foldername(name))[1] = auth.uid()::text`). Admins may `SELECT` all objects in `previews`. `anon` has zero access. Previews are read through short-lived signed URLs (`POST /storage/v1/object/sign/previews/...`).
- **What is previewed (`engine/media_previews.py`):** Non-confidential content draft media (`content_items.files_json`) and non-confidential uploaded assets (`assets_manager`) using `ffmpeg`/`ffprobe` (320 px WebP thumbnails, up to 1080 px WebP image previews without upscaling, and 480p H.264/AAC MP4 video previews capped at 30 s). Confidential sessions, confidential content items, and assets/content naming confidential products are skipped; if product rules cannot be read, nothing is uploaded.
- **60-day retention:** `video_preview` objects get `expires_at = now() + 60 days`. Once a day, the engine deletes its own expired video previews from Storage, stamps `deleted_at` on their `media_previews` rows, and prunes uploaded local cache files older than 7 days. `public.storage_usage()` (`security invoker`) reports `{bytes, files, by_kind}` over non-deleted rows visible to the caller.

## Client RPC Functions

Only two functions in `public` are executable by `anon` (as well as `authenticated`):

- `public.get_published_proposal(p_share_id text) returns jsonb` — returns the proposal `payload` and `kind` if the link exists, is `live`, and has not expired; returns `null` otherwise (without revealing whether a link was withdrawn or expired).
- `public.submit_quote(p_share_id text, p_choices jsonb, p_contact jsonb) returns uuid` — validates payload sizes and rate limit (max 20 submissions per `share_id` in 24 hours) for a `live`, unexpired proposal, copies `owner_id` from the proposal, inserts the row, and returns the new `id`.

## Shared AI Keys & `ai-proxy` Edge Function

Team NVIDIA NIM API keys are stored encrypted in **Supabase Vault** (`vault.secrets` with names `magnm_ai_key_<n>`) instead of being distributed to member machines.

- **Key management:**
  - `public.admin_set_ai_keys(p_keys text[])` — admin-only security-definer RPC. Validates each key (`^nvapi-[A-Za-z0-9_-]{20,200}$`), deduplicates up to 20 keys, replaces all `magnm_ai_key_<n>` Vault secrets, resets `ai_key_health`, and writes an `audit_log` row containing only the key count (never key values).
  - `public.admin_ai_key_labels()` — admin-only RPC returning masked labels (`key-<n> (...<last 4 chars>)`) joined with `ai_key_health`. Nobody (not even admins) can read raw keys back from the client API.
- **Service-role RPCs (revoked from `public`, `anon`, and `authenticated`):**
  - `public.ai_get_keys()` — returns `(label, api_key)` from `vault.decrypted_secrets` for `ai-proxy`.
  - `public.ai_check_and_count(p_owner uuid, p_estimated_tokens int)` — locks today's UTC `ai_usage_daily` row, checks per-member override or team default `ai_limits`, increments `requests` when allowed or `rejected` when over limit.
  - `public.ai_record_usage(p_owner uuid, p_prompt_tokens int, p_completion_tokens int)` — adds prompt and completion tokens to today's UTC usage row.
  - `public.ai_report_key(p_label text, p_status int, p_error text, p_cooldown_seconds int)` — upserts `ai_key_health`.
- **Edge Function (`supabase/functions/ai-proxy/`):**
  - Requires a valid user JWT (`verify_jwt` enabled) and an active `profiles` row (`active = true`).
  - Accepts `POST` OpenAI-compatible chat requests (`model`, `messages`, `max_tokens`, `temperature`, `top_p`, `stream`, `tools`, `tool_choice`, `stop`), enforces `ai_allowed_models` (`enabled` and `max_tokens_cap`), `<= 200` messages, `<= 200,000` text chars, and `<= 4 MB` total image `data:` URLs.
  - Checks daily quota (`ceil(chars/4) + max_tokens`), rotates through non-cooldown Vault keys (cached 60 s, up to 3 attempts per request, 120 s upstream timeout; cooldowns: 1 h on 401/403, `Retry-After` or 60 s on 429, 30 s on 5xx/timeouts), and proxies both JSON and SSE streaming (`stream_options: {include_usage: true}`) to `NIM_BASE_URL` (default `https://integrate.api.nvidia.com/v1`).
  - Deploy with: `supabase/dev.sh deploy ai-proxy` (optional Edge Function secret `NIM_BASE_URL` if overriding the NVIDIA NIM base URL).
- **How members use team keys:** When a member's laptop has no local NVIDIA NIM key for a role (and work is not marked confidential, which always uses local Ollama), `engine/ai_gateway.py` automatically routes non-streaming OpenAI-compatible calls through `{supabase_url}/functions/v1/ai-proxy` using the signed-in user's bearer token and logs `runs.provider = 'team'`. Laptops with their own NIM keys keep calling NIM directly. Admins manage shared keys, limits, usage, and allowed models in **Admin > AI keys**, and signed-in members see their mode and daily count in **Settings > Account**.

## Using `supabase/dev.sh`

`supabase/dev.sh` reads the dev database credentials from `~/.config/magnm/supabase-dev.env` (or `$DB_URL` in CI) and masks connection strings in output:

```bash
supabase/dev.sh push              # Apply new migrations in supabase/migrations/ to magnm-dev
supabase/dev.sh reset             # Refused: magnm-dev holds the team's real data (since 2026-11-21). Only forward migrations.
supabase/dev.sh test              # Run all SQL privacy test files in supabase/tests/*.sql
supabase/dev.sh sql FILE          # Execute a single SQL file against magnm-dev
supabase/dev.sh deploy ai-proxy   # Deploy the ai-proxy Edge Function to magnm-dev
```

## Bootstrapping the First Admin

Because sign-up is strictly invite-only and no seed file is used, insert the first admin invite using the service role (via the Supabase Dashboard SQL Editor or `supabase/dev.sh sql` on dev) before the admin signs in with Google for the first time:

```sql
insert into public.invites (email, role)
values ('your-admin-email@gmail.com', 'admin');
```

When that user signs in with Google, the `auth.users` trigger verifies the open invite, creates their `public.profiles` row with `role = 'admin'`, and marks `accepted_at` on the invite.

## Desktop Sign-In

The desktop engine listens on a random loopback port (`127.0.0.1:<port>`) and handles Google OAuth via PKCE at `GET /auth/callback`. In the Supabase Dashboard under Authentication > URL Configuration, add this pattern to **Redirect URLs**:

```
http://127.0.0.1:*/auth/callback
```

Public Supabase settings (`url` and `publishable_key`) live in `config/supabase.json` and may be overridden with `MAGNM_SUPABASE_URL` and `MAGNM_SUPABASE_PUBLISHABLE_KEY`.

## Workspace Sync Privacy Rules

While a team member is signed in, `engine/cloud_sync.py` performs a one-way background sync from the local SQLite database to Supabase:

- **What is mirrored (`mirror_records`):** `chat_session` (user/assistant text and short tool-use notes only; tool arguments and tool results are dropped), `team_message`, legacy `chat_turn`, `content_item`, `content_version`, `posted_link`, `research_note`, `review_comment` (without anchor geometry), `metric`, `brief` (workflow inputs/status without full `state_json`), and `task` (`jobs`).
- **Confidential work:** Confidential agent sessions (`confidential = 1`) and content items created inside them (plus their versions, posted links, review comments, and metrics) are mirrored only as metadata stubs (`"Confidential chat"` / `"Confidential item"`) with no message text or body.
- **Activity & device status:** Local `activity` rows and `sync_outbox` events flow to `activity_events`, and `device_status` reports `online` / `working` / `offline` for the local desktop device.
- **What is never mirrored:** API keys, `.env` values, key pool data, `agent_memory`, `search_cache`, `tool_calls`, `runs`, pitch proposals (handled separately), and file contents from disk.

## Pitch Client Links & Quote Requests (`engine/pitch_links.py`)

- **Publishing links:** When signed in, clicking **Copy link** publishes a sanitized payload (`proposal_id` removed, `contact` limited to `name` and `business`, confidential products blocked by `product_rules.json`) to `published_proposals` (`kind` mapped to `proposal`, `final`, or `default_quote`) and copies `{pitch_site_url}/p|q|f/<share_id>`. Withdrawing sets `status = 'withdrawn'`.
- **Auto-republish & incoming quote requests:** Saving a live proposal schedules `cloud_sync`, which republishes live links whose `updated_at` is newer than `published_at`. Each sync cycle also fetches unseen `quote_submissions`, stores them locally in `pitch_quote_requests` (computing totals locally and flagging client `total_shown` mismatches), publishes `pitch.quote_request` SSE events, logs activity, and patches `seen_at` only for stored IDs.

## Shared Team Knowledge (`engine/team_knowledge.py`)

- **What is synced:** `shared_knowledge` rows (`brand_kit`, `product_rules`, `agent_instructions`, `templates`) and `pitch_base_prices` (`item_id`, `price`). Files are validated for safe relative paths (`.md`/`.json` only, `agents/archive/` excluded, `<= 200 KB` per file, `<= 1 MB` per key) and `product_rules` are validated with `product_rules.validate_rules` before both publishing and applying.
- **Admin publish & member apply:** Admins publish via **Settings > Team knowledge** (and saving product rules or Pitch base prices queues background auto-publish in `cloud_sync`). During each sync cycle, active members automatically back up their local copy to `<DATA_ROOT>/state/knowledge_backups/<kind>/<UTC timestamp>/` (keeping the last 10 per kind), write newer remote versions atomically, clear caches, and emit `team_knowledge.updated` SSE events; admins are never overwritten automatically and apply remote updates explicitly. Active members see product rules and Pitch base prices read-only with HTTP 403 enforcement on local write routes.
