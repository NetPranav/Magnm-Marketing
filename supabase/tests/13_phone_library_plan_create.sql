-- Library, Plan and Create on the phone: phone_uploads (private bucket and table), the shared 1 GB budget, the
-- asset / schedule / editorial_plan mirror kinds and the four new phone action kinds (library_upload,
-- schedule_update, draft_create, workflow_request).
-- Written to pass on a database that already holds real rows: it measures what is there, only looks at its own test
-- identities, never deletes from storage tables and rolls everything back.
begin;
set local client_min_messages = notice;

do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_b uuid := '13131313-1313-4313-8313-131313131302';
  v_admin uuid := '13131313-1313-4313-8313-131313131303';
  v_deact uuid := '13131313-1313-4313-8313-131313131304';
  v_public boolean;
  v_limit bigint;
  v_mimes text[];
  v_count integer;
begin
  insert into public.invites (email, role)
  values
    ('a.lpc@example.com', 'member'),
    ('b.lpc@example.com', 'member'),
    ('admin.lpc@example.com', 'admin'),
    ('deact.lpc@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_a, 'a.lpc@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"A LPC"}'::jsonb),
    (v_b, 'b.lpc@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"B LPC"}'::jsonb),
    (v_admin, 'admin.lpc@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin LPC"}'::jsonb),
    (v_deact, 'deact.lpc@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deact LPC"}'::jsonb);

  update public.profiles set active = false where id = v_deact;

  -- 1. The bucket is private, capped at 50 MB, and only lists inert media and PDF types.
  select public, file_size_limit, allowed_mime_types into v_public, v_limit, v_mimes
  from storage.buckets where id = 'phone-uploads';
  if not found then raise exception 'FAIL: storage bucket phone-uploads does not exist'; end if;
  if v_public is distinct from false then raise exception 'FAIL: phone-uploads bucket must be private'; end if;
  if v_limit is distinct from 52428800 then raise exception 'FAIL: phone-uploads limit expected 52428800, got %', v_limit; end if;
  if v_mimes is null or 'text/html' = any(v_mimes) or 'image/svg+xml' = any(v_mimes)
     or 'application/x-msdownload' = any(v_mimes) or 'application/zip' = any(v_mimes)
     or not (v_mimes @> array['image/png', 'image/jpeg', 'video/mp4', 'application/pdf']) then
    raise exception 'FAIL: phone-uploads allowed_mime_types are wrong: %', v_mimes;
  end if;
  raise notice 'PASS: phone-uploads bucket is private, 50 MB, inert types only';

  -- Exactly four storage policies for this bucket, none for anon or public, none for admins.
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_uploads_%';
  if v_count <> 4 then raise exception 'FAIL: expected 4 phone_uploads storage policies, found %', v_count; end if;
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_uploads_%'
    and ('anon' = any(roles) or 'public' = any(roles));
  if v_count <> 0 then raise exception 'FAIL: anon or public appears in phone_uploads storage policies'; end if;
  select count(*) into v_count from pg_policies
  where schemaname = 'storage' and tablename = 'objects' and policyname like 'phone_uploads_%'
    and (coalesce(qual, '') || coalesce(with_check, '')) like '%is_admin%';
  if v_count <> 0 then raise exception 'FAIL: a phone_uploads storage policy lets admins in'; end if;
  select count(*) into v_count from pg_policies
  where schemaname = 'public' and tablename = 'phone_uploads'
    and (coalesce(qual, '') || coalesce(with_check, '')) like '%is_admin%';
  if v_count <> 0 then raise exception 'FAIL: a phone_uploads table policy lets admins in'; end if;
  raise notice 'PASS: four owner-only storage policies, nothing for anon or admins';

  -- Functions: the guard is not executable by clients, the payload checker is for signed-in clients only.
  if has_function_privilege('anon', 'public.guard_phone_upload()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.guard_phone_upload()', 'EXECUTE') then
    raise exception 'FAIL: guard_phone_upload() is executable by clients';
  end if;
  if has_function_privilege('anon', 'public.phone_workflow_inputs_ok(text, jsonb)', 'EXECUTE') then
    raise exception 'FAIL: anon can execute phone_workflow_inputs_ok';
  end if;
  if not has_function_privilege('authenticated', 'public.phone_workflow_inputs_ok(text, jsonb)', 'EXECUTE') then
    raise exception 'FAIL: signed-in clients cannot execute phone_workflow_inputs_ok (their inserts would fail)';
  end if;
  if has_function_privilege('anon', 'public.team_storage_used()', 'EXECUTE')
     or has_function_privilege('anon', 'public.storage_usage()', 'EXECUTE')
     or has_function_privilege('anon', 'public.guard_phone_file()', 'EXECUTE') then
    raise exception 'FAIL: the replaced storage functions became executable by anon';
  end if;
  raise notice 'PASS: new and replaced functions are not open to anon (the guard is not open to clients)';

  -- Client privileges on phone_uploads: no anon at all, no update except removing, no timestamps or owner on insert.
  if has_table_privilege('anon', 'public.phone_uploads', 'SELECT')
     or has_table_privilege('authenticated', 'public.phone_uploads', 'UPDATE')
     or has_table_privilege('authenticated', 'public.phone_uploads', 'TRUNCATE') then
    raise exception 'FAIL: table privileges on phone_uploads are too wide';
  end if;
  if has_column_privilege('authenticated', 'public.phone_uploads', 'owner_id', 'INSERT')
     or has_column_privilege('authenticated', 'public.phone_uploads', 'expires_at', 'INSERT')
     or has_column_privilege('authenticated', 'public.phone_uploads', 'created_at', 'INSERT')
     or has_column_privilege('authenticated', 'public.phone_uploads', 'removing', 'INSERT')
     or has_column_privilege('authenticated', 'public.phone_uploads', 'bytes', 'UPDATE')
     or has_column_privilege('authenticated', 'public.phone_uploads', 'storage_path', 'UPDATE')
     or not has_column_privilege('authenticated', 'public.phone_uploads', 'removing', 'UPDATE') then
    raise exception 'FAIL: column privileges on phone_uploads are wrong';
  end if;
  raise notice 'PASS: clients cannot set owner, dates or removing on insert and may only mark removing on update';
end;
$$;

-- 2. Member A uploads.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '13131313-1313-4313-8313-131313131301', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_b uuid := '13131313-1313-4313-8313-131313131302';
  v_row public.phone_uploads;
  v_n int;
  v_case jsonb;
  v_ok_path text := '13131313-1313-4313-8313-131313131301/' || repeat('a', 64) || '.png';
begin
  insert into public.phone_uploads (id, file_name, kind, content_type, bytes, storage_path)
  values ('dddddddd-0000-4000-8000-000000000001', 'launch photo.png', 'image', 'image/png', 1000, v_ok_path);
  select * into v_row from public.phone_uploads where id = 'dddddddd-0000-4000-8000-000000000001';
  if v_row.owner_id is distinct from v_a
     or v_row.expires_at < now() + interval '13 days' or v_row.expires_at > now() + interval '15 days'
     or v_row.removing then
    raise exception 'FAIL: a new upload is not owned by the caller, expiring in 14 days and not removing';
  end if;
  insert into public.phone_uploads (id, file_name, kind, content_type, bytes, storage_path) values
    ('dddddddd-0000-4000-8000-000000000002', 'clip.mp4', 'video', 'video/mp4', 5000000, v_a::text || '/' || repeat('b', 64) || '.mp4'),
    ('dddddddd-0000-4000-8000-000000000003', 'brief.pdf', 'pdf', 'application/pdf', 2000, v_a::text || '/' || repeat('c', 64) || '.pdf');
  raise notice 'PASS: owner can add an image, a video and a PDF; the server sets owner and expiry';

  -- Hostile inserts: every one is refused by a check, a privilege or a policy.
  for v_case in select * from jsonb_array_elements($j$[
    {"n":"a.png/../x","k":"image","c":"image/png","b":10,"p":"OWN/../../../etc.png"},
    {"n":"x.png","k":"image","c":"image/png","b":10,"p":"OWN/short.png"},
    {"n":"x.png","k":"image","c":"image/png","b":10,"p":"OWN/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.png"},
    {"n":"x.svg","k":"image","c":"image/svg+xml","b":10,"p":"OWN/HEX64.svg"},
    {"n":"x.html","k":"image","c":"text/html","b":10,"p":"OWN/HEX64.html"},
    {"n":"x.exe","k":"pdf","c":"application/x-msdownload","b":10,"p":"OWN/HEX64.exe"},
    {"n":"x.zip","k":"pdf","c":"application/zip","b":10,"p":"OWN/HEX64.zip"},
    {"n":"x.png","k":"pdf","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"x.png","k":"video","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":0,"p":"OWN/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":52428801,"p":"OWN/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":-5,"p":"OWN/HEX64.png"},
    {"n":"","k":"image","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"../../etc/passwd","k":"image","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"a\\b.png","k":"image","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"line\nbreak.png","k":"image","c":"image/png","b":10,"p":"OWN/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":10,"p":"OTHER/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":10,"p":"/OWN/HEX64.png"},
    {"n":"x.png","k":"image","c":"image/png","b":10,"p":"OWN//HEX64.png"}
  ]$j$::jsonb) loop
    begin
      insert into public.phone_uploads (file_name, kind, content_type, bytes, storage_path)
      values (
        v_case->>'n', v_case->>'k', v_case->>'c', (v_case->>'b')::bigint,
        replace(replace(replace(v_case->>'p', 'OWN', v_a::text), 'OTHER', v_b::text), 'HEX64', repeat('d', 64))
      );
      raise exception 'FAIL: accepted a hostile upload %', v_case;
    exception when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: path tricks, wrong types, odd sizes, odd names and another user''s folder are refused';

  -- The same object path cannot be reused by a second row.
  begin
    insert into public.phone_uploads (file_name, kind, content_type, bytes, storage_path)
    values ('again.png', 'image', 'image/png', 10, v_ok_path);
    raise exception 'FAIL: a second row reused an object path';
  exception when unique_violation then null;
  end;

  -- Owner, dates and removing are not the client's to choose.
  begin
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_b, 'x.png', 'image', 'image/png', 10, v_b::text || '/' || repeat('e', 64) || '.png');
    raise exception 'FAIL: a client chose the owner of an upload';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_uploads (file_name, kind, content_type, bytes, storage_path, expires_at)
    values ('x.png', 'image', 'image/png', 10, v_a::text || '/' || repeat('e', 64) || '.png', now() + interval '3 years');
    raise exception 'FAIL: a client chose the expiry of an upload';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_uploads (file_name, kind, content_type, bytes, storage_path, removing)
    values ('x.png', 'image', 'image/png', 10, v_a::text || '/' || repeat('e', 64) || '.png', true);
    raise exception 'FAIL: a client inserted a row already marked removing';
  exception when insufficient_privilege then null;
  end;
  -- Nothing can be edited: not the size, not the path, not the owner.
  begin
    update public.phone_uploads set bytes = 1 where id = 'dddddddd-0000-4000-8000-000000000002';
    raise exception 'FAIL: a client edited the size of an upload';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_uploads set owner_id = v_b where id = 'dddddddd-0000-4000-8000-000000000002';
    raise exception 'FAIL: a client moved an upload to another owner';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.phone_uploads set expires_at = now() + interval '9 years' where id = 'dddddddd-0000-4000-8000-000000000002';
    raise exception 'FAIL: a client lengthened an upload';
  exception when insufficient_privilege then null;
  end;
  -- Removing may only be set on an expired upload.
  begin
    update public.phone_uploads set removing = true where id = 'dddddddd-0000-4000-8000-000000000002';
    raise exception 'FAIL: an unexpired upload was marked removing';
  exception when check_violation then null;
  end;
  raise notice 'PASS: owner, dates and removing cannot be chosen; nothing can be edited; only expired rows can be removing';

  -- The owner can delete their own row.
  delete from public.phone_uploads where id = 'dddddddd-0000-4000-8000-000000000003';
  get diagnostics v_n = row_count;
  if v_n <> 1 then raise exception 'FAIL: the owner could not delete their own upload'; end if;
  raise notice 'PASS: the owner can delete an upload row';
end;
$$;

-- 3. The limit of 100 waiting uploads and the shared 1 GB budget (A's own rows only; rolled back at the end).
reset role;
do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_i int;
  v_used bigint;
  v_room bigint;
  v_before jsonb;
  v_after jsonb;
begin
  -- 100 waiting uploads per owner (A already has 2; fill up to 100 with 1-byte rows).
  for v_i in 1..98 loop
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_a, 'f' || v_i || '.png', 'image', 'image/png', 1, v_a::text || '/' || lpad(to_hex(v_i), 64, '0') || '.png');
  end loop;
  begin
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_a, 'one-too-many.png', 'image', 'image/png', 1, v_a::text || '/' || repeat('9', 64) || '.png');
    raise exception 'FAIL: a 101st waiting upload was accepted';
  exception when raise_exception then
    if sqlerrm not like '%Too many phone uploads%' then raise; end if;
  end;
  delete from public.phone_uploads where owner_id = v_a and file_name like 'f%.png';
  raise notice 'PASS: at most 100 waiting uploads per owner';

  -- The budget is one pool: previews, copies and uploads are summed. Fill to 5 MB below the limit, then 6 MB fails.
  v_before := public.storage_usage();
  select coalesce((select sum(bytes) from public.phone_files), 0)
       + coalesce((select sum(bytes) from public.phone_uploads), 0)
       + coalesce((select sum(bytes) from public.media_previews where deleted_at is null), 0)
    into v_used;
  v_room := 1073741824 - v_used - 5 * 1024 * 1024;
  v_i := 0;
  while v_room > 0 loop
    v_i := v_i + 1;
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_a, 'fill' || v_i || '.mp4', 'video', 'video/mp4', least(v_room, 52428800), v_a::text || '/' || lpad(to_hex(1000 + v_i), 64, '0') || '.mp4');
    v_room := v_room - least(v_room, 52428800);
    exit when v_i >= 90;
  end loop;
  begin
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_a, 'over.png', 'image', 'image/png', 6 * 1024 * 1024, v_a::text || '/' || repeat('8', 64) || '.png');
    raise exception 'FAIL: an upload past the shared 1 GB budget was accepted';
  exception when raise_exception then
    if sqlerrm not like '%budget%' then raise; end if;
  end;
  insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
  values (v_a, 'fits.png', 'image', 'image/png', 4 * 1024 * 1024, v_a::text || '/' || repeat('7', 64) || '.png');
  raise notice 'PASS: uploads obey the shared 1 GB budget';

  -- Get on phone copies count against the same pool (the replaced guard sums uploads too).
  begin
    insert into public.phone_files (owner_id, content_id, file_path, file_name, kind, content_type, bytes, storage_path, source_hash)
    values (v_a, 'cnt_budget', 'outputs/x.png', 'x.png', 'image', 'image/png', 3 * 1024 * 1024,
            v_a::text || '/' || repeat('6', 64) || '.png', repeat('0', 64));
    raise exception 'FAIL: a Get on phone copy ignored the uploads in the shared budget';
  exception when raise_exception then
    if sqlerrm not like '%budget%' then raise; end if;
  end;
  raise notice 'PASS: Get on phone copies see the uploads in the shared budget';

  -- The storage meter counts uploads as their own kind, and the team total includes them.
  v_after := public.storage_usage();
  if coalesce((v_after #>> '{by_kind,phone_upload,files}')::int, 0) < 1 then
    raise exception 'FAIL: storage_usage does not list phone_upload';
  end if;
  if (v_after ->> 'bytes')::bigint <= (v_before ->> 'bytes')::bigint then
    raise exception 'FAIL: storage_usage did not grow with the uploads';
  end if;
  raise notice 'PASS: the storage meter counts uploads';
  delete from public.phone_uploads where owner_id = v_a and (file_name like 'fill%' or file_name = 'fits.png');
end;
$$;

-- 4. Phone actions of the new kinds (member A).
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '13131313-1313-4313-8313-131313131301', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_case jsonb;
  v_n int;
begin
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('eeeeeeee-0000-4000-8000-000000000001', 'library_upload', 'dddddddd-0000-4000-8000-000000000001', '{"category":"brand"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000002', 'library_upload', 'dddddddd-0000-4000-8000-000000000002', '{"category":"recording","description":"Demo clip for the launch"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000003', 'schedule_update', 'market_scan', '{"enabled":false}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000004', 'schedule_update', 'market_scan', '{"interval_hours":12}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000005', 'schedule_update', 'cmo_daily', '{"time":"08:30","day":"*","enabled":true}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000006', 'schedule_update', 'weekly_summary', '{"day":"1"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000007', 'draft_create', 'new', '{"platform":"linkedin","title":"Why we built it","body":"A short post.\n\nSecond paragraph.","mode":"post"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000008', 'draft_create', 'new', '{"platform":"x","title":"Thread","body":"1/ Hello\n2/ World"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000009', 'workflow_request', 'post', '{"brief":"Announce the release","platforms":["linkedin","x"],"with_image":true,"repo_id":"cero-web"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-00000000000a', 'workflow_request', 'blog', '{"topic":"How the phone and laptop share work"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-00000000000b', 'workflow_request', 'video_script', '{"topic":"Launch teaser","duration_s":30,"aspect":"9:16"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-00000000000c', 'workflow_request', 'repurpose', '{"content_id":"cnt_20261120_abc123","targets":["x_thread","linkedin"]}'::jsonb),
    ('eeeeeeee-0000-4000-8000-00000000000d', 'workflow_request', 'weekly_plan', '{"platforms":["linkedin"],"posts_per_week":3,"week_start":"2026-11-30"}'::jsonb);
  raise notice 'PASS: owner can queue every new kind with its exact shape';

  -- The older kinds are untouched (a comment may still contain angle brackets).
  insert into public.phone_actions (id, kind, target_id, payload) values
    ('eeeeeeee-0000-4000-8000-000000000010', 'draft_comment', 'cnt_1', '{"body":"make it < 100 words","version":2}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000011', 'draft_posted', 'cnt_1', '{"url":"https://example.com/p/1"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000012', 'reminder_done', 'rem_1', '{}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000013', 'agent_task', 'writer', '{"message":"Draft a post"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000014', 'pitch_create', 'new', '{"kind":"proposal","client_name":"Meera"}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000015', 'pitch_update', 'prop_1', '{"base_version":"v1","fields":{"notes":"x"}}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000016', 'pitch_finalize', 'prop_1', '{}'::jsonb),
    ('eeeeeeee-0000-4000-8000-000000000017', 'pitch_link', 'prop_1', '{"action":"publish"}'::jsonb);
  raise notice 'PASS: all eight older kinds still accept their exact shapes';

  -- Hostile or malformed payloads are refused by the database itself.
  for v_case in select * from jsonb_array_elements($j$[
    {"kind":"library_upload","target":"not-a-uuid","payload":{"category":"brand"}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{"category":"weapons"}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{"category":"brand","path":"/etc/passwd"}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{"category":"brand","storage_path":"x/y.png"}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{"category":"brand","description":"line\nbreak"}},
    {"kind":"library_upload","target":"dddddddd-0000-4000-8000-000000000001","payload":{"category":7}},
    {"kind":"library_upload","target":"../dddddddd-0000-4000-8000-000000000001","payload":{"category":"brand"}},
    {"kind":"library_upload","target":"DDDDDDDD-0000-4000-8000-000000000001","payload":{"category":"brand"}},
    {"kind":"schedule_update","target":"market_scan","payload":{}},
    {"kind":"schedule_update","target":"market_scan","payload":{"enabled":"yes"}},
    {"kind":"schedule_update","target":"market_scan","payload":{"enabled":1}},
    {"kind":"schedule_update","target":"market_scan","payload":{"interval_hours":0}},
    {"kind":"schedule_update","target":"market_scan","payload":{"interval_hours":721}},
    {"kind":"schedule_update","target":"market_scan","payload":{"interval_hours":2.5}},
    {"kind":"schedule_update","target":"market_scan","payload":{"interval_hours":"6"}},
    {"kind":"schedule_update","target":"market_scan","payload":{"interval_hours":-3}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"time":"25:00"}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"time":"8:30"}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"time":"08:30; rm -rf"}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"day":"7"}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"day":"**"}},
    {"kind":"schedule_update","target":"cmo_daily","payload":{"day":1}},
    {"kind":"schedule_update","target":"market_scan","payload":{"command":"rm -rf /"}},
    {"kind":"schedule_update","target":"market_scan","payload":{"enabled":true,"cron":"* * * * *"}},
    {"kind":"schedule_update","target":"market_scan","payload":{"id":"other"}},
    {"kind":"schedule_update","target":"../market_scan","payload":{"enabled":true}},
    {"kind":"schedule_update","target":"Market Scan","payload":{"enabled":true}},
    {"kind":"schedule_update","target":"market_scan","payload":[true]},
    {"kind":"draft_create","target":"cnt_existing","payload":{"platform":"x","title":"t","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"title":"t","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"tiktok","title":"t","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"   ","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"   "}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"line\nbreak","body":"b"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"bad\u0007bell"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"b","mode":"publish"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"b","status":"posted"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"b","content_id":"cnt_other"}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":"t","body":"b","files":["/etc/passwd"]}},
    {"kind":"draft_create","target":"new","payload":{"platform":"x","title":7,"body":"b"}},
    {"kind":"workflow_request","target":"research","payload":{"topic":"x"}},
    {"kind":"workflow_request","target":"../post","payload":{"brief":"x"}},
    {"kind":"workflow_request","target":"post","payload":{}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"   "}},
    {"kind":"workflow_request","target":"post","payload":{"brief":7}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","platforms":[]}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","platforms":["tiktok"]}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","platforms":"linkedin"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","platforms":["x","x","x","x","x"]}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","with_image":"yes"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","repo_id":"../etc"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","repo_id":"UPPER"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","repo_id":"/srv/me/code"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","content_id":"cnt_1"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","product_id":"hidden-product"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"x","actor":"admin"}},
    {"kind":"workflow_request","target":"post","payload":{"brief":"bad\u0001ctrl"}},
    {"kind":"workflow_request","target":"blog","payload":{"brief":"x"}},
    {"kind":"workflow_request","target":"blog","payload":{"topic":"x","length_words":"99999"}},
    {"kind":"workflow_request","target":"video_script","payload":{"topic":"x","duration_s":45}},
    {"kind":"workflow_request","target":"video_script","payload":{"topic":"x","duration_s":"30"}},
    {"kind":"workflow_request","target":"video_script","payload":{"topic":"x","aspect":"1:1"}},
    {"kind":"workflow_request","target":"repurpose","payload":{"targets":["x_thread"]}},
    {"kind":"workflow_request","target":"repurpose","payload":{"content_id":"c\u0001","targets":["x_thread"]}},
    {"kind":"workflow_request","target":"repurpose","payload":{"content_id":"cnt_1","targets":["../x"]}},
    {"kind":"workflow_request","target":"repurpose","payload":{"content_id":"cnt_1","targets":[]}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"posts_per_week":0}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"posts_per_week":15}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"posts_per_week":"3"}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"week_start":"next monday"}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"week_start":"2026-13-01"}},
    {"kind":"workflow_request","target":"weekly_plan","payload":{"brief":"x"}},
    {"kind":"workflow_request","target":"weekly_plan","payload":[]},
    {"kind":"launch_rocket","target":"x","payload":{}},
    {"kind":"library_upload_v2","target":"x","payload":{}}
  ]$j$::jsonb) loop
    begin
      insert into public.phone_actions (id, kind, target_id, payload)
      values (gen_random_uuid(), v_case->>'kind', v_case->>'target', v_case->'payload');
      raise exception 'FAIL: accepted a bad action %', v_case;
    exception when check_violation then null;
    end;
  end loop;
  raise notice 'PASS: malformed, over-wide and hostile payloads of the new kinds are refused';

  -- Size caps: a body over 6000 characters, a topic over 1000, a brief over 2000.
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'draft_create', 'new', jsonb_build_object('platform', 'x', 'title', 't', 'body', repeat('x', 6001)));
    raise exception 'FAIL: a 6001 character draft body was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'workflow_request', 'blog', jsonb_build_object('topic', repeat('x', 1001)));
    raise exception 'FAIL: a 1001 character topic was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'workflow_request', 'post', jsonb_build_object('brief', repeat('x', 2001)));
    raise exception 'FAIL: a 2001 character brief was accepted';
  exception when check_violation then null;
  end;
  -- Owner and status stay out of the client's hands for the new kinds too.
  begin
    insert into public.phone_actions (id, kind, target_id, payload, owner_id)
    values (gen_random_uuid(), 'schedule_update', 'market_scan', '{"enabled":false}'::jsonb, '13131313-1313-4313-8313-131313131302');
    raise exception 'FAIL: a client chose the owner of a schedule action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: size caps hold and the owner cannot be chosen';

  -- The laptop claims and finishes a new kind like any other.
  if (select (public.claim_phone_action('eeeeeeee-0000-4000-8000-000000000001', 'laptop-lpc')).status) <> 'applying' then
    raise exception 'FAIL: could not claim a library_upload';
  end if;
  if (select (public.finish_phone_action('eeeeeeee-0000-4000-8000-000000000001', 'laptop-lpc', 'applied', 'Added to your Library.')).status) <> 'applied' then
    raise exception 'FAIL: could not finish a library_upload';
  end if;
  raise notice 'PASS: the new kinds are claimed and finished like the older ones';

  -- The mirror takes the new kinds, and still refuses invented ones.
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at) values
    ('asset', 'asset_lpc_1', 'logo.png', '{"name":"logo.png","file_group":"image","size_bytes":1200,"category":"brand"}'::jsonb, now()),
    ('asset', 'asset_lpc_conf', 'Confidential item', '{"confidential":true}'::jsonb, now()),
    ('schedule', 'market_scan', 'Market scan', '{"enabled":true,"interval_hours":6}'::jsonb, now()),
    ('editorial_plan', 'plan', 'Editorial calendar', '{"rows":[{"slot":"Mon","platforms":"x","topic":"t","status":"idea"}]}'::jsonb, now());
  begin
    insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
    values ('assets', 'x', 'x', '{}'::jsonb, now());
    raise exception 'FAIL: an invented mirror kind was accepted';
  exception when check_violation then null;
  end;
  insert into public.mirror_records (kind, local_id, title, data, local_updated_at)
  select k, 'lpc_kind_' || k, 't', '{}'::jsonb, now()
  from unnest(array['chat_session','team_message','chat_turn','content_item','content_version','research_note','review_comment','metric','posted_link','brief','task','pitch_proposal']) k;
  raise notice 'PASS: asset, schedule and editorial_plan are mirror kinds and every older kind is kept';

  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n < 20 then raise exception 'FAIL: A cannot read their own actions (%)', v_n; end if;
end;
$$;

-- 5. Member B, an admin and a deactivated member see and change nothing of A's.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '13131313-1313-4313-8313-131313131302', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_b uuid := '13131313-1313-4313-8313-131313131302';
  v_n int;
begin
  select count(*) into v_n from public.phone_uploads where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads A uploads (%)', v_n; end if;
  select count(*) into v_n from public.phone_actions where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: member B reads A actions (%)', v_n; end if;
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind in ('asset', 'schedule', 'editorial_plan');
  if v_n <> 0 then raise exception 'FAIL: member B reads A mirror records (%)', v_n; end if;
  delete from public.phone_uploads where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B deleted A uploads'; end if;
  update public.phone_uploads set removing = true where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B marked A uploads removing'; end if;
  update public.mirror_records set title = 'hacked' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: member B changed A mirror records'; end if;
  begin
    insert into public.phone_uploads (owner_id, file_name, kind, content_type, bytes, storage_path)
    values (v_a, 'planted.png', 'image', 'image/png', 10, v_a::text || '/' || repeat('5', 64) || '.png');
    raise exception 'FAIL: member B planted an upload for A';
  exception when insufficient_privilege or check_violation then null;
  end;
  -- B cannot claim or finish A's action.
  if (select public.claim_phone_action('eeeeeeee-0000-4000-8000-000000000002', 'laptop-b')) is not null then
    raise exception 'FAIL: member B claimed A action';
  end if;
  -- B cannot point an action at A's upload either: the action is B's own and the laptop checks the upload is B's.
  insert into public.phone_actions (id, kind, target_id, payload)
  values ('eeeeeeee-0000-4000-8000-0000000000b1', 'library_upload', 'dddddddd-0000-4000-8000-000000000001', '{"category":"brand"}'::jsonb);
  select count(*) into v_n from public.phone_uploads where id = 'dddddddd-0000-4000-8000-000000000001';
  if v_n <> 0 then raise exception 'FAIL: B can read the upload an action of theirs points at'; end if;
  raise notice 'PASS: another member reads, changes, plants and claims nothing of A''s';
end;
$$;

set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '13131313-1313-4313-8313-131313131303', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_a uuid := '13131313-1313-4313-8313-131313131301';
  v_n int;
begin
  -- Admins read the mirror (as before) but never uploads, and change nothing.
  select count(*) into v_n from public.phone_uploads where owner_id = v_a;
  if v_n <> 0 then raise exception 'FAIL: an admin reads A uploads (%)', v_n; end if;
  select count(*) into v_n from public.mirror_records where owner_id = v_a and kind = 'asset';
  if v_n < 1 then raise exception 'FAIL: an admin cannot read the asset mirror like the other kinds'; end if;
  update public.mirror_records set title = 'hacked' where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: an admin changed A mirror records'; end if;
  delete from public.phone_uploads where owner_id = v_a;
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FAIL: an admin deleted A uploads'; end if;
  raise notice 'PASS: an admin reads the mirror like before but never uploads, and writes nothing of A''s';
end;
$$;

set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '13131313-1313-4313-8313-131313131304', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_deact uuid := '13131313-1313-4313-8313-131313131304';
begin
  begin
    insert into public.phone_uploads (file_name, kind, content_type, bytes, storage_path)
    values ('x.png', 'image', 'image/png', 10, v_deact::text || '/' || repeat('4', 64) || '.png');
    raise exception 'FAIL: a deactivated member added an upload';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.phone_actions (id, kind, target_id, payload)
    values (gen_random_uuid(), 'schedule_update', 'market_scan', '{"enabled":false}'::jsonb);
    raise exception 'FAIL: a deactivated member queued an action';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: a deactivated member cannot upload or queue anything';
end;
$$;

-- 6. Anonymous callers get nothing.
reset role;
set local role anon;
do $$
declare
  v_n int;
begin
  begin
    select count(*) into v_n from public.phone_uploads;
    raise exception 'FAIL: anon can read phone_uploads';
  exception when insufficient_privilege then null;
  end;
  begin
    select public.team_storage_used();
    raise exception 'FAIL: anon can read the team storage total';
  exception when insufficient_privilege then null;
  end;
  raise notice 'PASS: anon reads no uploads and no storage totals';
end;
$$;

reset role;
rollback;
