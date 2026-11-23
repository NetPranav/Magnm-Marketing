-- Library, Plan and Create on the phone without the laptop.
-- Forward-only: nothing here changes or removes existing data. An engine that does not know a new kind leaves it
-- queued (it only asks for the kinds it knows), and an engine that does not mirror the new records never writes one.
--
-- 1. Phone uploads: a private bucket and a table for images, video and PDFs the phone adds to the owner's Library.
-- 2. Mirror kinds for laptop assets, schedules and the editorial calendar.
-- 3. Four new phone action kinds: library_upload, schedule_update, draft_create, workflow_request.
-- 4. The 1 GB budget stays ONE pool: previews, Get on phone copies and phone uploads are summed together.

-- 1. The bucket. Private, 50 MB, inert media and PDF types only. No admin or public access of any kind.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'phone-uploads', 'phone-uploads', false, 52428800,
  array['image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm', 'application/pdf']
)
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- One row per file the phone uploaded and the laptop has not yet taken into its Library. The phone makes the id (so
-- the library_upload action can name it), the server alone decides when the row expires.
create table public.phone_uploads (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  -- The name shown to the owner: never a path, never control characters.
  file_name text not null check (
    char_length(file_name) between 1 and 200 and file_name !~ '[[:cntrl:]]' and file_name !~ '[/\\]'
  ),
  kind text not null check (kind in ('image', 'video', 'pdf')),
  content_type text not null check (
    content_type in ('image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'video/webm', 'application/pdf')
  ),
  bytes bigint not null check (bytes between 1 and 52428800),
  storage_path text not null,
  created_at timestamptz not null default now(),
  -- Set by the guard trigger, always 14 days after the file was stored (the same life as a Get on phone copy).
  expires_at timestamptz not null default now() + interval '14 days',
  -- Set by the owner on an EXPIRED row only, so its object can be seen and deleted. Never reset.
  removing boolean not null default false,
  constraint phone_uploads_storage_path_unique unique (storage_path),
  constraint phone_uploads_storage_path_shape check (
    storage_path ~ ('^' || owner_id::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
  ),
  constraint phone_uploads_kind_matches_type check (
    (kind = 'image' and content_type like 'image/%')
    or (kind = 'video' and content_type like 'video/%')
    or (kind = 'pdf' and content_type = 'application/pdf')
  )
);

create index idx_phone_uploads_owner_created on public.phone_uploads (owner_id, created_at);

alter table public.phone_uploads enable row level security;

-- The server alone decides how long an upload lives. A client cannot send created_at or expires_at (no column
-- privilege). At most 100 waiting uploads per owner. The shared 1 GB budget counts every kind of stored file; the
-- advisory lock is the same one phone_files uses, so two writers cannot both squeeze under the limit.
create or replace function public.guard_phone_upload()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_used bigint;
begin
  if tg_op = 'UPDATE' then
    if new.id is distinct from old.id
       or new.owner_id is distinct from old.owner_id
       or new.file_name is distinct from old.file_name
       or new.kind is distinct from old.kind
       or new.content_type is distinct from old.content_type
       or new.bytes is distinct from old.bytes
       or new.storage_path is distinct from old.storage_path
       or new.created_at is distinct from old.created_at
       or new.expires_at is distinct from old.expires_at then
      raise exception 'A phone upload cannot be edited';
    end if;
    if old.removing then
      new.removing := true;
    elsif new.removing and old.expires_at > now() then
      raise exception 'Only an expired upload can be marked for removal' using errcode = '23514';
    end if;
    return new;
  end if;

  new.created_at := now();
  new.expires_at := now() + interval '14 days';
  new.removing := false;
  perform pg_advisory_xact_lock(hashtextextended('phone_files:project', 0));
  if (select count(*) from public.phone_uploads where owner_id = new.owner_id) >= 100 then
    raise exception 'Too many phone uploads are waiting (limit 100)';
  end if;
  select coalesce((select sum(bytes) from public.phone_files), 0)
       + coalesce((select sum(bytes) from public.phone_uploads), 0)
       + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
    into v_used;
  if v_used + new.bytes > 1073741824 then
    raise exception 'Cloud storage budget (1 GB) is full';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_phone_upload() from public, anon, authenticated;

create trigger trg_phone_uploads_guard
  before insert or update on public.phone_uploads
  for each row
  execute function public.guard_phone_upload();

-- phone_files (Get on phone) counts uploads too, so the pool stays one pool.
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
       + coalesce((select sum(bytes) from public.phone_uploads), 0)
       + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
    into v_used;
  if v_used + new.bytes > 1073741824 then
    raise exception 'Cloud storage budget (1 GB) is full';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_phone_file() from public, anon, authenticated;

-- RLS: the owner and nobody else (admins included). Deactivated members lose access at once.
create policy phone_uploads_select_own
  on public.phone_uploads
  for select
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_uploads_insert_own
  on public.phone_uploads
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_uploads_update_own
  on public.phone_uploads
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy phone_uploads_delete_own
  on public.phone_uploads
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Objects live at <owner id>/<64 random hex>.<extension>: the name carries no file name and nothing readable. Only
-- the owner (an active member) can touch them, and only while an unexpired row of the owner points at that exact
-- object. Admins get no policy at all. The order is: insert the row (the budget is checked here), then upload.
create policy phone_uploads_select_own
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'phone-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_uploads pu
      where pu.owner_id = (select auth.uid()) and pu.storage_path = name and (pu.expires_at > now() or pu.removing)
    )
  );

create policy phone_uploads_insert_own
  on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'phone-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ ('^' || (select auth.uid())::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_uploads pu
      where pu.owner_id = (select auth.uid()) and pu.storage_path = name and pu.expires_at > now()
    )
  );

create policy phone_uploads_update_own
  on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'phone-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
    and exists (
      select 1 from public.phone_uploads pu
      where pu.owner_id = (select auth.uid()) and pu.storage_path = name and pu.expires_at > now()
    )
  )
  with check (
    bucket_id = 'phone-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ ('^' || (select auth.uid())::text || '/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|webm|pdf)$')
    and (select public.is_active_member())
  );

create policy phone_uploads_delete_own
  on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'phone-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  );

-- Privileges: anon nothing. A client sets only what the phone decides and can never move a row to another owner.
revoke all on table public.phone_uploads from public, anon, authenticated;
grant select, delete on table public.phone_uploads to authenticated;
grant insert (id, file_name, kind, content_type, bytes, storage_path) on table public.phone_uploads to authenticated;
grant update (removing) on table public.phone_uploads to authenticated;
grant all on table public.phone_uploads to service_role;

-- The whole project's use as one number (previews, copies and uploads of everyone); it shows nothing about anyone's files.
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
    + coalesce((select sum(bytes) from public.phone_uploads), 0)
    + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
  )::bigint;
end;
$$;

revoke all on function public.team_storage_used() from public, anon, authenticated;
grant execute on function public.team_storage_used() to authenticated, service_role;

-- The storage meter counts uploads too (same shape as before; a new "phone_upload" kind in by_kind).
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
    union all
    select 'phone_upload' as kind, pu.bytes as bytes from public.phone_uploads pu
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

-- 2. Mirror kinds: laptop assets (names and sizes, never paths), schedules and the editorial calendar.
alter table public.mirror_records
  drop constraint if exists mirror_records_kind_check;

alter table public.mirror_records
  add constraint mirror_records_kind_check check (
    kind in (
      'chat_session',
      'team_message',
      'chat_turn',
      'content_item',
      'content_version',
      'research_note',
      'review_comment',
      'metric',
      'posted_link',
      'brief',
      'task',
      'pitch_proposal',
      'asset',
      'schedule',
      'editorial_plan'
    )
  );

-- 3. The inputs a phone may send to start a workflow. Pure checks only: it reads nothing, so a check constraint can
-- run it with the caller's rights. Every block is a CASE so a wrong type is a plain "no" and never an error.
create or replace function public.phone_workflow_inputs_ok(p_kind text, p jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    case when jsonb_typeof(p) = 'object' then
      -- Keys each kind accepts, and nothing else.
      (p - case p_kind
             when 'post' then array['brief', 'platforms', 'with_image', 'repo_id']
             when 'blog' then array['topic', 'repo_id']
             when 'video_script' then array['topic', 'duration_s', 'aspect', 'repo_id']
             when 'repurpose' then array['content_id', 'targets']
             when 'weekly_plan' then array['platforms', 'posts_per_week', 'week_start', 'repo_id']
             else array[]::text[]
           end) = '{}'::jsonb
      and p_kind in ('post', 'blog', 'video_script', 'repurpose', 'weekly_plan')
      -- The text each kind needs.
      and case when p_kind = 'post' then
            jsonb_typeof(p -> 'brief') = 'string' and char_length(btrim(p ->> 'brief')) between 1 and 2000
            and (p ->> 'brief') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
          when p_kind in ('blog', 'video_script') then
            jsonb_typeof(p -> 'topic') = 'string' and char_length(btrim(p ->> 'topic')) between 1 and 1000
            and (p ->> 'topic') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
          when p_kind = 'repurpose' then
            jsonb_typeof(p -> 'content_id') = 'string' and char_length(p ->> 'content_id') between 1 and 200
            and (p ->> 'content_id') !~ '[[:cntrl:]]'
          else true end
      and case when p ? 'platforms' then
            case when jsonb_typeof(p -> 'platforms') = 'array' then
              jsonb_array_length(p -> 'platforms') between 1 and 4
              and not exists (
                select 1 from jsonb_array_elements(p -> 'platforms') e
                where jsonb_typeof(e) <> 'string' or (e #>> '{}') not in ('x', 'linkedin', 'instagram', 'youtube')
              )
            else false end
          else true end
      and case when p ? 'with_image' then jsonb_typeof(p -> 'with_image') = 'boolean' else true end
      and case when p ? 'repo_id' then
            jsonb_typeof(p -> 'repo_id') = 'string' and (p ->> 'repo_id') ~ '^[a-z0-9][a-z0-9-]{0,62}$'
          else true end
      and case when p ? 'duration_s' then
            jsonb_typeof(p -> 'duration_s') = 'number' and (p ->> 'duration_s') in ('15', '30', '60')
          else true end
      and case when p ? 'aspect' then
            jsonb_typeof(p -> 'aspect') = 'string' and (p ->> 'aspect') in ('9:16', '16:9')
          else true end
      and case when p ? 'targets' then
            case when jsonb_typeof(p -> 'targets') = 'array' then
              jsonb_array_length(p -> 'targets') between 1 and 6
              and not exists (
                select 1 from jsonb_array_elements(p -> 'targets') e
                where jsonb_typeof(e) <> 'string' or (e #>> '{}') !~ '^[a-z][a-z0-9_]{0,31}$'
              )
            else false end
          else true end
      and case when p ? 'posts_per_week' then
            jsonb_typeof(p -> 'posts_per_week') = 'number' and (p ->> 'posts_per_week') ~ '^([1-9]|1[0-4])$'
          else true end
      and case when p ? 'week_start' then
            jsonb_typeof(p -> 'week_start') = 'string' and (p ->> 'week_start') ~ '^20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
          else true end
    else false end,
    false
  );
$$;

revoke all on function public.phone_workflow_inputs_ok(text, jsonb) from public, anon, authenticated;
grant execute on function public.phone_workflow_inputs_ok(text, jsonb) to authenticated, service_role;

-- The four kinds. Every existing kind and shape is kept exactly. The new ones:
--   library_upload   target = the phone_uploads id; payload = category (+ optional short description)
--   schedule_update  target = the schedule id; payload = any of enabled, interval_hours, time, day
--   draft_create     target = 'new'; payload = platform, title, body (+ mode post, thread or rewrite)
--   workflow_request target = the workflow kind; payload = that kind's inputs
alter table public.phone_actions drop constraint phone_actions_kind_check;
alter table public.phone_actions add constraint phone_actions_kind_check
  check (kind in (
    'draft_comment', 'draft_posted', 'reminder_done', 'agent_task',
    'pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link',
    'library_upload', 'schedule_update', 'draft_create', 'workflow_request'
  ));

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
    when 'pitch_create' then
      target_id = 'new'
      and payload::text !~ '[<>]'
      and public.phone_pitch_fields_ok(payload, true)
    when 'pitch_update' then
      (payload - 'base_version' - 'fields') = '{}'::jsonb
      and jsonb_typeof(payload -> 'base_version') = 'string'
      and char_length(payload ->> 'base_version') between 1 and 64
      and (payload ->> 'base_version') !~ '[[:cntrl:]]'
      and payload::text !~ '[<>]'
      and public.phone_pitch_fields_ok(payload -> 'fields', false)
    when 'pitch_finalize' then
      payload = '{}'::jsonb
    when 'pitch_link' then
      payload in ('{"action":"publish"}'::jsonb, '{"action":"withdraw"}'::jsonb)
    when 'library_upload' then
      target_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      and (payload - 'category' - 'description') = '{}'::jsonb
      and jsonb_typeof(payload -> 'category') = 'string'
      and (payload ->> 'category') in ('cero', 'brand', 'screenshot', 'recording', 'document')
      and (
        not (payload ? 'description')
        or (
          jsonb_typeof(payload -> 'description') = 'string'
          and char_length(payload ->> 'description') <= 300
          and (payload ->> 'description') !~ '[[:cntrl:]]'
        )
      )
    when 'schedule_update' then
      target_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
      and (payload - 'enabled' - 'interval_hours' - 'time' - 'day') = '{}'::jsonb
      and payload <> '{}'::jsonb
      and (not (payload ? 'enabled') or jsonb_typeof(payload -> 'enabled') = 'boolean')
      and (
        not (payload ? 'interval_hours')
        or (
          jsonb_typeof(payload -> 'interval_hours') = 'number'
          and (payload ->> 'interval_hours') ~ '^[1-9][0-9]{0,2}$'
          and (payload ->> 'interval_hours')::int <= 720
        )
      )
      and (
        not (payload ? 'time')
        or (
          jsonb_typeof(payload -> 'time') = 'string'
          and (payload ->> 'time') ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
        )
      )
      and (
        not (payload ? 'day')
        or (
          jsonb_typeof(payload -> 'day') = 'string'
          and (payload ->> 'day') ~ '^[*0-6]$'
        )
      )
    when 'draft_create' then
      target_id = 'new'
      and (payload - 'platform' - 'title' - 'body' - 'mode') = '{}'::jsonb
      and jsonb_typeof(payload -> 'platform') = 'string'
      and (payload ->> 'platform') in ('x', 'linkedin', 'instagram', 'youtube')
      and jsonb_typeof(payload -> 'title') = 'string'
      and char_length(btrim(payload ->> 'title')) between 1 and 200
      and (payload ->> 'title') !~ '[[:cntrl:]]'
      and jsonb_typeof(payload -> 'body') = 'string'
      and char_length(btrim(payload ->> 'body')) between 1 and 6000
      and (payload ->> 'body') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
      and (
        not (payload ? 'mode')
        or (jsonb_typeof(payload -> 'mode') = 'string' and (payload ->> 'mode') in ('post', 'thread', 'rewrite'))
      )
    when 'workflow_request' then
      target_id in ('post', 'blog', 'video_script', 'repurpose', 'weekly_plan')
      and public.phone_workflow_inputs_ok(target_id, payload)
    else false
  end, false)
);
