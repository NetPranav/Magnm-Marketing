-- Privacy, bucket, policy, and storage_usage() tests for media_previews.
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '55555555-5555-4555-8555-555555555501';
  v_member_a uuid := '55555555-5555-4555-8555-555555555502';
  v_member_b uuid := '55555555-5555-4555-8555-555555555503';
  v_public boolean;
  v_limit bigint;
  v_mimes text[];
  v_policy_count integer;
  v_anon_policies integer;
begin
  -- Verify private previews bucket configuration.
  select public, file_size_limit, allowed_mime_types
  into v_public, v_limit, v_mimes
  from storage.buckets
  where id = 'previews';

  if not found then
    raise exception 'FAIL: storage bucket previews does not exist';
  end if;
  if v_public is distinct from false then
    raise exception 'FAIL: previews bucket must be private (public = false)';
  end if;
  if v_limit is distinct from 15728640 then
    raise exception 'FAIL: previews bucket file_size_limit expected 15728640, got %', v_limit;
  end if;
  if not (v_mimes @> array['image/webp', 'video/mp4'] and array_length(v_mimes, 1) = 2) then
    raise exception 'FAIL: previews bucket allowed_mime_types mismatch: %', v_mimes;
  end if;
  raise notice 'PASS: previews bucket exists, is private, 15 MB limit, webp/mp4 only';

  -- Verify the four storage.objects policies for previews exist and exclude anon.
  select count(*) into v_policy_count
  from pg_policies
  where schemaname = 'storage'
    and tablename = 'objects'
    and policyname in (
      'previews_select_own_or_admin',
      'previews_insert_own',
      'previews_update_own',
      'previews_delete_own'
    );

  if v_policy_count <> 4 then
    raise exception 'FAIL: expected 4 previews policies on storage.objects, found %', v_policy_count;
  end if;

  select count(*) into v_anon_policies
  from pg_policies
  where schemaname = 'storage'
    and tablename = 'objects'
    and policyname like 'previews_%'
    and ('anon' = any(roles) or 'public' = any(roles));

  if v_anon_policies <> 0 then
    raise exception 'FAIL: anon or public role found in previews storage policies';
  end if;
  raise notice 'PASS: four storage policies exist for previews and grant nothing to anon';

  insert into public.invites (email, role)
  values
    ('admin.mp@example.com', 'admin'),
    ('member.mpa@example.com', 'member'),
    ('member.mpb@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.mp@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin MP"}'::jsonb),
    (v_member_a, 'member.mpa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member A"}'::jsonb),
    (v_member_b, 'member.mpb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member B"}'::jsonb);
end;
$$;

-- Member A inserts own preview rows and tests path/owner invariants.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '55555555-5555-4555-8555-555555555502', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '55555555-5555-4555-8555-555555555502';
  v_member_b uuid := '55555555-5555-4555-8555-555555555503';
  v_usage jsonb;
  v_count integer;
begin
  insert into public.media_previews (
    local_source_id, source_kind, parent_local_id, kind,
    storage_path, content_type, bytes, width, height
  )
  values
    (
      'content:post-1:img.png', 'content_file', 'post-1', 'thumbnail',
      v_member_a::text || '/thumbnail/abc.webp', 'image/webp', 12000, 320, 180
    ),
    (
      'content:post-1:vid.mp4', 'content_file', 'post-1', 'video_preview',
      v_member_a::text || '/video_preview/def.mp4', 'video/mp4', 240000, 854, 480
    ),
    (
      'asset:old-deleted', 'asset', null, 'thumbnail',
      v_member_a::text || '/thumbnail/old.webp', 'image/webp', 99999, 320, 320
    );

  update public.media_previews
  set deleted_at = now()
  where local_source_id = 'asset:old-deleted';

  select count(*) into v_count from public.media_previews where owner_id = v_member_a;
  if v_count <> 3 then
    raise exception 'FAIL: member A expected 3 own rows, got %', v_count;
  end if;
  raise notice 'PASS: member A inserted and read own preview rows';

  -- storage_path must start with owner_id || '/'.
  begin
    insert into public.media_previews (
      local_source_id, source_kind, kind, storage_path, content_type, bytes
    )
    values (
      'asset:bad-path', 'asset', 'thumbnail',
      v_member_b::text || '/thumbnail/bad.webp', 'image/webp', 100
    );
    raise exception 'FAIL: storage_path outside owner prefix was accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: storage_path must start with owner_id/';
  end;

  -- Cannot change owner_id on update.
  begin
    update public.media_previews
    set owner_id = v_member_b
    where local_source_id = 'content:post-1:img.png';
    raise exception 'FAIL: owner_id was changed on update';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: cannot change owner_id on update';
  end;

  -- Check member A storage_usage() excludes deleted row.
  v_usage := public.storage_usage();
  if (v_usage->>'bytes')::bigint <> 252000 or (v_usage->>'files')::bigint <> 2 then
    raise exception 'FAIL: member A storage_usage mismatch: %', v_usage;
  end if;
  raise notice 'PASS: member A storage_usage() counts only own non-deleted rows';

  -- Test storage.objects folder policy for member A if allowed in transaction.
  begin
    insert into storage.objects (bucket_id, name, owner)
    values ('previews', v_member_a::text || '/thumbnail/test-obj.webp', v_member_a);

    begin
      insert into storage.objects (bucket_id, name, owner)
      values ('previews', v_member_b::text || '/thumbnail/forged.webp', v_member_a);
      raise exception 'FAIL: member A inserted into member B storage folder';
    exception
      when others then
        if sqlerrm like 'FAIL:%' then
          raise;
        end if;
        raise notice 'PASS: storage.objects blocks inserting outside own folder';
    end;
  exception
    when insufficient_privilege then
      raise notice 'PASS: direct storage.objects insert blocked by Supabase ownership; relying on pg_policies checks';
  end;
end;
$$;

-- Switch to Member B: inserts own row, cannot read/modify Member A's rows.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '55555555-5555-4555-8555-555555555503', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '55555555-5555-4555-8555-555555555502';
  v_member_b uuid := '55555555-5555-4555-8555-555555555503';
  v_count integer;
  v_usage jsonb;
begin
  insert into public.media_previews (
    local_source_id, source_kind, kind, storage_path, content_type, bytes
  )
  values (
    'asset:b1', 'asset', 'image_preview',
    v_member_b::text || '/image_preview/b1.webp', 'image/webp', 50000
  );

  select count(*) into v_count from public.media_previews where owner_id = v_member_a;
  if v_count <> 0 then
    raise exception 'FAIL: member B read member A media_previews';
  end if;

  update public.media_previews set bytes = 1 where owner_id = v_member_a;
  delete from public.media_previews where owner_id = v_member_a;

  select count(*) into v_count
  from storage.objects
  where bucket_id = 'previews' and name like v_member_a::text || '/%';
  if v_count <> 0 then
    raise exception 'FAIL: member B read member A storage.objects';
  end if;

  v_usage := public.storage_usage();
  if (v_usage->>'bytes')::bigint <> 50000 or (v_usage->>'files')::bigint <> 1 then
    raise exception 'FAIL: member B storage_usage mismatch: %', v_usage;
  end if;
  raise notice 'PASS: member B isolated from member A media_previews and storage_usage()';
end;
$$;

-- Switch to Admin: reads all rows and totals, cannot write Member A's rows.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '55555555-5555-4555-8555-555555555501', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '55555555-5555-4555-8555-555555555502';
  v_member_b uuid := '55555555-5555-4555-8555-555555555503';
  v_count integer;
  v_bytes bigint;
  v_usage jsonb;
begin
  select count(*) into v_count
  from public.media_previews
  where owner_id in (v_member_a, v_member_b);
  if v_count <> 4 then
    raise exception 'FAIL: admin expected 4 media_previews rows, got %', v_count;
  end if;

  -- Admin cannot insert, update, or delete Member A's rows.
  begin
    insert into public.media_previews (
      owner_id, local_source_id, source_kind, kind, storage_path, content_type, bytes
    )
    values (
      v_member_a, 'asset:admin-spoof', 'asset', 'thumbnail',
      v_member_a::text || '/thumbnail/spoof.webp', 'image/webp', 100
    );
    raise exception 'FAIL: admin inserted media_previews row for member A';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
  end;

  update public.media_previews set bytes = 1 where owner_id = v_member_a;
  delete from public.media_previews where owner_id = v_member_a;

  select bytes into v_bytes
  from public.media_previews
  where owner_id = v_member_a and local_source_id = 'content:post-1:img.png';
  if v_bytes <> 12000 then
    raise exception 'FAIL: member A row was altered or deleted';
  end if;
  raise notice 'PASS: admin reads all rows and cannot write member A rows';

  select count(*) into v_count
  from storage.objects
  where bucket_id = 'previews' and name = v_member_a::text || '/thumbnail/test-obj.webp';
  if v_count <> 1 then
    raise exception 'FAIL: admin could not read member A object in previews bucket';
  end if;
  raise notice 'PASS: admin can select all objects in previews bucket';

  v_usage := public.storage_usage();
  if (v_usage->>'bytes')::bigint < 302000 or (v_usage->>'files')::bigint < 3 then
    raise exception 'FAIL: admin storage_usage expected >= 302000 bytes and >= 3 files, got %', v_usage;
  end if;
  if (v_usage->'by_kind'->'thumbnail'->>'files')::bigint < 1
     or (v_usage->'by_kind'->'video_preview'->>'files')::bigint < 1
     or (v_usage->'by_kind'->'image_preview'->>'files')::bigint < 1 then
    raise exception 'FAIL: admin storage_usage by_kind missing expected kinds: %', v_usage;
  end if;
  raise notice 'PASS: admin storage_usage() aggregates across team members by kind';
end;
$$;

-- Switch to anon: zero table/function access.
reset role;
set local role anon;

do $$
begin
  begin
    perform count(*) from public.media_previews;
    raise exception 'FAIL: anon selected from public.media_previews';
  exception
    when insufficient_privilege then
      null;
  end;

  begin
    perform public.storage_usage();
    raise exception 'FAIL: anon executed public.storage_usage()';
  exception
    when insufficient_privilege then
      null;
  end;

  raise notice 'PASS: anon has no access to media_previews or storage_usage()';
end;
$$;

reset role;
rollback;
