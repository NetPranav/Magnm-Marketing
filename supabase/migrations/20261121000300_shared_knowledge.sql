-- Shared knowledge and Pitch base prices: admin writes, every active member reads.

-- Shared team documents (brand kit, product rules, agent instructions, templates).
create table public.shared_knowledge (
  key text primary key check (key in ('brand_kit', 'product_rules', 'agent_instructions', 'templates')),
  data jsonb not null,
  updated_by uuid references public.profiles(id) on delete set null,
  updated_at timestamptz not null default now()
);

alter table public.shared_knowledge enable row level security;

-- Base prices for Magnm-Pitch line items.
create table public.pitch_base_prices (
  item_id text primary key check (char_length(item_id) between 1 and 64),
  price integer not null check (price >= 0),
  updated_by uuid references public.profiles(id) on delete set null,
  updated_at timestamptz not null default now()
);

alter table public.pitch_base_prices enable row level security;

-- Trigger function to stamp updated_by = auth.uid() and updated_at = now().
create or replace function public.set_updated_stamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.updated_by := auth.uid();
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.set_updated_stamp() from public, anon;

create trigger trg_shared_knowledge_stamp
  before insert or update on public.shared_knowledge
  for each row
  execute function public.set_updated_stamp();

create trigger trg_pitch_base_prices_stamp
  before insert or update on public.pitch_base_prices
  for each row
  execute function public.set_updated_stamp();

-- RLS policies for shared_knowledge.
create policy shared_knowledge_select_active
  on public.shared_knowledge
  for select
  to authenticated
  using ((select public.is_active_member()));

create policy shared_knowledge_insert_admin
  on public.shared_knowledge
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy shared_knowledge_update_admin
  on public.shared_knowledge
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy shared_knowledge_delete_admin
  on public.shared_knowledge
  for delete
  to authenticated
  using ((select public.is_admin()));

-- RLS policies for pitch_base_prices.
create policy pitch_base_prices_select_active
  on public.pitch_base_prices
  for select
  to authenticated
  using ((select public.is_active_member()));

create policy pitch_base_prices_insert_admin
  on public.pitch_base_prices
  for insert
  to authenticated
  with check ((select public.is_admin()));

create policy pitch_base_prices_update_admin
  on public.pitch_base_prices
  for update
  to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

create policy pitch_base_prices_delete_admin
  on public.pitch_base_prices
  for delete
  to authenticated
  using ((select public.is_admin()));

-- Privileges: anon gets nothing.
revoke all on table public.shared_knowledge from public, anon;
revoke all on table public.pitch_base_prices from public, anon;

grant select, insert, update, delete on table public.shared_knowledge to authenticated;
grant select, insert, update, delete on table public.pitch_base_prices to authenticated;

grant all on table public.shared_knowledge to service_role;
grant all on table public.pitch_base_prices to service_role;
