-- Pause and resume from the phone, and the task board and automatic-work state on the phone.
-- Forward-only: nothing here changes or removes existing data. An engine that does not know a new kind leaves it
-- queued (it only asks for the kinds it knows), and an engine that does not mirror the new records never writes one.
--
-- 1. Mirror kinds board_item (one record per item of the laptop's task board; a confidential item is a stub) and
--    automation (the "Pause automatic work" switch, what was missed, the pipeline's schedule and last run).
-- 2. Two new phone action kinds. Every existing kind and payload shape is kept exactly.
--      run_control         target = a workflow run id; payload {"action": "pause" | "resume"}
--      automation_control  target = 'all';             payload {"action": "pause" | "resume"}
--    Cancel is not a phone action. Pausing or resuming starts nothing, approves nothing and posts nothing.

-- 1. Mirror kinds.
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
      'pitch_proposal',
      'asset',
      'schedule',
      'editorial_plan',
      'board_item',
      'automation'
    )
  );

-- 2. The shape of a pause or resume. A pure check (it reads nothing), so a check constraint can run it with the
-- caller's rights. Every test is a comparison or a CASE, so a wrong type is a plain "no" and never an error.
create or replace function public.phone_control_ok(p_kind text, p_target text, p jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    jsonb_typeof(p) = 'object'
    and (p - 'action') = '{}'::jsonb
    and jsonb_typeof(p -> 'action') = 'string'
    and (p ->> 'action') in ('pause', 'resume')
    and case p_kind
      when 'run_control' then p_target ~ '^wf_[a-z_]{1,32}_[0-9]{1,16}_[0-9a-f]{6}$'
      when 'automation_control' then p_target = 'all'
      else false
    end,
    false
  );
$$;

revoke all on function public.phone_control_ok(text, text, jsonb) from public, anon, authenticated;
grant execute on function public.phone_control_ok(text, text, jsonb) to authenticated, service_role;

alter table public.phone_actions drop constraint phone_actions_kind_check;
alter table public.phone_actions add constraint phone_actions_kind_check
  check (kind in (
    'draft_comment', 'draft_posted', 'reminder_done', 'agent_task',
    'pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link',
    'library_upload', 'schedule_update', 'draft_create', 'workflow_request',
    'run_control', 'automation_control'
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
    when 'library_upload' then
      target_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      and (payload - 'category' - 'description') = '{}'::jsonb
      and jsonb_typeof(payload -> 'category') = 'string'
      and (payload ->> 'category') in ('cero', 'brand', 'screenshot', 'recording', 'document')
      and (
        not (payload ? 'description')
        or (
          jsonb_typeof(payload -> 'description') = 'string'
          and char_length(payload ->> 'description') <= 300
          and (payload ->> 'description') !~ '[[:cntrl:]]'
        )
      )
    when 'schedule_update' then
      target_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
      and (payload - 'enabled' - 'interval_hours' - 'time' - 'day') = '{}'::jsonb
      and payload <> '{}'::jsonb
      and (not (payload ? 'enabled') or jsonb_typeof(payload -> 'enabled') = 'boolean')
      and (
        not (payload ? 'interval_hours')
        or (
          jsonb_typeof(payload -> 'interval_hours') = 'number'
          and (payload ->> 'interval_hours') ~ '^[1-9][0-9]{0,2}$'
          and (payload ->> 'interval_hours')::int <= 720
        )
      )
      and (
        not (payload ? 'time')
        or (
          jsonb_typeof(payload -> 'time') = 'string'
          and (payload ->> 'time') ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
        )
      )
      and (
        not (payload ? 'day')
        or (
          jsonb_typeof(payload -> 'day') = 'string'
          and (payload ->> 'day') ~ '^[*0-6]$'
        )
      )
    when 'draft_create' then
      target_id = 'new'
      and (payload - 'platform' - 'title' - 'body' - 'mode') = '{}'::jsonb
      and jsonb_typeof(payload -> 'platform') = 'string'
      and (payload ->> 'platform') in ('x', 'linkedin', 'instagram', 'youtube')
      and jsonb_typeof(payload -> 'title') = 'string'
      and char_length(btrim(payload ->> 'title')) between 1 and 200
      and (payload ->> 'title') !~ '[[:cntrl:]]'
      and jsonb_typeof(payload -> 'body') = 'string'
      and char_length(btrim(payload ->> 'body')) between 1 and 6000
      and (payload ->> 'body') !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'
      and (
        not (payload ? 'mode')
        or (jsonb_typeof(payload -> 'mode') = 'string' and (payload ->> 'mode') in ('post', 'thread', 'rewrite'))
      )
    when 'workflow_request' then
      target_id in ('post', 'blog', 'video_script', 'repurpose', 'weekly_plan')
      and public.phone_workflow_inputs_ok(target_id, payload)
    when 'run_control' then
      public.phone_control_ok('run_control', target_id, payload)
    when 'automation_control' then
      public.phone_control_ok('automation_control', target_id, payload)
    else false
  end, false)
);
