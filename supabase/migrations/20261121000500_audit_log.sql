-- Audit log table and security definer triggers for admin actions.

-- Append-only audit log readable only by admins and written only by triggers.
create table public.audit_log (
  id bigint generated always as identity primary key,
  actor_id uuid,
  action text not null,
  target text not null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_audit_log_created_at on public.audit_log (created_at desc);

alter table public.audit_log enable row level security;

-- RLS policy for audit_log: admins read all; nobody inserts directly, updates, or deletes.
create policy audit_log_admin_select
  on public.audit_log
  for select
  to authenticated
  using ((select public.is_admin()));

-- Trigger function: record invite creation and revocation.
create or replace function public.audit_invite_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      'invite.created',
      new.email,
      jsonb_build_object('role', new.role)
    );
  elsif tg_op = 'UPDATE' then
    if old.revoked_at is null and new.revoked_at is not null then
      insert into public.audit_log (actor_id, action, target, data)
      values (
        auth.uid(),
        'invite.revoked',
        new.email,
        jsonb_build_object('revoked_at', new.revoked_at)
      );
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.audit_invite_change() from public, anon;

create trigger trg_audit_invites
  after insert or update on public.invites
  for each row
  execute function public.audit_invite_change();

-- Trigger function: record profile role or active status changes.
create or replace function public.audit_profile_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.role is distinct from old.role or new.active is distinct from old.active then
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      'profile.role_or_active_changed',
      new.id::text,
      jsonb_build_object(
        'email', new.email,
        'old_role', old.role,
        'new_role', new.role,
        'old_active', old.active,
        'new_active', new.active
      )
    );
  end if;
  return new;
end;
$$;

revoke all on function public.audit_profile_change() from public, anon;

create trigger trg_audit_profiles
  after update on public.profiles
  for each row
  execute function public.audit_profile_change();

-- Trigger function: record shared_knowledge changes.
create or replace function public.audit_shared_knowledge_change()
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
      'shared_knowledge.deleted',
      old.key,
      jsonb_build_object('key', old.key)
    );
    return old;
  else
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      case when tg_op = 'INSERT' then 'shared_knowledge.created' else 'shared_knowledge.updated' end,
      new.key,
      new.data
    );
    return new;
  end if;
end;
$$;

revoke all on function public.audit_shared_knowledge_change() from public, anon;

create trigger trg_audit_shared_knowledge
  after insert or update or delete on public.shared_knowledge
  for each row
  execute function public.audit_shared_knowledge_change();

-- Trigger function: record pitch_base_prices changes.
create or replace function public.audit_pitch_base_price_change()
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
      'pitch_base_price.deleted',
      old.item_id,
      jsonb_build_object('old_price', old.price)
    );
    return old;
  else
    insert into public.audit_log (actor_id, action, target, data)
    values (
      auth.uid(),
      case when tg_op = 'INSERT' then 'pitch_base_price.created' else 'pitch_base_price.updated' end,
      new.item_id,
      jsonb_build_object(
        'old_price', case when tg_op = 'UPDATE' then old.price else null end,
        'new_price', new.price
      )
    );
    return new;
  end if;
end;
$$;

revoke all on function public.audit_pitch_base_price_change() from public, anon;

create trigger trg_audit_pitch_base_prices
  after insert or update or delete on public.pitch_base_prices
  for each row
  execute function public.audit_pitch_base_price_change();

-- Privileges: anon gets nothing; authenticated gets select only (subject to RLS).
revoke all on table public.audit_log from public, anon, authenticated;
revoke all on sequence public.audit_log_id_seq from public, anon, authenticated;

grant select on table public.audit_log to authenticated;
grant all on table public.audit_log to service_role;
grant usage, select on sequence public.audit_log_id_seq to service_role;
