-- Restrict auth.users sign-ups to Google OAuth only (in addition to open invite requirement).

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_provider text;
  v_email text;
  v_role text;
begin
  v_provider := new.raw_app_meta_data->>'provider';
  if v_provider is not null and v_provider <> 'google' then
    raise exception 'Sign-up rejected: only Google sign-in is allowed';
  end if;

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
