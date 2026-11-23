-- Phone actions: a cloud queue for things done on the phone while the laptop is away
-- (comment on a draft, mark it posted, finish a reminder). The laptop pulls the owner's own queue and applies it.
-- The kind list is a check constraint: a later migration adds a kind by replacing the two constraints below.

create table public.phone_actions (
  -- Made by the phone (a random uuid), so sending the same action twice is harmless.
  id uuid primary key,
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  kind text not null check (kind in ('draft_comment', 'draft_posted', 'reminder_done')),
  -- The local id of the draft or reminder the action is about.
  target_id text not null check (char_length(target_id) between 1 and 200 and target_id !~ '[[:cntrl:]]'),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued', 'applying', 'applied', 'rejected')),
  -- A short reason or confirmation written by the laptop. Never the text of the action.
  result text check (result is null or (char_length(result) <= 300 and result !~ '[[:cntrl:]]')),
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  finished_at timestamptz,
  applied_by_device text check (applied_by_device is null or char_length(applied_by_device) between 1 and 128),
  expires_at timestamptz not null default now() + interval '30 days',
  constraint phone_actions_payload_shape check (
    jsonb_typeof(payload) = 'object'
    and octet_length(payload::text) <= 8192
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
      else false
    end, false)
  )
);

create index idx_phone_actions_owner_status on public.phone_actions (owner_id, status, created_at);

alter table public.phone_actions enable row level security;

-- At most 200 waiting actions per owner. The advisory lock serializes concurrent inserts of one owner.
create or replace function public.limit_phone_action_queue()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('phone_actions:' || new.owner_id::text, 0));
  if (
    select count(*) from public.phone_actions
    where owner_id = new.owner_id and status in ('queued', 'applying')
  ) >= 200 then
    -- A plain exception (P0001) reaches the phone as HTTP 400 with this text, so it can show the reason.
    raise exception 'Too many waiting phone actions (limit 200)';
  end if;
  return new;
end;
$$;

revoke all on function public.limit_phone_action_queue() from public, anon, authenticated;

create trigger trg_phone_actions_limit
  before insert on public.phone_actions
  for each row
  execute function public.limit_phone_action_queue();

-- What a phone queued never changes, and an outcome never walks back.
-- Allowed moves: queued -> applying | rejected, applying -> applied | rejected.
create or replace function public.guard_phone_action_update()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.id is distinct from old.id
     or new.owner_id is distinct from old.owner_id
     or new.kind is distinct from old.kind
     or new.target_id is distinct from old.target_id
     or new.payload is distinct from old.payload
     or new.created_at is distinct from old.created_at
     or new.expires_at is distinct from old.expires_at then
    raise exception 'A phone action cannot be edited after it is queued';
  end if;
  if old.status in ('applied', 'rejected') then
    raise exception 'A finished phone action cannot change';
  end if;
  if not (
    new.status = old.status
    or (old.status = 'queued' and new.status in ('applying', 'rejected'))
    or (old.status = 'applying' and new.status in ('applied', 'rejected'))
  ) then
    raise exception 'A phone action cannot move from % to %', old.status, new.status;
  end if;
  return new;
end;
$$;

revoke all on function public.guard_phone_action_update() from public, anon, authenticated;

create trigger trg_phone_actions_guard
  before update on public.phone_actions
  for each row
  execute function public.guard_phone_action_update();

-- RLS: the owner reads and queues their own. Admins read (like the mirror) but never change anything.
create policy phone_actions_select_own_or_admin
  on public.phone_actions
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy phone_actions_insert_own
  on public.phone_actions
  for insert
  to authenticated
  with check (
    owner_id = (select auth.uid())
    and (select public.is_active_member())
    and status = 'queued'
  );

-- Privileges: no update or delete from any client, and inserts may only set what the phone decides.
revoke all on table public.phone_actions from public, anon, authenticated;
grant select on table public.phone_actions to authenticated;
grant insert (id, kind, target_id, payload) on table public.phone_actions to authenticated;
grant all on table public.phone_actions to service_role;

-- The laptop claims one action. Only one device wins; the claiming device can claim again after a crash.
-- A queued action past its expiry is closed with a reason instead of being claimed.
create or replace function public.claim_phone_action(p_id uuid, p_device text)
returns public.phone_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
  v_row public.phone_actions;
begin
  if v_uid is null or not public.is_active_member() then
    raise exception 'Not allowed' using errcode = '42501';
  end if;
  if p_device is null or char_length(p_device) not between 1 and 128 then
    raise exception 'A device id is required' using errcode = '22023';
  end if;

  update public.phone_actions
     set status = 'rejected',
         result = 'Expired before the laptop applied it',
         finished_at = now()
   where owner_id = v_uid and status = 'queued' and expires_at <= now();
  delete from public.phone_actions
   where owner_id = v_uid and status in ('applied', 'rejected') and expires_at <= now() - interval '7 days';

  update public.phone_actions
     set status = 'applying', applied_by_device = p_device, claimed_at = now()
   where id = p_id and owner_id = v_uid and status = 'queued' and expires_at > now()
  returning * into v_row;
  if found then
    return v_row;
  end if;

  select * into v_row
    from public.phone_actions
   where id = p_id and owner_id = v_uid and status = 'applying' and applied_by_device = p_device;
  if found then
    return v_row;
  end if;
  return null;
end;
$$;

-- The claiming laptop records the outcome once. Repeating the same outcome is harmless.
create or replace function public.finish_phone_action(p_id uuid, p_device text, p_status text, p_result text)
returns public.phone_actions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
  v_row public.phone_actions;
  v_result text;
begin
  if v_uid is null or not public.is_active_member() then
    raise exception 'Not allowed' using errcode = '42501';
  end if;
  if p_status is null or p_status not in ('applied', 'rejected') then
    raise exception 'Status must be applied or rejected' using errcode = '22023';
  end if;
  if p_device is null or char_length(p_device) not between 1 and 128 then
    raise exception 'A device id is required' using errcode = '22023';
  end if;
  v_result := nullif(left(btrim(regexp_replace(coalesce(p_result, ''), '[[:cntrl:]]', ' ', 'g')), 300), '');

  update public.phone_actions
     set status = p_status, result = v_result, finished_at = now()
   where id = p_id and owner_id = v_uid and status = 'applying' and applied_by_device = p_device
  returning * into v_row;
  if found then
    return v_row;
  end if;

  select * into v_row
    from public.phone_actions
   where id = p_id and owner_id = v_uid and status = p_status and applied_by_device = p_device;
  if found then
    return v_row;
  end if;
  return null;
end;
$$;

-- New functions are executable by signed-in clients by default: take that back, then grant only what clients need.
revoke all on function public.claim_phone_action(uuid, text) from public, anon, authenticated;
revoke all on function public.finish_phone_action(uuid, text, text, text) from public, anon, authenticated;
grant execute on function public.claim_phone_action(uuid, text) to authenticated, service_role;
grant execute on function public.finish_phone_action(uuid, text, text, text) to authenticated, service_role;
