-- Privacy, constraint and audit tests for team_repos (global repositories an admin adds for the whole team).
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '88888888-8888-4888-8888-888888888801';
  v_member uuid := '88888888-8888-4888-8888-888888888802';
  v_deactivated uuid := '88888888-8888-4888-8888-888888888803';
begin
  insert into public.invites (email, role)
  values
    ('admin.tr@example.com', 'admin'),
    ('member.tr@example.com', 'member'),
    ('deact.tr@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.tr@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin TR"}'::jsonb),
    (v_member, 'member.tr@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member TR"}'::jsonb),
    (v_deactivated, 'deact.tr@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deactivated TR"}'::jsonb);

  update public.profiles set active = false where id = v_deactivated;
end;
$$;

-- Admin: constraints, stamping, case-insensitive uniqueness, audit.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '88888888-8888-4888-8888-888888888801', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_admin uuid := '88888888-8888-4888-8888-888888888801';
  v_row public.team_repos;
  v_count int;
  v_bad text;
begin
  -- Hostile or malformed values are rejected by the table checks.
  foreach v_bad in array array[
    'owner', 'owner/', '/repo', 'owner/repo/extra', 'owner/..', 'owner/.', '-owner/repo', 'own er/repo',
    'owner/repo name', 'owner/repo;rm', 'owner/re$po', 'https://github.com/owner/repo', 'git@github.com:owner/repo',
    'owner/repo?x=1', 'owner/repo#frag', E'owner/repo\n', 'ow..ner/repo/..'
  ] loop
    begin
      insert into public.team_repos (id, github, name, product_id) values ('bad-slug', v_bad, 'Bad', 'bad-slug');
      raise exception 'FAIL: team_repos accepted github value %', v_bad;
    exception
      when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: team_repos rejects malformed github slugs';

  foreach v_bad in array array['Upper', '-lead', 'has space', 'a/b', '../x', '', repeat('a', 64)] loop
    begin
      insert into public.team_repos (id, github, name, product_id) values (v_bad, 'acme/ok-id', 'Ok', 'ok-id');
      raise exception 'FAIL: team_repos accepted id %', v_bad;
    exception
      when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: team_repos rejects malformed ids';

  begin
    insert into public.team_repos (id, github, name, product_id) values ('long-name', 'acme/long-name', repeat('n', 81), 'long-name');
    raise exception 'FAIL: team_repos accepted an 81 character name';
  exception when check_violation then null;
  end;
  begin
    insert into public.team_repos (id, github, name, product_id) values ('ctl-name', 'acme/ctl-name', E'Line\nbreak', 'ctl-name');
    raise exception 'FAIL: team_repos accepted a name with a control character';
  exception when check_violation then null;
  end;
  begin
    insert into public.team_repos (id, github, name, product_id) values ('bad-prod', 'acme/bad-prod', 'Name', 'Bad Product');
    raise exception 'FAIL: team_repos accepted a malformed product_id';
  exception when check_violation then null;
  end;
  foreach v_bad in array array['--upload-pack=x', '../main', 'a..b', 'feature/', 'a//b', 'x.lock', 'with space', repeat('b', 101)] loop
    begin
      insert into public.team_repos (id, github, name, product_id, branch)
      values ('bad-branch', 'acme/bad-branch', 'Name', 'bad-branch', v_bad);
      raise exception 'FAIL: team_repos accepted branch %', v_bad;
    exception when check_violation then null;
    end;
  end loop;
  begin
    insert into public.team_repos (id, github, name, product_id, status)
    values ('bad-status', 'acme/bad-status', 'Name', 'bad-status', 'live');
    raise exception 'FAIL: team_repos accepted an unknown status';
  exception when check_violation then null;
  end;
  raise notice 'PASS: team_repos caps and checks name, product_id, branch and status';

  -- A valid insert is stamped from the session, whatever the client sends.
  insert into public.team_repos (id, github, name, product_id, branch, confidential, created_by, updated_by, created_at)
  values ('team-app', 'Acme/Team-App', 'Team App', 'team-app', 'main', true,
          '00000000-0000-4000-8000-000000000000', '00000000-0000-4000-8000-000000000000', '2000-01-01')
  returning * into v_row;
  if v_row.created_by is distinct from v_admin or v_row.updated_by is distinct from v_admin
     or v_row.created_at < now() - interval '1 minute' then
    raise exception 'FAIL: team_repos insert was not stamped by the trigger';
  end if;
  raise notice 'PASS: team_repos insert is stamped by trigger';

  begin
    insert into public.team_repos (id, github, name, product_id) values ('team-app-2', 'acme/team-app', 'Dup', 'team-app-2');
    raise exception 'FAIL: team_repos accepted a case-insensitive duplicate github slug';
  exception when unique_violation then null;
  end;
  raise notice 'PASS: team_repos github slug is unique ignoring case';

  update public.team_repos set name = 'Team App Renamed', created_by = null where id = 'team-app' returning * into v_row;
  if v_row.created_by is distinct from v_admin or v_row.updated_by is distinct from v_admin then
    raise exception 'FAIL: team_repos update changed the creation stamp';
  end if;
  begin
    update public.team_repos set id = 'other-id' where id = 'team-app';
    raise exception 'FAIL: team_repos id was changed';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: team_repos id and creation stamp are immutable';

  insert into public.team_repos (id, github, name, product_id) values ('team-two', 'acme/team-two', 'Team Two', 'team-two');
  delete from public.team_repos where id = 'team-two';

  select count(*) into v_count
  from public.audit_log
  where actor_id = v_admin
    and action in ('team_repo.created', 'team_repo.updated', 'team_repo.deleted');
  if v_count < 4 then
    raise exception 'FAIL: expected audit_log rows for the team_repos lifecycle, got %', v_count;
  end if;
  raise notice 'PASS: admin changes to team_repos are audited';
end;
$$;

-- Active member: can read, cannot write in any way.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '88888888-8888-4888-8888-888888888802', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_name text;
  v_count int;
  v_audit int;
begin
  select name into v_name from public.team_repos where id = 'team-app';
  if v_name is distinct from 'Team App Renamed' then
    raise exception 'FAIL: active member cannot read team_repos';
  end if;

  begin
    insert into public.team_repos (id, github, name, product_id) values ('member-add', 'acme/member-add', 'Member', 'member-add');
    raise exception 'FAIL: member inserted into team_repos';
  exception when insufficient_privilege then null;
  end;

  update public.team_repos set confidential = false, name = 'Hijacked' where id = 'team-app';
  delete from public.team_repos where id = 'team-app';
  select name into v_name from public.team_repos where id = 'team-app';
  if v_name is distinct from 'Team App Renamed' then
    raise exception 'FAIL: member modified or deleted team_repos';
  end if;
  if not (select confidential from public.team_repos where id = 'team-app') then
    raise exception 'FAIL: member downgraded a confidential team repo';
  end if;
  raise notice 'PASS: active member can read but not insert, update or delete team_repos';

  select count(*) into v_audit from public.audit_log;
  if v_audit <> 0 then
    raise exception 'FAIL: member can read the audit log';
  end if;
end;
$$;

-- Deactivated member sees nothing and cannot write.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '88888888-8888-4888-8888-888888888803', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.team_repos;
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member read team_repos';
  end if;
  begin
    insert into public.team_repos (id, github, name, product_id) values ('deact-add', 'acme/deact-add', 'Deact', 'deact-add');
    raise exception 'FAIL: deactivated member inserted into team_repos';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: deactivated member cannot read or write team_repos';
end;
$$;

-- anon has no access at all.
reset role;
set local role anon;
select set_config('request.jwt.claims', '{}', true);

do $$
begin
  begin
    perform count(*) from public.team_repos;
    raise exception 'FAIL: anon selected from team_repos';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.team_repos (id, github, name, product_id) values ('anon-add', 'acme/anon-add', 'Anon', 'anon-add');
    raise exception 'FAIL: anon inserted into team_repos';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon has zero access to team_repos';
end;
$$;

reset role;

do $$
begin
  if has_function_privilege('anon', 'public.stamp_team_repo()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.audit_team_repo_change()', 'EXECUTE') then
    raise exception 'FAIL: team_repos trigger functions are executable by clients';
  end if;
  raise notice 'PASS: team_repos trigger functions are not executable by clients';
end;
$$;

rollback;
