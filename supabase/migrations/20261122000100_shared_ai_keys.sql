-- Shared AI keys (Vault-backed), allowed NIM models, per-member/team daily limits, usage counters, and key health.

-- 1. Allowed NVIDIA NIM models and per-model max_tokens cap.
create table public.ai_allowed_models (
  model text primary key check (char_length(model) between 1 and 200),
  enabled boolean not null default true,
  max_tokens_cap int not null default 4096 check (max_tokens_cap between 1 and 32768),
  updated_at timestamptz not null default now()
);

alter table public.ai_allowed_models enable row level security;

create or replace function public.touch_ai_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.touch_ai_updated_at() from public, anon;

create trigger trg_ai_allowed_models_updated_at
  before update on public.ai_allowed_models
  for each row
  execute function public.touch_ai_updated_at();

create policy ai_allowed_models_select_active
  on public.ai_allowed_models
  for select
  to authenticated
  using ((select public.is_active_member()));

create policy ai_allowed_models_insert_admin
  on public.ai_allowed_models
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy ai_allowed_models_update_admin
  on public.ai_allowed_models
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy ai_allowed_models_delete_admin
  on public.ai_allowed_models
  for delete
  to authenticated
  using ((select public.is_admin()));

-- Seed NIM models from config/models.json (tiers, vision_models, extra_models).
insert into public.ai_allowed_models (model, enabled, max_tokens_cap)
values
  ('nvidia/nemotron-3-ultra-550b-a55b', true, 8000),
  ('mistralai/mistral-large-4-1t-a52b', true, 8000),
  ('minimaxai/minimax-m3.1-flash', true, 8000),
  ('nvidia/nemotron-3.5-super-vl-120b-a12b', true, 4096),
  ('nvidia/nemotron-3-nano-omni-30b-a3b-reasoning', true, 4096),
  ('nvidia/nemotron-3.5-lightning-30b-a3b', true, 4096),
  ('nvidia/nemotron-3.5-bolt-e4b', true, 4096),
  ('google/gemma-4-31b-it', true, 4096),
  ('meta/llama-3.2-90b-vision-instruct', true, 4096),
  ('meta/llama-3.2-11b-vision-instruct', true, 4096),
  ('nvidia/riva-translate-4b-instruct-v2', true, 4096),
  ('nvidia/nemotron-3.5-content-safety', true, 4096),
  ('nvidia/cosmos-reason2-8b', true, 4096)
on conflict (model) do nothing;

-- 2. Daily AI limits: one team default row (owner_id is null) plus optional per-member overrides.
create table public.ai_limits (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid unique references public.profiles(id) on delete cascade,
  daily_requests int not null default 200 check (daily_requests >= 0),
  daily_tokens int not null default 400000 check (daily_tokens >= 0),
  updated_by uuid references public.profiles(id) on delete set null,
  updated_at timestamptz not null default now()
);

create unique index idx_ai_limits_team_default
  on public.ai_limits ((true))
  where owner_id is null;

alter table public.ai_limits enable row level security;

create or replace function public.stamp_ai_limits()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if auth.uid() is not null then
    new.updated_by := auth.uid();
  end if;
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.stamp_ai_limits() from public, anon;

create trigger trg_ai_limits_stamp
  before insert or update on public.ai_limits
  for each row
  execute function public.stamp_ai_limits();

create policy ai_limits_select_own_default_or_admin
  on public.ai_limits
  for select
  to authenticated
  using (
    ((owner_id is null or owner_id = (select auth.uid())) and (select public.is_active_member()))
    or (select public.is_admin())
  );

create policy ai_limits_insert_admin
  on public.ai_limits
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy ai_limits_update_admin
  on public.ai_limits
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy ai_limits_delete_admin
  on public.ai_limits
  for delete
  to authenticated
  using ((select public.is_admin()) and owner_id is not null);

-- Insert the single team default row.
insert into public.ai_limits (owner_id, daily_requests, daily_tokens)
values (null, 200, 400000);

-- 3. Daily AI usage counters (UTC day): read by owner/admin, written only by service_role functions.
create table public.ai_usage_daily (
  owner_id uuid not null references public.profiles(id) on delete cascade,
  day date not null,
  requests int not null default 0 check (requests >= 0),
  prompt_tokens bigint not null default 0 check (prompt_tokens >= 0),
  completion_tokens bigint not null default 0 check (completion_tokens >= 0),
  rejected int not null default 0 check (rejected >= 0),
  updated_at timestamptz not null default now(),
  primary key (owner_id, day)
);

alter table public.ai_usage_daily enable row level security;

create policy ai_usage_daily_select_own_or_admin
  on public.ai_usage_daily
  for select
  to authenticated
  using (
    (owner_id = (select auth.uid()) and (select public.is_active_member()))
    or (select public.is_admin())
  );

-- 4. Shared key health: read by admins, written only by service_role / admin functions.
create table public.ai_key_health (
  label text primary key check (char_length(label) between 1 and 64),
  last_status int,
  last_ok_at timestamptz,
  last_error text check (last_error is null or char_length(last_error) <= 300),
  cooldown_until timestamptz,
  updated_at timestamptz not null default now()
);

alter table public.ai_key_health enable row level security;

create policy ai_key_health_select_admin
  on public.ai_key_health
  for select
  to authenticated
  using ((select public.is_admin()));

-- 5. Vault-backed shared key management functions.

-- Admin RPC: validate, deduplicate (max 20), and replace all magnm_ai_key_<n> Vault secrets.
create or replace function public.admin_set_ai_keys(p_keys text[])
returns int
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_raw text;
  v_dedup text[] := '{}'::text[];
  v_count int := 0;
  v_idx int;
begin
  if not public.is_admin() then
    raise exception 'Admin access required';
  end if;

  if p_keys is null then
    raise exception 'Keys array is required';
  end if;

  foreach v_raw in array p_keys loop
    if v_raw is null or v_raw !~ '^nvapi-[A-Za-z0-9_-]{20,200}$' then
      raise exception 'Invalid NVIDIA NIM API key format';
    end if;
    if not (v_raw = any(v_dedup)) then
      v_dedup := array_append(v_dedup, v_raw);
    end if;
  end loop;

  v_count := coalesce(array_length(v_dedup, 1), 0);
  if v_count > 20 then
    raise exception 'At most 20 shared AI keys are allowed';
  end if;

  delete from vault.secrets
  where name ~ '^magnm_ai_key_[0-9]+$';

  for v_idx in 1..v_count loop
    perform vault.create_secret(
      v_dedup[v_idx],
      'magnm_ai_key_' || v_idx::text,
      'Magnm shared NVIDIA NIM key ' || v_idx::text
    );
  end loop;

  delete from public.ai_key_health;

  insert into public.audit_log (actor_id, action, target, data)
  values (
    auth.uid(),
    'ai_keys.updated',
    'magnm_ai_keys',
    jsonb_build_object('count', v_count)
  );

  return v_count;
end;
$$;

revoke all on function public.admin_set_ai_keys(text[]) from public, anon;
grant execute on function public.admin_set_ai_keys(text[]) to authenticated, service_role;

-- Admin RPC: list masked labels and health status without ever returning key values.
create or replace function public.admin_ai_key_labels()
returns table (
  label text,
  last_status int,
  last_ok_at timestamptz,
  cooldown_until timestamptz,
  last_error text
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Admin access required';
  end if;

  return query
  with keys as (
    select
      substring(s.name from '^magnm_ai_key_([0-9]+)$')::int as key_num,
      'key-' || substring(s.name from '^magnm_ai_key_([0-9]+)$')
        || ' (...' || right(s.decrypted_secret, 4) || ')' as key_label
    from vault.decrypted_secrets s
    where s.name ~ '^magnm_ai_key_[0-9]+$'
  )
  select
    k.key_label,
    h.last_status,
    h.last_ok_at,
    h.cooldown_until,
    h.last_error
  from keys k
  left join public.ai_key_health h on h.label = k.key_label
  order by k.key_num asc;
end;
$$;

revoke all on function public.admin_ai_key_labels() from public, anon;
grant execute on function public.admin_ai_key_labels() to authenticated, service_role;

-- Service-role only: fetch decrypted team keys for the ai-proxy Edge Function.
create or replace function public.ai_get_keys()
returns table (
  label text,
  api_key text
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    'key-' || substring(s.name from '^magnm_ai_key_([0-9]+)$')
      || ' (...' || right(s.decrypted_secret, 4) || ')' as label,
    s.decrypted_secret as api_key
  from vault.decrypted_secrets s
  where s.name ~ '^magnm_ai_key_[0-9]+$'
  order by substring(s.name from '^magnm_ai_key_([0-9]+)$')::int asc;
$$;

revoke all on function public.ai_get_keys() from public, anon, authenticated;
grant execute on function public.ai_get_keys() to service_role;

-- Service-role only: atomically check daily quota and increment request or rejected count.
create or replace function public.ai_check_and_count(
  p_owner uuid,
  p_estimated_tokens int
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_active boolean;
  v_est bigint := greatest(coalesce(p_estimated_tokens, 0), 0);
  v_limit_requests int := 200;
  v_limit_tokens bigint := 400000;
  v_day date := (now() at time zone 'UTC')::date;
  v_used_requests int := 0;
  v_used_tokens bigint := 0;
begin
  select p.active into v_active
  from public.profiles p
  where p.id = p_owner;

  if v_active is distinct from true then
    return jsonb_build_object(
      'allowed', false,
      'reason', 'inactive_or_missing_profile',
      'requests_left', 0,
      'tokens_left', 0
    );
  end if;

  select l.daily_requests, l.daily_tokens
  into v_limit_requests, v_limit_tokens
  from public.ai_limits l
  where l.owner_id = p_owner or l.owner_id is null
  order by (l.owner_id is not null) desc
  limit 1;

  v_limit_requests := coalesce(v_limit_requests, 200);
  v_limit_tokens := coalesce(v_limit_tokens, 400000);

  insert into public.ai_usage_daily (owner_id, day)
  values (p_owner, v_day)
  on conflict (owner_id, day) do nothing;

  select
    u.requests,
    u.prompt_tokens + u.completion_tokens
  into v_used_requests, v_used_tokens
  from public.ai_usage_daily u
  where u.owner_id = p_owner
    and u.day = v_day
  for update;

  if v_used_requests >= v_limit_requests then
    update public.ai_usage_daily
    set rejected = rejected + 1,
        updated_at = now()
    where owner_id = p_owner
      and day = v_day;

    return jsonb_build_object(
      'allowed', false,
      'reason', 'daily_requests_limit',
      'requests_left', 0,
      'tokens_left', greatest(v_limit_tokens - v_used_tokens, 0)
    );
  end if;

  if v_used_tokens >= v_limit_tokens or (v_used_tokens + v_est) > v_limit_tokens then
    update public.ai_usage_daily
    set rejected = rejected + 1,
        updated_at = now()
    where owner_id = p_owner
      and day = v_day;

    return jsonb_build_object(
      'allowed', false,
      'reason', 'daily_tokens_limit',
      'requests_left', greatest(v_limit_requests - v_used_requests, 0),
      'tokens_left', greatest(v_limit_tokens - v_used_tokens, 0)
    );
  end if;

  update public.ai_usage_daily
  set requests = requests + 1,
      updated_at = now()
  where owner_id = p_owner
    and day = v_day;

  return jsonb_build_object(
    'allowed', true,
    'reason', null,
    'requests_left', greatest(v_limit_requests - (v_used_requests + 1), 0),
    'tokens_left', greatest(v_limit_tokens - v_used_tokens, 0)
  );
end;
$$;

revoke all on function public.ai_check_and_count(uuid, int) from public, anon, authenticated;
grant execute on function public.ai_check_and_count(uuid, int) to service_role;

-- Service-role only: record prompt and completion tokens for today's UTC usage row.
create or replace function public.ai_record_usage(
  p_owner uuid,
  p_prompt_tokens int,
  p_completion_tokens int
)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into public.ai_usage_daily (owner_id, day, prompt_tokens, completion_tokens, updated_at)
  values (
    p_owner,
    (now() at time zone 'UTC')::date,
    greatest(coalesce(p_prompt_tokens, 0), 0),
    greatest(coalesce(p_completion_tokens, 0), 0),
    now()
  )
  on conflict (owner_id, day) do update
    set prompt_tokens = public.ai_usage_daily.prompt_tokens + excluded.prompt_tokens,
        completion_tokens = public.ai_usage_daily.completion_tokens + excluded.completion_tokens,
        updated_at = now();
$$;

revoke all on function public.ai_record_usage(uuid, int, int) from public, anon, authenticated;
grant execute on function public.ai_record_usage(uuid, int, int) to service_role;

-- Service-role only: upsert health status and optional cooldown for a key label.
create or replace function public.ai_report_key(
  p_label text,
  p_status int,
  p_error text,
  p_cooldown_seconds int
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_label text := left(btrim(coalesce(p_label, '')), 64);
  v_error text;
  v_ok_at timestamptz;
  v_cooldown timestamptz;
begin
  if v_label = '' then
    return;
  end if;

  if p_error is not null and btrim(p_error) <> '' then
    v_error := left(btrim(p_error), 300);
  end if;

  if p_status between 200 and 299 then
    v_ok_at := now();
  end if;

  if coalesce(p_cooldown_seconds, 0) > 0 then
    v_cooldown := now() + make_interval(secs => p_cooldown_seconds);
  end if;

  insert into public.ai_key_health (label, last_status, last_ok_at, last_error, cooldown_until, updated_at)
  values (v_label, p_status, v_ok_at, v_error, v_cooldown, now())
  on conflict (label) do update
    set last_status = excluded.last_status,
        last_ok_at = coalesce(excluded.last_ok_at, public.ai_key_health.last_ok_at),
        last_error = excluded.last_error,
        cooldown_until = excluded.cooldown_until,
        updated_at = now();
end;
$$;

revoke all on function public.ai_report_key(text, int, text, int) from public, anon, authenticated;
grant execute on function public.ai_report_key(text, int, text, int) to service_role;

-- Privileges: anon gets nothing; authenticated gets only the intended table privileges under RLS.
revoke all on table public.ai_allowed_models from public, anon;
revoke all on table public.ai_limits from public, anon;
revoke all on table public.ai_usage_daily from public, anon, authenticated;
revoke all on table public.ai_key_health from public, anon, authenticated;

grant select, insert, update, delete on table public.ai_allowed_models to authenticated;
grant select, insert, update, delete on table public.ai_limits to authenticated;
grant select on table public.ai_usage_daily to authenticated;
grant select on table public.ai_key_health to authenticated;

grant all on table public.ai_allowed_models to service_role;
grant all on table public.ai_limits to service_role;
grant all on table public.ai_usage_daily to service_role;
grant all on table public.ai_key_health to service_role;
