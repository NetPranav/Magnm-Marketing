-- Pause and resume from the phone (run_control, automation_control) and the task board / automatic-work mirror kinds
-- (board_item, automation).
-- Written to pass on a database that already holds real rows: it only looks at its own test identities, never deletes
-- from storage tables and rolls everything back.
begin;
set local client_min_messages = notice;

do $$
declare
  v_a uuid := '14141414-1414-4414-8414-141414141401';
  v_b uuid := '14141414-1414-4414-8414-141414141402';
  v_deact uuid := '14141414-1414-4414-8414-141414141403';
begin
  insert into public.invites (email, role)
  values ('a.pb@example.com', 'member'), ('b.pb@example.com', 'member'), ('deact.pb@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_a, 'a.pb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"A PB"}'::jsonb),
    (v_b, 'b.pb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"B PB"}'::jsonb),
    (v_deact, 'deact.pb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact PB"}'::jsonb);

  update public.profiles set active = false where id = v_deact;

  -- The new function is not callable by anonymous clients; signed-in clients need it for the check constraint.
  if has_function_privilege('anon', 'public.phone_control_ok(text, text, jsonb)', 'execute') then
    raise exception 'FAIL: anon can execute phone_control_ok';
  end if;
  if not has_function_privilege('authenticated', 'public.phone_control_ok(text, text, jsonb)', 'execute') then
    raise exception 'FAIL: signed-in clients cannot execute phone_control_ok, so the constraint would fail for them';
  end if;
  raise notice 'PASS: phone_control_ok is not executable by anon';

  -- The pure check says yes to the exact shapes and a plain no to everything else (never an error).
  if not public.phone_control_ok('run_control', 'wf_post_1795464083916_312cad', '{"action":"pause"}'::jsonb)
     or not public.phone_control_ok('run_control', 'wf_weekly_plan_1_aaaaaa', '{"action":"resume"}'::jsonb)
     or not public.phone_control_ok('automation_control', 'all', '{"action":"pause"}'::jsonb) then
    raise exception 'FAIL: the exact shapes were refused';
  end if;
  if public.phone_control_ok('run_control', 'all', '{"action":"pause"}'::jsonb)
     or public.phone_control_ok('automation_control', 'wf_post_1_aaaaaa', '{"action":"pause"}'::jsonb)
     or public.phone_control_ok('other', 'all', '{"action":"pause"}'::jsonb)
     or public.phone_control_ok('run_control', null, '{"action":"pause"}'::jsonb)
     or public.phone_control_ok('run_control', 'wf_post_1_aaaaaa', null)
     or public.phone_control_ok('run_control', 'wf_post_1_aaaaaa', '[]'::jsonb)
     or public.phone_control_ok('run_control', 'wf_post_1_aaaaaa', '"pause"'::jsonb) then
    raise exception 'FAIL: a malformed call was accepted';
  end if;
  raise notice 'PASS: phone_control_ok accepts the exact shapes only and never raises';
end;
$$;

-- Member A queues pause and resume.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '14141414-1414-4414-8414-141414141401', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '14141414-1414-4414-8414-141414141401';
  v_case jsonb;
  v_n int;
begin
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('ffffffff-0000-4000-8000-000000000001', 'run_control', 'wf_post_1795464083916_312cad', '{"action":"pause"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000002', 'run_control', 'wf_post_1795464083916_312cad', '{"action":"resume"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000003', 'automation_control', 'all', '{"action":"pause"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000004', 'automation_control', 'all', '{"action":"resume"}'::jsonb);
  raise notice 'PASS: owner can queue pause and resume for a run and for automatic work';

  -- Every older kind keeps its exact shape.
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('ffffffff-0000-4000-8000-000000000010', 'draft_comment', 'cnt_1', '{"body":"make it < 100 words","version":2}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000011', 'draft_posted', 'cnt_1', '{"url":"https://example.com/p/1"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000012', 'reminder_done', 'rem_1', '{}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000013', 'agent_task', 'writer', '{"message":"Draft a post"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000014', 'pitch_create', 'new', '{"kind":"proposal","client_name":"Meera"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000015', 'pitch_update', 'prop_1', '{"base_version":"v1","fields":{"notes":"x"}}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000016', 'pitch_finalize', 'prop_1', '{}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000017', 'pitch_link', 'prop_1', '{"action":"publish"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000018', 'library_upload', 'dddddddd-0000-4000-8000-000000000001', '{"category":"brand"}'::jsonb),
    ('ffffffff-0000-4000-8000-000000000019', 'schedule_update', 'market_scan', '{"enabled":false}'::jsonb),
    ('ffffffff-0000-4000-8000-00000000001a', 'draft_create', 'new', '{"platform":"x","title":"t","body":"b"}'::jsonb),
    ('ffffffff-0000-4000-8000-00000000001b', 'workflow_request', 'post', '{"brief":"Announce the release"}'::jsonb);
  raise notice 'PASS: all twelve older kinds still accept their exact shapes';

  -- Hostile payloads and targets are refused by the database itself. Cancel is not a phone action.
  for v_case in select * from jsonb_array_elements($j$[
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":"cancel"}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":"delete"}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":"PAUSE"}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":["pause"]}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":null}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":1}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":"pause","extra":1}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":{"action":"pause","run":"other"}},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":[]},
    {"kind":"run_control","target":"wf_post_1_abcdef","payload":"pause"},
    {"kind":"run_control","target":"all","payload":{"action":"pause"}},
    {"kind":"run_control","target":"../etc/passwd","payload":{"action":"pause"}},
    {"kind":"run_control","target":"wf_post_1_abcdef' or 1=1","payload":{"action":"pause"}},
    {"kind":"run_control","target":"WF_POST_1_ABCDEF","payload":{"action":"pause"}},
    {"kind":"run_control","target":"wf_post_1_abcde","payload":{"action":"pause"}},
    {"kind":"run_control","target":"wf_post_1_abcdeg","payload":{"action":"pause"}},
    {"kind":"run_control","target":"wf_post_abcdef","payload":{"action":"pause"}},
    {"kind":"run_control","target":"wf_post_1_abcdef\n","payload":{"action":"pause"}},
    {"kind":"automation_control","target":"wf_post_1_abcdef","payload":{"action":"pause"}},
    {"kind":"automation_control","target":"everything","payload":{"action":"pause"}},
    {"kind":"automation_control","target":"ALL","payload":{"action":"pause"}},
    {"kind":"automation_control","target":"all","payload":{}},
    {"kind":"automation_control","target":"all","payload":{"action":"stop"}},
    {"kind":"automation_control","target":"all","payload":{"paused":true}},
    {"kind":"automation_control","target":"all","payload":{"action":"resume","missed":"run"}},
    {"kind":"automation_control","target":"all","payload":{"action":"pause","action2":"x"}},
    {"kind":"run_teleport","target":"all","payload":{"action":"pause"}},
    {"kind":"automation_control_v2","target":"all","payload":{"action":"pause"}}
  ]$j$::jsonb) loop
    begin
      insert into public.phone_actions (id, kind, target_id, payload)
      values (gen_random_uuid(), v_case->>'kind', v_case->>'target', v_case->'payload');
      raise exception 'FAIL: accepted a bad action %', v_case;
    exception when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: malformed, over-wide and hostile pause and resume requests are refused';

  -- Owner and status stay out of the client's hands.
  begin
    insert into public.phone_actions (id, kind, target_id, payload, owner_id)
    values (gen_random_uuid(), 'automation_control', 'all', '{"action":"pause"}'::jsonb, '14141414-1414-4414-8414-141414141402');
    raise exception 'FAIL: a client chose the owner of a control action';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload, status)
    values (gen_random_uuid(), 'automation_control', 'all', '{"action":"pause"}'::jsonb, 'applied');
    raise exception 'FAIL: a client set the status of a control action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: the owner and the status cannot be chosen';

  -- The laptop claims and finishes the new kinds like any other.
  if (select (public.claim_phone_action('ffffffff-0000-4000-8000-000000000003', 'laptop-pb')).status) <> 'applying' then
    raise exception 'FAIL: could not claim an automation_control';
  end if;
  if (select (public.finish_phone_action('ffffffff-0000-4000-8000-000000000003', 'laptop-pb', 'applied', 'Automatic work paused.')).status) <> 'applied' then
    raise exception 'FAIL: could not finish an automation_control';
  end if;
  raise notice 'PASS: the new kinds are claimed and finished like the older ones';

  -- The mirror takes the new kinds, still refuses invented ones and keeps every older one.
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at) values
    ('board_item', 'run:wf_post_1_abcdef', 'post: Share an update', '{"kind":"workflow","column":"running","actions":["pause"]}'::jsonb, now()),
    ('board_item', 'run:wf_post_2_abcdef', 'Confidential item', '{"confidential":true,"column":"running"}'::jsonb, now()),
    ('automation', 'state', 'Automatic work', '{"paused":false,"missed":[]}'::jsonb, now());
  begin
    insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
    values ('board_items', 'x', 'x', '{}'::jsonb, now());
    raise exception 'FAIL: an invented mirror kind was accepted';
  exception when check_violation then null;
  end;
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  select k, 'pb_kind_' || k, 't', '{}'::jsonb, now()
  from unnest(array['chat_session','team_message','chat_turn','content_item','content_version','research_note','review_comment','metric','posted_link','brief','task','pitch_proposal','asset','schedule','editorial_plan']) k;
  raise notice 'PASS: board_item and automation are mirror kinds and every older kind is kept';

  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n < 16 then raise exception 'FAIL: A cannot read their own actions (%)', v_n; end if;
end;
$$;

-- Member B, a deactivated member and anonymous callers see and change nothing of A's.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '14141414-1414-4414-8414-141414141402', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '14141414-1414-4414-8414-141414141401';
  v_n int;
begin
  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads A actions (%)', v_n; end if;
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind in ('board_item', 'automation');
  if v_n <> 0 then raise exception 'FAIL: member B reads A board records (%)', v_n; end if;
  update public.mirror_records set title = 'hacked' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B changed A mirror records'; end if;
  begin
    update public.phone_actions set status = 'applied' where owner_id = v_a;
    get diagnostics v_n = row_count;
    if v_n <> 0 then raise exception 'FAIL: member B changed A actions'; end if;
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: another member reads and changes nothing of A''s board or actions';
end;
$$;

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '14141414-1414-4414-8414-141414141403', 'role', 'authenticated')::text,
  true
);

do $$
begin
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'automation_control', 'all', '{"action":"pause"}'::jsonb);
    raise exception 'FAIL: a deactivated member queued a control action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: a deactivated member cannot queue a pause';
end;
$$;

reset role;
set local role anon;
do $$
declare
  v_n int;
begin
  begin
    select count(*) into v_n from public.phone_actions;
    raise exception 'FAIL: anon can read phone_actions';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'automation_control', 'all', '{"action":"pause"}'::jsonb);
    raise exception 'FAIL: anon queued a control action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon reads and queues nothing';
end;
$$;

reset role;
rollback;
