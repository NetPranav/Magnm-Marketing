-- Activity events, device status heartbeats, and workspace mirror records.

-- Append-only timeline of member and agent activity events.
create table public.activity_events (
  id bigint generated always as identity primary key,
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  ts timestamptz not null default now(),
  kind text not null check (char_length(kind) between 1 and 64),
  agent_id text,
  summary text check (summary is null or char_length(summary) <= 500),
  data jsonb not null default '{}'::jsonb check (octet_length(data::text) <= 32768)
);

create index idx_activity_events_owner_ts on public.activity_events (owner_id, ts desc);

alter table public.activity_events enable row level security;

-- RLS policies for activity_events: members insert/read their own; admins read all; no update or delete.
create policy activity_events_select_own_or_admin
  on public.activity_events
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy activity_events_insert_own
  on public.activity_events
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Live heartbeat per member device (desktop or android).
create table public.device_status (
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  device_id text not null check (char_length(device_id) between 1 and 128),
  device_kind text not null check (device_kind in ('desktop', 'android')),
  status text not null check (status in ('online', 'idle', 'working', 'offline')),
  running_agents jsonb not null default '[]'::jsonb,
  app_version text,
  last_seen_at timestamptz not null default now(),
  primary key (owner_id, device_id)
);

alter table public.device_status enable row level security;

-- RLS policies for device_status: members manage their own rows; admins read all.
create policy device_status_select_own_or_admin
  on public.device_status
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy device_status_insert_own
  on public.device_status
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy device_status_update_own
  on public.device_status
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy device_status_delete_own
  on public.device_status
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Read-only workspace mirror of each member's local work for the admin.
create table public.mirror_records (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  kind text not null check (
    kind in (
      'chat_turn',
      'content_item',
      'content_version',
      'research_note',
      'review_comment',
      'metric',
      'posted_link',
      'brief',
      'task'
    )
  ),
  local_id text not null check (char_length(local_id) between 1 and 200),
  parent_local_id text,
  title text check (title is null or char_length(title) <= 300),
  data jsonb not null check (octet_length(data::text) <= 262144),
  local_updated_at timestamptz not null,
  synced_at timestamptz not null default now(),
  deleted_at timestamptz,
  unique (owner_id, kind, local_id)
);

create index idx_mirror_records_owner_kind_updated
  on public.mirror_records (owner_id, kind, local_updated_at desc);

alter table public.mirror_records enable row level security;

-- Prevent changing owner_id on mirror_records updates.
create or replace function public.guard_mirror_record_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.owner_id is distinct from old.owner_id then
    raise exception 'Cannot change owner_id of a mirror record';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_mirror_record_owner() from public, anon;

create trigger trg_mirror_records_guard_owner
  before update on public.mirror_records
  for each row
  execute function public.guard_mirror_record_owner();

-- RLS policies for mirror_records: owner manages their own rows; admin reads all but cannot write others' rows.
create policy mirror_records_select_own_or_admin
  on public.mirror_records
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy mirror_records_insert_own
  on public.mirror_records
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy mirror_records_update_own
  on public.mirror_records
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy mirror_records_delete_own
  on public.mirror_records
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Privileges: anon gets nothing.
revoke all on table public.activity_events from public, anon;
revoke all on table public.device_status from public, anon;
revoke all on table public.mirror_records from public, anon;
revoke all on sequence public.activity_events_id_seq from public, anon;

grant select, insert on table public.activity_events to authenticated;
grant usage, select on sequence public.activity_events_id_seq to authenticated, service_role;
grant select, insert, update, delete on table public.device_status to authenticated;
grant select, insert, update, delete on table public.mirror_records to authenticated;

grant all on table public.activity_events to service_role;
grant all on table public.device_status to service_role;
grant all on table public.mirror_records to service_role;
