-- Get on phone: full-quality copies of finished draft files, kept 14 days in a private bucket, plus the agent_task
-- phone action kind (a request for an agent that needs the laptop's tools).
-- Forward-only: nothing here changes or removes existing data. An engine that does not know a new part ignores it.

-- 1. Private bucket. Only inert media and PDF types; 50 MB per object; no admin or public access of any kind.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'phone-files', 'phone-files', false, 52428800,
  array['image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm', 'application/pdf']
)
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- 2. One row per draft file that has a copy in the bucket. The laptop writes it; the phone reads it to know what it
-- can fetch and when the copy goes away.
create table public.phone_files (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  -- The local id of the draft and the path of the file as attached to it (the phone matches on these two).
  content_id text not null check (char_length(content_id) between 1 and 200 and content_id !~ '[[:cntrl:]]'),
  file_path text not null check (
    char_length(file_path) between 1 and 500
    and file_path !~ '[[:cntrl:]]'
    and file_path !~ '^[/\\]'
    and file_path !~ '(^|[/\\])\.\.([/\\]|$)'
  ),
  file_name text not null check (
    char_length(file_name) between 1 and 200 and file_name !~ '[[:cntrl:]]' and file_name !~ '[/\\]'
  ),
  kind text not null check (kind in ('image', 'video', 'pdf')),
  content_type text not null check (
    content_type in ('image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm', 'application/pdf')
  ),
  bytes bigint not null check (bytes between 1 and 52428800),
  storage_path text not null,
  -- Changes when the local file changes, so an unchanged file is never uploaded twice.
  source_hash text not null check (source_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  -- Set by the guard trigger below, always 14 days after the file was stored.
  expires_at timestamptz not null default now() + interval '14 days',
  -- Set by the owner on an expired copy only, so its object can be seen and deleted. Never reset except by a new file.
  removing boolean not null default false,
  constraint phone_files_unique_file unique (owner_id, content_id, file_path),
  constraint phone_files_storage_path_shape check (
    storage_path ~ ('^' || owner_id::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
  ),
  constraint phone_files_kind_matches_type check (
    (kind = 'image' and content_type like 'image/%')
    or (kind = 'video' and content_type like 'video/%')
    or (kind = 'pdf' and content_type = 'application/pdf')
  )
);

create index idx_phone_files_owner_content on public.phone_files (owner_id, content_id);
create index idx_phone_files_owner_expires on public.phone_files (owner_id, expires_at);

alter table public.phone_files enable row level security;

-- The server alone decides how long a copy lives: 14 days from when its file was stored. A client cannot send or
-- change created_at or expires_at (no column privilege), so a skewed laptop clock cannot lengthen or shorten it.
-- The 1 GB budget is ONE pool for the whole project (Supabase's free storage is shared), so previews and copies of
-- ALL members are summed; the function is security definer so it can see every owner's rows. The global advisory
-- lock serializes writes so two uploads cannot both squeeze under the limit.
create or replace function public.guard_phone_file()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_used bigint;
begin
  if tg_op = 'UPDATE' then
    if old.removing then
      new.removing := true;
    elsif new.removing and old.expires_at > now() then
      raise exception 'Only an expired copy can be marked for removal' using errcode = '23514';
    end if;
  end if;
  if tg_op = 'INSERT' or new.storage_path is distinct from old.storage_path then
    new.created_at := now();
    new.expires_at := now() + interval '14 days';
    new.removing := false;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('phone_files:project', 0));
  select coalesce((select sum(bytes) from public.phone_files where id <> new.id), 0)
       + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
    into v_used;
  if v_used + new.bytes > 1073741824 then
    -- A plain exception (P0001) reaches the laptop as HTTP 400 with this text, never as a server outage (53xxx).
    raise exception 'Cloud storage budget (1 GB) is full';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_phone_file() from public, anon, authenticated;

create trigger trg_phone_files_guard
  before insert or update on public.phone_files
  for each row
  execute function public.guard_phone_file();

-- RLS: the owner and nobody else (admins included). Deactivated members lose access at once.
create policy phone_files_select_own
  on public.phone_files
  for select
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_files_insert_own
  on public.phone_files
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_files_update_own
  on public.phone_files
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_files_delete_own
  on public.phone_files
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Objects live at <owner id>/<64 hex characters>.<extension>: the name carries no file name, no folder and nothing
-- readable. Only the owner (an active member) can touch them, and only while an unexpired copy row of the owner points
-- at that exact object, so a link cannot be signed (and nothing can be stored) for an expired, deleted or foreign
-- copy. Admins get no policy at all.
-- Deleting an object needs to see it, so the owner first sets "removing" on an EXPIRED row (the guard trigger below
-- allows nothing else); the laptop therefore never has to read an expired object, it only marks, deletes, then drops
-- the row. The laptop adds a copy in this order: insert the row (the budget is checked here), then upload the object.
create policy phone_files_select_own
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'phone-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_files pf
      where pf.owner_id = (select auth.uid()) and pf.storage_path = name and (pf.expires_at > now() or pf.removing)
    )
  );

create policy phone_files_insert_own
  on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'phone-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ ('^' || (select auth.uid())::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_files pf
      where pf.owner_id = (select auth.uid()) and pf.storage_path = name and pf.expires_at > now()
    )
  );

create policy phone_files_update_own
  on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'phone-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_files pf
      where pf.owner_id = (select auth.uid()) and pf.storage_path = name and pf.expires_at > now()
    )
  )
  with check (
    bucket_id = 'phone-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ ('^' || (select auth.uid())::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
    and (select public.is_active_member())
  );

create policy phone_files_delete_own
  on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'phone-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  );

-- Privileges: anon nothing; clients can never move a row to another owner or draft.
revoke all on table public.phone_files from public, anon, authenticated;
grant select, delete on table public.phone_files to authenticated;
grant insert (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  on table public.phone_files to authenticated;
grant update (file_name, kind, content_type, bytes, storage_path, source_hash, removing)
  on table public.phone_files to authenticated;
grant all on table public.phone_files to service_role;

-- What the whole project has used (previews and copies of everyone), as one number. The laptop uses it to decide
-- what to skip; it shows nothing about anyone's files.
create or replace function public.team_storage_used()
returns bigint
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null or not public.is_active_member() then
    raise exception 'Not allowed' using errcode = '42501';
  end if;
  return (
    coalesce((select sum(bytes) from public.phone_files), 0)
    + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
  )::bigint;
end;
$$;

revoke all on function public.team_storage_used() from public, anon, authenticated;
grant execute on function public.team_storage_used() to authenticated, service_role;

-- 3. The storage meter counts the copies too (same shape as before; a new "phone_file" kind in by_kind).
create or replace function public.storage_usage()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  with parts as (
    select mp.kind as kind, mp.bytes as bytes from public.media_previews mp where mp.deleted_at is null
    union all
    select 'phone_file' as kind, pf.bytes as bytes from public.phone_files pf
  )
  select jsonb_build_object(
    'bytes', coalesce((select sum(p.bytes) from parts p), 0)::bigint,
    'files', coalesce((select count(*) from parts p), 0)::bigint,
    'by_kind', coalesce((
      select jsonb_object_agg(
        k.kind,
        jsonb_build_object('bytes', k.total_bytes, 'files', k.file_count)
      )
      from (
        select p.kind, coalesce(sum(p.bytes), 0)::bigint as total_bytes, count(*)::bigint as file_count
        from parts p
        group by p.kind
      ) k
    ), '{}'::jsonb)
  );
$$;

revoke all on function public.storage_usage() from public, anon, authenticated;
grant execute on function public.storage_usage() to authenticated, service_role;

-- 4. agent_task: a request for an agent that needs a laptop tool. target_id is the agent id, payload is the message.
-- The length cap is in bytes of the JSON text; 17000 fits 4000 characters of any script.
alter table public.phone_actions drop constraint phone_actions_kind_check;
alter table public.phone_actions add constraint phone_actions_kind_check
  check (kind in ('draft_comment', 'draft_posted', 'reminder_done', 'agent_task'));

alter table public.phone_actions drop constraint phone_actions_payload_shape;
alter table public.phone_actions add constraint phone_actions_payload_shape check (
  jsonb_typeof(payload) = 'object'
  and octet_length(payload::text) <= 17000
  and coalesce(case kind
    when 'draft_comment' then
      (payload - 'body' - 'version') = '{}'::jsonb
      and jsonb_typeof(payload -> 'body') = 'string'
      and char_length(btrim(payload ->> 'body')) between 1 and 4000
      and (
        not (payload ? 'version')
        or (
          jsonb_typeof(payload -> 'version') = 'number'
          and (payload ->> 'version') ~ '^[1-9][0-9]{0,8}$'
        )
      )
    when 'draft_posted' then
      (payload - 'url') = '{}'::jsonb
      and (
        not (payload ? 'url')
        or (
          jsonb_typeof(payload -> 'url') = 'string'
          and char_length(payload ->> 'url') between 1 and 2000
          and (payload ->> 'url') ~ '^https?://[^[:space:][:cntrl:]]+$'
        )
      )
    when 'reminder_done' then
      payload = '{}'::jsonb
    when 'agent_task' then
      (payload - 'message') = '{}'::jsonb
      and jsonb_typeof(payload -> 'message') = 'string'
      and char_length(btrim(payload ->> 'message')) between 1 and 4000
      and (payload ->> 'message') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
      and target_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    else false
  end, false)
);
