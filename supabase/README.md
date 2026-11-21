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
| `published_proposals` | Published Pitch links (`share_id`, `owner_id`, `local_proposal_id`, `kind` in `proposal`/`final`/`default_quote`, `payload` up to 512 KB, `status` in `live`/`withdrawn`, `expires_at`). | Owner reads their own rows; admins read all rows. | Owner inserts, updates, and deletes their own rows. Clients read only through `public.get_published_proposal(share_id)`. |
| `quote_submissions` | Client quote responses (`id`, `share_id`, `owner_id` copied from the proposal, `choices` up to 64 KB, `contact` up to 8 KB, `created_at`, `seen_at`). | Proposal owner reads their own rows; admins read all rows. | Inserted only via `public.submit_quote(share_id, choices, contact)` (rate-limited to 20 per `share_id` per 24 hours). Proposal owner may update only `seen_at`. |
| `media_previews` | Metadata for compressed thumbnails, image previews, and video previews stored in the private `previews` bucket (`owner_id`, `local_source_id`, `source_kind` in `content_file`/`asset`, `parent_local_id`, `kind` in `thumbnail`/`image_preview`/`video_preview`, `storage_path` prefixed by `<owner_id>/`, `content_type`, `bytes`, dimensions/duration, `expires_at`, `deleted_at`). | Owner reads their own rows; admins read all rows. | Owner inserts, updates, and deletes their own rows (cannot change `owner_id`). Admins cannot modify other members' rows. |
| `audit_log` | Append-only audit log (`actor_id`, `action`, `target`, `data`, `created_at`) written by security-definer triggers on `invites`, `profiles`, `shared_knowledge`, and `pitch_base_prices`. | Admins only. | Triggers only; nobody can insert directly, update, or delete rows. |

## Private Media Previews Storage (`previews` bucket)

- **Bucket:** `previews` (`public = false`, 15 MB per-object limit, `image/webp` and `video/mp4` only).
- **Storage policies (`storage.objects`):** Active members may `SELECT`, `INSERT`, `UPDATE`, and `DELETE` only objects whose first path segment is their own user ID (`(storage.foldername(name))[1] = auth.uid()::text`). Admins may `SELECT` all objects in `previews`. `anon` has zero access. Previews are read through short-lived signed URLs (`POST /storage/v1/object/sign/previews/...`).
- **What is previewed (`engine/media_previews.py`):** Non-confidential content draft media (`content_items.files_json`) and non-confidential uploaded assets (`assets_manager`) using `ffmpeg`/`ffprobe` (320 px WebP thumbnails, up to 1080 px WebP image previews without upscaling, and 480p H.264/AAC MP4 video previews capped at 30 s). Confidential sessions, confidential content items, and assets/content naming confidential products are skipped; if product rules cannot be read, nothing is uploaded.
- **60-day retention:** `video_preview` objects get `expires_at = now() + 60 days`. Once a day, the engine deletes its own expired video previews from Storage, stamps `deleted_at` on their `media_previews` rows, and prunes uploaded local cache files older than 7 days. `public.storage_usage()` (`security invoker`) reports `{bytes, files, by_kind}` over non-deleted rows visible to the caller.

## Client RPC Functions

Only two functions in `public` are executable by `anon` (as well as `authenticated`):

- `public.get_published_proposal(p_share_id text) returns jsonb` — returns the proposal `payload` and `kind` if the link exists, is `live`, and has not expired; returns `null` otherwise (without revealing whether a link was withdrawn or expired).
- `public.submit_quote(p_share_id text, p_choices jsonb, p_contact jsonb) returns uuid` — validates payload sizes and rate limit (max 20 submissions per `share_id` in 24 hours) for a `live`, unexpired proposal, copies `owner_id` from the proposal, inserts the row, and returns the new `id`.

## Using `supabase/dev.sh`

`supabase/dev.sh` reads the dev database credentials from `~/.config/magnm/supabase-dev.env` (or `$DB_URL` in CI) and masks connection strings in output:

```bash
supabase/dev.sh push      # Apply new migrations in supabase/migrations/ to magnm-dev
supabase/dev.sh reset     # Refused: magnm-dev holds the team's real data (since 2026-11-21). Only forward migrations.
supabase/dev.sh test      # Run all SQL privacy test files in supabase/tests/*.sql
supabase/dev.sh sql FILE  # Execute a single SQL file against magnm-dev
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
