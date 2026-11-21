-- Fix: Supabase rejects DELETE without WHERE for API requests (pg-safeupdate), which made admin_set_ai_keys fail
-- when called from the app. Same function, with an explicit WHERE on the key-health reset.
create or replace function public.admin_set_ai_keys(p_keys text[])
returns int
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_raw text;
  v_dedup text[] := '{}'::text[];
  v_count int := 0;
  v_idx int;
begin
  if not public.is_admin() then
    raise exception 'Admin access required';
  end if;

  if p_keys is null then
    raise exception 'Keys array is required';
  end if;

  foreach v_raw in array p_keys loop
    if v_raw is null or v_raw !~ '^nvapi-[A-Za-z0-9_-]{20,200}$' then
      raise exception 'Invalid NVIDIA NIM API key format';
    end if;
    if not (v_raw = any(v_dedup)) then
      v_dedup := array_append(v_dedup, v_raw);
    end if;
  end loop;

  v_count := coalesce(array_length(v_dedup, 1), 0);
  if v_count > 20 then
    raise exception 'At most 20 shared AI keys are allowed';
  end if;

  delete from vault.secrets
  where name ~ '^magnm_ai_key_[0-9]+$';

  for v_idx in 1..v_count loop
    perform vault.create_secret(
      v_dedup[v_idx],
      'magnm_ai_key_' || v_idx::text,
      'Magnm shared NVIDIA NIM key ' || v_idx::text
    );
  end loop;

  delete from public.ai_key_health where true;

  insert into public.audit_log (actor_id, action, target, data)
  values (
    auth.uid(),
    'ai_keys.updated',
    'magnm_ai_keys',
    jsonb_build_object('count', v_count)
  );

  return v_count;
end;
$$;
