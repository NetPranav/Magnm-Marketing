-- Privacy and RPC tests for Pitch links (published_proposals, quote_submissions, get_published_proposal, submit_quote).
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '33333333-3333-4333-8333-333333333301';
  v_member_a uuid := '33333333-3333-4333-8333-333333333302';
  v_member_b uuid := '33333333-3333-4333-8333-333333333303';
begin
  insert into public.invites (email, role)
  values
    ('admin.p3@example.com', 'admin'),
    ('member.pa@example.com', 'member'),
    ('member.pb@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.p3@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin P3"}'::jsonb),
    (v_member_a, 'member.pa@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member PA"}'::jsonb),
    (v_member_b, 'member.pb@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member PB"}'::jsonb);
end;
$$;

-- Member A publishes a live proposal, a withdrawn proposal, and an expired proposal.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '33333333-3333-4333-8333-333333333302', 'role', 'authenticated')::text,
  true
);

do $$
begin
  insert into public.published_proposals (share_id, local_proposal_id, kind, payload, status, expires_at)
  values
    ('share_live_1234567890', 'prop-1', 'proposal', '{"client":"Acme Corp","total":45000}'::jsonb, 'live', now() + interval '7 days'),
    ('share_withdrawn_12345', 'prop-2', 'proposal', '{"client":"Old Corp"}'::jsonb, 'withdrawn', null),
    ('share_expired_1234567', 'prop-3', 'final', '{"client":"Past Corp"}'::jsonb, 'live', now() - interval '1 hour');
  raise notice 'PASS: member A published live, withdrawn, and expired proposals';
end;
$$;

-- Switch to anon client and test get_published_proposal and submit_quote.
set local role anon;
select set_config('request.jwt.claims', '{}', true);

do $$
declare
  v_member_a uuid := '33333333-3333-4333-8333-333333333302';
  v_res jsonb;
  v_sub_id uuid;
  v_i integer;
begin
  -- Live link returns payload + kind.
  v_res := public.get_published_proposal('share_live_1234567890');
  if v_res is null or v_res->>'kind' <> 'proposal' or v_res->'payload'->>'client' <> 'Acme Corp' or v_res ? 'client' then
    raise exception 'FAIL: get_published_proposal returned unexpected value for live link: %', v_res;
  end if;
  raise notice 'PASS: get_published_proposal returns payload and kind for live link';

  -- Withdrawn, expired, and unknown links all return null.
  if public.get_published_proposal('share_withdrawn_12345') is not null then
    raise exception 'FAIL: get_published_proposal returned data for withdrawn link';
  end if;

  if public.get_published_proposal('share_expired_1234567') is not null then
    raise exception 'FAIL: get_published_proposal returned data for expired link';
  end if;

  if public.get_published_proposal('share_unknown_1234567') is not null then
    raise exception 'FAIL: get_published_proposal returned data for unknown link';
  end if;
  raise notice 'PASS: get_published_proposal returns null for withdrawn, expired, and unknown links';

  -- submit_quote works for anon on live link.
  v_sub_id := public.submit_quote(
    'share_live_1234567890',
    '{"selected":["web_landing","cms"]}'::jsonb,
    '{"name":"Priya","email":"priya@acme.example"}'::jsonb
  );
  if v_sub_id is null then
    raise exception 'FAIL: submit_quote returned null id';
  end if;
  raise notice 'PASS: submit_quote works for anon on live proposal';

  -- submit_quote is rejected for withdrawn link.
  begin
    perform public.submit_quote(
      'share_withdrawn_12345',
      '{"selected":[]}'::jsonb,
      '{"name":"Priya"}'::jsonb
    );
    raise exception 'FAIL: submit_quote succeeded on withdrawn link';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: submit_quote rejected for withdrawn link';
  end;

  -- submit_quote is rejected for expired link.
  begin
    perform public.submit_quote(
      'share_expired_1234567',
      '{"selected":[]}'::jsonb,
      '{"name":"Priya"}'::jsonb
    );
    raise exception 'FAIL: submit_quote succeeded on expired link';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: submit_quote rejected for expired link';
  end;

  -- submit_quote is rejected for oversized choices (> 64 KB) and oversized contact (> 8 KB).
  begin
    perform public.submit_quote(
      'share_live_1234567890',
      jsonb_build_object('blob', repeat('x', 70000)),
      '{"name":"Priya"}'::jsonb
    );
    raise exception 'FAIL: submit_quote accepted oversized choices';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: submit_quote rejected oversized choices (> 64 KB)';
  end;

  begin
    perform public.submit_quote(
      'share_live_1234567890',
      '{"selected":[]}'::jsonb,
      jsonb_build_object('blob', repeat('y', 9000))
    );
    raise exception 'FAIL: submit_quote accepted oversized contact';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: submit_quote rejected oversized contact (> 8 KB)';
  end;

  -- Rate limit: 19 more submissions succeed (total 20 in 24h), 21st is rejected.
  for v_i in 2..20 loop
    perform public.submit_quote(
      'share_live_1234567890',
      jsonb_build_object('seq', v_i),
      '{"name":"Client"}'::jsonb
    );
  end loop;

  begin
    perform public.submit_quote(
      'share_live_1234567890',
      '{"seq":21}'::jsonb,
      '{"name":"Client"}'::jsonb
    );
    raise exception 'FAIL: 21st submission in 24 hours succeeded';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: 21st quote submission in 24 hours is rejected';
  end;
end;
$$;

-- Switch to Member B and verify Member B cannot see or modify Member A's proposals or submissions.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '33333333-3333-4333-8333-333333333303', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count integer;
begin
  select count(*) into v_count from public.published_proposals;
  if v_count <> 0 then
    raise exception 'FAIL: member B saw % proposals belonging to member A', v_count;
  end if;

  select count(*) into v_count from public.quote_submissions;
  if v_count <> 0 then
    raise exception 'FAIL: member B saw % quote submissions belonging to member A', v_count;
  end if;

  update public.published_proposals set status = 'withdrawn' where share_id = 'share_live_1234567890';
  delete from public.published_proposals where share_id = 'share_live_1234567890';
  update public.quote_submissions set seen_at = now();
  raise notice 'PASS: member B cannot read, update, or delete member A proposals or submissions';
end;
$$;

-- Switch to Member A and verify owner_id was copied from the proposal and only seen_at can be updated.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '33333333-3333-4333-8333-333333333302', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_member_a uuid := '33333333-3333-4333-8333-333333333302';
  v_count integer;
begin
  select count(*) into v_count
  from public.quote_submissions
  where share_id = 'share_live_1234567890'
    and owner_id = v_member_a
    and seen_at is null;

  if v_count <> 20 then
    raise exception 'FAIL: member A expected 20 unseen submissions with owner_id=member_a, got %', v_count;
  end if;

  update public.quote_submissions
  set seen_at = now()
  where share_id = 'share_live_1234567890';

  begin
    update public.quote_submissions
    set contact = '{"tampered":true}'::jsonb
    where share_id = 'share_live_1234567890';
    raise exception 'FAIL: member A modified contact on quote_submissions';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then
        raise;
      end if;
      raise notice 'PASS: proposal owner reads own submissions (with owner_id from proposal) and may update only seen_at';
  end;
end;
$$;

-- Switch to Admin and verify admin can read all proposals and quote submissions.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '33333333-3333-4333-8333-333333333301', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count integer;
begin
  select count(*) into v_count from public.published_proposals;
  if v_count <> 3 then
    raise exception 'FAIL: admin expected 3 published_proposals, got %', v_count;
  end if;

  select count(*) into v_count from public.quote_submissions;
  if v_count <> 20 then
    raise exception 'FAIL: admin expected 20 quote_submissions, got %', v_count;
  end if;
  raise notice 'PASS: admin can read all published proposals and quote submissions';
end;
$$;

reset role;
rollback;
