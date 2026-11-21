-- Team core: profiles, invites, invite-only sign-up trigger, role helpers, and team directory.

-- Invites table: admin-managed allowlist for Google sign-in.
create table public.invites (
  email text primary key check (email = lower(email) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  role text not null check (role in ('admin', 'member')),
  invited_by uuid,
  created_at timestamptz not null default now(),
  accepted_at timestamptz,
  revoked_at timestamptz
);

alter table public.invites enable row level security;

-- Profiles table: one row per active or deactivated team member.
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique check (email = lower(email) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  display_name text,
  avatar_url text,
  role text not null check (role in ('admin', 'member')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz
);

alter table public.profiles enable row level security;

alter table public.invites
  add constraint invites_invited_by_fkey
  foreign key (invited_by) references public.profiles(id) on delete set null;

-- Normalize invite email to lower-case before insert or update.
create or replace function public.normalize_invite_email()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.email is not null then
    new.email := lower(btrim(new.email));
  end if;
  return new;
end;
$$;

revoke all on function public.normalize_invite_email() from public, anon;

create trigger trg_invites_normalize_email
  before insert or update on public.invites
  for each row
  execute function public.normalize_invite_email();

-- Helper: true when the current user has an active admin profile.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.profiles
    where id = auth.uid()
      and role = 'admin'
      and active = true
  );
$$;

revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated, service_role;

-- Helper: true when the current user has an active profile of any role.
create or replace function public.is_active_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.profiles
    where id = auth.uid()
      and active = true
  );
$$;

revoke all on function public.is_active_member() from public, anon;
grant execute on function public.is_active_member() to authenticated, service_role;

-- Trigger on auth.users insert: require an open, unrevoked invite and create the profile.
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email text;
  v_role text;
begin
  v_email := lower(btrim(coalesce(new.email, '')));
  if v_email = '' then
    raise exception 'Email is required for sign-up';
  end if;

  select i.role into v_role
  from public.invites i
  where i.email = v_email
    and i.revoked_at is null
    and i.accepted_at is null
  for update;

  if v_role is null then
    raise exception 'Sign-up rejected: no open invite for %', v_email;
  end if;

  insert into public.profiles (id, email, display_name, avatar_url, role, active, created_at)
  values (
    new.id,
    v_email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name'),
    new.raw_user_meta_data->>'avatar_url',
    v_role,
    true,
    now()
  );

  update public.invites
  set accepted_at = now()
  where email = v_email;

  return new;
end;
$$;

revoke all on function public.handle_new_auth_user() from public, anon;

drop trigger if exists trg_on_auth_user_created on auth.users;
create trigger trg_on_auth_user_created
  after insert on auth.users
  for each row
  execute function public.handle_new_auth_user();

-- Guard profile updates: non-admins can only change display_name, avatar_url, last_seen_at.
-- Nobody can demote or deactivate the last active admin.
create or replace function public.guard_profile_update()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_caller_is_admin boolean;
begin
  if coalesce(current_setting('role', true), '') in ('authenticated', 'anon') or auth.uid() is not null then
    v_caller_is_admin := public.is_admin();
  else
    v_caller_is_admin := true;
  end if;

  if (
    new.id is distinct from old.id
    or new.email is distinct from old.email
    or new.role is distinct from old.role
    or new.active is distinct from old.active
    or new.created_at is distinct from old.created_at
  ) and not v_caller_is_admin then
    raise exception 'Only admins may change id, email, role, or active status';
  end if;

  if old.role = 'admin' and old.active = true and (new.role <> 'admin' or new.active = false) then
    if not exists (
      select 1
      from public.profiles p
      where p.role = 'admin'
        and p.active = true
        and p.id <> old.id
    ) then
      raise exception 'Cannot demote or deactivate the last active admin';
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.guard_profile_update() from public, anon;

create trigger trg_profiles_guard_update
  before update on public.profiles
  for each row
  execute function public.guard_profile_update();

-- Team directory function and view: active members see id, display_name, avatar_url of active teammates (never email).
create or replace function public.list_team_directory()
returns table (
  id uuid,
  display_name text,
  avatar_url text
)
language sql
stable
security definer
set search_path = ''
as $$
  select p.id, p.display_name, p.avatar_url
  from public.profiles p
  where p.active = true
    and public.is_active_member();
$$;

revoke all on function public.list_team_directory() from public, anon;
grant execute on function public.list_team_directory() to authenticated, service_role;

create view public.team_directory
with (security_invoker = true)
as
select id, display_name, avatar_url
from public.list_team_directory();

-- RLS policies for profiles.
create policy profiles_select_self_or_admin
  on public.profiles
  for select
  to authenticated
  using ((id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy profiles_update_self_or_admin
  on public.profiles
  for update
  to authenticated
  using ((id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()))
  with check ((id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

-- RLS policies for invites (admin only).
create policy invites_admin_select
  on public.invites
  for select
  to authenticated
  using ((select public.is_admin()));

create policy invites_admin_insert
  on public.invites
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy invites_admin_update
  on public.invites
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy invites_admin_delete
  on public.invites
  for delete
  to authenticated
  using ((select public.is_admin()));

-- Privileges: anon gets nothing; authenticated gets required table/view privileges subject to RLS.
revoke all on table public.profiles from public, anon;
revoke all on table public.invites from public, anon;
revoke all on table public.team_directory from public, anon;

grant select, update on table public.profiles to authenticated;
grant select, insert, update, delete on table public.invites to authenticated;
grant select on table public.team_directory to authenticated;

grant all on table public.profiles to service_role;
grant all on table public.invites to service_role;
grant select on table public.team_directory to service_role;
