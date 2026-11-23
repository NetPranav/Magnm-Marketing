-- Personal API keys and personal repositories, synced between a member's laptop and phone.
-- Both are owner only: an admin gets no read path to either. Forward-only; nothing here changes an existing table,
-- the phone_actions constraints or the mirror_records constraints.

-- ---------------------------------------------------------------------------------------------------------------
-- 1. Personal NVIDIA NIM keys (Vault)
-- ---------------------------------------------------------------------------------------------------------------
-- The key itself lives only in Vault, under magnm_personal_key_<owner id>_<slot>. The shared team keys use other
-- names (magnm_ai_key_<n>), so admin_set_ai_keys never touches these. This table keeps only what a device needs to
-- compare keys without reading them: the last four characters, a SHA-256 fingerprint and a version. Nobody but
-- service_role can read the table; members go through the functions below, which only ever see their own rows.
create table public.personal_api_keys (
  owner_id uuid not null references public.profiles(id) on delete cascade,
  -- Slot names are the engine's key pool variable names: NVIDIA_NIM_API_KEY and NVIDIA_NIM_API_KEY1 .. 10.
  name text not null check (name ~ '^NVIDIA_NIM_API_KEY([1-9]|10)?$'),
  secret_id uuid not null,
  last4 text not null check (char_length(last4) = 4),
  fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  version int not null default 1 check (version >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_id, name)
);

alter table public.personal_api_keys enable row level security;

revoke all on table public.personal_api_keys from public, anon, authenticated;
grant all on table public.personal_api_keys to service_role;

-- Removing a row (a delete, or a deleted member) removes its Vault secret too.
create or replace function public.forget_personal_key_secret()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from vault.secrets s where s.id = old.secret_id;
  return old;
end;
$$;

revoke all on function public.forget_personal_key_secret() from public, anon, authenticated;

create trigger trg_personal_api_keys_forget
  after delete on public.personal_api_keys
  for each row
  execute function public.forget_personal_key_secret();

-- Save or replace the caller's key in one slot. Returns the label data only, never the key.
create or replace function public.personal_key_set(p_name text, p_key text)
returns table (name text, last4 text, fingerprint text, version int)
language plpgsql
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_owner uuid := auth.uid();
  v_fp text;
  v_vault_name text;
  v_secret uuid;
  v_row public.personal_api_keys;
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  if p_name is null or p_name !~ '^NVIDIA_NIM_API_KEY([1-9]|10)?$' then
    raise exception 'Unknown key slot';
  end if;
  if p_key is null or p_key !~ '^nvapi-[A-Za-z0-9_-]{20,200}$' then
    raise exception 'Invalid NVIDIA NIM API key format';
  end if;

  v_fp := encode(sha256(convert_to(p_key, 'UTF8')), 'hex');
  if exists (
    select 1 from public.personal_api_keys k
    where k.owner_id = v_owner and k.fingerprint = v_fp and k.name <> p_name
  ) then
    raise exception 'That key is already saved in another slot';
  end if;

  select * into v_row
  from public.personal_api_keys k
  where k.owner_id = v_owner and k.name = p_name
  for update;

  if found then
    perform vault.update_secret(v_row.secret_id, p_key);
    update public.personal_api_keys k
    set last4 = right(p_key, 4),
        fingerprint = v_fp,
        version = k.version + 1,
        updated_at = now()
    where k.owner_id = v_owner and k.name = p_name;
  else
    v_vault_name := 'magnm_personal_key_' || v_owner::text || '_' || p_name;
    delete from vault.secrets s where s.name = v_vault_name;
    v_secret := vault.create_secret(p_key, v_vault_name, 'Magnm personal NVIDIA NIM key');
    insert into public.personal_api_keys (owner_id, name, secret_id, last4, fingerprint)
    values (v_owner, p_name, v_secret, right(p_key, 4), v_fp);
  end if;

  return query
  select k.name, k.last4, k.fingerprint, k.version
  from public.personal_api_keys k
  where k.owner_id = v_owner and k.name = p_name;
end;
$$;

-- The caller's own key labels: slot, last four characters, fingerprint, version. Never a key.
create or replace function public.personal_key_list()
returns table (name text, last4 text, fingerprint text, version int, updated_at timestamptz)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_owner uuid := auth.uid();
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  return query
  select k.name, k.last4, k.fingerprint, k.version, k.updated_at
  from public.personal_api_keys k
  where k.owner_id = v_owner
  order by k.name;
end;
$$;

-- The caller's own keys for the slots asked for (at most eleven). Unknown slots and other members' keys return nothing.
create or replace function public.personal_key_get(p_names text[])
returns table (name text, api_key text)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_owner uuid := auth.uid();
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  if p_names is null or coalesce(array_length(p_names, 1), 0) > 11 then
    raise exception 'Too many key slots requested';
  end if;
  return query
  select k.name, s.decrypted_secret
  from public.personal_api_keys k
  join vault.decrypted_secrets s on s.id = k.secret_id
  where k.owner_id = v_owner
    and k.name = any (p_names)
  order by k.name;
end;
$$;

-- Remove the caller's key in one slot (the Vault secret goes with it). True if there was one.
create or replace function public.personal_key_delete(p_name text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid := auth.uid();
  v_deleted int;
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  if p_name is null or p_name !~ '^NVIDIA_NIM_API_KEY([1-9]|10)?$' then
    raise exception 'Unknown key slot';
  end if;
  delete from public.personal_api_keys k where k.owner_id = v_owner and k.name = p_name;
  get diagnostics v_deleted = row_count;
  return v_deleted > 0;
end;
$$;

revoke all on function public.personal_key_set(text, text) from public, anon, authenticated;
revoke all on function public.personal_key_list() from public, anon, authenticated;
revoke all on function public.personal_key_get(text[]) from public, anon, authenticated;
revoke all on function public.personal_key_delete(text) from public, anon, authenticated;
grant execute on function public.personal_key_set(text, text) to authenticated;
grant execute on function public.personal_key_list() to authenticated;
grant execute on function public.personal_key_get(text[]) to authenticated;
grant execute on function public.personal_key_delete(text) to authenticated;

-- ---------------------------------------------------------------------------------------------------------------
-- 2. Personal repositories
-- ---------------------------------------------------------------------------------------------------------------
-- One row per repository of one member. `uid` never changes, so the other device can follow a rename of `id`.
-- Every editable field carries the version (`rev` at the time) and time of its last edit, so two devices that edit
-- different fields never overwrite each other and the later edit of one field wins. A folder path edited on the
-- phone is `pending` until the laptop has checked it, then `ok` or `rejected` with a short reason; the laptop checks
-- every path it reads from here before using it. Members read their rows; writes go through the functions below.
create table public.personal_repos (
  uid uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  id text not null check (id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  name text not null check (char_length(name) between 1 and 80 and name !~ '[[:cntrl:]]'),
  kind text not null check (kind in ('github', 'local')),
  github text check (
    github is null
    or (
      char_length(github) between 3 and 140
      and github ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}$'
      and split_part(github, '/', 2) not in ('.', '..')
    )
  ),
  local_path text check (
    local_path is null
    or (
      char_length(local_path) between 1 and 500
      and local_path !~ '[[:cntrl:]]'
      and local_path !~ '(^|[/\\])\.\.([/\\]|$)'
    )
  ),
  path_status text not null default 'ok' check (path_status in ('ok', 'pending', 'rejected')),
  path_reason text check (path_reason is null or (char_length(path_reason) <= 200 and path_reason !~ '[[:cntrl:]]')),
  branch text check (
    branch is null
    or (char_length(branch) <= 100 and branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]*$' and branch !~ '\.\.|//|/$|\.lock$')
  ),
  status text not null default 'in_development' check (status in ('released', 'in_development', 'secret')),
  marketing_angle text check (
    marketing_angle is null
    or (char_length(marketing_angle) <= 500 and translate(marketing_angle, E'\n\t', '') !~ '[[:cntrl:]]')
  ),
  confidential boolean not null default false,
  rev bigint not null default 1,
  field_rev jsonb not null default '{}'::jsonb,
  field_at jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint personal_repos_kind_shape check (
    (kind = 'github' and github is not null) or (kind = 'local' and local_path is not null)
  ),
  constraint personal_repos_owner_id_key unique (owner_id, id)
);

create unique index personal_repos_owner_github_key on public.personal_repos (owner_id, lower(github)) where github is not null;

alter table public.personal_repos enable row level security;

create policy personal_repos_select_owner
  on public.personal_repos
  for select
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

revoke all on table public.personal_repos from public, anon, authenticated;
grant select on table public.personal_repos to authenticated;
grant all on table public.personal_repos to service_role;

-- At most 100 repositories per member; the owner and uid never change.
create or replace function public.guard_personal_repo()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    perform pg_advisory_xact_lock(hashtextextended('personal_repos:' || new.owner_id::text, 0));
    if (select count(*) from public.personal_repos r where r.owner_id = new.owner_id) >= 100 then
      raise exception 'At most 100 repositories are allowed';
    end if;
    new.created_at := now();
  else
    if new.owner_id is distinct from old.owner_id or new.uid is distinct from old.uid then
      raise exception 'The owner of a repository cannot change';
    end if;
    new.created_at := old.created_at;
  end if;
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.guard_personal_repo() from public, anon, authenticated;

create trigger trg_personal_repos_guard
  before insert or update on public.personal_repos
  for each row
  execute function public.guard_personal_repo();

-- Add (p_uid null) or edit one repository of the caller. Only the fields present in p_fields change, and only a
-- field whose value really changes gets a new version. p_checked is true when the laptop has already checked the
-- folder path it sends; any other path edit becomes `pending`.
create or replace function public.personal_repo_save(p_uid uuid, p_fields jsonb, p_checked boolean default false)
returns public.personal_repos
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid := auth.uid();
  v_key text;
  v_row public.personal_repos;
  v_rev bigint;
  v_now timestamptz := now();
  v_fr jsonb;
  v_fa jsonb;
  v_any boolean := false;
  v_id text;
  v_name text;
  v_kind text;
  v_github text;
  v_path text;
  v_branch text;
  v_status text;
  v_angle text;
  v_conf boolean;
  v_ps text;
  v_pr text;
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  if p_fields is null or jsonb_typeof(p_fields) <> 'object' then
    raise exception 'Repository fields must be an object';
  end if;

  for v_key in select jsonb_object_keys(p_fields) loop
    if v_key <> all (array['id', 'name', 'kind', 'github', 'local_path', 'branch', 'status', 'marketing_angle', 'confidential']) then
      raise exception 'Unknown repository field';
    end if;
    if v_key = 'confidential' then
      if jsonb_typeof(p_fields -> v_key) <> 'boolean' then
        raise exception 'confidential must be true or false';
      end if;
    elsif jsonb_typeof(p_fields -> v_key) not in ('string', 'null') then
      raise exception 'Repository field values must be text';
    end if;
  end loop;

  if p_uid is null then
    v_path := p_fields ->> 'local_path';
    insert into public.personal_repos (
      owner_id, id, name, kind, github, local_path, path_status, branch, status, marketing_angle, confidential,
      rev, field_rev, field_at
    )
    values (
      v_owner,
      p_fields ->> 'id',
      p_fields ->> 'name',
      p_fields ->> 'kind',
      p_fields ->> 'github',
      v_path,
      case when v_path is not null and not coalesce(p_checked, false) then 'pending' else 'ok' end,
      p_fields ->> 'branch',
      coalesce(p_fields ->> 'status', 'in_development'),
      p_fields ->> 'marketing_angle',
      coalesce((p_fields ->> 'confidential')::boolean, false),
      1,
      jsonb_build_object('id', 1, 'name', 1, 'kind', 1, 'github', 1, 'local_path', 1, 'branch', 1,
                         'status', 1, 'marketing_angle', 1, 'confidential', 1),
      jsonb_build_object('id', v_now, 'name', v_now, 'kind', v_now, 'github', v_now, 'local_path', v_now,
                         'branch', v_now, 'status', v_now, 'marketing_angle', v_now, 'confidential', v_now)
    )
    returning * into v_row;
    return v_row;
  end if;

  select * into v_row
  from public.personal_repos r
  where r.uid = p_uid and r.owner_id = v_owner
  for update;
  if not found then
    raise exception 'Repository not found';
  end if;

  v_rev := v_row.rev + 1;
  v_fr := v_row.field_rev;
  v_fa := v_row.field_at;

  v_id := case when p_fields ? 'id' then p_fields ->> 'id' else v_row.id end;
  v_name := case when p_fields ? 'name' then p_fields ->> 'name' else v_row.name end;
  v_kind := case when p_fields ? 'kind' then p_fields ->> 'kind' else v_row.kind end;
  v_github := case when p_fields ? 'github' then p_fields ->> 'github' else v_row.github end;
  v_path := case when p_fields ? 'local_path' then p_fields ->> 'local_path' else v_row.local_path end;
  v_branch := case when p_fields ? 'branch' then p_fields ->> 'branch' else v_row.branch end;
  v_status := case when p_fields ? 'status' then p_fields ->> 'status' else v_row.status end;
  v_angle := case when p_fields ? 'marketing_angle' then p_fields ->> 'marketing_angle' else v_row.marketing_angle end;
  v_conf := case when p_fields ? 'confidential' then (p_fields ->> 'confidential')::boolean else v_row.confidential end;
  v_ps := v_row.path_status;
  v_pr := v_row.path_reason;

  if v_id is distinct from v_row.id then
    v_fr := v_fr || jsonb_build_object('id', v_rev); v_fa := v_fa || jsonb_build_object('id', v_now); v_any := true;
  end if;
  if v_name is distinct from v_row.name then
    v_fr := v_fr || jsonb_build_object('name', v_rev); v_fa := v_fa || jsonb_build_object('name', v_now); v_any := true;
  end if;
  if v_kind is distinct from v_row.kind then
    v_fr := v_fr || jsonb_build_object('kind', v_rev); v_fa := v_fa || jsonb_build_object('kind', v_now); v_any := true;
  end if;
  if v_github is distinct from v_row.github then
    v_fr := v_fr || jsonb_build_object('github', v_rev); v_fa := v_fa || jsonb_build_object('github', v_now); v_any := true;
  end if;
  if v_path is distinct from v_row.local_path then
    v_fr := v_fr || jsonb_build_object('local_path', v_rev); v_fa := v_fa || jsonb_build_object('local_path', v_now); v_any := true;
    v_ps := case when v_path is not null and not coalesce(p_checked, false) then 'pending' else 'ok' end;
    v_pr := null;
  end if;
  if v_branch is distinct from v_row.branch then
    v_fr := v_fr || jsonb_build_object('branch', v_rev); v_fa := v_fa || jsonb_build_object('branch', v_now); v_any := true;
  end if;
  if v_status is distinct from v_row.status then
    v_fr := v_fr || jsonb_build_object('status', v_rev); v_fa := v_fa || jsonb_build_object('status', v_now); v_any := true;
  end if;
  if v_angle is distinct from v_row.marketing_angle then
    v_fr := v_fr || jsonb_build_object('marketing_angle', v_rev); v_fa := v_fa || jsonb_build_object('marketing_angle', v_now); v_any := true;
  end if;
  if v_conf is distinct from v_row.confidential then
    v_fr := v_fr || jsonb_build_object('confidential', v_rev); v_fa := v_fa || jsonb_build_object('confidential', v_now); v_any := true;
  end if;

  if not v_any then
    return v_row;
  end if;

  update public.personal_repos r
  set id = v_id, name = v_name, kind = v_kind, github = v_github, local_path = v_path, branch = v_branch,
      status = v_status, marketing_angle = v_angle, confidential = v_conf,
      path_status = v_ps, path_reason = v_pr,
      rev = v_rev, field_rev = v_fr, field_at = v_fa
  where r.uid = p_uid and r.owner_id = v_owner
  returning * into v_row;
  return v_row;
end;
$$;

-- The laptop's answer for a folder path: ok, or rejected with a short reason. A result for a path that is no longer
-- the repository's path is ignored.
create or replace function public.personal_repo_path_result(p_uid uuid, p_path text, p_ok boolean, p_reason text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid := auth.uid();
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  perform 1 from public.personal_repos r where r.uid = p_uid and r.owner_id = v_owner;
  if not found then
    raise exception 'Repository not found';
  end if;
  update public.personal_repos r
  set path_status = case when coalesce(p_ok, false) then 'ok' else 'rejected' end,
      path_reason = case
        when coalesce(p_ok, false) then null
        else nullif(left(btrim(regexp_replace(coalesce(p_reason, ''), '[[:cntrl:]]', ' ', 'g')), 200), '')
      end
  where r.uid = p_uid and r.owner_id = v_owner and r.local_path is not distinct from p_path;
end;
$$;

-- Remove one repository of the caller. True if there was one.
create or replace function public.personal_repo_delete(p_uid uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid := auth.uid();
  v_deleted int;
begin
  if v_owner is null or not public.is_active_member() then
    raise exception 'Active member access required';
  end if;
  delete from public.personal_repos r where r.uid = p_uid and r.owner_id = v_owner;
  get diagnostics v_deleted = row_count;
  return v_deleted > 0;
end;
$$;

revoke all on function public.personal_repo_save(uuid, jsonb, boolean) from public, anon, authenticated;
revoke all on function public.personal_repo_path_result(uuid, text, boolean, text) from public, anon, authenticated;
revoke all on function public.personal_repo_delete(uuid) from public, anon, authenticated;
grant execute on function public.personal_repo_save(uuid, jsonb, boolean) to authenticated;
grant execute on function public.personal_repo_path_result(uuid, text, boolean, text) to authenticated;
grant execute on function public.personal_repo_delete(uuid) to authenticated;
