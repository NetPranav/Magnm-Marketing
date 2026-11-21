-- Privacy and invariant tests for Team Core (profiles, invites, invite-only sign-up, team_directory).
begin;
set local client_min_messages = notice;

-- Clean test identities if any exist in transaction.
do $$
declare
  v_admin1 uuid := '11111111-1111-4111-8111-111111111101';
  v_admin2 uuid := '11111111-1111-4111-8111-111111111102';
  v_member1 uuid := '11111111-1111-4111-8111-111111111103';
  v_member2 uuid := '11111111-1111-4111-8111-111111111104';
  v_uninvited uuid := '11111111-1111-4111-8111-111111111199';
  v_revoked uuid := '11111111-1111-4111-8111-111111111198';
  v_email_provider uuid := '11111111-1111-4111-8111-111111111197';
  v_role text;
  v_email text;
  v_accepted timestamptz;
  v_revoked_at timestamptz;
  v_profile_count integer;
begin
  -- 1. Sign-up without an invite fails.
  begin
    insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
    values (
      v_uninvited,
      'intruder@example.com',
      '{"provider":"google","providers":["google"]}'::jsonb,
      '{"full_name":"Intruder"}'::jsonb
    );
    raise exception 'FAIL: sign-up without invite succeeded';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: sign-up without an invite fails';
  end;

  -- 2. Sign-up with a revoked invite fails.
  insert into public.invites (email, role, revoked_at)
  values ('revoked@example.com', 'member', now());

  begin
    insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
    values (
      v_revoked,
      'revoked@example.com',
      '{"provider":"google","providers":["google"]}'::jsonb,
      '{"full_name":"Revoked User"}'::jsonb
    );
    raise exception 'FAIL: sign-up with revoked invite succeeded';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: sign-up with a revoked invite fails';
  end;

  -- 2b. Invited email signing up with provider "email" is rejected and invite stays open.
  insert into public.invites (email, role)
  values ('email.only@example.com', 'member');

  begin
    insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
    values (
      v_email_provider,
      'email.only@example.com',
      '{"provider":"email","providers":["email"]}'::jsonb,
      '{"full_name":"Email Provider"}'::jsonb
    );
    raise exception 'FAIL: sign-up with email provider succeeded';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
  end;

  select accepted_at, revoked_at into v_accepted, v_revoked_at
  from public.invites
  where email = 'email.only@example.com';

  select count(*) into v_profile_count
  from public.profiles
  where email = 'email.only@example.com';

  if v_accepted is not null or v_revoked_at is not null or v_profile_count <> 0 then
    raise exception 'FAIL: rejected email-provider sign-up mutated invite or created profile';
  end if;
  raise notice 'PASS: invited email with provider email is rejected and invite stays open';

  -- 3. Sign-up with an open invite creates profile with right role (email case ignored).
  insert into public.invites (email, role)
  values ('Admin.One@Example.com', 'admin');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values (
    v_admin1,
    'ADMIN.ONE@EXAMPLE.COM',
    '{"provider":"google","providers":["google"]}'::jsonb,
    '{"full_name":"Admin One","avatar_url":"https://example.com/a1.png"}'::jsonb
  );

  select role, email into v_role, v_email
  from public.profiles
  where id = v_admin1;

  if v_role is distinct from 'admin' or v_email is distinct from 'admin.one@example.com' then
    raise exception 'FAIL: expected admin profile with lowercase email, got role=% email=%', v_role, v_email;
  end if;

  select accepted_at into v_accepted
  from public.invites
  where email = 'admin.one@example.com';

  if v_accepted is null then
    raise exception 'FAIL: invite accepted_at was not stamped';
  end if;
  raise notice 'PASS: sign-up with an invite creates the right role (email case ignored)';

  -- Create member 1 and member 2 via invites.
  insert into public.invites (email, role, invited_by)
  values
    ('member.one@example.com', 'member', v_admin1),
    ('member.two@example.com', 'member', v_admin1);

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (
      v_member1,
      'Member.One@example.com',
      '{"provider":"google","providers":["google"]}'::jsonb,
      '{"full_name":"Member One","avatar_url":"https://example.com/m1.png"}'::jsonb
    ),
    (
      v_member2,
      'member.two@example.com',
      '{"provider":"google","providers":["google"]}'::jsonb,
      '{"full_name":"Member Two","avatar_url":"https://example.com/m2.png"}'::jsonb
    );

  select role into v_role from public.profiles where id = v_member1;
  if v_role is distinct from 'member' then
    raise exception 'FAIL: member1 role should be member, got %', v_role;
  end if;
  raise notice 'PASS: member sign-up via invite gets member role';
end;
$$;

-- Switch to member 1 and test member permissions.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '11111111-1111-4111-8111-111111111103', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member1 uuid := '11111111-1111-4111-8111-111111111103';
  v_count integer;
  v_has_email_col boolean;
begin
  -- Member can read only their own profile row (not teammates' profiles/emails).
  select count(*) into v_count from public.profiles;
  if v_count <> 1 then
    raise exception 'FAIL: member should see only 1 profile row, saw %', v_count;
  end if;
  raise notice 'PASS: member reads only their own profile row';

  -- Member can update their own display_name, avatar_url, last_seen_at.
  update public.profiles
  set display_name = 'Member One Updated',
      avatar_url = 'https://example.com/m1-new.png',
      last_seen_at = now()
  where id = v_member1;

  -- Member cannot change their own role.
  begin
    update public.profiles set role = 'admin' where id = v_member1;
    raise exception 'FAIL: member changed own role to admin';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot change their own role';
  end;

  -- Member cannot change their own active flag.
  begin
    update public.profiles set active = false where id = v_member1;
    raise exception 'FAIL: member changed own active status';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot change their own active flag';
  end;

  -- Member cannot change their own email.
  begin
    update public.profiles set email = 'other@example.com' where id = v_member1;
    raise exception 'FAIL: member changed own email';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot change their own email';
  end;

  -- Member cannot read invites.
  select count(*) into v_count from public.invites;
  if v_count <> 0 then
    raise exception 'FAIL: member should see 0 invites, saw %', v_count;
  end if;
  raise notice 'PASS: member cannot read invites';

  -- Member cannot create an invite.
  begin
    insert into public.invites (email, role) values ('sneaky@example.com', 'member');
    raise exception 'FAIL: member inserted an invite';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot create invites';
  end;

  -- Team directory shows active teammates without email.
  select count(*) into v_count from public.team_directory;
  if v_count < 3 then
    raise exception 'FAIL: expected at least 3 active teammates in team_directory, got %', v_count;
  end if;

  select exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'team_directory'
      and column_name = 'email'
  ) into v_has_email_col;

  if v_has_email_col then
    raise exception 'FAIL: team_directory exposes email column';
  end if;
  raise notice 'PASS: team_directory shows active teammates and hides emails';
end;
$$;

-- Switch to Admin 1 to test admin capabilities and last-admin protection.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '11111111-1111-4111-8111-111111111101', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_admin1 uuid := '11111111-1111-4111-8111-111111111101';
  v_member2 uuid := '11111111-1111-4111-8111-111111111104';
  v_count integer;
begin
  -- Admin can read all profiles and invites.
  select count(*) into v_count from public.profiles;
  if v_count < 3 then
    raise exception 'FAIL: admin should see all profiles, saw %', v_count;
  end if;

  select count(*) into v_count from public.invites;
  if v_count < 4 then
    raise exception 'FAIL: admin should see all invites, saw %', v_count;
  end if;
  raise notice 'PASS: admin can read all profiles and invites';

  -- Admin can create, revoke, and delete invites.
  insert into public.invites (email, role) values ('temp.invite@example.com', 'member');
  update public.invites set revoked_at = now() where email = 'temp.invite@example.com';
  delete from public.invites where email = 'temp.invite@example.com';
  raise notice 'PASS: admin can create, revoke, and delete invites';

  -- The last active admin cannot be demoted.
  begin
    update public.profiles set role = 'member' where id = v_admin1;
    raise exception 'FAIL: last active admin was demoted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: last active admin cannot be demoted';
  end;

  -- The last active admin cannot be deactivated.
  begin
    update public.profiles set active = false where id = v_admin1;
    raise exception 'FAIL: last active admin was deactivated';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: last active admin cannot be deactivated';
  end;

  -- Admin can deactivate member 2.
  update public.profiles set active = false where id = v_member2;
  raise notice 'PASS: admin can deactivate a member';
end;
$$;

-- Switch to deactivated member 2 and verify they see nothing.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '11111111-1111-4111-8111-111111111104', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member2 uuid := '11111111-1111-4111-8111-111111111104';
  v_count integer;
begin
  if public.is_active_member() then
    raise exception 'FAIL: deactivated member is_active_member() returned true';
  end if;

  select count(*) into v_count from public.profiles;
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member saw % profiles', v_count;
  end if;

  select count(*) into v_count from public.team_directory;
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member saw % rows in team_directory', v_count;
  end if;

  update public.profiles set display_name = 'Should Not Work' where id = v_member2;
  raise notice 'PASS: deactivated member sees nothing';
end;
$$;

reset role;
rollback;
