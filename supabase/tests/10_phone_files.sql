-- Privacy and abuse tests for phone_files (full-quality copies of finished draft files, kept 14 days) and the
-- agent_task phone action kind.
begin;
set local client_min_messages = notice;

do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_b uuid := '10101010-1010-4010-8010-101010101002';
  v_admin uuid := '10101010-1010-4010-8010-101010101003';
  v_deact uuid := '10101010-1010-4010-8010-101010101004';
  v_public boolean;
  v_limit bigint;
  v_mimes text[];
  v_count integer;
begin
  -- The bucket is private, capped at 50 MB, and only lists inert media and PDF types.
  select public, file_size_limit, allowed_mime_types into v_public, v_limit, v_mimes
  from storage.buckets where id = 'phone-files';
  if not found then
    raise exception 'FAIL: storage bucket phone-files does not exist';
  end if;
  if v_public is distinct from false then
    raise exception 'FAIL: phone-files bucket must be private';
  end if;
  if v_limit is distinct from 52428800 then
    raise exception 'FAIL: phone-files bucket limit expected 52428800, got %', v_limit;
  end if;
  if v_mimes is null or 'text/html' = any(v_mimes) or 'image/svg+xml' = any(v_mimes)
     or not (v_mimes @> array['image/png', 'image/jpeg', 'video/mp4', 'application/pdf']) then
    raise exception 'FAIL: phone-files allowed_mime_types are wrong: %', v_mimes;
  end if;
  raise notice 'PASS: phone-files bucket is private, 50 MB, inert types only';

  -- Exactly four storage policies for this bucket, none for anon or public, and none for admins.
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_files_%';
  if v_count <> 4 then
    raise exception 'FAIL: expected 4 phone_files storage policies, found %', v_count;
  end if;
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_files_%'
    and ('anon' = any(roles) or 'public' = any(roles));
  if v_count <> 0 then
    raise exception 'FAIL: anon or public appears in phone_files storage policies';
  end if;
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_files_%'
    and (coalesce(qual, '') || coalesce(with_check, '')) like '%is_admin%';
  if v_count <> 0 then
    raise exception 'FAIL: a phone_files storage policy lets admins in';
  end if;
  raise notice 'PASS: four owner-only storage policies, nothing for anon or admins';

  -- New functions are not executable by clients.
  if has_function_privilege('anon', 'public.guard_phone_file()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.guard_phone_file()', 'EXECUTE') then
    raise exception 'FAIL: guard_phone_file() is executable by clients';
  end if;
  if has_function_privilege('anon', 'public.team_storage_used()', 'EXECUTE')
     or has_function_privilege('public', 'public.team_storage_used()', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.team_storage_used()', 'EXECUTE') then
    raise exception 'FAIL: team_storage_used() has the wrong grants';
  end if;
  if has_table_privilege('anon', 'public.phone_files', 'SELECT')
     or has_table_privilege('anon', 'public.phone_files', 'INSERT')
     or has_table_privilege('authenticated', 'public.phone_files', 'TRUNCATE') then
    raise exception 'FAIL: phone_files grants are too wide';
  end if;
  raise notice 'PASS: phone_files functions and grants are narrow';

  insert into public.invites (email, role) values
    ('a.pf@example.com', 'member'), ('b.pf@example.com', 'member'),
    ('admin.pf@example.com', 'admin'), ('deact.pf@example.com', 'member');
  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data) values
    (v_a, 'a.pf@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"A PF"}'::jsonb),
    (v_b, 'b.pf@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"B PF"}'::jsonb),
    (v_admin, 'admin.pf@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin PF"}'::jsonb),
    (v_deact, 'deact.pf@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact PF"}'::jsonb);
  update public.profiles set active = false where id = v_deact;
end;
$$;

-- Member A: inserts, constraints, budget.
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101001', 'role', 'authenticated')::text, true);

do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_b uuid := '10101010-1010-4010-8010-101010101002';
  v_h1 text := repeat('a', 64);
  v_h2 text := repeat('b', 64);
  v_row public.phone_files;
  v_usage jsonb;
  v_case jsonb;
  v_count integer;
begin
  insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  values ('cnt_1', 'outputs/cards/hero.png', 'hero.png', 'image', 'image/png', 120000,
          v_a::text || '/' || v_h1 || '.png', v_h1);
  select * into v_row from public.phone_files where content_id = 'cnt_1';
  if v_row.owner_id is distinct from v_a
     or v_row.expires_at < now() + interval '13 days' or v_row.expires_at > now() + interval '15 days' then
    raise exception 'FAIL: a new phone file is not owned by the caller and expiring in 14 days (%)', v_row.expires_at;
  end if;
  raise notice 'PASS: owner can add a copy; it is owned by the caller and expires in 14 days';

  insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  values ('cnt_1', 'outputs/cards/demo.mp4', 'demo.mp4', 'video', 'video/mp4', 50000000,
          v_a::text || '/' || v_h2 || '.mp4', v_h2),
         ('cnt_2', 'outputs/brief.pdf', 'brief.pdf', 'pdf', 'application/pdf', 52428800,
          v_a::text || '/' || repeat('c', 64) || '.pdf', repeat('c', 64));
  raise notice 'PASS: a file of exactly 50 MB is accepted';

  -- Client cannot choose the owner.
  begin
    insert into public.phone_files (owner_id, content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values (v_b, 'cnt_9', 'a/b.png', 'b.png', 'image', 'image/png', 10, v_b::text || '/' || repeat('d', 64) || '.png', repeat('d', 64));
    raise exception 'FAIL: inserted a row for another owner';
  exception when insufficient_privilege or check_violation then null;
  end;

  -- Every bad shape is refused.
  for v_case in select * from jsonb_array_elements(jsonb_build_array(
      jsonb_build_object('why', 'path in another owners folder', 'path', v_b::text || '/' || repeat('e', 64) || '.png'),
      jsonb_build_object('why', 'path with traversal', 'path', v_a::text || '/../' || v_b::text || '/' || repeat('e', 64) || '.png'),
      jsonb_build_object('why', 'path with a subfolder', 'path', v_a::text || '/x/' || repeat('e', 64) || '.png'),
      jsonb_build_object('why', 'path without owner', 'path', repeat('e', 64) || '.png'),
      jsonb_build_object('why', 'path with a readable name', 'path', v_a::text || '/secret-plan.png'),
      jsonb_build_object('why', 'html extension', 'path', v_a::text || '/' || repeat('e', 64) || '.html'),
      jsonb_build_object('why', 'oversize', 'bytes', 52428801),
      jsonb_build_object('why', 'zero bytes', 'bytes', 0),
      jsonb_build_object('why', 'negative bytes', 'bytes', -5),
      jsonb_build_object('why', 'html type', 'ctype', 'text/html'),
      jsonb_build_object('why', 'svg type', 'ctype', 'image/svg+xml'),
      jsonb_build_object('why', 'kind and type disagree', 'kind', 'video'),
      jsonb_build_object('why', 'unknown kind', 'kind', 'script'),
      jsonb_build_object('why', 'control character in name', 'fname', E'a\nb.png'),
      jsonb_build_object('why', 'slash in name', 'fname', 'a/b.png'),
      jsonb_build_object('why', 'absolute file path', 'fpath', '/etc/passwd'),
      jsonb_build_object('why', 'dotdot file path', 'fpath', 'a/../../b.png'),
      jsonb_build_object('why', 'backslash start', 'fpath', E'\\\\host\\share\\b.png'),
      jsonb_build_object('why', 'empty content id', 'cid', ''),
      jsonb_build_object('why', 'control in content id', 'cid', E'x\ny'),
      jsonb_build_object('why', 'short source hash', 'shash', 'abc')
    )) loop
    begin
      insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
      values (
        coalesce(v_case->>'cid', 'cnt_bad'), coalesce(v_case->>'fpath', 'x/bad.png'), coalesce(v_case->>'fname', 'bad.png'),
        coalesce(v_case->>'kind', 'image'), coalesce(v_case->>'ctype', 'image/png'),
        coalesce((v_case->>'bytes')::bigint, 100),
        coalesce(v_case->>'path', v_a::text || '/' || repeat('e', 64) || '.png'),
        coalesce(v_case->>'shash', repeat('e', 64))
      );
      raise exception 'FAIL: accepted a bad row (%)', v_case->>'why';
    exception when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: bad paths, sizes, types, names and expiries are refused';

  -- One copy per draft file.
  begin
    insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values ('cnt_1', 'outputs/cards/hero.png', 'hero.png', 'image', 'image/png', 5,
            v_a::text || '/' || repeat('f', 64) || '.png', repeat('f', 64));
    raise exception 'FAIL: a second copy of the same draft file was accepted';
  exception when unique_violation then null;
  end;
  raise notice 'PASS: one cloud copy per draft file';

  -- A re-upload may replace the copy, but never move it to another owner or draft.
  update public.phone_files
     set bytes = 130000, source_hash = repeat('9', 64), storage_path = v_a::text || '/' || repeat('9', 64) || '.png'
   where content_id = 'cnt_1' and file_path = 'outputs/cards/hero.png';
  begin
    update public.phone_files set owner_id = v_b where content_id = 'cnt_1';
    raise exception 'FAIL: owner_id was changed';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_files set content_id = 'cnt_other' where content_id = 'cnt_1';
    raise exception 'FAIL: content_id was changed';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_files set expires_at = now() + interval '90 days' where content_id = 'cnt_1';
    raise exception 'FAIL: expiry was extended by a client';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_files set created_at = now() + interval '90 days' where content_id = 'cnt_1';
    raise exception 'FAIL: created_at was changed by a client';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash, expires_at)
    values ('cnt_x', 'a/x.png', 'x.png', 'image', 'image/png', 5, v_a::text || '/' || repeat('7', 64) || '.png', repeat('7', 64),
            now() + interval '30 days');
    raise exception 'FAIL: a client chose its own expiry';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_files set storage_path = v_b::text || '/' || repeat('9', 64) || '.png' where content_id = 'cnt_1';
    raise exception 'FAIL: storage_path was moved to another owner';
  exception when check_violation then null;
  end;
  raise notice 'PASS: a copy can be replaced but not re-owned, moved or kept longer';

  -- The storage meter counts these bytes.
  v_usage := public.storage_usage();
  if (v_usage->>'bytes')::bigint <> 130000 + 50000000 + 52428800
     or (v_usage->'by_kind'->'phone_file'->>'files')::bigint <> 3 then
    raise exception 'FAIL: storage_usage() does not count phone files: %', v_usage;
  end if;
  raise notice 'PASS: storage_usage() counts phone files';

  -- Storage objects: own folder with a hashed name only.
  begin
    -- No copy row for this name (the old file of the replaced copy): nothing may be stored under it.
    insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_a::text || '/' || v_h1 || '.png', v_a);
    raise exception 'FAIL: stored an object that no copy row points at';
  exception when insufficient_privilege or check_violation then null;
  end;
  insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_a::text || '/' || repeat('9', 64) || '.png', v_a);
  for v_case in select * from jsonb_array_elements(jsonb_build_array(
      jsonb_build_object('name', v_b::text || '/' || v_h1 || '.png'),
      jsonb_build_object('name', v_a::text || '/../' || v_b::text || '/' || v_h1 || '.png'),
      jsonb_build_object('name', v_a::text || '/evil.html'),
      jsonb_build_object('name', v_a::text || '/x/' || v_h1 || '.png'),
      jsonb_build_object('name', v_h1 || '.png'),
      jsonb_build_object('name', v_a::text || '/' || v_h1 || '.svg')
    )) loop
      begin
        insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_case->>'name', v_a);
        raise exception 'FAIL: stored an object named %', v_case->>'name';
      exception when insufficient_privilege or check_violation then null;
      end;
  end loop;
  raise notice 'PASS: storage accepts only <own id>/<hash>.<ext> objects that an unexpired copy row points at';

  -- 1 GB budget: previews count too.
  insert into public.media_previews (local_source_id, source_kind, kind, storage_path, content_type, bytes)
  values ('asset:big', 'asset', 'video_preview', v_a::text || '/video_preview/big.mp4', 'video/mp4', 1073741824 - 102558800 - 100);
  begin
    insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values ('cnt_3', 'o/x.png', 'x.png', 'image', 'image/png', 200,
            v_a::text || '/' || repeat('1', 64) || '.png', repeat('1', 64));
    raise exception 'FAIL: a copy over the 1 GB budget was accepted';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    if sqlerrm not like '%budget%' then
      raise exception 'FAIL: wrong error for the budget: %', sqlerrm;
    end if;
  end;
  insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  values ('cnt_3', 'o/x.png', 'x.png', 'image', 'image/png', 100,
          v_a::text || '/' || repeat('1', 64) || '.png', repeat('1', 64));
  raise notice 'PASS: the 1 GB budget (previews and phone files together) is enforced by the database';

  -- The owner can remove their copies.
  delete from public.phone_files where content_id = 'cnt_3';
  select count(*) into v_count from public.phone_files;
  if v_count <> 3 then
    raise exception 'FAIL: owner delete failed, rows: %', v_count;
  end if;
  raise notice 'PASS: owner can delete their own copy rows';
end;
$$;

-- The server restarts the 14 days only when a new file is stored, never for other edits.
reset role;
update public.phone_files set expires_at = now() - interval '1 day' where content_id = 'cnt_2';
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101001', 'role', 'authenticated')::text, true);

do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_exp timestamptz;
begin
  update public.phone_files set file_name = 'brief-renamed.pdf' where content_id = 'cnt_2';
  select expires_at into v_exp from public.phone_files where content_id = 'cnt_2';
  if v_exp > now() then
    raise exception 'FAIL: a rename restarted the 14 days';
  end if;
  update public.phone_files
     set storage_path = v_a::text || '/' || repeat('8', 64) || '.pdf', source_hash = repeat('8', 64)
   where content_id = 'cnt_2';
  select expires_at into v_exp from public.phone_files where content_id = 'cnt_2';
  if v_exp < now() + interval '13 days' or v_exp > now() + interval '15 days' then
    raise exception 'FAIL: a newly stored file did not get 14 days (%)', v_exp;
  end if;
  raise notice 'PASS: the server restarts the 14 days only for a newly stored file';
end;
$$;

-- Storage reads need an existing, unexpired copy row of the caller: expiry is enforced by the server.
do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_n integer;
  v_path text := '10101010-1010-4010-8010-101010101001/' || repeat('8', 64) || '.pdf';
begin
  insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_path, v_a);
  select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_path;
  if v_n <> 1 then
    raise exception 'FAIL: the owner cannot read an object of an unexpired copy';
  end if;
  begin
    update public.phone_files set removing = true where content_id = 'cnt_2';
    raise exception 'FAIL: an unexpired copy was marked for removal';
  exception when check_violation then null;
  end;
  -- A copy row that is deleted takes its object out of reach (the object itself is still stored).
  insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  values ('cnt_9', 'o/n.png', 'n.png', 'image', 'image/png', 10, v_a::text || '/' || repeat('6', 64) || '.png', repeat('6', 64));
  insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_a::text || '/' || repeat('6', 64) || '.png', v_a);
  delete from public.phone_files where content_id = 'cnt_9';
  select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_a::text || '/' || repeat('6', 64) || '.png';
  if v_n <> 0 then
    raise exception 'FAIL: an object stays readable after its copy row was deleted';
  end if;
  raise notice 'PASS: reads need an unexpired copy row; a deleted row takes the object out of reach';
end;
$$;

-- Expire cnt_2 (the server clock moved on).
reset role;
update public.phone_files set expires_at = now() - interval '1 minute' where content_id = 'cnt_2';
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101001', 'role', 'authenticated')::text, true);

do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_n integer;
  v_path text := '10101010-1010-4010-8010-101010101001/' || repeat('8', 64) || '.pdf';
  v_limit bigint := 1073741824;
  v_other bigint;
begin
  -- Expired: the object cannot be read (so no link can be signed) and nothing new can be stored under it.
  select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_path;
  if v_n <> 0 then
    raise exception 'FAIL: an object of an expired copy is still readable';
  end if;
  begin
    insert into storage.objects (bucket_id, name, owner) values ('phone-files', v_a::text || '/' || repeat('5', 64) || '.pdf', v_a);
    raise exception 'FAIL: stored an object without a copy row';
  exception when insufficient_privilege then null;
  end;
  -- Deleting needs to see the object, so the laptop first marks an expired copy for removal; then the object is
  -- visible (and deletable) and the row can go too.
  update public.phone_files set removing = true where content_id = 'cnt_2';
  select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_path;
  if v_n <> 1 then
    raise exception 'FAIL: an expired copy marked for removal cannot be seen for deletion';
  end if;
  begin
    delete from storage.objects where bucket_id = 'phone-files' and name = v_path;
    select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_path;
    if v_n <> 0 then
      raise exception 'FAIL: the expired object was not deleted';
    end if;
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    -- Real Supabase only deletes objects through the Storage API (storage.protect_delete): check the policy instead.
    if not exists (
      select 1 from pg_policies
      where schemaname = 'storage' and tablename = 'objects' and policyname = 'phone_files_delete_own'
        and cmd = 'DELETE' and roles = '{authenticated}'
    ) then
      raise exception 'FAIL: no owner delete policy for phone-files objects';
    end if;
  end;
  delete from public.phone_files where content_id = 'cnt_2';
  select count(*) into v_n from storage.objects where bucket_id = 'phone-files' and name = v_path;
  if v_n <> 0 then
    raise exception 'FAIL: an object stays reachable after its copy row is gone';
  end if;
  raise notice 'PASS: an expired copy cannot be read; the laptop marks it, deletes the object, then the row';

  -- Leave the owner exactly 100 bytes under the whole-project budget, for the next member.
  select coalesce(sum(bytes), 0) into v_other from public.phone_files;
  update public.media_previews set bytes = v_limit - 100 - v_other where local_source_id = 'asset:big';
end;
$$;

-- Member B: sees and touches nothing of A's.
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101002', 'role', 'authenticated')::text, true);

do $$
declare
  v_a uuid := '10101010-1010-4010-8010-101010101001';
  v_count integer;
begin
  select count(*) into v_count from public.phone_files;
  if v_count <> 0 then
    raise exception 'FAIL: member B reads % of member A phone_files rows', v_count;
  end if;
  update public.phone_files set bytes = 1;
  delete from public.phone_files;
  select count(*) into v_count from storage.objects where bucket_id = 'phone-files';
  if v_count <> 0 then
    raise exception 'FAIL: member B reads member A phone-files objects';
  end if;
  -- The budget is one pool for the whole project: member A already uses all but 100 bytes of it.
  if public.team_storage_used() <> 1073741824 - 100 then
    raise exception 'FAIL: team_storage_used() is not the total of everyone: %', public.team_storage_used();
  end if;
  begin
    insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values ('cnt_b', 'o/b.png', 'b.png', 'image', 'image/png', 200,
            '10101010-1010-4010-8010-101010101002/' || repeat('4', 64) || '.png', repeat('4', 64));
    raise exception 'FAIL: two members together went over the 1 GB project budget';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;
    if sqlerrm not like '%budget%' then raise exception 'FAIL: wrong error: %', sqlerrm; end if;
  end;
  insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
  values ('cnt_b', 'o/b.png', 'b.png', 'image', 'image/png', 100,
          '10101010-1010-4010-8010-101010101002/' || repeat('4', 64) || '.png', repeat('4', 64));
  delete from public.phone_files where content_id = 'cnt_b';
  raise notice 'PASS: the 1 GB budget is shared by all members';
  begin
    delete from storage.objects where bucket_id = 'phone-files';
  exception when others then
    if sqlerrm like 'FAIL:%' then raise; end if;  -- real Supabase blocks direct deletes for everyone
  end;
  update storage.objects set name = name where bucket_id = 'phone-files';
  raise notice 'PASS: member B cannot read, change or delete member A copies or objects';
end;
$$;

-- Admin: no access to other members' copies, rows or objects.
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101003', 'role', 'authenticated')::text, true);

do $$
declare
  v_count integer;
begin
  select count(*) into v_count from public.phone_files;
  if v_count <> 0 then
    raise exception 'FAIL: an admin reads other members phone_files rows (%)', v_count;
  end if;
  select count(*) into v_count from storage.objects where bucket_id = 'phone-files';
  if v_count <> 0 then
    raise exception 'FAIL: an admin reads phone-files objects';
  end if;
  raise notice 'PASS: admins cannot read other members full-quality copies';
end;
$$;

-- Deactivated member: nothing.
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101004', 'role', 'authenticated')::text, true);

do $$
begin
  begin
    insert into public.phone_files (content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values ('cnt_d', 'a/b.png', 'b.png', 'image', 'image/png', 10,
            '10101010-1010-4010-8010-101010101004/' || repeat('d', 64) || '.png', repeat('d', 64));
    raise exception 'FAIL: a deactivated member added a copy';
  exception when insufficient_privilege or check_violation then null;
  end;
  begin
    insert into storage.objects (bucket_id, name, owner)
    values ('phone-files', '10101010-1010-4010-8010-101010101004/' || repeat('d', 64) || '.png', '10101010-1010-4010-8010-101010101004');
    raise exception 'FAIL: a deactivated member stored an object';
  exception when insufficient_privilege or check_violation then null;
  end;
  raise notice 'PASS: a deactivated member cannot add copies or objects';
end;
$$;

-- Anon: nothing.
reset role;
set local role anon;

do $$
begin
  begin
    perform count(*) from public.phone_files;
    raise exception 'FAIL: anon selected from phone_files';
  exception when insufficient_privilege then null;
  end;
  begin
    -- Supabase grants anon select on storage.objects and relies on policies; either way nothing may come back.
    if (select count(*) from storage.objects where bucket_id = 'phone-files') <> 0 then
      raise exception 'FAIL: anon reads phone-files objects';
    end if;
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.team_storage_used();
    raise exception 'FAIL: anon executed team_storage_used()';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon has no access to phone_files';
end;
$$;

-- agent_task phone action kind.
reset role;
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101001', 'role', 'authenticated')::text, true);

do $$
declare
  v_row public.phone_actions;
  v_case jsonb;
begin
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000001', 'agent_task', 'writer', '{"message":"Write a LinkedIn post about our week."}'::jsonb);
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000002', 'agent_task', 'video-producer', jsonb_build_object('message', repeat('x', 4000)));
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000003', 'agent_task', 'cmo', jsonb_build_object('message', repeat(chr(2325), 4000)));
  raise notice 'PASS: agent_task is accepted, including 4000 long non-latin characters';

  -- A hostile or malformed task never reaches the table.
  for v_case in select * from jsonb_array_elements('[
      {"target":"writer","payload":{}},
      {"target":"writer","payload":{"message":""}},
      {"target":"writer","payload":{"message":"   "}},
      {"target":"writer","payload":{"message":5}},
      {"target":"writer","payload":{"message":["hi"]}},
      {"target":"writer","payload":{"message":"hi","tools":["delegate"]}},
      {"target":"writer","payload":{"message":"hi","agent":"cmo"}},
      {"target":"writer","payload":"hi"},
      {"target":"writer","payload":["hi"]},
      {"target":"Writer","payload":{"message":"hi"}},
      {"target":"../writer","payload":{"message":"hi"}},
      {"target":"writer/../cmo","payload":{"message":"hi"}},
      {"target":"wri ter","payload":{"message":"hi"}},
      {"target":"-writer","payload":{"message":"hi"}},
      {"target":"writer;drop","payload":{"message":"hi"}},
      {"target":"","payload":{"message":"hi"}}
    ]'::jsonb) loop
    begin
      insert into public.phone_actions (id, kind, target_id, payload)
      values (gen_random_uuid(), 'agent_task', v_case->>'target', v_case->'payload');
      raise exception 'FAIL: accepted a bad agent_task %', v_case;
    exception when check_violation then null;
    end;
  end loop;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'agent_task', 'writer', jsonb_build_object('message', repeat('x', 4001)));
    raise exception 'FAIL: accepted a 4001 character message';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'agent_task', 'writer', jsonb_build_object('message', E'hi\u0001there'));
    raise exception 'FAIL: accepted a control character';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'agent_task', 'writer', jsonb_build_object('message', E'hi\x7fthere'));
    raise exception 'FAIL: accepted a delete character';
  exception when check_violation then null;
  end;
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000004', 'agent_task', 'writer', jsonb_build_object('message', E'two\nlines\tand tab'));
  raise notice 'PASS: malformed or hostile agent_task rows are refused';

  -- The other kinds still behave as before.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('bbbbbbbb-0000-4000-8000-000000000010', 'draft_comment', 'cnt_1', '{"body":"ok"}'::jsonb);
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'draft_comment', 'cnt_1', '{"message":"hi"}'::jsonb);
    raise exception 'FAIL: a draft_comment took an agent_task payload';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'run_agent', 'writer', '{"message":"hi"}'::jsonb);
    raise exception 'FAIL: an unknown kind was accepted';
  exception when check_violation then null;
  end;
  raise notice 'PASS: the other kinds are unchanged';

  -- The laptop claims and finishes an agent_task like any other action; the result stays short.
  select * into v_row from public.claim_phone_action('bbbbbbbb-0000-4000-8000-000000000001', 'dev_1');
  if v_row.status <> 'applying' then
    raise exception 'FAIL: could not claim an agent_task';
  end if;
  select * into v_row from public.finish_phone_action('bbbbbbbb-0000-4000-8000-000000000001', 'dev_1', 'applied', 'Replied. session:abc');
  if v_row.status <> 'applied' then
    raise exception 'FAIL: could not finish an agent_task';
  end if;
  raise notice 'PASS: agent_task can be claimed and finished';
end;
$$;

-- Another member never sees it; an admin reads but cannot change it.
select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101002', 'role', 'authenticated')::text, true);

do $$
begin
  if (select count(*) from public.phone_actions where kind = 'agent_task') <> 0 then
    raise exception 'FAIL: member B reads member A agent_task rows';
  end if;
  begin
    perform public.claim_phone_action('bbbbbbbb-0000-4000-8000-000000000002', 'dev_b');
  exception when others then null;
  end;
  raise notice 'PASS: member B cannot see or claim member A tasks';
end;
$$;

select set_config('request.jwt.claims',
  json_build_object('sub', '10101010-1010-4010-8010-101010101003', 'role', 'authenticated')::text, true);

do $$
begin
  if (select count(*) from public.phone_actions where kind = 'agent_task') < 4 then
    raise exception 'FAIL: admin could not read agent_task rows';
  end if;
  begin
    update public.phone_actions set status = 'rejected' where kind = 'agent_task';
  exception when insufficient_privilege then null;
  end;
  if (select count(*) from public.phone_actions where kind = 'agent_task' and status = 'rejected') <> 0 then
    raise exception 'FAIL: admin changed agent_task rows';
  end if;
  raise notice 'PASS: admin reads agent_task rows and changes none';
end;
$$;

reset role;
rollback;
