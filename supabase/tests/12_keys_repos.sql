-- Personal API keys (Vault, owner only) and personal repositories (owner only) synced between laptop and phone.
-- Works on a non-empty database: it only counts and reads rows of its own test users. Every key below is fake.
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '12121212-1212-4212-8212-121212121201';
  v_a uuid := '12121212-1212-4212-8212-121212121202';
  v_b uuid := '12121212-1212-4212-8212-121212121203';
  v_deact uuid := '12121212-1212-4212-8212-121212121204';
begin
  insert into public.invites (email, role)
  values
    ('admin.kr@example.com', 'admin'),
    ('member.kra@example.com', 'member'),
    ('member.krb@example.com', 'member'),
    ('deact.kr@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.kr@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin KR"}'::jsonb),
    (v_a, 'member.kra@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member KRA"}'::jsonb),
    (v_b, 'member.krb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member KRB"}'::jsonb),
    (v_deact, 'deact.kr@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact KR"}'::jsonb);

  update public.profiles set active = false where id = v_deact;
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Member A: personal keys
-- ---------------------------------------------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121202', 'role', 'authenticated')::text, true);

do $$
declare
  v_key1 text := 'nvapi-FAKEKEYUSERA00000000000000000001';
  v_key2 text := 'nvapi-FAKEKEYUSERA00000000000000000002';
  v_row record;
  v_cnt int;
  v_bad text;
  v_slot text;
begin
  select * into v_row from public.personal_key_set('NVIDIA_NIM_API_KEY', v_key1);
  if v_row.name <> 'NVIDIA_NIM_API_KEY' or v_row.last4 <> '0001' or v_row.version <> 1
     or v_row.fingerprint <> encode(sha256(convert_to(v_key1, 'UTF8')), 'hex') then
    raise exception 'FAIL: personal_key_set returned %', to_jsonb(v_row);
  end if;
  if position('FAKEKEY' in to_jsonb(v_row)::text) > 0 then
    raise exception 'FAIL: personal_key_set returned the key';
  end if;
  raise notice 'PASS: a member can save a personal key and gets back only the label, fingerprint and version';

  select count(*) into v_cnt from public.personal_key_list();
  if v_cnt <> 1 then raise exception 'FAIL: expected 1 listed key, got %', v_cnt; end if;
  if exists (select 1 from public.personal_key_list() l where position('FAKEKEY' in to_jsonb(l)::text) > 0) then
    raise exception 'FAIL: personal_key_list exposed a key';
  end if;
  if (select api_key from public.personal_key_get(array['NVIDIA_NIM_API_KEY'])) is distinct from v_key1 then
    raise exception 'FAIL: the owner cannot read back their own key';
  end if;
  raise notice 'PASS: list shows labels only and get returns the key to its owner';

  -- Replacing a slot keeps one secret and bumps the version.
  select * into v_row from public.personal_key_set('NVIDIA_NIM_API_KEY', v_key2);
  if v_row.version <> 2 or v_row.last4 <> '0002' then
    raise exception 'FAIL: replacing a key gave %', to_jsonb(v_row);
  end if;
  if (select api_key from public.personal_key_get(array['NVIDIA_NIM_API_KEY'])) is distinct from v_key2 then
    raise exception 'FAIL: the replaced key was not stored';
  end if;
  raise notice 'PASS: replacing a key keeps the slot and bumps the version';

  -- Bad slot names and bad key shapes are refused, and the error never repeats the key.
  foreach v_bad in array array['NVIDIA_NIM_API_KEY11', 'NVIDIA_NIM_API_KEY0', 'OPENAI_API_KEY', 'nvidia_nim_api_key',
    'NVIDIA_NIM_API_KEY; drop table x', '../NVIDIA_NIM_API_KEY', '', 'NVIDIA_NIM_API_KEY1 ', E'NVIDIA_NIM_API_KEY\n'] loop
    begin
      perform * from public.personal_key_set(v_bad, 'nvapi-FAKEKEYUSERA00000000000000000009');
      raise exception 'FAIL: a key was saved under slot name %', v_bad;
    exception when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      if position('FAKEKEY' in sqlerrm) > 0 then raise exception 'FAIL: error text repeated the key'; end if;
    end;
  end loop;
  foreach v_bad in array array['', 'nvapi-short', 'sk-FAKEKEYUSERA00000000000000000003', 'nvapi-FAKEKEYUSERA0000000000000000 4',
    E'nvapi-FAKEKEYUSERA00000000000000000005\n', ' nvapi-FAKEKEYUSERA00000000000000000006',
    'nvapi-FAKEKEYUSERA00000000000000000007;', 'nvapi-' || repeat('A', 201)] loop
    begin
      perform * from public.personal_key_set('NVIDIA_NIM_API_KEY1', v_bad);
      raise exception 'FAIL: a malformed key was saved: %', left(v_bad, 12);
    exception when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      if position('FAKEKEY' in sqlerrm) > 0 then raise exception 'FAIL: error text repeated the key'; end if;
    end;
  end loop;
  begin
    perform * from public.personal_key_set('NVIDIA_NIM_API_KEY1', null);
    raise exception 'FAIL: a null key was saved';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform * from public.personal_key_set(null, v_key1);
    raise exception 'FAIL: a null slot was saved';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: bad slot names and malformed keys are refused without echoing the key';

  -- The same key cannot sit in two slots.
  begin
    perform * from public.personal_key_set('NVIDIA_NIM_API_KEY1', v_key2);
    raise exception 'FAIL: the same key was saved in two slots';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    if position('FAKEKEY' in sqlerrm) > 0 then raise exception 'FAIL: error text repeated the key'; end if;
  end;
  raise notice 'PASS: one key cannot fill two slots';

  -- All eleven slots work; there is no twelfth.
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY1', 'nvapi-FAKEKEYUSERA00000000000000000011');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY2', 'nvapi-FAKEKEYUSERA00000000000000000012');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY3', 'nvapi-FAKEKEYUSERA00000000000000000013');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY4', 'nvapi-FAKEKEYUSERA00000000000000000014');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY5', 'nvapi-FAKEKEYUSERA00000000000000000015');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY6', 'nvapi-FAKEKEYUSERA00000000000000000016');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY7', 'nvapi-FAKEKEYUSERA00000000000000000017');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY8', 'nvapi-FAKEKEYUSERA00000000000000000018');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY9', 'nvapi-FAKEKEYUSERA00000000000000000019');
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY10', 'nvapi-FAKEKEYUSERA00000000000000000020');
  select count(*) into v_cnt from public.personal_key_list();
  if v_cnt <> 11 then raise exception 'FAIL: expected 11 slots, got %', v_cnt; end if;
  raise notice 'PASS: eleven key slots are available and no more';

  -- Delete returns true once, false after, and a stranger slot name is refused.
  if not public.personal_key_delete('NVIDIA_NIM_API_KEY10') then raise exception 'FAIL: delete returned false'; end if;
  if public.personal_key_delete('NVIDIA_NIM_API_KEY10') then raise exception 'FAIL: second delete returned true'; end if;
  begin
    perform public.personal_key_delete('OPENAI_API_KEY');
    raise exception 'FAIL: delete accepted an unknown slot';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  select count(*) into v_cnt from public.personal_key_list();
  if v_cnt <> 10 then raise exception 'FAIL: expected 10 slots after delete, got %', v_cnt; end if;
  raise notice 'PASS: delete removes one slot and refuses unknown names';

  -- get refuses a long or hostile name list instead of running it.
  begin
    perform * from public.personal_key_get(array(select 'NVIDIA_NIM_API_KEY' from generate_series(1, 40)));
    raise exception 'FAIL: get accepted 40 names';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  if exists (select 1 from public.personal_key_get(array['OPENAI_API_KEY', 'x'' or ''1''=''1'])) then
    raise exception 'FAIL: get returned something for unknown names';
  end if;
  raise notice 'PASS: get is bounded and ignores unknown names';

  -- The table itself and Vault are out of reach for a client.
  begin
    perform count(*) from public.personal_api_keys;
    raise exception 'FAIL: a client selected from personal_api_keys';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.personal_api_keys (owner_id, name, secret_id, last4, fingerprint)
    values ('12121212-1212-4212-8212-121212121202', 'NVIDIA_NIM_API_KEY10', gen_random_uuid(), 'abcd', repeat('a', 64));
    raise exception 'FAIL: a client inserted into personal_api_keys';
  exception when insufficient_privilege then null;
  end;
  begin
    perform count(*) from vault.decrypted_secrets;
    raise exception 'FAIL: a client read vault.decrypted_secrets';
  exception when insufficient_privilege then null;
  end;
  begin
    perform count(*) from vault.secrets;
    raise exception 'FAIL: a client read vault.secrets';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: clients cannot touch the key table or Vault directly';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Member B: cannot see or touch A's keys; own keys are separate
-- ---------------------------------------------------------------------------------------------------------------
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121203', 'role', 'authenticated')::text, true);

do $$
declare
  v_key_b text := 'nvapi-FAKEKEYUSERB00000000000000000001';
  v_cnt int;
begin
  select count(*) into v_cnt from public.personal_key_list();
  if v_cnt <> 0 then raise exception 'FAIL: B lists % keys that are not theirs', v_cnt; end if;
  if exists (select 1 from public.personal_key_get(array['NVIDIA_NIM_API_KEY', 'NVIDIA_NIM_API_KEY1', 'NVIDIA_NIM_API_KEY2'])) then
    raise exception 'FAIL: B read the key of another member';
  end if;
  if public.personal_key_delete('NVIDIA_NIM_API_KEY') then
    raise exception 'FAIL: B deleted the key of another member';
  end if;
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY', v_key_b);
  if (select api_key from public.personal_key_get(array['NVIDIA_NIM_API_KEY'])) is distinct from v_key_b then
    raise exception 'FAIL: B did not get their own key back';
  end if;
  -- B may even save a key equal to a key A saved; it must not reveal or clash with A.
  perform * from public.personal_key_set('NVIDIA_NIM_API_KEY1', 'nvapi-FAKEKEYUSERA00000000000000000011');
  raise notice 'PASS: member B sees, reads and deletes only their own keys';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Admin: no access to personal keys by any route
-- ---------------------------------------------------------------------------------------------------------------
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121201', 'role', 'authenticated')::text, true);

do $$
declare
  v_cnt int;
begin
  select count(*) into v_cnt from public.personal_key_list();
  if v_cnt <> 0 then raise exception 'FAIL: admin lists % personal keys', v_cnt; end if;
  if exists (select 1 from public.personal_key_get(array['NVIDIA_NIM_API_KEY', 'NVIDIA_NIM_API_KEY1'])) then
    raise exception 'FAIL: admin read a member personal key';
  end if;
  if public.personal_key_delete('NVIDIA_NIM_API_KEY') then
    raise exception 'FAIL: admin deleted a member personal key';
  end if;
  begin
    perform count(*) from public.personal_api_keys;
    raise exception 'FAIL: admin selected from personal_api_keys';
  exception when insufficient_privilege then null;
  end;
  begin
    perform count(*) from vault.decrypted_secrets;
    raise exception 'FAIL: admin read Vault directly';
  exception when insufficient_privilege then null;
  end;
  -- The team key screens never show personal keys.
  begin
    perform * from public.ai_get_keys();
    raise exception 'FAIL: admin called ai_get_keys';
  exception when insufficient_privilege then null;
  end;
  -- Replacing the team keys must not wipe or expose personal ones.
  perform public.admin_set_ai_keys(array['nvapi-FAKETEAMKEY00000000000000000001']);
  select count(*) into v_cnt from public.admin_ai_key_labels();
  if v_cnt <> 1 then
    raise exception 'FAIL: admin_ai_key_labels lists % keys, expected only the one team key', v_cnt;
  end if;
  raise notice 'PASS: an admin cannot list, read, delete or reach personal keys, and team key screens do not show them';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Deactivated member and anon
-- ---------------------------------------------------------------------------------------------------------------
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121204', 'role', 'authenticated')::text, true);

do $$
begin
  begin
    perform * from public.personal_key_set('NVIDIA_NIM_API_KEY', 'nvapi-FAKEKEYDEACT0000000000000000001');
    raise exception 'FAIL: a deactivated member saved a key';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform * from public.personal_key_list();
    raise exception 'FAIL: a deactivated member listed keys';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform * from public.personal_key_get(array['NVIDIA_NIM_API_KEY']);
    raise exception 'FAIL: a deactivated member read keys';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: a deactivated member cannot use personal keys';
end;
$$;

reset role;
set local role anon;
select set_config('request.jwt.claims', json_build_object('role', 'anon')::text, true);

do $$
begin
  begin
    perform * from public.personal_key_list();
    raise exception 'FAIL: anon listed keys';
  exception when insufficient_privilege then null;
  end;
  begin
    perform * from public.personal_key_get(array['NVIDIA_NIM_API_KEY']);
    raise exception 'FAIL: anon read keys';
  exception when insufficient_privilege then null;
  end;
  begin
    perform * from public.personal_key_set('NVIDIA_NIM_API_KEY', 'nvapi-FAKEKEYANON00000000000000000001');
    raise exception 'FAIL: anon saved a key';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.personal_key_delete('NVIDIA_NIM_API_KEY');
    raise exception 'FAIL: anon deleted a key';
  exception when insufficient_privilege then null;
  end;
  begin
    perform count(*) from public.personal_api_keys;
    raise exception 'FAIL: anon selected from personal_api_keys';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon has no access to personal keys';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Superuser view: where the keys are, and where they are not
-- ---------------------------------------------------------------------------------------------------------------
reset role;

do $$
declare
  v_a uuid := '12121212-1212-4212-8212-121212121202';
  v_b uuid := '12121212-1212-4212-8212-121212121203';
  v_cnt int;
begin
  -- A's ten keys and B's two are in Vault under the owner and slot name; the team key is untouched.
  select count(*) into v_cnt from vault.decrypted_secrets where name like 'magnm\_personal\_key\_' || v_a::text || '\_%';
  if v_cnt <> 10 then raise exception 'FAIL: expected 10 vault secrets for A, got %', v_cnt; end if;
  select count(*) into v_cnt from vault.decrypted_secrets where name like 'magnm\_personal\_key\_' || v_b::text || '\_%';
  if v_cnt <> 2 then raise exception 'FAIL: expected 2 vault secrets for B, got %', v_cnt; end if;
  if not exists (select 1 from vault.decrypted_secrets where name = 'magnm_ai_key_1'
                 and decrypted_secret = 'nvapi-FAKETEAMKEY00000000000000000001') then
    raise exception 'FAIL: the shared team key is missing';
  end if;
  raise notice 'PASS: personal keys live in Vault by owner and slot, apart from the shared team keys';

  -- Shared team key lookup (used by the ai-proxy function) never returns a personal key.
  set local role service_role;
  select count(*) into v_cnt from public.ai_get_keys() k where position('FAKEKEYUSER' in k.api_key) > 0;
  reset role;
  if v_cnt <> 0 then raise exception 'FAIL: a personal key reached the shared key pool'; end if;
  raise notice 'PASS: ai_get_keys never returns a personal key';

  -- No key text in audit, activity or mirror.
  select count(*) into v_cnt from public.audit_log
   where position('FAKEKEY' in coalesce(target, '') || coalesce(action, '') || coalesce(data::text, '')) > 0
      or action like 'personal_key%';
  if v_cnt <> 0 then raise exception 'FAIL: audit_log has personal key rows or text (%)', v_cnt; end if;
  select count(*) into v_cnt from public.activity_events where position('FAKEKEY' in coalesce(summary, '') || coalesce(data::text, '')) > 0;
  if v_cnt <> 0 then raise exception 'FAIL: activity_events has key text'; end if;
  select count(*) into v_cnt from public.mirror_records where position('FAKEKEY' in coalesce(title, '') || coalesce(data::text, '')) > 0;
  if v_cnt <> 0 then raise exception 'FAIL: mirror_records has key text'; end if;
  raise notice 'PASS: no key text or personal key row in audit, activity or mirror';

  -- Deleting a member removes their keys from Vault too.
  delete from auth.users where id = v_b;
  select count(*) into v_cnt from vault.decrypted_secrets where name like 'magnm\_personal\_key\_' || v_b::text || '\_%';
  if v_cnt <> 0 then raise exception 'FAIL: % vault secrets stayed after the member was deleted', v_cnt; end if;
  select count(*) into v_cnt from vault.decrypted_secrets where name like 'magnm\_personal\_key\_' || v_a::text || '\_%';
  if v_cnt <> 10 then raise exception 'FAIL: deleting B changed the secrets of A (%)', v_cnt; end if;
  raise notice 'PASS: deleting a member removes their keys from Vault and leaves other members alone';
end;
$$;

-- Deleting a slot as the owner removes the vault secret.
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121202', 'role', 'authenticated')::text, true);
select public.personal_key_delete('NVIDIA_NIM_API_KEY9');
reset role;

do $$
declare
  v_a uuid := '12121212-1212-4212-8212-121212121202';
begin
  if exists (select 1 from vault.decrypted_secrets where name = 'magnm_personal_key_' || v_a::text || '_NVIDIA_NIM_API_KEY9') then
    raise exception 'FAIL: the vault secret stayed after the key was deleted';
  end if;
  raise notice 'PASS: deleting a key removes its Vault secret';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Personal repositories: member A
-- ---------------------------------------------------------------------------------------------------------------
-- B was deleted above, so create a fresh second member for the repo checks.
do $$
declare
  v_c uuid := '12121212-1212-4212-8212-121212121205';
begin
  insert into public.invites (email, role) values ('member.krc@example.com', 'member');
  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values (v_c, 'member.krc@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member KRC"}'::jsonb);
end;
$$;

set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121202', 'role', 'authenticated')::text, true);

do $$
declare
  v_a uuid := '12121212-1212-4212-8212-121212121202';
  v_row public.personal_repos;
  v_row2 public.personal_repos;
  v_bad text;
  v_cnt int;
  v_uid uuid;
begin
  v_row := public.personal_repo_save(null,
    '{"id":"demo-app","name":"Demo App","kind":"github","github":"Acme/Demo-App","branch":"main","status":"released","marketing_angle":"Fast and calm."}'::jsonb);
  if v_row.owner_id <> v_a or v_row.rev <> 1 or v_row.path_status <> 'ok' or v_row.uid is null or v_row.confidential then
    raise exception 'FAIL: new repo is wrong: %', to_jsonb(v_row);
  end if;
  v_uid := v_row.uid;
  raise notice 'PASS: a member can add a GitHub repository to their own list';

  -- Spoofing the owner or internals is refused, not ignored.
  foreach v_bad in array array['owner_id', 'uid', 'rev', 'field_rev', 'field_at', 'created_at', 'path_status', 'path_reason', 'secret'] loop
    begin
      perform public.personal_repo_save(null, jsonb_build_object('id', 'spoof-x', 'name', 'Spoof', 'kind', 'github',
        'github', 'acme/spoof-x', v_bad, '12121212-1212-4212-8212-121212121299'));
      raise exception 'FAIL: personal_repo_save accepted field %', v_bad;
    exception when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
    end;
  end loop;
  begin
    perform public.personal_repo_save(null, '[1,2]'::jsonb);
    raise exception 'FAIL: personal_repo_save accepted an array';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.personal_repo_save(null, null);
    raise exception 'FAIL: personal_repo_save accepted null';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.personal_repo_save(null, '{"id":"nonbool","name":"N","kind":"github","github":"acme/nonbool","confidential":"no"}'::jsonb);
    raise exception 'FAIL: personal_repo_save accepted a non-boolean confidential flag';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.personal_repo_save(null, '{"id":"nonstr","name":5,"kind":"github","github":"acme/nonstr"}'::jsonb);
    raise exception 'FAIL: personal_repo_save accepted a numeric name';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  select count(*) into v_cnt from public.personal_repos where id like 'spoof%' or id like 'non%';
  if v_cnt <> 0 then raise exception 'FAIL: a refused repo was stored'; end if;
  raise notice 'PASS: spoofed owner, internal fields and wrong types are refused';

  -- Hostile values.
  foreach v_bad in array array['owner', 'owner/', '/repo', 'owner/repo/extra', 'owner/..', 'owner/.', '-owner/repo', 'own er/repo',
    'owner/repo;rm', 'https://github.com/owner/repo', 'git@github.com:owner/repo', E'owner/repo\n', 'owner/repo?x=1', 'owner/repo$(id)'] loop
    begin
      perform public.personal_repo_save(null, jsonb_build_object('id', 'bad-gh', 'name', 'Bad', 'kind', 'github', 'github', v_bad));
      raise exception 'FAIL: accepted github value %', v_bad;
    exception when check_violation then null;
    end;
  end loop;
  foreach v_bad in array array['Upper', '-lead', 'has space', 'a/b', '../x', '', repeat('a', 64), 'x;y'] loop
    begin
      perform public.personal_repo_save(null, jsonb_build_object('id', v_bad, 'name', 'Bad', 'kind', 'github', 'github', 'acme/ok-id'));
      raise exception 'FAIL: accepted id %', v_bad;
    exception when check_violation then null;
    end;
  end loop;
  foreach v_bad in array array['--upload-pack=x', '../main', 'a..b', 'feature/', 'a//b', 'x.lock', 'with space', repeat('b', 101)] loop
    begin
      perform public.personal_repo_save(null, jsonb_build_object('id', 'bad-br', 'name', 'Bad', 'kind', 'github', 'github', 'acme/bad-br', 'branch', v_bad));
      raise exception 'FAIL: accepted branch %', v_bad;
    exception when check_violation then null;
    end;
  end loop;
  foreach v_bad in array array['../etc', '/home/x/../../etc', 'a/../b', '..', E'/tmp/x\ny', E'/tmp/x\ty', '', repeat('/a', 251), E'C:\\x\\..\\y'] loop
    begin
      perform public.personal_repo_save(null, jsonb_build_object('id', 'bad-path', 'name', 'Bad', 'kind', 'local', 'local_path', v_bad));
      raise exception 'FAIL: accepted local_path %', left(v_bad, 20);
    exception when check_violation then null;
    end;
  end loop;
  begin
    perform public.personal_repo_save(null, '{"id":"no-path","name":"No Path","kind":"local"}'::jsonb);
    raise exception 'FAIL: a local repo without a path was accepted';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, '{"id":"no-gh","name":"No GH","kind":"github"}'::jsonb);
    raise exception 'FAIL: a github repo without a slug was accepted';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'long-name', 'name', repeat('n', 81), 'kind', 'github', 'github', 'acme/long-name'));
    raise exception 'FAIL: accepted an 81 character name';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'ctl-name', 'name', E'Line\nbreak', 'kind', 'github', 'github', 'acme/ctl-name'));
    raise exception 'FAIL: accepted a name with a control character';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'big-angle', 'name', 'Big', 'kind', 'github', 'github', 'acme/big-angle', 'marketing_angle', repeat('x', 501)));
    raise exception 'FAIL: accepted a 501 character marketing angle';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'ctl-angle', 'name', 'Ctl', 'kind', 'github', 'github', 'acme/ctl-angle', 'marketing_angle', E'bad\x07bell'));
    raise exception 'FAIL: accepted a marketing angle with a control character';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'bad-st', 'name', 'Bad', 'kind', 'github', 'github', 'acme/bad-st', 'status', 'live'));
    raise exception 'FAIL: accepted an unknown status';
  exception when check_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, jsonb_build_object('id', 'bad-kind', 'name', 'Bad', 'kind', 'svn', 'github', 'acme/bad-kind'));
    raise exception 'FAIL: accepted an unknown kind';
  exception when check_violation then null;
  end;
  raise notice 'PASS: personal_repos rejects hostile ids, slugs, branches, paths, names, angles, kinds and statuses';

  begin
    perform public.personal_repo_save(null, '{"id":"dup-gh","name":"Dup","kind":"github","github":"acme/demo-app"}'::jsonb);
    raise exception 'FAIL: accepted a duplicate github slug ignoring case';
  exception when unique_violation then null;
  end;
  begin
    perform public.personal_repo_save(null, '{"id":"demo-app","name":"Dup Id","kind":"github","github":"acme/other"}'::jsonb);
    raise exception 'FAIL: accepted a duplicate id';
  exception when unique_violation then null;
  end;
  raise notice 'PASS: ids and GitHub slugs are unique per owner';

  -- Per-field versions: only changed fields get a new version.
  v_row2 := public.personal_repo_save(v_uid, '{"name":"Demo App 2","branch":"main"}'::jsonb);
  if v_row2.rev <> 2 or v_row2.name <> 'Demo App 2' or (v_row2.field_rev->>'name')::bigint <> 2
     or v_row2.field_rev ? 'branch' and (v_row2.field_rev->>'branch')::bigint = 2 then
    raise exception 'FAIL: per-field version is wrong: %', to_jsonb(v_row2);
  end if;
  if v_row2.status <> 'released' or v_row2.marketing_angle <> 'Fast and calm.' then
    raise exception 'FAIL: an update changed a field it did not send';
  end if;
  v_row2 := public.personal_repo_save(v_uid, '{"status":"in_development","confidential":true}'::jsonb);
  if v_row2.rev <> 3 or (v_row2.field_rev->>'status')::bigint <> 3 or (v_row2.field_rev->>'name')::bigint <> 2
     or not v_row2.confidential or v_row2.field_at->>'status' is null then
    raise exception 'FAIL: second per-field version is wrong: %', to_jsonb(v_row2);
  end if;
  v_row2 := public.personal_repo_save(v_uid, '{"confidential":false}'::jsonb);
  raise notice 'PASS: each edited field carries its own version and untouched fields stay as they are';

  -- Local path: an edit that was not checked by the laptop is pending; the laptop's answer sets ok or rejected.
  v_row2 := public.personal_repo_save(v_uid, '{"local_path":"/work/demo-app"}'::jsonb);
  if v_row2.path_status <> 'pending' or v_row2.local_path <> '/work/demo-app' then
    raise exception 'FAIL: an unchecked path edit is not pending: %', to_jsonb(v_row2);
  end if;
  perform public.personal_repo_path_result(v_uid, '/somewhere/else', true, null);
  if (select path_status from public.personal_repos where uid = v_uid) <> 'pending' then
    raise exception 'FAIL: a stale path result changed the status';
  end if;
  perform public.personal_repo_path_result(v_uid, '/work/demo-app', false, 'Folder does not exist ' || repeat('x', 400));
  select * into v_row2 from public.personal_repos where uid = v_uid;
  if v_row2.path_status <> 'rejected' or char_length(v_row2.path_reason) > 200 then
    raise exception 'FAIL: rejected path result is wrong: %', to_jsonb(v_row2);
  end if;
  perform public.personal_repo_path_result(v_uid, '/work/demo-app', true, 'ignored');
  select * into v_row2 from public.personal_repos where uid = v_uid;
  if v_row2.path_status <> 'ok' or v_row2.path_reason is not null then
    raise exception 'FAIL: ok path result is wrong: %', to_jsonb(v_row2);
  end if;
  v_row2 := public.personal_repo_save(v_uid, '{"local_path":"/work/demo-app-2"}'::jsonb, true);
  if v_row2.path_status <> 'ok' then raise exception 'FAIL: a checked path edit is not ok'; end if;
  raise notice 'PASS: an unchecked path edit is pending until the laptop marks it ok or rejected';

  -- Rename keeps the uid (the other device can follow) and refuses a taken id.
  perform public.personal_repo_save(null, '{"id":"second-app","name":"Second","kind":"github","github":"acme/second-app"}'::jsonb);
  begin
    perform public.personal_repo_save(v_uid, '{"id":"second-app"}'::jsonb);
    raise exception 'FAIL: renamed onto an id that is taken';
  exception when unique_violation then null;
  end;
  v_row2 := public.personal_repo_save(v_uid, '{"id":"renamed-app"}'::jsonb);
  if v_row2.uid <> v_uid or v_row2.id <> 'renamed-app' or (v_row2.field_rev->>'id')::bigint <> v_row2.rev then
    raise exception 'FAIL: rename is wrong: %', to_jsonb(v_row2);
  end if;
  raise notice 'PASS: renaming keeps the stable uid and the unique id rule';

  -- A client cannot write the table directly.
  begin
    insert into public.personal_repos (id, name, kind, github) values ('direct', 'Direct', 'github', 'acme/direct');
    raise exception 'FAIL: direct insert into personal_repos';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.personal_repos set name = 'Direct';
    raise exception 'FAIL: direct update of personal_repos';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.personal_repos where uid = v_uid;
    raise exception 'FAIL: direct delete from personal_repos';
  exception when insufficient_privilege then null;
  end;
  select count(*) into v_cnt from public.personal_repos;
  if v_cnt <> 2 then raise exception 'FAIL: A sees % repos, expected 2', v_cnt; end if;
  raise notice 'PASS: clients read their repos but write only through the functions';
end;
$$;

-- Limit of 100 repos per owner, and the 101st is refused.
do $$
declare
  v_i int;
  v_cnt int;
begin
  select count(*) into v_cnt from public.personal_repos;
  for v_i in (v_cnt + 1)..100 loop
    perform public.personal_repo_save(null, jsonb_build_object('id', 'bulk-' || v_i, 'name', 'Bulk ' || v_i, 'kind', 'github', 'github', 'acme/bulk-' || v_i));
  end loop;
  begin
    perform public.personal_repo_save(null, '{"id":"bulk-over","name":"Over","kind":"github","github":"acme/bulk-over"}'::jsonb);
    raise exception 'FAIL: the 101st repository was accepted';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  select count(*) into v_cnt from public.personal_repos;
  if v_cnt <> 100 then raise exception 'FAIL: expected 100 repos, got %', v_cnt; end if;
  raise notice 'PASS: at most 100 repositories per member';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Member C (another member): cannot see or change A's repos
-- ---------------------------------------------------------------------------------------------------------------
reset role;
create temp table kr_uids as
  select uid, id from public.personal_repos where owner_id = '12121212-1212-4212-8212-121212121202' and id in ('renamed-app', 'second-app');
grant select on kr_uids to authenticated;

set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121205', 'role', 'authenticated')::text, true);

do $$
declare
  v_uid uuid;
  v_row public.personal_repos;
  v_cnt int;
begin
  select uid into v_uid from kr_uids where id = 'renamed-app';
  select count(*) into v_cnt from public.personal_repos;
  if v_cnt <> 0 then raise exception 'FAIL: C sees % repos of other members', v_cnt; end if;
  if exists (select 1 from public.personal_repos where uid = v_uid) then
    raise exception 'FAIL: C read the repo of A by uid';
  end if;
  begin
    perform public.personal_repo_save(v_uid, '{"name":"Hijacked","confidential":true}'::jsonb);
    raise exception 'FAIL: C changed the repo of A';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  begin
    perform public.personal_repo_path_result(v_uid, '/work/demo-app-2', false, 'nope');
    raise exception 'FAIL: C set a path result on the repo of A';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  if public.personal_repo_delete(v_uid) then raise exception 'FAIL: C deleted the repo of A'; end if;
  -- The same id and slug are free for C: uniqueness is per owner and leaks nothing.
  v_row := public.personal_repo_save(null, '{"id":"renamed-app","name":"Mine","kind":"github","github":"acme/demo-app"}'::jsonb);
  if v_row.owner_id <> '12121212-1212-4212-8212-121212121205' then raise exception 'FAIL: C row has the wrong owner'; end if;
  raise notice 'PASS: another member cannot see, change, check or delete my repositories';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Admin, deactivated member, anon
-- ---------------------------------------------------------------------------------------------------------------
select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121201', 'role', 'authenticated')::text, true);

do $$
declare
  v_uid uuid;
  v_cnt int;
begin
  select uid into v_uid from kr_uids where id = 'renamed-app';
  select count(*) into v_cnt from public.personal_repos;
  if v_cnt <> 0 then raise exception 'FAIL: admin reads % personal repos', v_cnt; end if;
  begin
    perform public.personal_repo_save(v_uid, '{"name":"Admin edit"}'::jsonb);
    raise exception 'FAIL: admin changed a member personal repo';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  if public.personal_repo_delete(v_uid) then raise exception 'FAIL: admin deleted a member personal repo'; end if;
  raise notice 'PASS: an admin cannot read or change personal repositories';
end;
$$;

select set_config('request.jwt.claims',
  json_build_object('sub', '12121212-1212-4212-8212-121212121204', 'role', 'authenticated')::text, true);

do $$
declare
  v_cnt int;
begin
  select count(*) into v_cnt from public.personal_repos;
  if v_cnt <> 0 then raise exception 'FAIL: a deactivated member reads repos'; end if;
  begin
    perform public.personal_repo_save(null, '{"id":"deact-x","name":"D","kind":"github","github":"acme/deact-x"}'::jsonb);
    raise exception 'FAIL: a deactivated member added a repo';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
  end;
  raise notice 'PASS: a deactivated member cannot use personal repositories';
end;
$$;

reset role;
set local role anon;
select set_config('request.jwt.claims', json_build_object('role', 'anon')::text, true);

do $$
begin
  begin
    perform count(*) from public.personal_repos;
    raise exception 'FAIL: anon selected from personal_repos';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.personal_repo_save(null, '{"id":"anon-x","name":"A","kind":"github","github":"acme/anon-x"}'::jsonb);
    raise exception 'FAIL: anon saved a repo';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.personal_repo_delete(gen_random_uuid());
    raise exception 'FAIL: anon deleted a repo';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.personal_repo_path_result(gen_random_uuid(), '/x', true, null);
    raise exception 'FAIL: anon set a path result';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon has no access to personal repositories';
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- Privileges and cleanup
-- ---------------------------------------------------------------------------------------------------------------
reset role;

do $$
declare
  v_fn text;
  v_cnt int;
begin
  foreach v_fn in array array[
    'public.personal_key_set(text, text)', 'public.personal_key_list()', 'public.personal_key_get(text[])',
    'public.personal_key_delete(text)', 'public.personal_repo_save(uuid, jsonb, boolean)',
    'public.personal_repo_delete(uuid)', 'public.personal_repo_path_result(uuid, text, boolean, text)'
  ] loop
    if has_function_privilege('anon', v_fn, 'EXECUTE') or has_function_privilege('public', v_fn, 'EXECUTE')
       or not has_function_privilege('authenticated', v_fn, 'EXECUTE') then
      raise exception 'FAIL: wrong execute privileges on %', v_fn;
    end if;
  end loop;
  foreach v_fn in array array['public.guard_personal_repo()', 'public.forget_personal_key_secret()'] loop
    if has_function_privilege('anon', v_fn, 'EXECUTE') or has_function_privilege('authenticated', v_fn, 'EXECUTE')
       or has_function_privilege('public', v_fn, 'EXECUTE') then
      raise exception 'FAIL: trigger function % is executable by clients', v_fn;
    end if;
  end loop;
  if has_table_privilege('anon', 'public.personal_api_keys', 'SELECT') or has_table_privilege('authenticated', 'public.personal_api_keys', 'SELECT')
     or has_table_privilege('authenticated', 'public.personal_api_keys', 'INSERT')
     or has_table_privilege('anon', 'public.personal_repos', 'SELECT')
     or has_table_privilege('authenticated', 'public.personal_repos', 'INSERT')
     or has_table_privilege('authenticated', 'public.personal_repos', 'UPDATE')
     or has_table_privilege('authenticated', 'public.personal_repos', 'DELETE')
     or has_table_privilege('authenticated', 'public.personal_repos', 'TRUNCATE')
     or not has_table_privilege('authenticated', 'public.personal_repos', 'SELECT') then
    raise exception 'FAIL: table privileges on the personal tables are wrong';
  end if;
  select count(*) into v_cnt from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relname in ('personal_api_keys', 'personal_repos') and c.relrowsecurity;
  if v_cnt <> 2 then raise exception 'FAIL: row-level security is not on for both personal tables'; end if;
  raise notice 'PASS: the personal functions and tables are granted to signed-in clients only, and RLS is on';

  -- Removing a member removes their repositories.
  delete from auth.users where id = '12121212-1212-4212-8212-121212121202';
  select count(*) into v_cnt from public.personal_repos where owner_id = '12121212-1212-4212-8212-121212121202';
  if v_cnt <> 0 then raise exception 'FAIL: repos stayed after the member was deleted'; end if;
  select count(*) into v_cnt from vault.decrypted_secrets where name like 'magnm\_personal\_key\_12121212%';
  if v_cnt <> 0 then raise exception 'FAIL: personal keys stayed after the member was deleted'; end if;
  raise notice 'PASS: deleting a member removes their repositories and keys';
end;
$$;

rollback;
