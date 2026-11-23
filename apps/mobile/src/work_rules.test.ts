import { beforeEach, describe, expect, it } from 'vitest';
import { ACTION_KINDS, PhoneActionError, enqueueAction, kindLabel, loadActions, validateActionInput } from './actions';
import type { ProductRulesConfig } from './types';

const UID = 'uid-alpha';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};
const UPLOAD_ID = '123e4567-e89b-42d3-a456-426614174000';

type Input = Parameters<typeof validateActionInput>[0];

beforeEach(() => window.localStorage.clear());

describe('the new phone action kinds', () => {
  it('keeps every older kind and adds the four new ones', () => {
    for (const kind of ['draft_comment', 'draft_posted', 'reminder_done', 'agent_task', 'pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link']) {
      expect(ACTION_KINDS).toContain(kind);
    }
    for (const kind of ['library_upload', 'schedule_update', 'draft_create', 'workflow_request']) {
      expect(ACTION_KINDS).toContain(kind);
      expect(kindLabel(kind as never)).not.toBe('Reminder done');
    }
  });

  it('accepts each new kind with its exact shape', () => {
    const good: Input[] = [
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'brand' } },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'recording', description: 'Demo clip' } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: false } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { interval_hours: 12 } },
      { kind: 'schedule_update', target_id: 'cmo_daily', payload: { time: '08:30', day: '*', enabled: true } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'linkedin', title: 'Why we built it', body: 'A short post.\n\nMore.', mode: 'post' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 'Thread', body: '1/ Hello' } },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'Announce the release', platforms: ['linkedin', 'x'], with_image: true, repo_id: 'cero-web' } },
      { kind: 'workflow_request', target_id: 'blog', payload: { topic: 'How the phone and laptop share work' } },
      { kind: 'workflow_request', target_id: 'video_script', payload: { topic: 'Teaser', duration_s: 30, aspect: '9:16' } },
      { kind: 'workflow_request', target_id: 'repurpose', payload: { content_id: 'cnt_1', targets: ['x_thread', 'linkedin'] } },
      { kind: 'workflow_request', target_id: 'weekly_plan', payload: { platforms: ['linkedin'], posts_per_week: 3, week_start: '2026-11-30' } },
    ];
    for (const input of good) {
      expect(() => validateActionInput(input), JSON.stringify(input)).not.toThrow();
      const clean = validateActionInput(input);
      expect(clean.kind).toBe(input.kind);
      expect(clean.target_id).toBe(input.target_id);
    }
  });

  it('refuses hostile or malformed payloads', () => {
    const bad: Input[] = [
      { kind: 'library_upload', target_id: 'not-a-uuid', payload: { category: 'brand' } },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: {} },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'weapons' } },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'brand', path: '/etc/passwd' } as never },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'brand', description: 'a\nb' } },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'brand', description: 'x'.repeat(301) } },
      { kind: 'library_upload', target_id: `../${UPLOAD_ID}`, payload: { category: 'brand' } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: {} },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: 'yes' as never } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { interval_hours: 0 } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { interval_hours: 721 } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { interval_hours: 2.5 } },
      { kind: 'schedule_update', target_id: 'cmo_daily', payload: { time: '25:00' } },
      { kind: 'schedule_update', target_id: 'cmo_daily', payload: { day: '7' } },
      { kind: 'schedule_update', target_id: 'market_scan', payload: { command: 'rm -rf /' } as never },
      { kind: 'schedule_update', target_id: '../market_scan', payload: { enabled: true } },
      { kind: 'schedule_update', target_id: 'Market Scan', payload: { enabled: true } },
      { kind: 'draft_create', target_id: 'cnt_existing', payload: { platform: 'x', title: 't', body: 'b' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'tiktok' as never, title: 't', body: 'b' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: '', body: 'b' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 'a\nb', body: 'b' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 't', body: 'x'.repeat(6001) } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 't', body: 'b', status: 'posted' } as never },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 't', body: 'b', mode: 'publish' as never } },
      { kind: 'workflow_request', target_id: 'research', payload: { topic: 'x' } },
      { kind: 'workflow_request', target_id: 'post', payload: {} },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'x', platforms: ['tiktok'] as never } },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'x', product_id: 'secretproj' } as never },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'x', repo_id: '../etc' } },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'x', repo_id: '/srv/me/code' } },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'y'.repeat(2001) } },
      { kind: 'workflow_request', target_id: 'blog', payload: { topic: 'x', length_words: '99999' } as never },
      { kind: 'workflow_request', target_id: 'video_script', payload: { topic: 'x', duration_s: 45 as never } },
      { kind: 'workflow_request', target_id: 'repurpose', payload: { targets: ['x_thread'] } },
      { kind: 'workflow_request', target_id: 'repurpose', payload: { content_id: 'cnt_1', targets: ['../x'] } },
      { kind: 'workflow_request', target_id: 'weekly_plan', payload: { posts_per_week: 15 } },
      { kind: 'workflow_request', target_id: 'weekly_plan', payload: { week_start: 'tomorrow' } },
      { kind: 'workflow_request', target_id: 'weekly_plan', payload: { week_start: '2026-13-40' } },
    ];
    for (const input of bad) {
      expect(() => validateActionInput(input), JSON.stringify(input).slice(0, 80)).toThrow(PhoneActionError);
      expect(() => enqueueAction(input, UID, RULES)).toThrow(PhoneActionError);
    }
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('never queues text that names a confidential product, and fails closed without rules', () => {
    const texts: Input[] = [
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 'ProjectNightfall plan', body: 'b' } },
      { kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 't', body: 'About ProjectNightfall.' } },
      { kind: 'workflow_request', target_id: 'post', payload: { brief: 'Tease ProjectNightfall' } },
      { kind: 'workflow_request', target_id: 'blog', payload: { topic: 'ProjectNightfall deep dive' } },
      { kind: 'library_upload', target_id: UPLOAD_ID, payload: { category: 'brand', description: 'ProjectNightfall mockup' } },
    ];
    for (const input of texts) {
      expect(() => enqueueAction(input, UID, RULES)).toThrow(/confidential/i);
      expect(() => enqueueAction(input, UID, null)).toThrow(/rules/i);
    }
    expect(loadActions(UID)).toHaveLength(0);
    expect(window.localStorage.getItem('magnm.mobile.phoneActions.v1') ?? '').not.toContain('Nightfall');
  });

  it('queues a valid action as waiting and keeps it across a reload', () => {
    const action = enqueueAction({ kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: false }, label: 'Pause market scan' }, UID, RULES);
    expect(action.state).toBe('waiting');
    expect(action.sent).toBe(false);
    expect(loadActions(UID).map((a) => a.kind)).toEqual(['schedule_update']);
    expect(loadActions('uid-beta')).toEqual([]);
  });

  it('drops stored items of the new kinds that fail the checks', () => {
    const good = enqueueAction({ kind: 'draft_create', target_id: 'new', payload: { platform: 'x', title: 't', body: 'b' } }, UID, RULES);
    const stored = JSON.parse(window.localStorage.getItem('magnm.mobile.phoneActions.v1') || '[]');
    window.localStorage.setItem(
      'magnm.mobile.phoneActions.v1',
      JSON.stringify([
        ...stored,
        { ...good, id: '11111111-1111-4111-8111-111111111111', payload: { platform: 'x', title: 't', body: 'b', files: ['/etc/passwd'] } },
        { ...good, id: '22222222-2222-4222-8222-222222222222', kind: 'workflow_request', target_id: 'research', payload: { topic: 'x' } },
      ]),
    );
    expect(loadActions(UID).map((a) => a.id)).toEqual([good.id]);
  });
});
