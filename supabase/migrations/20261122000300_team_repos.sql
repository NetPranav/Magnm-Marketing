-- Team repositories: an admin adds GitHub repositories once and every active member gets them.
-- GitHub only: a local path is not portable. Members read; only admins write; nothing for anon.

create table public.team_repos (
  -- Stable slug id, never changed after creation.
  id text primary key check (id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  -- GitHub 'owner/repo'. Strict characters so it can never be a path, URL host or git option.
  github text not null check (
    char_length(github) between 3 and 140
    and github ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}$'
    and split_part(github, '/', 2) not in ('.', '..')
  ),
  name text not null check (char_length(name) between 1 and 80 and name !~ '[[:cntrl:]]'),
  product_id text not null check (product_id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  branch text check (
    branch is null
    or (char_length(branch) <= 100 and branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]*$' and branch !~ '\.\.|//|/$|\.lock$')
  ),
  confidential boolean not null default false,
  status text not null default 'in_development'
    check (status in ('released', 'in_development', 'secret')),
  created_by uuid references public.profiles(id) on delete set null,
  updated_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One row per GitHub repository, whatever the letter case.
create unique index team_repos_github_lower_key on public.team_repos (lower(github));

alter table public.team_repos enable row level security;

-- Stamp who and when from the session, never from the client; the id and creation stamps cannot change.
create or replace function public.stamp_team_repo()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.created_by := auth.uid();
    new.created_at := now();
  else
    if new.id is distinct from old.id then
      raise exception 'team_repos.id cannot be changed';
    end if;
    new.created_by := old.created_by;
    new.created_at := old.created_at;
  end if;
  new.updated_by := auth.uid();
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.stamp_team_repo() from public, anon, authenticated;

create trigger trg_team_repos_stamp
  before insert or update on public.team_repos
  for each row
  execute function public.stamp_team_repo();

-- Audit every change, like shared knowledge. Only admins can read the audit log.
create or replace function public.audit_team_repo_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      'team_repo.deleted',
      old.id,
      jsonb_build_object('github', old.github, 'name', old.name, 'confidential', old.confidential)
    );
    return old;
  else
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      case when tg_op = 'INSERT' then 'team_repo.created' else 'team_repo.updated' end,
      new.id,
      jsonb_build_object(
        'github', new.github,
        'name', new.name,
        'product_id', new.product_id,
        'branch', new.branch,
        'status', new.status,
        'confidential', new.confidential
      )
    );
    return new;
  end if;
end;
$$;

revoke all on function public.audit_team_repo_change() from public, anon, authenticated;

create trigger trg_audit_team_repos
  after insert or update or delete on public.team_repos
  for each row
  execute function public.audit_team_repo_change();

-- RLS: every active member reads; only admins write.
create policy team_repos_select_active
  on public.team_repos
  for select
  to authenticated
  using ((select public.is_active_member()));

create policy team_repos_insert_admin
  on public.team_repos
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy team_repos_update_admin
  on public.team_repos
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy team_repos_delete_admin
  on public.team_repos
  for delete
  to authenticated
  using ((select public.is_admin()));

-- Privileges: anon gets nothing.
revoke all on table public.team_repos from public, anon;
grant select, insert, update, delete on table public.team_repos to authenticated;
grant all on table public.team_repos to service_role;
