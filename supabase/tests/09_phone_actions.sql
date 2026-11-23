-- Privacy and abuse tests for phone_actions: the cloud queue for actions made on the phone while the laptop is away.
begin;
set local client_min_messages = notice;

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999901';
  v_b uuid := '99999999-9999-4999-8999-999999999902';
  v_admin uuid := '99999999-9999-4999-8999-999999999903';
  v_deact uuid := '99999999-9999-4999-8999-999999999904';
begin
  insert into public.invites (email, role)
  values
    ('a.pa@example.com', 'member'),
    ('b.pa@example.com', 'member'),
    ('admin.pa@example.com', 'admin'),
    ('deact.pa@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_a, 'a.pa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"A PA"}'::jsonb),
    (v_b, 'b.pa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"B PA"}'::jsonb),
    (v_admin, 'admin.pa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin PA"}'::jsonb),
    (v_deact, 'deact.pa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact PA"}'::jsonb);

  update public.profiles set active = false where id = v_deact;
end;
$$;

-- Member A: inserts, constraints, claim and finish.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999901', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999901';
  v_row public.phone_actions;
  v_count int;
  v_i int;
  v_bad jsonb;
begin
  -- Valid actions of every enabled kind are queued, owned by the caller and expire in 30 days.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000001', 'draft_comment', 'cnt_1', '{"body":"Tighten the opening.","version":2}'::jsonb);
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000002', 'draft_posted', 'cnt_1', '{"url":"https://example.com/post/1"}'::jsonb);
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000003', 'reminder_done', 'rem_1', '{}'::jsonb);
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000004', 'draft_posted', 'cnt_2', '{}'::jsonb);

  select * into v_row from public.phone_actions where id = 'aaaaaaaa-0000-4000-8000-000000000001';
  if v_row.owner_id is distinct from v_a or v_row.status <> 'queued'
     or v_row.expires_at < now() + interval '29 days' or v_row.expires_at > now() + interval '31 days'
     or v_row.applied_by_device is not null or v_row.result is not null then
    raise exception 'FAIL: a new phone action is not queued, owned by the caller and expiring in 30 days';
  end if;
  raise notice 'PASS: owner can queue the three enabled action kinds';

  -- A repeated send of the same id is harmless.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000001', 'draft_comment', 'cnt_1', '{"body":"Tighten the opening.","version":2}'::jsonb)
  on conflict (id) do nothing;
  select count(*) into v_count from public.phone_actions where id = 'aaaaaaaa-0000-4000-8000-000000000001';
  if v_count <> 1 then
    raise exception 'FAIL: repeated insert made a second row';
  end if;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values ('aaaaaaaa-0000-4000-8000-000000000001', 'draft_comment', 'cnt_1', '{"body":"x"}'::jsonb);
    raise exception 'FAIL: a plain duplicate id was accepted';
  exception when unique_violation then null;
  end;
  raise notice 'PASS: the client-made id makes a repeated send idempotent';

  -- Client cannot choose owner, status, result, device or expiry.
  begin
    insert into public.phone_actions (id, owner_id, kind, target_id, payload)
    values ('aaaaaaaa-0000-4000-8000-000000000010', '99999999-9999-4999-8999-999999999902', 'reminder_done', 'rem_9', '{}'::jsonb);
    raise exception 'FAIL: inserted a row for another owner';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, status)
    values ('aaaaaaaa-0000-4000-8000-000000000011', 'reminder_done', 'rem_9', '{}'::jsonb, 'applied');
    raise exception 'FAIL: inserted a row that is already applied';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, expires_at)
    values ('aaaaaaaa-0000-4000-8000-000000000012', 'reminder_done', 'rem_9', '{}'::jsonb, now() + interval '3650 days');
    raise exception 'FAIL: chose its own expiry';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, applied_by_device, result)
    values ('aaaaaaaa-0000-4000-8000-000000000013', 'reminder_done', 'rem_9', '{}'::jsonb, 'dev_x', 'forged');
    raise exception 'FAIL: chose its own device or result';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: client cannot set owner, status, result, device or expiry';

  -- Kind list and payload shape are enforced per kind.
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values ('aaaaaaaa-0000-4000-8000-000000000020', 'run_agent', 'writer', '{"text":"do things"}'::jsonb);
    raise exception 'FAIL: an unknown kind was accepted';
  exception when check_violation then null;
  end;
  for v_bad in select * from jsonb_array_elements('[
      {"kind":"draft_comment","payload":{}},
      {"kind":"draft_comment","payload":{"body":""}},
      {"kind":"draft_comment","payload":{"body":"   "}},
      {"kind":"draft_comment","payload":{"body":5}},
      {"kind":"draft_comment","payload":{"body":"ok","extra":"nope"}},
      {"kind":"draft_comment","payload":{"body":"ok","version":"2"}},
      {"kind":"draft_comment","payload":{"body":"ok","version":0}},
      {"kind":"draft_comment","payload":{"body":"ok","version":2.5}},
      {"kind":"draft_comment","payload":["ok"]},
      {"kind":"draft_comment","payload":"ok"},
      {"kind":"draft_posted","payload":{"url":7}},
      {"kind":"draft_posted","payload":{"url":"ftp://example.com/x"}},
      {"kind":"draft_posted","payload":{"url":"javascript:alert(1)"}},
      {"kind":"draft_posted","payload":{"url":"https://example.com/a b"}},
      {"kind":"draft_posted","payload":{"body":"x"}},
      {"kind":"reminder_done","payload":{"x":1}},
      {"kind":"reminder_done","payload":[]}
    ]'::jsonb) loop
    begin
      insert into public.phone_actions (id, kind, target_id, payload)
      values (gen_random_uuid(), v_bad->>'kind', 'cnt_1', v_bad->'payload');
      raise exception 'FAIL: accepted bad payload %', v_bad;
    exception when check_violation then null;
    end;
  end loop;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'draft_comment', 'cnt_1', jsonb_build_object('body', repeat('x', 4001)));
    raise exception 'FAIL: accepted a 4001 character comment';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'draft_posted', 'cnt_1', jsonb_build_object('url', 'https://example.com/' || repeat('x', 2100)));
    raise exception 'FAIL: accepted a 2100 character url';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'reminder_done', '', '{}'::jsonb);
    raise exception 'FAIL: accepted an empty target';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'reminder_done', repeat('t', 201), '{}'::jsonb);
    raise exception 'FAIL: accepted a 201 character target';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'reminder_done', E'rem\n1', '{}'::jsonb);
    raise exception 'FAIL: accepted a target with a control character';
  exception when check_violation then null;
  end;
  raise notice 'PASS: unknown kinds, bad payload shapes, oversize and bad targets are rejected';

  -- Nobody edits or deletes a row from the client.
  begin
    update public.phone_actions set payload = '{"body":"changed"}'::jsonb where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: owner edited a payload';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_actions set status = 'applied' where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: owner marked their own action applied';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.phone_actions where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: owner deleted a row';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: clients cannot update or delete phone actions';

  -- One laptop claims, once.
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1');
  if v_row.id is null or v_row.status <> 'applying' or v_row.applied_by_device <> 'dev_laptop_1' or v_row.claimed_at is null then
    raise exception 'FAIL: claim did not move the action to applying for the device';
  end if;
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_2');
  if v_row.id is not null then
    raise exception 'FAIL: a second device claimed an action already claimed';
  end if;
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1');
  if v_row.id is null or v_row.status <> 'applying' then
    raise exception 'FAIL: the claiming device cannot resume its own claim after a crash';
  end if;
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-0000000000ff', 'dev_laptop_1');
  if v_row.id is not null then
    raise exception 'FAIL: claimed an action that does not exist';
  end if;
  raise notice 'PASS: an action is claimed atomically by one device and can be resumed by it';

  -- Finish: only the claiming device, only to applied or rejected, once.
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_2', 'applied', 'Done');
  if v_row.id is not null then
    raise exception 'FAIL: a device that did not claim finished the action';
  end if;
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000002', 'dev_laptop_1', 'applied', 'Done');
  if v_row.id is not null then
    raise exception 'FAIL: finished an action that was never claimed';
  end if;
  begin
    perform public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1', 'queued', 'x');
    raise exception 'FAIL: finish accepted status queued';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1', 'applying', 'x');
    raise exception 'FAIL: finish accepted status applying';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  select * into v_row from public.finish_phone_action(
    'aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1', 'applied', E'Comment added.\u0007' || repeat('z', 400));
  if v_row.status <> 'applied' or v_row.finished_at is null or char_length(v_row.result) > 300
     or v_row.result ~ '[[:cntrl:]]' then
    raise exception 'FAIL: finish did not record a short clean result';
  end if;
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1', 'applied', 'again');
  if v_row.id is null or v_row.status <> 'applied' then
    raise exception 'FAIL: repeating a finish is not harmless';
  end if;
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1', 'rejected', 'flip');
  if v_row.id is not null then
    raise exception 'FAIL: a finished action changed its outcome';
  end if;
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000001', 'dev_laptop_1');
  if v_row.id is not null then
    raise exception 'FAIL: a finished action was claimed again';
  end if;
  raise notice 'PASS: finish records one clean short outcome, only for the claiming device';

  -- A rejected outcome keeps its reason.
  perform public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000004', 'dev_laptop_1');
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000004', 'dev_laptop_1', 'rejected', 'Draft not found');
  if v_row.status <> 'rejected' or v_row.result <> 'Draft not found' then
    raise exception 'FAIL: rejected outcome not recorded';
  end if;

  -- The per-owner cap of 200 waiting actions.
  select count(*) into v_count from public.phone_actions where status in ('queued', 'applying');
  for v_i in 1..(200 - v_count) loop
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'reminder_done', 'rem_' || v_i, '{}'::jsonb);
  end loop;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'reminder_done', 'rem_over', '{}'::jsonb);
    raise exception 'FAIL: the 201st waiting action was accepted';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    if sqlerrm not like 'Too many waiting phone actions%' then
      raise exception 'FAIL: the cap raised the wrong error: %', sqlerrm;
    end if;
  end;
  raise notice 'PASS: at most 200 waiting actions per owner';
end;
$$;

-- Member B sees and touches nothing of A.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999902', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count int;
  v_row public.phone_actions;
begin
  select count(*) into v_count from public.phone_actions;
  if v_count <> 0 then
    raise exception 'FAIL: member B can read member A phone actions (% rows)', v_count;
  end if;
  -- B queues its own action under a different id, then tries to claim and finish A's rows.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000001', 'reminder_done', 'rem_b', '{}'::jsonb);
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_laptop_b');
  if v_row.id is not null then
    raise exception 'FAIL: member B claimed member A action';
  end if;
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_laptop_b', 'applied', 'x');
  if v_row.id is not null then
    raise exception 'FAIL: member B finished member A action';
  end if;
  -- Re-using A's id with ON CONFLICT cannot overwrite or reveal anything.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('aaaaaaaa-0000-4000-8000-000000000003', 'reminder_done', 'rem_b', '{}'::jsonb)
  on conflict (id) do nothing;
  select count(*) into v_count from public.phone_actions;
  if v_count <> 1 then
    raise exception 'FAIL: member B sees % rows, expected only its own', v_count;
  end if;
  raise notice 'PASS: a member cannot read, claim or finish another member''s phone actions';
end;
$$;

-- Admin: may read, may not change or act for anyone else.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999903', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count int;
  v_row public.phone_actions;
begin
  select count(*) into v_count from public.phone_actions;
  if v_count < 2 then
    raise exception 'FAIL: admin cannot read member phone actions (read-only access)';
  end if;
  begin
    update public.phone_actions set status = 'rejected' where id = 'aaaaaaaa-0000-4000-8000-000000000003';
    raise exception 'FAIL: admin updated a member action';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.phone_actions where id = 'aaaaaaaa-0000-4000-8000-000000000003';
    raise exception 'FAIL: admin deleted a member action';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, owner_id, kind, target_id, payload)
    values ('cccccccc-0000-4000-8000-000000000001', '99999999-9999-4999-8999-999999999901', 'reminder_done', 'rem_x', '{}'::jsonb);
    raise exception 'FAIL: admin inserted an action for a member';
  exception when insufficient_privilege then null;
  end;
  select * into v_row from public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_admin');
  if v_row.id is not null then
    raise exception 'FAIL: admin claimed a member action';
  end if;
  select * into v_row from public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_admin', 'applied', 'x');
  if v_row.id is not null then
    raise exception 'FAIL: admin finished a member action';
  end if;
  raise notice 'PASS: admins can read but not change, claim or finish member phone actions';
end;
$$;

-- Deactivated member: nothing at all.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999904', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.phone_actions;
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member can read phone actions';
  end if;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values ('dddddddd-0000-4000-8000-000000000001', 'reminder_done', 'rem_d', '{}'::jsonb);
    raise exception 'FAIL: deactivated member queued an action';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_d');
    raise exception 'FAIL: deactivated member called claim';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_d', 'applied', 'x');
    raise exception 'FAIL: deactivated member called finish';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: a deactivated member cannot read, queue, claim or finish';
end;
$$;

-- anon: nothing, and the functions are not executable.
reset role;
set local role anon;
select set_config('request.jwt.claims', '{}', true);

do $$
begin
  begin
    perform count(*) from public.phone_actions;
    raise exception 'FAIL: anon selected from phone_actions';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values ('eeeeeeee-0000-4000-8000-000000000001', 'reminder_done', 'rem_e', '{}'::jsonb);
    raise exception 'FAIL: anon inserted into phone_actions';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.claim_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_anon');
    raise exception 'FAIL: anon called claim_phone_action';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.finish_phone_action('aaaaaaaa-0000-4000-8000-000000000003', 'dev_anon', 'applied', 'x');
    raise exception 'FAIL: anon called finish_phone_action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon has zero access to phone_actions and its functions';
end;
$$;

reset role;

-- Function privileges: only the two RPCs are callable, and only by signed-in clients.
do $$
declare
  v_bad text;
begin
  if has_function_privilege('public', 'public.claim_phone_action(uuid, text)', 'EXECUTE')
     or has_function_privilege('anon', 'public.claim_phone_action(uuid, text)', 'EXECUTE')
     or has_function_privilege('public', 'public.finish_phone_action(uuid, text, text, text)', 'EXECUTE')
     or has_function_privilege('anon', 'public.finish_phone_action(uuid, text, text, text)', 'EXECUTE') then
    raise exception 'FAIL: phone action RPCs are executable by public or anon';
  end if;
  if not has_function_privilege('authenticated', 'public.claim_phone_action(uuid, text)', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.finish_phone_action(uuid, text, text, text)', 'EXECUTE') then
    raise exception 'FAIL: signed-in clients cannot execute the phone action RPCs';
  end if;
  select string_agg(p.proname, ', ') into v_bad
  from pg_proc p
  where p.pronamespace = 'public'::regnamespace
    and p.proname in ('guard_phone_action_update', 'limit_phone_action_queue')
    and (has_function_privilege('public', p.oid, 'EXECUTE')
         or has_function_privilege('anon', p.oid, 'EXECUTE')
         or has_function_privilege('authenticated', p.oid, 'EXECUTE'));
  if v_bad is not null then
    raise exception 'FAIL: phone action trigger functions are executable by clients: %', v_bad;
  end if;
  raise notice 'PASS: phone action functions are executable only by the clients that need them';
end;
$$;

-- Even a privileged writer cannot rewrite what a phone queued, or walk a finished outcome back.
do $$
begin
  begin
    update public.phone_actions set kind = 'reminder_done' where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: kind was rewritten';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    update public.phone_actions set payload = '{"body":"rewritten"}'::jsonb where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: payload was rewritten';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    update public.phone_actions set target_id = 'other' where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: target was rewritten';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    update public.phone_actions set owner_id = '99999999-9999-4999-8999-999999999902' where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: owner was rewritten';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    update public.phone_actions set status = 'queued' where id = 'aaaaaaaa-0000-4000-8000-000000000001';
    raise exception 'FAIL: a finished action went back to queued';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: kind, target, payload, owner and finished outcomes are immutable';
end;
$$;

-- Expiry: an old queued action is never claimed, and is closed with a reason.
do $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', '99999999-9999-4999-8999-999999999902', 'role', 'authenticated')::text, true);
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000002', 'reminder_done', 'rem_old', '{}'::jsonb);
  -- Age the row as the table owner (clients cannot change expiry), with the guard off just for this step.
  alter table public.phone_actions disable trigger trg_phone_actions_guard;
  update public.phone_actions set expires_at = now() - interval '1 day' where id = 'bbbbbbbb-0000-4000-8000-000000000002';
  alter table public.phone_actions enable trigger trg_phone_actions_guard;
end;
$$;

set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999902', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_row public.phone_actions;
begin
  select * into v_row from public.claim_phone_action('bbbbbbbb-0000-4000-8000-000000000002', 'dev_laptop_b');
  if v_row.id is not null then
    raise exception 'FAIL: an expired action was claimed';
  end if;
  select * into v_row from public.phone_actions where id = 'bbbbbbbb-0000-4000-8000-000000000002';
  if v_row.status <> 'rejected' or v_row.result is null then
    raise exception 'FAIL: an expired action was not closed with a reason (status %)', v_row.status;
  end if;
  raise notice 'PASS: expired actions are not applied and say why';
end;
$$;

reset role;

rollback;
