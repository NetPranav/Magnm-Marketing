-- Privacy, constraint, and audit tests for shared_knowledge and pitch_base_prices.
begin;
set local client_min_messages = notice;

do $$
declare
  v_admin uuid := '77777777-7777-4777-8777-777777777701';
  v_member uuid := '77777777-7777-4777-8777-777777777702';
  v_deactivated uuid := '77777777-7777-4777-8777-777777777703';
begin
  insert into public.invites (email, role)
  values
    ('admin.tk@example.com', 'admin'),
    ('member.tk@example.com', 'member'),
    ('deact.tk@example.com', 'member');

  insert into auth.users (id, email, raw_app_meta_data, raw_user_meta_data)
  values
    (v_admin, 'admin.tk@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Admin TK"}'::jsonb),
    (v_member, 'member.tk@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Member TK"}'::jsonb),
    (v_deactivated, 'deact.tk@example.com', '{"provider":"google","providers":["google"]}'::jsonb, '{"full_name":"Deactivated TK"}'::jsonb);

  update public.profiles set active = false where id = v_deactivated;
end;
$$;

-- Admin inserts, updates, and deletes shared_knowledge and pitch_base_prices.
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '77777777-7777-4777-8777-777777777701', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_admin uuid := '77777777-7777-4777-8777-777777777701';
  v_updated_by uuid;
  v_count int;
begin
  -- Invalid shared_knowledge key rejected.
  begin
    insert into public.shared_knowledge (key, data) values ('unknown_key', '{}'::jsonb);
    raise exception 'FAIL: invalid shared_knowledge key accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      raise notice 'PASS: shared_knowledge rejects invalid key';
  end;

  -- Negative pitch_base_prices price rejected.
  begin
    insert into public.pitch_base_prices (item_id, price) values ('w_front', -5);
    raise exception 'FAIL: negative pitch_base_prices price accepted';
  exception
    when others then
      if sqlerrm like 'FAIL:%' then raise; end if;
      raise notice 'PASS: pitch_base_prices rejects negative price';
  end;

  insert into public.shared_knowledge (key, data)
  values ('templates', '{"files":{"playbooks/launch.md":"v1"},"version":1}'::jsonb)
  on conflict (key) do update set data = excluded.data;

  update public.shared_knowledge
  set data = '{"files":{"playbooks/launch.md":"v2"},"version":1}'::jsonb
  where key = 'templates';

  select updated_by into v_updated_by from public.shared_knowledge where key = 'templates';
  if v_updated_by is distinct from v_admin then
    raise exception 'FAIL: shared_knowledge update did not stamp updated_by';
  end if;

  insert into public.pitch_base_prices (item_id, price)
  values ('tk_test_item', 4500)
  on conflict (item_id) do update set price = excluded.price;

  update public.pitch_base_prices set price = 4900 where item_id = 'tk_test_item';

  select updated_by into v_updated_by from public.pitch_base_prices where item_id = 'tk_test_item';
  if v_updated_by is distinct from v_admin then
    raise exception 'FAIL: pitch_base_prices update did not stamp updated_by';
  end if;

  -- Check audit log entries for update and delete.
  delete from public.shared_knowledge where key = 'templates';
  delete from public.pitch_base_prices where item_id = 'tk_test_item';

  select count(*) into v_count
  from public.audit_log
  where actor_id = v_admin
    and action in (
      'shared_knowledge.updated',
      'shared_knowledge.deleted',
      'pitch_base_price.created',
      'pitch_base_price.updated',
      'pitch_base_price.deleted'
    );
  if v_count < 5 then
    raise exception 'FAIL: expected audit_log rows for shared_knowledge and pitch_base_prices lifecycle, got %', v_count;
  end if;
  raise notice 'PASS: admin write/update/delete on shared_knowledge and pitch_base_prices writes audit_log';

  -- Re-seed rows for member checks.
  insert into public.shared_knowledge (key, data)
  values ('templates', '{"files":{"playbooks/launch.md":"v3"},"version":1}'::jsonb)
  on conflict (key) do update set data = excluded.data;

  insert into public.pitch_base_prices (item_id, price)
  values ('tk_test_item', 5100)
  on conflict (item_id) do update set price = excluded.price;
end;
$$;

-- Active member can read, cannot insert, update, or delete.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '77777777-7777-4777-8777-777777777702', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_price int;
  v_data jsonb;
begin
  select data into v_data from public.shared_knowledge where key = 'templates';
  if v_data is null then
    raise exception 'FAIL: active member cannot read shared_knowledge';
  end if;

  select price into v_price from public.pitch_base_prices where item_id = 'tk_test_item';
  if v_price is distinct from 5100 then
    raise exception 'FAIL: active member cannot read pitch_base_prices';
  end if;

  update public.shared_knowledge set data = '{"hijacked":true}'::jsonb where key = 'templates';
  delete from public.shared_knowledge where key = 'templates';
  update public.pitch_base_prices set price = 1 where item_id = 'tk_test_item';
  delete from public.pitch_base_prices where item_id = 'tk_test_item';

  select data into v_data from public.shared_knowledge where key = 'templates';
  if (v_data->>'hijacked') = 'true' or v_data is null then
    raise exception 'FAIL: member modified or deleted shared_knowledge';
  end if;

  select price into v_price from public.pitch_base_prices where item_id = 'tk_test_item';
  if v_price is distinct from 5100 then
    raise exception 'FAIL: member modified or deleted pitch_base_prices';
  end if;
  raise notice 'PASS: active member can read but cannot update or delete shared_knowledge or pitch_base_prices';
end;
$$;

-- Deactivated member cannot read shared_knowledge or pitch_base_prices.
select set_config(
  'request.jwt.claims',
  json_build_object('sub', '77777777-7777-4777-8777-777777777703', 'role', 'authenticated')::text,
  true
);

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.shared_knowledge where key = 'templates';
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member read shared_knowledge';
  end if;
  select count(*) into v_count from public.pitch_base_prices where item_id = 'tk_test_item';
  if v_count <> 0 then
    raise exception 'FAIL: deactivated member read pitch_base_prices';
  end if;
  raise notice 'PASS: deactivated member cannot read shared_knowledge or pitch_base_prices';
end;
$$;

reset role;
rollback;
