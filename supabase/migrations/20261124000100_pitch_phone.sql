-- Pitch on the phone without the laptop: four new phone action kinds and a mirror kind for proposals.
-- Forward-only: nothing here changes or removes existing data. An engine that does not know a new kind leaves it
-- queued (it only asks for the kinds it knows), and an engine that does not mirror proposals simply never writes one.
-- Admin on the phone adds no table, policy or function: it reuses the existing admin RLS and RPCs.

-- 1. Proposals are mirrored like the other workspace records (owner writes, admins read, others see nothing).
alter table public.mirror_records
  drop constraint if exists mirror_records_kind_check;

alter table public.mirror_records
  add constraint mirror_records_kind_check check (
    kind in (
      'chat_session',
      'team_message',
      'chat_turn',
      'content_item',
      'content_version',
      'research_note',
      'review_comment',
      'metric',
      'posted_link',
      'brief',
      'task',
      'pitch_proposal'
    )
  );

-- 2. The fields a phone may change on a proposal (the field whitelist). Everything the laptop does not recognise is
-- refused here and again on the laptop, which also runs the same Pitch rules as the web app.
-- Pure checks only: it reads nothing. A check constraint runs it with the caller's rights, so signed-in clients may
-- execute it; anon may not.
create or replace function public.phone_pitch_fields_ok(f jsonb, allow_create_fields boolean)
returns boolean
language sql
immutable
set search_path = ''
as $$
  -- Every block is a CASE so a wrong type is a plain "no" and never an error, whatever order Postgres evaluates in.
  select coalesce(
    case when jsonb_typeof(f) = 'object' then
      (f - array['client_name', 'business_name', 'contact', 'notes', 'preset', 'hidden_sheets', 'hidden_about', 'price_overrides']
         - case when allow_create_fields then array['kind', 'from_preset'] else array[]::text[] end) = '{}'::jsonb
      -- Names and notes.
      and case when f ? 'client_name' then jsonb_typeof(f -> 'client_name') = 'string' and char_length(f ->> 'client_name') <= 200 else true end
      and case when f ? 'business_name' then jsonb_typeof(f -> 'business_name') = 'string' and char_length(f ->> 'business_name') <= 200 else true end
      and case when f ? 'notes' then jsonb_typeof(f -> 'notes') = 'string' and char_length(f ->> 'notes') <= 4000 else true end
      -- Create only: the kind (never final or default) and a built-in preset slug.
      and case when f ? 'kind' then jsonb_typeof(f -> 'kind') = 'string' and f ->> 'kind' in ('proposal', 'quote_form') else true end
      and case when f ? 'from_preset' then jsonb_typeof(f -> 'from_preset') = 'string' and f ->> 'from_preset' ~ '^[a-z0-9][a-z0-9-]{0,62}$' else true end
      -- Client contact: five short strings.
      and case when f ? 'contact' then
        case when jsonb_typeof(f -> 'contact') = 'object' then
          ((f -> 'contact') - array['name', 'business', 'email', 'phone', 'notes']) = '{}'::jsonb
          and not exists (
            select 1 from jsonb_each(f -> 'contact') e
            where jsonb_typeof(e.value) <> 'string' or char_length(e.value #>> '{}') > 1000
          )
        else false end
      else true end
      -- Texts and which services each sheet shows.
      and case when f ? 'preset' then
        case when jsonb_typeof(f -> 'preset') = 'object' then
          ((f -> 'preset') - array['title', 'lead', 'whatWeDo', 'portfolio', 'thisPlan', 'initialSelected', 'sections']) = '{}'::jsonb
          and not exists (
            select 1 from jsonb_each(f -> 'preset') e
            where e.key in ('title', 'lead', 'whatWeDo', 'portfolio', 'thisPlan')
              and (jsonb_typeof(e.value) <> 'string' or char_length(e.value #>> '{}') > 1000)
          )
          and case when (f -> 'preset') ? 'initialSelected' then
            case when jsonb_typeof(f -> 'preset' -> 'initialSelected') = 'array' then
              jsonb_array_length(f -> 'preset' -> 'initialSelected') <= 200
              and not exists (
                select 1 from jsonb_array_elements(f -> 'preset' -> 'initialSelected') e
                where jsonb_typeof(e) <> 'string' or char_length(e #>> '{}') > 80
              )
            else false end
          else true end
          and case when (f -> 'preset') ? 'sections' then
            case when jsonb_typeof(f -> 'preset' -> 'sections') = 'object' then
              ((f -> 'preset' -> 'sections') - array['p2', 'p3', 'p4', 'p5']) = '{}'::jsonb
              and not exists (
                select 1 from jsonb_each(f -> 'preset' -> 'sections') s
                where case when jsonb_typeof(s.value) <> 'array' then true
                      when jsonb_array_length(s.value) > 200 then true
                      else exists (
                        select 1 from jsonb_array_elements(s.value) e
                        where jsonb_typeof(e) <> 'string' or char_length(e #>> '{}') > 80
                      ) end
              )
            else false end
          else true end
        else false end
      else true end
      -- Sheets and about sections that are hidden.
      and case when f ? 'hidden_sheets' then
        case when jsonb_typeof(f -> 'hidden_sheets') = 'array' then
          jsonb_array_length(f -> 'hidden_sheets') <= 8
          and not exists (
            select 1 from jsonb_array_elements(f -> 'hidden_sheets') e
            where jsonb_typeof(e) <> 'string' or (e #>> '{}') not in ('p2', 'p3', 'p4', 'p5')
          )
        else false end
      else true end
      and case when f ? 'hidden_about' then
        case when jsonb_typeof(f -> 'hidden_about') = 'array' then
          jsonb_array_length(f -> 'hidden_about') <= 16
          and not exists (
            select 1 from jsonb_array_elements(f -> 'hidden_about') e
            where jsonb_typeof(e) <> 'string'
               or (e #>> '{}') not in ('lead', 'what_we_do', 'portfolio', 'this_plan', 'architecture', 'timeline', 'payment')
          )
        else false end
      else true end
      -- Prices per proposal: whole numbers from 0 to 10,000,000 (only a person sets a price).
      and case when f ? 'price_overrides' then
        case when jsonb_typeof(f -> 'price_overrides') = 'object' then
          (select count(*) from jsonb_object_keys(f -> 'price_overrides')) <= 100
          and not exists (
            select 1 from jsonb_each(f -> 'price_overrides') e
            where case when char_length(e.key) > 64 then true
                  when jsonb_typeof(e.value) <> 'number' then true
                  when (e.value #>> '{}') !~ '^[0-9]{1,8}$' then true
                  else (e.value #>> '{}')::bigint > 10000000 end
          )
        else false end
      else true end
      and (allow_create_fields or f <> '{}'::jsonb)
    else false end,
    false
  );
$$;

revoke all on function public.phone_pitch_fields_ok(jsonb, boolean) from public, anon, authenticated;
grant execute on function public.phone_pitch_fields_ok(jsonb, boolean) to authenticated, service_role;

-- 3. The four kinds. The shapes are exact: pitch_create sends the fields of a new proposal (target 'new'),
-- pitch_update the fields to change plus the version the phone saw, pitch_finalize nothing, pitch_link publish or
-- withdraw. No text may contain angle brackets (the same rule as the web app).
alter table public.phone_actions drop constraint phone_actions_kind_check;
alter table public.phone_actions add constraint phone_actions_kind_check
  check (kind in (
    'draft_comment', 'draft_posted', 'reminder_done', 'agent_task',
    'pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link'
  ));

alter table public.phone_actions drop constraint phone_actions_payload_shape;
alter table public.phone_actions add constraint phone_actions_payload_shape check (
  jsonb_typeof(payload) = 'object'
  and octet_length(payload::text) <= 17000
  and coalesce(case kind
    when 'draft_comment' then
      (payload - 'body' - 'version') = '{}'::jsonb
      and jsonb_typeof(payload -> 'body') = 'string'
      and char_length(btrim(payload ->> 'body')) between 1 and 4000
      and (
        not (payload ? 'version')
        or (
          jsonb_typeof(payload -> 'version') = 'number'
          and (payload ->> 'version') ~ '^[1-9][0-9]{0,8}$'
        )
      )
    when 'draft_posted' then
      (payload - 'url') = '{}'::jsonb
      and (
        not (payload ? 'url')
        or (
          jsonb_typeof(payload -> 'url') = 'string'
          and char_length(payload ->> 'url') between 1 and 2000
          and (payload ->> 'url') ~ '^https?://[^[:space:][:cntrl:]]+$'
        )
      )
    when 'reminder_done' then
      payload = '{}'::jsonb
    when 'agent_task' then
      (payload - 'message') = '{}'::jsonb
      and jsonb_typeof(payload -> 'message') = 'string'
      and char_length(btrim(payload ->> 'message')) between 1 and 4000
      and (payload ->> 'message') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
      and target_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    when 'pitch_create' then
      target_id = 'new'
      and payload::text !~ '[<>]'
      and public.phone_pitch_fields_ok(payload, true)
    when 'pitch_update' then
      (payload - 'base_version' - 'fields') = '{}'::jsonb
      and jsonb_typeof(payload -> 'base_version') = 'string'
      and char_length(payload ->> 'base_version') between 1 and 64
      and (payload ->> 'base_version') !~ '[[:cntrl:]]'
      and payload::text !~ '[<>]'
      and public.phone_pitch_fields_ok(payload -> 'fields', false)
    when 'pitch_finalize' then
      payload = '{}'::jsonb
    when 'pitch_link' then
      payload in ('{"action":"publish"}'::jsonb, '{"action":"withdraw"}'::jsonb)
    else false
  end, false)
);
