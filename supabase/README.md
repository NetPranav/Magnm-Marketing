# Magnm Studio — Supabase Schema & Privacy Rules

This directory holds the Postgres 17 migrations and row-level security (RLS) privacy tests for **Magnm Studio** (Phase 4).

## Environments

- **Development (`magnm-dev`):** a dedicated cloud Supabase project with no real user data. You can apply migrations, reset it, and run tests against it using `supabase/dev.sh`.
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
| `audit_log` | Append-only audit log (`actor_id`, `action`, `target`, `data`, `created_at`) written by security-definer triggers on `invites`, `profiles`, `shared_knowledge`, and `pitch_base_prices`. | Admins only. | Triggers only; nobody can insert directly, update, or delete rows. |

## Client RPC Functions

Only two functions in `public` are executable by `anon` (as well as `authenticated`):

- `public.get_published_proposal(p_share_id text) returns jsonb` — returns the proposal `payload` and `kind` if the link exists, is `live`, and has not expired; returns `null` otherwise (without revealing whether a link was withdrawn or expired).
- `public.submit_quote(p_share_id text, p_choices jsonb, p_contact jsonb) returns uuid` — validates payload sizes and rate limit (max 20 submissions per `share_id` in 24 hours) for a `live`, unexpired proposal, copies `owner_id` from the proposal, inserts the row, and returns the new `id`.

## Using `supabase/dev.sh`

`supabase/dev.sh` reads the dev database credentials from `~/.config/magnm/supabase-dev.env` (or `$DB_URL` in CI) and masks connection strings in output:

```bash
supabase/dev.sh push      # Apply new migrations in supabase/migrations/ to magnm-dev
supabase/dev.sh reset     # Wipe magnm-dev and re-apply all migrations cleanly
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
