-- Schema guard tests: every public table has RLS enabled, anon has zero table/view access, and only the two Pitch RPCs are executable by anon.
begin;
set local client_min_messages = notice;

do $$
declare
  v_bad text;
  v_table_count integer;
begin
  -- 1. Confirm all 15 expected public tables exist and every table in public has RLS enabled.
  select count(*) into v_table_count
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and c.relkind = 'r';

  if v_table_count < 15 then
    raise exception 'FAIL: expected at least 15 tables in public, found %', v_table_count;
  end if;

  select string_agg(c.relname, ', ')
  into v_bad
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and c.relkind = 'r'
    and not c.relrowsecurity;

  if v_bad is not null then
    raise exception 'FAIL: public tables without RLS enabled: %', v_bad;
  end if;
  raise notice 'PASS: every table in public has RLS enabled';

  -- 2. Confirm anon has no table or view privileges in public.
  select string_agg(c.relname, ', ')
  into v_bad
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and c.relkind in ('r', 'v', 'm')
    and (
      has_table_privilege('anon', c.oid, 'SELECT')
      or has_table_privilege('anon', c.oid, 'INSERT')
      or has_table_privilege('anon', c.oid, 'UPDATE')
      or has_table_privilege('anon', c.oid, 'DELETE')
    );

  if v_bad is not null then
    raise exception 'FAIL: anon has table/view privileges on: %', v_bad;
  end if;
  raise notice 'PASS: anon has no table or view privileges in public';

  -- 3. Confirm no function in public other than get_published_proposal and submit_quote is executable by anon.
  select string_agg(p.proname, ', ')
  into v_bad
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public'
    and has_function_privilege('anon', p.oid, 'EXECUTE')
    and p.proname not in ('get_published_proposal', 'submit_quote');

  if v_bad is not null then
    raise exception 'FAIL: anon can execute non-Pitch public functions: %', v_bad;
  end if;

  if not has_function_privilege('anon', 'public.get_published_proposal(text)', 'EXECUTE')
     or not has_function_privilege('anon', 'public.submit_quote(text, jsonb, jsonb)', 'EXECUTE')
  then
    raise exception 'FAIL: anon cannot execute one of the two Pitch RPC functions';
  end if;
  raise notice 'PASS: only get_published_proposal and submit_quote in public are executable by anon';

  -- 3b. Confirm shared AI service-role functions are NOT executable by public, anon, or authenticated.
  select string_agg(p.proname, ', ')
  into v_bad
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public'
    and p.proname in ('ai_get_keys', 'ai_check_and_count', 'ai_record_usage', 'ai_report_key')
    and (
      has_function_privilege('public', p.oid, 'EXECUTE')
      or has_function_privilege('anon', p.oid, 'EXECUTE')
      or has_function_privilege('authenticated', p.oid, 'EXECUTE')
    );

  if v_bad is not null then
    raise exception 'FAIL: non-service role can execute service-only AI functions: %', v_bad;
  end if;
  raise notice 'PASS: shared AI service-role functions are executable only by service_role';
end;
$$;

-- 4. Switch to role anon and verify direct SELECT on every public table and view fails.
set local role anon;

do $$
declare
  v_rel record;
begin
  for v_rel in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'v')
    order by c.relname
  loop
    begin
      execute format('select count(*) from public.%I', v_rel.relname);
      raise exception 'FAIL: anon was able to SELECT from public.%', v_rel.relname;
    exception
      when insufficient_privilege then
        null;
    end;
  end loop;
  raise notice 'PASS: anon cannot select from any table or view in public';
end;
$$;

reset role;
-- API requests run with pg-safeupdate: DELETE or UPDATE without WHERE fails there (but not in these direct tests).
do $$
declare v_bad text;
begin
  select string_agg(p.proname, ', ') into v_bad
  from pg_proc p
  where p.pronamespace = 'public'::regnamespace
    and (p.prosrc ~* 'delete\s+from\s+[a-z_\.]+\s*;' or p.prosrc ~* 'update\s+[a-z_\.]+\s+set\s+[^;]*;'
         and not p.prosrc ~* 'update\s+[a-z_\.]+\s+set\s+[^;]*\swhere\s[^;]*;');
  if v_bad is not null then
    raise exception 'FAIL: functions with DELETE/UPDATE lacking WHERE (blocked by pg-safeupdate): %', v_bad;
  end if;
  raise notice 'PASS: no public function runs DELETE/UPDATE without WHERE';
end $$;

rollback;
