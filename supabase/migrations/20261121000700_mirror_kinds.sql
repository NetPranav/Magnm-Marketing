-- Extend mirror_records.kind check to allow chat_session and team_message.

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
      'task'
    )
  );
