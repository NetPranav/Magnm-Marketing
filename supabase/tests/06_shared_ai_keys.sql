-- Privacy, Vault key isolation, quota enforcement, and health tracking tests for shared AI keys.
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '66666666-6666-4666-8666-666666666601';
  v_member_a uuid := '66666666-6666-4666-8666-666666666602';
  v_member_b uuid := '66666666-6666-4666-8666-666666666603';
  v_model_count int;
  v_default_req int;
  v_default_tok int;
begin
  -- Verify seeded NIM models and team default limit exist.
  select count(*) into v_model_count from public.ai_allowed_models where enabled = true;
  if v_model_count < 10 then
    raise exception 'FAIL: expected seeded ai_allowed_models rows, found %', v_model_count;
  end if;

  select daily_requests, daily_tokens
  into v_default_req, v_default_tok
  from public.ai_limits
  where owner_id is null;

  if v_default_req is distinct from 200 or v_default_tok is distinct from 400000 then
    raise exception 'FAIL: expected default ai_limits (200, 400000), got (%, %)', v_default_req, v_default_tok;
  end if;
  raise notice 'PASS: seeded ai_allowed_models and default ai_limits row exist';

  insert into public.invites (email, role)
  values
    ('admin.ai@example.com', 'admin'),
    ('member.aia@example.com', 'member'),
    ('member.aib@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.ai@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin AI"}'::jsonb),
    (v_member_a, 'member.aia@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member A"}'::jsonb),
    (v_member_b, 'member.aib@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member B"}'::jsonb);
end;
$$;

-- Switch to Admin: set keys, verify labels, audit log, validation, and per-member limit override.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '66666666-6666-4666-8666-666666666601', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '66666666-6666-4666-8666-666666666602';
  v_member_b uuid := '66666666-6666-4666-8666-666666666603';
  v_fake_1 text := 'nvapi-FAKEKEY00000000000000000001ABCD';
  v_fake_2 text := 'nvapi-FAKEKEY00000000000000000002WXYZ';
  v_count int;
  v_labels text[];
  v_audit_data jsonb;
  v_too_many text[] := '{}'::text[];
  i int;
begin
  -- Invalid key format rejected.
  begin
    perform public.admin_set_ai_keys(array['not-a-nim-key']);
    raise exception 'FAIL: invalid key format was accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      raise notice 'PASS: admin_set_ai_keys rejects invalid key format';
  end;

  -- Short nvapi- key rejected.
  begin
    perform public.admin_set_ai_keys(array['nvapi-tooshort']);
    raise exception 'FAIL: short key was accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      raise notice 'PASS: admin_set_ai_keys rejects too-short key';
  end;

  -- More than 20 distinct keys rejected.
  for i in 1..21 loop
    v_too_many := array_append(
      v_too_many,
      'nvapi-FAKEKEY000000000000000000' || lpad(i::text, 4, '0')
    );
  end loop;
  begin
    perform public.admin_set_ai_keys(v_too_many);
    raise exception 'FAIL: more than 20 keys were accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      raise notice 'PASS: admin_set_ai_keys rejects more than 20 keys';
  end;

  -- Set 2 fake keys (with a duplicate) -> deduplicated count = 2.
  v_count := public.admin_set_ai_keys(array[v_fake_1, v_fake_2, v_fake_1]);
  if v_count <> 2 then
    raise exception 'FAIL: expected deduplicated count 2, got %', v_count;
  end if;

  -- Admin reads labels: masked, ends in last 4 chars inside parens, never contains full key.
  select array_agg(label order by label) into v_labels
  from public.admin_ai_key_labels();

  if v_labels is distinct from array['key-1 (...ABCD)', 'key-2 (...WXYZ)'] then
    raise exception 'FAIL: unexpected labels: %', v_labels;
  end if;
  if position(v_fake_1 in array_to_string(v_labels, ',')) > 0
     or position(v_fake_2 in array_to_string(v_labels, ',')) > 0 then
    raise exception 'FAIL: full key leaked into labels';
  end if;
  raise notice 'PASS: admin sets keys (deduplicated) and reads masked labels';

  -- Audit log records only count and never contains key text.
  select a.data into v_audit_data
  from public.audit_log a
  where a.action = 'ai_keys.updated'
  order by a.id desc
  limit 1;

  if v_audit_data is distinct from '{"count": 2}'::jsonb then
    raise exception 'FAIL: unexpected audit_log data for ai_keys.updated: %', v_audit_data;
  end if;
  if position('nvapi-' in v_audit_data::text) > 0 then
    raise exception 'FAIL: key text leaked into audit_log';
  end if;
  raise notice 'PASS: audit_log records key count without key text';

  -- Admin sets custom limit override for Member A (2 requests, 500 tokens) and Member B (1 request, 1000 tokens).
  insert into public.ai_limits (owner_id, daily_requests, daily_tokens)
  values
    (v_member_a, 2, 500),
    (v_member_b, 1, 1000);
  raise notice 'PASS: admin set per-member ai_limits overrides';
end;
$$;

-- Switch to service_role to verify ai_get_keys, ai_check_and_count, ai_record_usage, ai_report_key.
reset role;
set local role service_role;

do $$
declare
  v_member_a uuid := '66666666-6666-4666-8666-666666666602';
  v_member_b uuid := '66666666-6666-4666-8666-666666666603';
  v_fake_1 text := 'nvapi-FAKEKEY00000000000000000001ABCD';
  v_fake_2 text := 'nvapi-FAKEKEY00000000000000000002WXYZ';
  v_got_keys text[];
  v_res jsonb;
  v_req int;
  v_rej int;
  v_pt bigint;
  v_ct bigint;
begin
  select array_agg(api_key order by label) into v_got_keys from public.ai_get_keys();
  if v_got_keys is distinct from array[v_fake_1, v_fake_2] then
    raise exception 'FAIL: ai_get_keys returned unexpected keys';
  end if;
  raise notice 'PASS: ai_get_keys returns decrypted keys for service_role';

  -- Member A has override: 2 requests, 500 tokens.
  v_res := public.ai_check_and_count(v_member_a, 200);
  if (v_res->>'allowed')::boolean is not true or (v_res->>'requests_left')::int <> 1 then
    raise exception 'FAIL: member A call 1 should be allowed with 1 request left, got %', v_res;
  end if;

  perform public.ai_record_usage(v_member_a, 120, 80);

  v_res := public.ai_check_and_count(v_member_a, 200);
  if (v_res->>'allowed')::boolean is not true or (v_res->>'requests_left')::int <> 0 then
    raise exception 'FAIL: member A call 2 should be allowed with 0 requests left, got %', v_res;
  end if;

  -- 3rd call exceeds daily_requests = 2.
  v_res := public.ai_check_and_count(v_member_a, 50);
  if (v_res->>'allowed')::boolean is not false or v_res->>'reason' <> 'daily_requests_limit' then
    raise exception 'FAIL: member A call 3 should be rejected for daily_requests_limit, got %', v_res;
  end if;

  select requests, rejected, prompt_tokens, completion_tokens
  into v_req, v_rej, v_pt, v_ct
  from public.ai_usage_daily
  where owner_id = v_member_a
    and day = (now() at time zone 'UTC')::date;

  if v_req <> 2 or v_rej <> 1 or v_pt <> 120 or v_ct <> 80 then
    raise exception 'FAIL: member A usage mismatch: req=% rej=% pt=% ct=%', v_req, v_rej, v_pt, v_ct;
  end if;
  raise notice 'PASS: ai_check_and_count enforces per-member request limit and increments rejected';

  -- Member B has override: 1 request, 1000 tokens; test token limit refusal.
  v_res := public.ai_check_and_count(v_member_b, 1200);
  if (v_res->>'allowed')::boolean is not false or v_res->>'reason' <> 'daily_tokens_limit' then
    raise exception 'FAIL: member B oversized token call should be rejected, got %', v_res;
  end if;

  v_res := public.ai_check_and_count(v_member_b, 400);
  if (v_res->>'allowed')::boolean is not true then
    raise exception 'FAIL: member B valid token call should be allowed, got %', v_res;
  end if;
  raise notice 'PASS: ai_check_and_count enforces token limit and per-member overrides';

  -- Report key health.
  perform public.ai_report_key('key-1 (...ABCD)', 200, null, 0);
  perform public.ai_report_key('key-2 (...WXYZ)', 429, 'rate limited', 60);
end;
$$;

-- Switch to Member A: verify strict isolation and zero access to keys/labels/service functions/writes.
reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '66666666-6666-4666-8666-666666666602', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '66666666-6666-4666-8666-666666666602';
  v_member_b uuid := '66666666-6666-4666-8666-666666666603';
  v_count int;
begin
  -- Member A can read enabled models, own/default limits, and own usage.
  select count(*) into v_count from public.ai_allowed_models;
  if v_count < 10 then
    raise exception 'FAIL: active member could not read ai_allowed_models';
  end if;

  select count(*) into v_count from public.ai_limits;
  if v_count <> 2 then
    raise exception 'FAIL: member A should see exactly 2 ai_limits rows (default + own), saw %', v_count;
  end if;

  select count(*) into v_count from public.ai_limits where owner_id = v_member_b;
  if v_count <> 0 then
    raise exception 'FAIL: member A saw member B ai_limits row';
  end if;

  select count(*) into v_count from public.ai_usage_daily;
  if v_count <> 1 then
    raise exception 'FAIL: member A should see only own ai_usage_daily row, saw %', v_count;
  end if;

  select count(*) into v_count from public.ai_usage_daily where owner_id = v_member_b;
  if v_count <> 0 then
    raise exception 'FAIL: member A saw member B usage';
  end if;
  raise notice 'PASS: member A reads models, default+own limits, and own usage only';

  -- Member A cannot read ai_key_health.
  select count(*) into v_count from public.ai_key_health;
  if v_count <> 0 then
    raise exception 'FAIL: member A saw ai_key_health rows';
  end if;

  -- Member A cannot call admin_ai_key_labels or admin_set_ai_keys.
  begin
    perform * from public.admin_ai_key_labels();
    raise exception 'FAIL: member A called admin_ai_key_labels';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
  end;

  begin
    perform public.admin_set_ai_keys(array['nvapi-FAKEKEY00000000000000000009ZZZZ']);
    raise exception 'FAIL: member A called admin_set_ai_keys';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
  end;

  -- Member A cannot call service_role functions.
  begin
    perform * from public.ai_get_keys();
    raise exception 'FAIL: member A called ai_get_keys';
  exception
    when insufficient_privilege then null;
  end;

  begin
    perform public.ai_check_and_count(v_member_a, 10);
    raise exception 'FAIL: member A called ai_check_and_count';
  exception
    when insufficient_privilege then null;
  end;

  begin
    perform public.ai_record_usage(v_member_a, 1, 1);
    raise exception 'FAIL: member A called ai_record_usage';
  exception
    when insufficient_privilege then null;
  end;

  begin
    perform public.ai_report_key('key-1 (...ABCD)', 200, null, 0);
    raise exception 'FAIL: member A called ai_report_key';
  exception
    when insufficient_privilege then null;
  end;

  -- Member A cannot write ai_allowed_models, ai_limits, ai_usage_daily, or ai_key_health.
  begin
    insert into public.ai_allowed_models (model) values ('evil/model');
    raise exception 'FAIL: member A inserted into ai_allowed_models';
  exception
    when insufficient_privilege then null;
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
  end;

  begin
    insert into public.ai_limits (owner_id, daily_requests, daily_tokens) values (v_member_a, 99999, 99999999);
    raise exception 'FAIL: member A inserted into ai_limits';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
  end;

  begin
    insert into public.ai_usage_daily (owner_id, day, requests) values (v_member_a, current_date + 1, 0);
    raise exception 'FAIL: member A inserted into ai_usage_daily';
  exception
    when insufficient_privilege then null;
  end;

  begin
    insert into public.ai_key_health (label, last_status) values ('spoof', 200);
    raise exception 'FAIL: member A inserted into ai_key_health';
  exception
    when insufficient_privilege then null;
  end;

  raise notice 'PASS: member A blocked from keys, labels, service functions, and direct writes';
end;
$$;

-- Switch to Admin: verify admin sees all usage and key health via admin_ai_key_labels.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '66666666-6666-4666-8666-666666666601', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '66666666-6666-4666-8666-666666666602';
  v_member_b uuid := '66666666-6666-4666-8666-666666666603';
  v_count int;
  v_status int;
  v_cooldown timestamptz;
begin
  select count(*) into v_count
  from public.ai_usage_daily
  where owner_id in (v_member_a, v_member_b);
  if v_count <> 2 then
    raise exception 'FAIL: admin should see both members ai_usage_daily rows, saw %', v_count;
  end if;

  select last_status, cooldown_until
  into v_status, v_cooldown
  from public.admin_ai_key_labels()
  where label = 'key-2 (...WXYZ)';

  if v_status <> 429 or v_cooldown is null then
    raise exception 'FAIL: expected key-2 status 429 with cooldown, got status=% cooldown=%', v_status, v_cooldown;
  end if;
  raise notice 'PASS: admin sees all usage and key health status/cooldown via labels';
end;
$$;

-- Switch to anon: zero access to any table or function.
reset role;
set local role anon;

do $$
begin
  begin
    perform count(*) from public.ai_allowed_models;
    raise exception 'FAIL: anon read ai_allowed_models';
  exception when insufficient_privilege then null;
  end;

  begin
    perform count(*) from public.ai_limits;
    raise exception 'FAIL: anon read ai_limits';
  exception when insufficient_privilege then null;
  end;

  begin
    perform count(*) from public.ai_usage_daily;
    raise exception 'FAIL: anon read ai_usage_daily';
  exception when insufficient_privilege then null;
  end;

  begin
    perform count(*) from public.ai_key_health;
    raise exception 'FAIL: anon read ai_key_health';
  exception when insufficient_privilege then null;
  end;

  begin
    perform * from public.admin_ai_key_labels();
    raise exception 'FAIL: anon called admin_ai_key_labels';
  exception when insufficient_privilege then null;
  end;

  begin
    perform public.admin_set_ai_keys(array['nvapi-FAKEKEY00000000000000000009ZZZZ']);
    raise exception 'FAIL: anon called admin_set_ai_keys';
  exception when insufficient_privilege then null;
  end;

  begin
    perform * from public.ai_get_keys();
    raise exception 'FAIL: anon called ai_get_keys';
  exception when insufficient_privilege then null;
  end;

  begin
    perform public.ai_check_and_count('66666666-6666-4666-8666-666666666602', 10);
    raise exception 'FAIL: anon called ai_check_and_count';
  exception when insufficient_privilege then null;
  end;

  raise notice 'PASS: anon has zero access to shared AI tables and functions';
end;
$$;

reset role;
rollback;
