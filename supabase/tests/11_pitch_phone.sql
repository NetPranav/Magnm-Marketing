-- Pitch and Admin on the phone: new phone action kinds (pitch_create, pitch_update, pitch_finalize, pitch_link), the
-- pitch_proposal mirror kind, and the admin surface the phone reads and writes straight from the cloud.
-- Written to pass on a database that already holds real rows: it counts what is there, only looks at its own test
-- identities, never deletes from storage tables and rolls everything back.
begin;
set local client_min_messages = notice;

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_b uuid := '99999999-9999-4999-8999-999999999a02';
  v_admin uuid := '99999999-9999-4999-8999-999999999a03';
  v_deact uuid := '99999999-9999-4999-8999-999999999a04';
begin
  insert into public.invites (email, role)
  values
    ('a.pp@example.com', 'member'),
    ('b.pp@example.com', 'member'),
    ('admin.pp@example.com', 'admin'),
    ('deact.pp@example.com', 'admin');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_a, 'a.pp@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"A PP"}'::jsonb),
    (v_b, 'b.pp@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"B PP"}'::jsonb),
    (v_admin, 'admin.pp@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin PP"}'::jsonb),
    (v_deact, 'deact.pp@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact PP"}'::jsonb);

  update public.profiles set active = false where id = v_deact;
end;
$$;

-- 1. The helper that checks pitch payloads is not an open door: anon cannot run it, signed-in clients can (a check
-- constraint runs it with their rights) and it is not a way to read anything.
do $$
begin
  if has_function_privilege('anon', 'public.phone_pitch_fields_ok(jsonb, boolean)', 'EXECUTE') then
    raise exception 'FAIL: anon can execute phone_pitch_fields_ok';
  end if;
  if not has_function_privilege('authenticated', 'public.phone_pitch_fields_ok(jsonb, boolean)', 'EXECUTE') then
    raise exception 'FAIL: signed-in clients cannot execute phone_pitch_fields_ok (their inserts would fail)';
  end if;
  raise notice 'PASS: the payload checker is executable by signed-in clients only';
end;
$$;

-- 2. Member A queues pitch actions.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999a01', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_case jsonb;
  v_n int;
  v_row public.phone_actions;
  v_big text;
begin
  -- Valid actions of every new kind.
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('cccccccc-0000-4000-8000-000000000001', 'pitch_create', 'new',
     '{"kind":"proposal","client_name":"Meera Shah","business_name":"Shah Clinics","contact":{"name":"Meera","email":"m@example.com","phone":"+91 90000 00000","notes":"Call after 5"},"notes":"Warm lead","price_overrides":{"w_front":7000}}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000002', 'pitch_create', 'new',
     '{"from_preset":"urban-company","kind":"quote_form"}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000003', 'pitch_create', 'new', '{}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000004', 'pitch_update', 'prop_abc123',
     '{"base_version":"2026-11-23T10:00:00.123456+00:00","fields":{"client_name":"Meera S.","preset":{"title":"Clinic plan","lead":"Hello","initialSelected":["w_front"],"sections":{"p2":["w_front"]}},"hidden_sheets":["p3"],"hidden_about":["portfolio"],"price_overrides":{"w_front":6500}}}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000005', 'pitch_finalize', 'prop_abc123', '{}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000006', 'pitch_link', 'prop_abc123', '{"action":"publish"}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000007', 'pitch_link', 'prop_abc123', '{"action":"withdraw"}'::jsonb);

  select * into v_row from public.phone_actions where id = 'cccccccc-0000-4000-8000-000000000004';
  if v_row.owner_id is distinct from v_a or v_row.status <> 'queued' then
    raise exception 'FAIL: a queued pitch_update is not owned by the caller or not queued';
  end if;
  raise notice 'PASS: owner can queue pitch_create, pitch_update, pitch_finalize and pitch_link';

  -- The older kinds still work.
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('cccccccc-0000-4000-8000-000000000008', 'draft_comment', 'cnt_1', '{"body":"make it < 100 words"}'::jsonb),
    ('cccccccc-0000-4000-8000-000000000009', 'reminder_done', 'rem_1', '{}'::jsonb);
  raise notice 'PASS: the older kinds are unchanged (a comment may still contain angle brackets)';

  -- Hostile or malformed pitch payloads are refused by the database itself.
  for v_case in select * from jsonb_array_elements($j$[
    {"kind":"pitch_update","target":"prop_1","payload":{"fields":{"client_name":"x"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1"}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"","fields":{"notes":"x"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":5,"fields":{"notes":"x"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"notes":"x"},"extra":1}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"slug":"takeover"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"status":"sent"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"kind":"final"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"created_by":"agent"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"share_id":"abcdefghijklmnop"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"custom_items":[]}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"payment_plan":[]}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"from_preset":"general"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"client_name":"<script>alert(1)</script>"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"notes":"a > b"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"lead":"<b>hi</b>"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"contact":{"name":"a<b"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"client_name":7}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"client_name":null}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"contact":"Meera"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"contact":{"whatsapp":"1"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"slug":"takeover"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"architectureBlock":{}}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"sections":{"p9":["w_front"]}}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"initialSelected":"w_front"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"preset":{"initialSelected":[1]}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"hidden_sheets":["p9"]}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"hidden_sheets":"p3"}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"hidden_about":["title"]}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":"7000"}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":-1}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":99999999}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":12.5}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":true}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":null}}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":[1]}}},
    {"kind":"pitch_update","target":"prop_1","payload":{"base_version":"v1","fields":{"price_overrides":{"w_front":{"p":1}}}}},
    {"kind":"pitch_create","target":"prop_1","payload":{}},
    {"kind":"pitch_create","target":"new","payload":{"kind":"final"}},
    {"kind":"pitch_create","target":"new","payload":{"kind":"default"}},
    {"kind":"pitch_create","target":"new","payload":{"slug":"mine"}},
    {"kind":"pitch_create","target":"new","payload":{"from_preset":"../etc"}},
    {"kind":"pitch_create","target":"new","payload":{"from_preset":7}},
    {"kind":"pitch_create","target":"new","payload":{"duplicate_of":"prop_other"}},
    {"kind":"pitch_create","target":"new","payload":{"owner_id":"99999999-9999-4999-8999-999999999a02"}},
    {"kind":"pitch_create","target":"new","payload":{"client_name":"<img src=x onerror=1>"}},
    {"kind":"pitch_create","target":"new","payload":[]},
    {"kind":"pitch_finalize","target":"prop_1","payload":{"slug":"x"}},
    {"kind":"pitch_finalize","target":"prop_1","payload":{"price_overrides":{"w_front":1}}},
    {"kind":"pitch_link","target":"prop_1","payload":{}},
    {"kind":"pitch_link","target":"prop_1","payload":{"action":"delete"}},
    {"kind":"pitch_link","target":"prop_1","payload":{"action":"publish","share_id":"abcdefghijklmnop"}},
    {"kind":"pitch_link","target":"prop_1","payload":{"action":["publish"]}},
    {"kind":"pitch_link","target":"prop_1","payload":{"action":"Publish"}}
  ]$j$::jsonb) loop
    begin
      insert into public.phone_actions (id, kind, target_id, payload)
      values (gen_random_uuid(), v_case->>'kind', v_case->>'target', v_case->'payload');
      raise exception 'FAIL: accepted a bad pitch action %', v_case;
    exception when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: malformed, over-wide and hostile pitch payloads are refused (angle brackets, unknown fields, bad prices)';

  -- A payload over the size cap, and control characters in the target, are refused.
  v_big := repeat('x', 4001);
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'pitch_update', 'prop_1', jsonb_build_object('base_version', 'v1', 'fields', jsonb_build_object('notes', v_big)));
    raise exception 'FAIL: a notes text over 4000 characters was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'pitch_update', E'prop_1\u0001', '{"base_version":"v1","fields":{"notes":"x"}}'::jsonb);
    raise exception 'FAIL: a control character in the target was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'pitch_update', 'prop_1', jsonb_build_object('base_version', 'v1', 'fields', jsonb_build_object('price_overrides', (select jsonb_object_agg('item_' || g, 100) from generate_series(1, 150) g))));
    raise exception 'FAIL: 150 price overrides were accepted';
  exception when check_violation then null;
  end;
  raise notice 'PASS: oversize texts, control characters and 150 price overrides are refused';

  -- Owner, status, device and expiry stay out of the client's hands, as before.
  begin
    insert into public.phone_actions (id, kind, target_id, payload, owner_id)
    values (gen_random_uuid(), 'pitch_link', 'prop_1', '{"action":"publish"}'::jsonb, '99999999-9999-4999-8999-999999999a02');
    raise exception 'FAIL: a client chose the owner of a pitch action';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, status)
    values (gen_random_uuid(), 'pitch_link', 'prop_1', '{"action":"publish"}'::jsonb, 'applied');
    raise exception 'FAIL: a client chose the status of a pitch action';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_actions set payload = '{"action":"withdraw"}'::jsonb where id = 'cccccccc-0000-4000-8000-000000000006';
    raise exception 'FAIL: a client edited a queued pitch action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: owner, status and payload of a queued pitch action cannot be chosen or changed by the client';

  -- The laptop's claim and finish work for the new kinds like any other.
  if (select (public.claim_phone_action('cccccccc-0000-4000-8000-000000000004', 'laptop-pp')).status) <> 'applying' then
    raise exception 'FAIL: could not claim a pitch_update';
  end if;
  if (select (public.finish_phone_action('cccccccc-0000-4000-8000-000000000004', 'laptop-pp', 'applied', 'Saved as a copy: Meera S. (phone)')).status) <> 'applied' then
    raise exception 'FAIL: could not finish a pitch_update';
  end if;
  raise notice 'PASS: pitch actions are claimed and finished like the older kinds';

  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n < 9 then
    raise exception 'FAIL: expected the actions of A to be readable by A';
  end if;
end;
$$;

-- 3. The pitch_proposal mirror kind: a stub or a full record, the owner's alone; admins read.
do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_n int;
begin
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  values
    ('pitch_proposal', 'prop_pp_1', 'Shah Clinics', '{"kind":"proposal","version":"2026-11-23T10:00:00+00:00","client_name":"Meera"}'::jsonb, now()),
    ('pitch_proposal', 'prop_pp_conf', 'Confidential item', '{"confidential":true}'::jsonb, now());
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind = 'pitch_proposal';
  if v_n <> 2 then
    raise exception 'FAIL: the owner cannot store pitch_proposal mirror records (%)', v_n;
  end if;
  begin
    insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
    values ('pitch_proposals', 'prop_x', 'x', '{}'::jsonb, now());
    raise exception 'FAIL: an invented mirror kind was accepted';
  exception when check_violation then null;
  end;
  -- Every older kind is still accepted.
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  select k, 'kind_check_' || k, 't', '{}'::jsonb, now()
  from unnest(array['chat_session','team_message','chat_turn','content_item','content_version','research_note','review_comment','metric','posted_link','brief','task']) k;
  raise notice 'PASS: pitch_proposal is a mirror kind and the older kinds are kept';
end;
$$;

-- 3b. Published links and quote requests: A has some of each.
reset role;
do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
begin
  insert into public.published_proposals (share_id, owner_id, local_proposal_id, kind, payload, status)
  values ('ppShareAAAAAAAAAAAA', v_a, 'prop_pp_1', 'proposal', '{"mode":"present"}'::jsonb, 'live');
  insert into public.quote_submissions (share_id, owner_id, choices, contact)
  values ('ppShareAAAAAAAAAAAA', v_a, '{"selected":["w_front"],"total_shown":6000}'::jsonb, '{"name":"Client One","phone":"+91 1"}'::jsonb);
end;
$$;

-- 4. Member B (another member) sees none of it and can change none of it.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999a02', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_b uuid := '99999999-9999-4999-8999-999999999a02';
  v_n int;
  v_msg text;
begin
  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads member A phone actions (%)', v_n; end if;
  select count(*) into v_n from public.mirror_records where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads member A mirror records (%)', v_n; end if;
  select count(*) into v_n from public.published_proposals where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads member A published links (%)', v_n; end if;
  select count(*) into v_n from public.quote_submissions where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads member A quote requests (%)', v_n; end if;
  raise notice 'PASS: another member reads none of A''s pitch actions, proposals, links or quote requests';

  -- B cannot withdraw, change or delete A's link, nor touch A's mirror records.
  update public.published_proposals set status = 'withdrawn' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B changed A links'; end if;
  delete from public.published_proposals where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B deleted A links'; end if;
  update public.mirror_records set title = 'hacked' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B changed A mirror records'; end if;
  delete from public.mirror_records where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B deleted A mirror records'; end if;
  update public.quote_submissions set seen_at = now() where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B marked A quote requests seen'; end if;
  raise notice 'PASS: another member cannot withdraw, change or delete anything of A''s';

  -- B cannot plant a mirror record or a published link in A's name.
  begin
    insert into public.mirror_records (owner_id, kind, local_id, title, data, local_updated_at)
    values (v_a, 'pitch_proposal', 'prop_planted', 'planted', '{}'::jsonb, now());
    raise exception 'FAIL: member B planted a mirror record for A';
  exception when insufficient_privilege or check_violation then null;
  end;
  begin
    insert into public.published_proposals (share_id, owner_id, local_proposal_id, kind, payload)
    values ('ppPlantedBBBBBBBBBB', v_a, 'prop_x', 'proposal', '{}'::jsonb);
    raise exception 'FAIL: member B published a link for A';
  exception when insufficient_privilege or check_violation then null;
  end;
  raise notice 'PASS: another member cannot plant records or links for A';

  -- The admin surface: B is a member, so it is empty or refused.
  select count(*) into v_n from public.invites;
  if v_n <> 0 then raise exception 'FAIL: a member reads invites (%)', v_n; end if;
  select count(*) into v_n from public.profiles;
  if v_n <> 1 then raise exception 'FAIL: a member reads other profiles (%)', v_n; end if;
  select count(*) into v_n from public.audit_log;
  if v_n <> 0 then raise exception 'FAIL: a member reads the audit log (%)', v_n; end if;
  select count(*) into v_n from public.ai_key_health;
  if v_n <> 0 then raise exception 'FAIL: a member reads key health (%)', v_n; end if;
  select count(*) into v_n from public.device_status where owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other devices (%)', v_n; end if;
  select count(*) into v_n from public.activity_events where owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other activity (%)', v_n; end if;
  select count(*) into v_n from public.ai_usage_daily where owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other AI usage (%)', v_n; end if;
  select count(*) into v_n from public.ai_limits where owner_id is not null and owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other AI limits (%)', v_n; end if;
  select count(*) into v_n from public.media_previews where owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other previews (%)', v_n; end if;
  select count(*) into v_n from public.phone_files where owner_id <> v_b;
  if v_n <> 0 then raise exception 'FAIL: a member reads other phone files (%)', v_n; end if;
  raise notice 'PASS: a member reads no admin data (invites, profiles, audit, key health, devices, activity, usage, limits, previews)';

  begin
    perform public.admin_ai_key_labels();
    raise exception 'FAIL: a member listed shared key labels';
  exception when others then
    get stacked diagnostics v_msg = message_text;
    if v_msg like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.admin_set_ai_keys(array[]::text[]);
    raise exception 'FAIL: a member replaced the shared keys';
  exception when others then
    get stacked diagnostics v_msg = message_text;
    if v_msg like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: a member cannot call the admin key functions';

  -- A member cannot invite, promote, deactivate or change limits.
  begin
    insert into public.invites (email, role) values ('planted.pp@example.com', 'admin');
    raise exception 'FAIL: a member created an invite';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.profiles set role = 'admin' where id = v_b;
    raise exception 'FAIL: a member made themselves admin';
  exception when others then
    get stacked diagnostics v_msg = message_text;
    if v_msg like 'FAIL:%' then raise; end if;
  end;
  update public.profiles set active = false where id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: a member deactivated another member'; end if;
  begin
    insert into public.ai_limits (owner_id, daily_requests, daily_tokens) values (v_b, 100000, 100000000);
    raise exception 'FAIL: a member raised their own AI limit';
  exception when insufficient_privilege then null;
  end;
  update public.ai_limits set daily_requests = 100000 where owner_id = v_b or owner_id is null;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: a member updated AI limits'; end if;
  update public.ai_allowed_models set enabled = false;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: a member changed allowed models'; end if;
  raise notice 'PASS: a member cannot invite, promote, deactivate or change AI limits and models';
end;
$$;

-- 5. A deactivated admin has no admin power left.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999a04', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_n int;
begin
  if public.is_admin() then raise exception 'FAIL: a deactivated admin is still admin'; end if;
  select count(*) into v_n from public.profiles;
  if v_n <> 0 then raise exception 'FAIL: a deactivated admin reads profiles (%)', v_n; end if;
  select count(*) into v_n from public.mirror_records;
  if v_n <> 0 then raise exception 'FAIL: a deactivated admin reads mirror records (%)', v_n; end if;
  select count(*) into v_n from public.audit_log;
  if v_n <> 0 then raise exception 'FAIL: a deactivated admin reads the audit log (%)', v_n; end if;
  raise notice 'PASS: a deactivated admin reads nothing';
end;
$$;

-- 6. The admin reads members' pitch work and writes only what the web admin may write.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '99999999-9999-4999-8999-999999999a03', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_b uuid := '99999999-9999-4999-8999-999999999a02';
  v_admin uuid := '99999999-9999-4999-8999-999999999a03';
  v_n int;
  v_before int;
  v_msg text;
begin
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind = 'pitch_proposal';
  if v_n <> 2 then raise exception 'FAIL: admin cannot read A pitch mirror records (%)', v_n; end if;
  select count(*) into v_n from public.published_proposals where owner_id = v_a;
  if v_n < 1 then raise exception 'FAIL: admin cannot read A links'; end if;
  select count(*) into v_n from public.quote_submissions where owner_id = v_a;
  if v_n < 1 then raise exception 'FAIL: admin cannot read A quote requests'; end if;
  select count(*) into v_n from public.phone_actions where owner_id = v_a and kind like 'pitch_%';
  if v_n < 7 then raise exception 'FAIL: admin cannot read A pitch actions (%)', v_n; end if;
  select count(*) into v_n from public.profiles where id in (v_a, v_b, v_admin);
  if v_n <> 3 then raise exception 'FAIL: admin cannot read the team'; end if;
  raise notice 'PASS: admin reads members'' pitch records, links, quote requests and actions';

  -- Admin cannot write for others.
  update public.mirror_records set title = 'admin edit' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: admin changed A mirror records'; end if;
  delete from public.mirror_records where owner_id = v_a and kind = 'pitch_proposal';
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: admin deleted A mirror records'; end if;
  update public.published_proposals set status = 'withdrawn' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: admin withdrew A links'; end if;
  begin
    update public.phone_actions set status = 'rejected' where owner_id = v_a;
    get diagnostics v_n = row_count;
    if v_n <> 0 then raise exception 'FAIL: admin changed A phone actions'; end if;
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, owner_id)
    values (gen_random_uuid(), 'pitch_link', 'prop_pp_1', '{"action":"publish"}'::jsonb, v_a);
    raise exception 'FAIL: admin queued a pitch action for A';
  exception when insufficient_privilege then null;
  end;
  -- The claim function only ever serves the caller's own queue.
  if public.claim_phone_action('cccccccc-0000-4000-8000-000000000005', 'laptop-admin') is not null then
    raise exception 'FAIL: admin claimed A pitch action';
  end if;
  begin
    insert into public.mirror_records (owner_id, kind, local_id, title, data, local_updated_at)
    values (v_a, 'pitch_proposal', 'prop_admin_planted', 'x', '{}'::jsonb, now());
    raise exception 'FAIL: admin planted a mirror record for A';
  exception when insufficient_privilege or check_violation then null;
  end;
  raise notice 'PASS: admin cannot edit, delete, withdraw, queue or claim anything of a member''s';

  -- Admin writes the web admin may do: invite, revoke, role, deactivate, limits.
  insert into public.invites (email, role) values ('new.pp@example.com', 'member');
  update public.invites set revoked_at = now() where email = 'new.pp@example.com';
  update public.profiles set role = 'admin' where id = v_b;
  get diagnostics v_n = row_count;
  if v_n <> 1 then raise exception 'FAIL: admin could not change a role'; end if;
  update public.profiles set role = 'member' where id = v_b;
  update public.profiles set active = false where id = v_b;
  update public.profiles set active = true where id = v_b;
  insert into public.ai_limits (owner_id, daily_requests, daily_tokens) values (v_b, 50, 1000)
  on conflict (owner_id) do update set daily_requests = excluded.daily_requests;
  delete from public.ai_limits where owner_id = v_b;
  raise notice 'PASS: admin can invite, revoke, change role, deactivate and set limits';

  -- The shared limit row can never be deleted from a client (owner_id is null).
  delete from public.ai_limits where owner_id is null;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: admin deleted the team default limit'; end if;

  -- Audit log: admin reads, nobody writes from a client.
  select count(*) into v_n from public.audit_log;
  if v_n < 1 then raise exception 'FAIL: admin cannot read the audit log'; end if;
  begin
    insert into public.audit_log (actor_id, action, target, data) values (v_admin, 'x', 'y', '{}'::jsonb);
    raise exception 'FAIL: admin wrote to the audit log from a client';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.audit_log;
    raise exception 'FAIL: admin deleted audit log rows from a client';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: admin reads the audit log and nobody can write or delete it from a client';

  -- The last active admin can be neither demoted nor deactivated (other admins are demoted inside this test
  -- transaction only, which is rolled back).
  update public.profiles set role = 'member' where id <> v_admin and role = 'admin' and active = true;
  begin
    update public.profiles set role = 'member' where id = v_admin;
    raise exception 'FAIL: the last active admin was demoted';
  exception when others then
    get stacked diagnostics v_msg = message_text;
    if v_msg like 'FAIL:%' then raise; end if;
  end;
  begin
    update public.profiles set active = false where id = v_admin;
    raise exception 'FAIL: the last active admin was deactivated';
  exception when others then
    get stacked diagnostics v_msg = message_text;
    if v_msg like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: the last active admin cannot be demoted or deactivated';
end;
$$;

-- 7. After all of that, A's records are still exactly as A left them.
reset role;
do $$
declare
  v_a uuid := '99999999-9999-4999-8999-999999999a01';
  v_n int;
begin
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind = 'pitch_proposal' and title in ('Shah Clinics', 'Confidential item');
  if v_n <> 2 then raise exception 'FAIL: A pitch mirror records were changed by someone else (%)', v_n; end if;
  select count(*) into v_n from public.published_proposals where owner_id = v_a and status = 'live';
  if v_n <> 1 then raise exception 'FAIL: A link was withdrawn by someone else'; end if;
  select count(*) into v_n from public.quote_submissions where owner_id = v_a and seen_at is null;
  if v_n <> 1 then raise exception 'FAIL: A quote request was marked seen by someone else'; end if;
  raise notice 'PASS: nothing of A''s was changed by another member or by the admin';
end;
$$;

rollback;
