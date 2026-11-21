-- Privacy and audit tests for activity_events, device_status, mirror_records, shared_knowledge, pitch_base_prices, and audit_log.
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '22222222-2222-4222-8222-222222222201';
  v_member_a uuid := '22222222-2222-4222-8222-222222222202';
  v_member_b uuid := '22222222-2222-4222-8222-222222222203';
begin
  insert into public.invites (email, role)
  values
    ('admin.m2@example.com', 'admin'),
    ('member.a@example.com', 'member'),
    ('member.b@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.m2@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin M2"}'::jsonb),
    (v_member_a, 'member.a@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member A"}'::jsonb),
    (v_member_b, 'member.b@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member B"}'::jsonb);
end;
$$;

-- Member A inserts activity, device status, and mirror record.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '22222222-2222-4222-8222-222222222202', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '22222222-2222-4222-8222-222222222202';
  v_member_b uuid := '22222222-2222-4222-8222-222222222203';
begin
  insert into public.activity_events (kind, agent_id, summary, data)
  values ('draft_created', 'writer', 'Drafted launch post', '{"words":120}'::jsonb);

  insert into public.device_status (device_id, device_kind, status, running_agents, app_version)
  values ('laptop-a', 'desktop', 'working', '["writer"]'::jsonb, '0.4.0');

  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  values ('content_item', 'post-1', 'Member A Post', '{"body":"hello"}'::jsonb, now());

  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  values
    ('chat_session', 'sess-1', 'Session 1', '{"agent_id":"cmo","messages":[]}'::jsonb, now()),
    ('team_message', 'msg-1', 'Hello team', '{"sender":"CEO","text":"Hello team"}'::jsonb, now());

  -- Unknown mirror kind is rejected.
  begin
    insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
    values ('unknown_kind', 'bad-1', 'Bad Kind', '{}'::jsonb, now());
    raise exception 'FAIL: unknown mirror kind was accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: unknown mirror kind is rejected';
  end;

  -- Member A cannot insert on behalf of Member B.
  begin
    insert into public.mirror_records (owner_id, kind, local_id, title, data, local_updated_at)
    values (v_member_b, 'content_item', 'spoof-1', 'Spoofed', '{}'::jsonb, now());
    raise exception 'FAIL: member A inserted mirror record for member B';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member A cannot insert mirror record for member B';
  end;
end;
$$;

-- Member B inserts their own rows and tries to access Member A's rows.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '22222222-2222-4222-8222-222222222203', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '22222222-2222-4222-8222-222222222202';
  v_member_b uuid := '22222222-2222-4222-8222-222222222203';
  v_count integer;
begin
  insert into public.activity_events (kind, agent_id, summary, data)
  values ('research_done', 'researcher', 'Checked HN', '{}'::jsonb);

  insert into public.device_status (device_id, device_kind, status)
  values ('laptop-b', 'desktop', 'online');

  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  values ('brief', 'brief-b1', 'Member B Brief', '{"goal":"teasers"}'::jsonb, now());

  -- Member B cannot read Member A's activity, device status, or mirror records.
  select count(*) into v_count from public.activity_events where owner_id = v_member_a;
  if v_count <> 0 then
    raise exception 'FAIL: member B read member A activity_events';
  end if;

  select count(*) into v_count from public.device_status where owner_id = v_member_a;
  if v_count <> 0 then
    raise exception 'FAIL: member B read member A device_status';
  end if;

  select count(*) into v_count from public.mirror_records where owner_id = v_member_a;
  if v_count <> 0 then
    raise exception 'FAIL: member B read member A mirror_records';
  end if;
  raise notice 'PASS: member B cannot read member A activity, device status, or mirror records';

  -- Member B cannot update or delete Member A's device_status or mirror_records.
  update public.device_status set status = 'offline' where owner_id = v_member_a;
  delete from public.device_status where owner_id = v_member_a;
  update public.mirror_records set title = 'Hijacked' where owner_id = v_member_a;
  delete from public.mirror_records where owner_id = v_member_a;

  -- Member B cannot write shared_knowledge or pitch_base_prices.
  begin
    insert into public.shared_knowledge (key, data)
    values ('brand_kit', '{"font":"Instrument Serif"}'::jsonb);
    raise exception 'FAIL: member B wrote shared_knowledge';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot write shared_knowledge';
  end;

  begin
    insert into public.pitch_base_prices (item_id, price)
    values ('web_landing', 15000);
    raise exception 'FAIL: member B wrote pitch_base_prices';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: member cannot write pitch_base_prices';
  end;

  -- Member B cannot read audit_log.
  select count(*) into v_count from public.audit_log;
  if v_count <> 0 then
    raise exception 'FAIL: member B read % rows from audit_log', v_count;
  end if;
  raise notice 'PASS: member cannot read audit_log';
end;
$$;

-- Switch to Admin and verify read-all on activity/status/mirror, no write on others' mirror, and shared knowledge + audit log.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '22222222-2222-4222-8222-222222222201', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_admin uuid := '22222222-2222-4222-8222-222222222201';
  v_member_a uuid := '22222222-2222-4222-8222-222222222202';
  v_member_b uuid := '22222222-2222-4222-8222-222222222203';
  v_count integer;
  v_title text;
  v_updated_by uuid;
begin
  -- Verify Member A's rows were untouched by Member B.
  select title into v_title
  from public.mirror_records
  where owner_id = v_member_a and local_id = 'post-1';

  if v_title is distinct from 'Member A Post' then
    raise exception 'FAIL: member A mirror record was altered or missing';
  end if;
  raise notice 'PASS: member B could not update or delete member A rows';

  -- Admin reads all activity, device_status, and mirror_records.
  select count(*) into v_count from public.activity_events where owner_id in (v_member_a, v_member_b);
  if v_count <> 2 then
    raise exception 'FAIL: admin expected 2 activity_events, got %', v_count;
  end if;

  select count(*) into v_count from public.device_status where owner_id in (v_member_a, v_member_b);
  if v_count <> 2 then
    raise exception 'FAIL: admin expected 2 device_status rows, got %', v_count;
  end if;

  select count(*) into v_count from public.mirror_records where owner_id in (v_member_a, v_member_b);
  if v_count <> 4 then
    raise exception 'FAIL: admin expected 4 mirror_records rows, got %', v_count;
  end if;
  raise notice 'PASS: admin can read all activity, device status, and mirror records';

  -- Admin cannot insert, update, or delete a member's mirror rows.
  begin
    insert into public.mirror_records (owner_id, kind, local_id, title, data, local_updated_at)
    values (v_member_a, 'task', 'admin-spoof', 'Admin Write', '{}'::jsonb, now());
    raise exception 'FAIL: admin inserted mirror record for member A';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
  end;

  update public.mirror_records set title = 'Admin Edited' where owner_id = v_member_a;
  delete from public.mirror_records where owner_id = v_member_a;

  select title into v_title
  from public.mirror_records
  where owner_id = v_member_a and local_id = 'post-1';

  if v_title is distinct from 'Member A Post' then
    raise exception 'FAIL: admin modified or deleted member A mirror record';
  end if;
  raise notice 'PASS: admin cannot write, update, or delete a member''s mirror rows';

  -- Admin can write shared_knowledge and pitch_base_prices, stamping updated_by.
  insert into public.shared_knowledge (key, data)
  values ('brand_kit', '{"palette":"Pitch"}'::jsonb);

  insert into public.pitch_base_prices (item_id, price)
  values ('web_landing', 15000);

  select updated_by into v_updated_by from public.shared_knowledge where key = 'brand_kit';
  if v_updated_by is distinct from v_admin then
    raise exception 'FAIL: shared_knowledge updated_by should be %, got %', v_admin, v_updated_by;
  end if;

  select updated_by into v_updated_by from public.pitch_base_prices where item_id = 'web_landing';
  if v_updated_by is distinct from v_admin then
    raise exception 'FAIL: pitch_base_prices updated_by should be %, got %', v_admin, v_updated_by;
  end if;
  raise notice 'PASS: admin writes shared_knowledge and pitch_base_prices with updated_by stamp';

  -- Audit log records invite creation, profile changes, shared_knowledge, and pitch_base_prices.
  update public.profiles set role = 'admin' where id = v_member_b;
  update public.profiles set role = 'member' where id = v_member_b;

  select count(*) into v_count
  from public.audit_log
  where action in (
    'invite.created',
    'profile.role_or_active_changed',
    'shared_knowledge.created',
    'pitch_base_price.created'
  );

  if v_count < 4 then
    raise exception 'FAIL: expected audit_log entries for invite, profile, shared_knowledge, and pitch_base_price, got %', v_count;
  end if;
  raise notice 'PASS: audit_log records admin changes and is readable by admin';

  -- Nobody (not even admin) can directly insert into audit_log.
  begin
    insert into public.audit_log (action, target) values ('fake.action', 'none');
    raise exception 'FAIL: direct insert into audit_log succeeded';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: direct insert into audit_log is rejected';
  end;
end;
$$;

-- Switch back to Member A and verify active member can read shared_knowledge and pitch_base_prices.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '22222222-2222-4222-8222-222222222202', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count integer;
begin
  select count(*) into v_count from public.shared_knowledge where key = 'brand_kit';
  if v_count <> 1 then
    raise exception 'FAIL: active member could not read shared_knowledge';
  end if;

  select count(*) into v_count from public.pitch_base_prices where item_id = 'web_landing';
  if v_count <> 1 then
    raise exception 'FAIL: active member could not read pitch_base_prices';
  end if;
  raise notice 'PASS: active members can read shared_knowledge and pitch_base_prices';
end;
$$;

reset role;
rollback;
