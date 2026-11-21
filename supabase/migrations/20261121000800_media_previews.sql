-- Private media previews bucket, storage RLS policies, metadata table, and storage_usage() RPC.

-- Private Storage bucket for thumbnails, image previews (WebP), and video previews (MP4).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('previews', 'previews', false, 15728640, array['image/webp', 'video/mp4'])
on conflict (id) do nothing;

-- Storage object policies for bucket 'previews':
-- Active members may select, insert, update, and delete only inside their own <uid>/ prefix.
-- Admins may select all objects in 'previews'. Anon has no access.
create policy previews_select_own_or_admin
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'previews'
    and (
      ((storage.foldername(name))[1] = (select auth.uid())::text and (select public.is_active_member()))
      or (select public.is_admin())
    )
  );

create policy previews_insert_own
  on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'previews'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  );

create policy previews_update_own
  on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'previews'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  )
  with check (
    bucket_id = 'previews'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  );

create policy previews_delete_own
  on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'previews'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (select public.is_active_member())
  );

-- Metadata table for generated media previews stored in the private previews bucket.
create table public.media_previews (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  local_source_id text not null check (char_length(local_source_id) between 1 and 200),
  source_kind text not null check (source_kind in ('content_file', 'asset')),
  parent_local_id text check (parent_local_id is null or char_length(parent_local_id) <= 200),
  kind text not null check (kind in ('thumbnail', 'image_preview', 'video_preview')),
  storage_path text not null check (
    storage_path like owner_id::text || '/%'
    and char_length(storage_path) > char_length(owner_id::text) + 1
  ),
  content_type text not null check (content_type in ('image/webp', 'video/mp4')),
  bytes bigint not null check (bytes >= 0),
  width int check (width is null or width > 0),
  height int check (height is null or height > 0),
  duration_s numeric check (duration_s is null or duration_s >= 0),
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  deleted_at timestamptz,
  unique (owner_id, local_source_id, kind)
);

create index idx_media_previews_owner_kind
  on public.media_previews (owner_id, kind)
  where deleted_at is null;

create index idx_media_previews_expires
  on public.media_previews (expires_at)
  where deleted_at is null and expires_at is not null;

alter table public.media_previews enable row level security;

-- Prevent changing owner_id on media_previews updates.
create or replace function public.guard_media_preview_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.owner_id is distinct from old.owner_id then
    raise exception 'Cannot change owner_id of a media preview';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_media_preview_owner() from public, anon;

create trigger trg_media_previews_guard_owner
  before update on public.media_previews
  for each row
  execute function public.guard_media_preview_owner();

-- RLS policies for media_previews: owner manages own rows; admin reads all but cannot write others' rows.
create policy media_previews_select_own_or_admin
  on public.media_previews
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy media_previews_insert_own
  on public.media_previews
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy media_previews_update_own
  on public.media_previews
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy media_previews_delete_own
  on public.media_previews
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Security-invoker storage usage summary across non-deleted previews visible to the caller.
create or replace function public.storage_usage()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select jsonb_build_object(
    'bytes', coalesce((select sum(mp.bytes) from public.media_previews mp where mp.deleted_at is null), 0)::bigint,
    'files', coalesce((select count(*) from public.media_previews mp where mp.deleted_at is null), 0)::bigint,
    'by_kind', coalesce((
      select jsonb_object_agg(
        k.kind,
        jsonb_build_object('bytes', k.total_bytes, 'files', k.file_count)
      )
      from (
        select
          mp.kind,
          coalesce(sum(mp.bytes), 0)::bigint as total_bytes,
          count(*)::bigint as file_count
        from public.media_previews mp
        where mp.deleted_at is null
        group by mp.kind
      ) k
    ), '{}'::jsonb)
  );
$$;

revoke all on function public.storage_usage() from public, anon;
grant execute on function public.storage_usage() to authenticated, service_role;

-- Privileges: anon gets nothing.
revoke all on table public.media_previews from public, anon;
grant select, insert, update, delete on table public.media_previews to authenticated;
grant all on table public.media_previews to service_role;
