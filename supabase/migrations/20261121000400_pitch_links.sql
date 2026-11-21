-- Pitch links (published proposals) and quote form submissions, plus public RPCs for clients.

-- Proposals, final quotations, and default quote forms published with a random share_id.
create table public.published_proposals (
  share_id text primary key check (share_id ~ '^[A-Za-z0-9_-]{16,64}$'),
  owner_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  local_proposal_id text,
  kind text not null check (kind in ('proposal', 'final', 'default_quote')),
  payload jsonb not null check (octet_length(payload::text) <= 524288),
  status text not null default 'live' check (status in ('live', 'withdrawn')),
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.published_proposals enable row level security;

-- Keep updated_at current and prevent changing owner_id on published_proposals.
create or replace function public.before_update_published_proposal()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.owner_id is distinct from old.owner_id then
    raise exception 'Cannot change owner_id of a published proposal';
  end if;
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.before_update_published_proposal() from public, anon;

create trigger trg_published_proposals_before_update
  before update on public.published_proposals
  for each row
  execute function public.before_update_published_proposal();

-- RLS policies for published_proposals: owner manages own rows; admin reads all.
create policy published_proposals_select_own_or_admin
  on public.published_proposals
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy published_proposals_insert_own
  on public.published_proposals
  for insert
  to authenticated
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy published_proposals_update_own
  on public.published_proposals
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

create policy published_proposals_delete_own
  on public.published_proposals
  for delete
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Quote form submissions sent by clients through submit_quote().
create table public.quote_submissions (
  id uuid primary key default gen_random_uuid(),
  share_id text not null references public.published_proposals(share_id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  choices jsonb not null check (octet_length(choices::text) <= 65536),
  contact jsonb not null check (octet_length(contact::text) <= 8192),
  created_at timestamptz not null default now(),
  seen_at timestamptz
);

create index idx_quote_submissions_share_created
  on public.quote_submissions (share_id, created_at desc);

create index idx_quote_submissions_owner_created
  on public.quote_submissions (owner_id, created_at desc);

alter table public.quote_submissions enable row level security;

-- Only seen_at may be updated on quote_submissions.
create or replace function public.guard_quote_submission_update()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.id is distinct from old.id
    or new.share_id is distinct from old.share_id
    or new.owner_id is distinct from old.owner_id
    or new.choices is distinct from old.choices
    or new.contact is distinct from old.contact
    or new.created_at is distinct from old.created_at
  then
    raise exception 'Only seen_at may be updated on quote_submissions';
  end if;
  return new;
end;
$$;

revoke all on function public.guard_quote_submission_update() from public, anon;

create trigger trg_quote_submissions_guard_update
  before update on public.quote_submissions
  for each row
  execute function public.guard_quote_submission_update();

-- RLS policies for quote_submissions: owner reads own and updates seen_at; admin reads all.
create policy quote_submissions_select_own_or_admin
  on public.quote_submissions
  for select
  to authenticated
  using ((owner_id = (select auth.uid()) and (select public.is_active_member())) or (select public.is_admin()));

create policy quote_submissions_update_own
  on public.quote_submissions
  for update
  to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_active_member()))
  with check (owner_id = (select auth.uid()) and (select public.is_active_member()));

-- Client RPC: fetch a live, unexpired published proposal payload plus kind; returns null otherwise.
create or replace function public.get_published_proposal(p_share_id text)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_kind text;
  v_payload jsonb;
begin
  if p_share_id is null or p_share_id !~ '^[A-Za-z0-9_-]{16,64}$' then
    return null;
  end if;

  select p.kind, p.payload
  into v_kind, v_payload
  from public.published_proposals p
  where p.share_id = p_share_id
    and p.status = 'live'
    and (p.expires_at is null or p.expires_at > now());

  if v_kind is null then
    return null;
  end if;


  return jsonb_build_object('kind', v_kind, 'payload', v_payload);
end;
$$;

revoke all on function public.get_published_proposal(text) from public;
grant execute on function public.get_published_proposal(text) to anon, authenticated, service_role;

-- Client RPC: submit a quote response for a live, unexpired proposal (max 20 per share_id in 24h).
create or replace function public.submit_quote(
  p_share_id text,
  p_choices jsonb,
  p_contact jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner_id uuid;
  v_recent_count integer;
  v_new_id uuid;
begin
  if p_share_id is null or p_share_id !~ '^[A-Za-z0-9_-]{16,64}$' then
    raise exception 'Invalid or unavailable proposal link';
  end if;

  if p_choices is null or p_contact is null then
    raise exception 'Choices and contact are required';
  end if;

  if octet_length(p_choices::text) > 65536 then
    raise exception 'Choices exceed 64 KB limit';
  end if;

  if octet_length(p_contact::text) > 8192 then
    raise exception 'Contact exceeds 8 KB limit';
  end if;

  select p.owner_id
  into v_owner_id
  from public.published_proposals p
  where p.share_id = p_share_id
    and p.status = 'live'
    and (p.expires_at is null or p.expires_at > now())
  for update;

  if v_owner_id is null then
    raise exception 'Invalid or unavailable proposal link';
  end if;

  select count(*)
  into v_recent_count
  from public.quote_submissions q
  where q.share_id = p_share_id
    and q.created_at > now() - interval '24 hours';

  if v_recent_count >= 20 then
    raise exception 'Rate limit exceeded for this proposal link';
  end if;

  insert into public.quote_submissions (share_id, owner_id, choices, contact)
  values (p_share_id, v_owner_id, p_choices, p_contact)
  returning id into v_new_id;

  return v_new_id;
end;
$$;

revoke all on function public.submit_quote(text, jsonb, jsonb) from public;
grant execute on function public.submit_quote(text, jsonb, jsonb) to anon, authenticated, service_role;

-- Privileges: clients never get table access.
revoke all on table public.published_proposals from public, anon;
revoke all on table public.quote_submissions from public, anon;

grant select, insert, update, delete on table public.published_proposals to authenticated;
grant select, update on table public.quote_submissions to authenticated;

grant all on table public.published_proposals to service_role;
grant all on table public.quote_submissions to service_role;
