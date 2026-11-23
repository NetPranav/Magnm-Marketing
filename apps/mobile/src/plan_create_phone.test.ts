import { beforeEach, describe, expect, it } from 'vitest';
import { PhoneActionError, enqueueAction, loadActions, validateActionInput } from './actions';
import { SESSION_SECRET_KEY } from './auth';
import type { ContextCacheState } from './cache';
import {
  LAPTOP_STEPS,
  PHONE_STEPS,
  buildDraftInput,
  buildPhoneStepPrompt,
  buildResearchInput,
  buildWorkflowInput,
  fetchTeamRepos,
  parseDraftReply,
  runPhoneStep,
} from './createPhone';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { buildScheduleChange, describeCron, pendingScheduleChange, planFromMirror } from './planPhone';
import type { MirrorRecord, PhoneAction, ProductRulesConfig } from './types';

const UID = 'uid-alpha';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};
const ok = (body: unknown, status = 200): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });

function rec(kind: string, id: string, data: Record<string, unknown>, extra: Partial<MirrorRecord> = {}): MirrorRecord {
  return { id: `m-${kind}-${id}`, owner_id: UID, kind, local_id: id, title: String(data.name ?? data.title ?? id), data, local_updated_at: '2026-11-24T08:00:00+00:00', cached_at: Date.now(), ...extra };
}
const cacheOf = (records: MirrorRecord[]) => ({ ownerUid: UID, mirrorRecords: records, productRules: null, laptop: null }) as unknown as ContextCacheState;

beforeEach(() => window.localStorage.clear());

describe('Plan from the cloud copy', () => {
  const records = [
    rec('schedule', 'market_scan', { name: 'Market scanner', description: 'Scans for developer talk', enabled: true, interval_hours: 6, cron: null, weekly: false, last_run: '2026-11-24T06:00:00+00:00', last_status: 'ok' }),
    rec('schedule', 'weekly_summary', { name: 'Weekly summary', enabled: false, interval_hours: null, cron: '0 9 * * 1', weekly: true }),
    rec('editorial_plan', 'plan', { rows: [{ slot: 'Mon 24', platforms: 'X, LinkedIn', topic: 'Cero launch', status: 'idea' }, { slot: 'Tue 25', platforms: '', topic: 'Confidential item', status: '' }] }),
    rec('brief', 'wf_weekly_plan_1', { kind: 'weekly_plan', status: 'waiting_user', inputs: { week_start: '2026-11-30', platforms: ['linkedin'] }, proposed_slots: [{ date: '2026-11-30', platform: 'linkedin', topic: 'Cero tips', angle: 'short', product_id: 'cero', reason: 'because' }] }, { title: 'weekly_plan: 2026-11-30', local_updated_at: '2026-11-24T09:00:00+00:00' }),
    rec('brief', 'wf_post_1', { kind: 'post', status: 'done', inputs: { brief: 'Announce the release' } }, { title: 'post: Announce the release' }),
    rec('brief', 'wf_conf', { confidential: true }, { title: 'Confidential item' }),
    rec('brief', 'wf_summary', { kind: 'weekly_summary', narrative: 'x' }),
    rec('schedule', 'theirs', { name: 'Other users', enabled: true }, { owner_id: 'uid-beta' }),
  ];

  it('shows schedules, the calendar, the weekly plan and the briefs', () => {
    const plan = planFromMirror(cacheOf(records));
    expect(plan.schedules.map((s) => s.id)).toEqual(['market_scan', 'weekly_summary']);
    expect(plan.schedules[0]).toMatchObject({ name: 'Market scanner', enabled: true, intervalHours: 6 });
    expect(plan.schedules[1]).toMatchObject({ enabled: false, weekly: true, cron: '0 9 * * 1' });
    expect(plan.calendar).toHaveLength(2);
    expect(plan.calendar[0].topic).toBe('Cero launch');
    expect(plan.weeklyPlan?.slots).toHaveLength(1);
    expect(plan.weeklyPlan?.weekStart).toBe('2026-11-30');
    expect(plan.briefs.map((b) => b.id)).toEqual(['wf_weekly_plan_1', 'wf_post_1', 'wf_conf']);
    expect(plan.briefs.find((b) => b.id === 'wf_conf')).toMatchObject({ confidential: true, title: 'Confidential item' });
  });

  it('never uses another accounts records and survives junk', () => {
    const junk = [
      rec('editorial_plan', 'plan', { rows: 'nope' }),
      rec('schedule', 'bad id!', { name: 5, enabled: 'yes', interval_hours: 'many', cron: { x: 1 } }),
      rec('brief', 'wf_x', { proposed_slots: [1, 'a', null, { date: 5 }] }),
    ];
    const plan = planFromMirror(cacheOf([...junk, rec('schedule', 'theirs', { name: 'x' }, { owner_id: 'uid-beta' })]));
    expect(plan.schedules.map((s) => s.id)).not.toContain('theirs');
    expect(plan.calendar).toEqual([]);
    expect(() => planFromMirror(cacheOf([]))).not.toThrow();
  });

  it('describes a time schedule in plain words', () => {
    expect(describeCron('0 9 * * 1')).toBe('Every Monday at 09:00 IST');
    expect(describeCron('46 8 * * *')).toBe('Every day at 08:46 IST');
    expect(describeCron('garbage')).toBe('garbage');
    expect(describeCron(null)).toBeNull();
  });

  it('turns a schedule edit into a queued action and shows the waiting change', () => {
    const input = buildScheduleChange('market_scan', { enabled: false });
    expect(input).toMatchObject({ kind: 'schedule_update', target_id: 'market_scan', payload: { enabled: false } });
    const action = enqueueAction(input, UID, RULES);
    expect(pendingScheduleChange([action], 'market_scan')?.payload).toEqual({ enabled: false });
    expect(pendingScheduleChange([action], 'cmo_daily')).toBeNull();
    expect(() => validateActionInput(buildScheduleChange('market_scan', { interval_hours: 0 }))).toThrow(PhoneActionError);
    const finished = { ...action, state: 'applied' } as PhoneAction;
    expect(pendingScheduleChange([finished], 'market_scan')).toBeNull();
    expect(loadActions(UID)).toHaveLength(1);
  });
});

describe('Create: text-only steps run on the phone', () => {
  async function bridgeFor(reply: string, calls: Array<Record<string, unknown>> = []) {
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req: NativeHttpRequest) => {
        if (req.url.includes('/functions/v1/ai-proxy')) {
          calls.push(JSON.parse(req.body || '{}'));
          return ok({ choices: [{ message: { content: reply } }] });
        }
        if (req.url.includes('/rest/v1/shared_knowledge')) return ok([{ key: 'product_rules', data: RULES, updated_at: null }]);
        return ok([]);
      },
    });
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }));
    return bridge;
  }

  it('offers post, thread and rewrite on the phone and the rest as waiting for the laptop', () => {
    expect(PHONE_STEPS.map((s) => s.id)).toEqual(['post', 'thread', 'rewrite']);
    expect(LAPTOP_STEPS.map((s) => s.id)).toEqual(['post_image', 'blog', 'video_script', 'repurpose', 'weekly_plan', 'research']);
  });

  it('asks the writer for text only, with no tools, and returns a title and a body', async () => {
    const calls: Array<Record<string, unknown>> = [];
    const bridge = await bridgeFor('TITLE: Why we built Cero\n\nCero is out today. Try it.', calls);
    const out = await runPhoneStep({ step: 'post', platform: 'linkedin', brief: 'Announce Cero' }, bridge);
    expect(out).toEqual({ title: 'Why we built Cero', body: 'Cero is out today. Try it.' });
    expect(calls).toHaveLength(1);
    expect(calls[0].tools).toBeUndefined();
    expect(JSON.stringify(calls[0])).toContain('Announce Cero');
  });

  it('builds a prompt per step and keeps the owners text as data inside it', () => {
    expect(buildPhoneStepPrompt({ step: 'thread', platform: 'x', brief: 'Launch' })).toMatch(/thread/i);
    const rewrite = buildPhoneStepPrompt({ step: 'rewrite', platform: 'linkedin', brief: 'Shorter', original: 'Old text' });
    expect(rewrite).toContain('Old text');
    expect(rewrite).toContain('Shorter');
    expect(() => buildPhoneStepPrompt({ step: 'rewrite', platform: 'linkedin', brief: 'Shorter' })).toThrow();
  });

  it('parses odd replies without losing text or inventing a title', () => {
    expect(parseDraftReply('Just a body with no title line at all, quite a long first line that goes on and on and on past the limit.')).toMatchObject({ body: expect.stringContaining('Just a body') });
    expect(parseDraftReply('TITLE: A\n\nB').title).toBe('A');
    expect(parseDraftReply('  ').body).toBe('');
    expect(parseDraftReply('TITLE: ' + 'x'.repeat(400) + '\n\nBody').title.length).toBeLessThanOrEqual(200);
    expect(parseDraftReply('TITLE: bad\u0007title\n\nbody\u0007text')).toEqual({ title: 'badtitle', body: 'bodytext' });
  });

  it('refuses a request naming a confidential product before anything is sent', async () => {
    const calls: Array<Record<string, unknown>> = [];
    const bridge = await bridgeFor('x', calls);
    await expect(runPhoneStep({ step: 'post', platform: 'x', brief: 'Tease ProjectNightfall' }, bridge)).rejects.toThrow(/confidential/i);
    expect(calls).toHaveLength(0);
  });

  it('saves the result as a draft queued for the laptop', () => {
    const input = buildDraftInput({ title: 'Why we built it', body: 'Body text' }, 'linkedin', 'post');
    expect(input).toMatchObject({ kind: 'draft_create', target_id: 'new', payload: { platform: 'linkedin', title: 'Why we built it', body: 'Body text', mode: 'post' } });
    const action = enqueueAction(input, UID, RULES);
    expect(action.state).toBe('waiting');
    expect(() => enqueueAction(buildDraftInput({ title: 'ProjectNightfall', body: 'b' }, 'x', 'post'), UID, RULES)).toThrow(/confidential/i);
  });
});

describe('Create: laptop-only work is queued as a workflow request', () => {
  it('builds each request with exact inputs the laptop and the cloud accept', () => {
    const cases = [
      buildWorkflowInput('post_image', { brief: 'Announce', platforms: ['linkedin', 'x'], repo_id: 'cero-web' }),
      buildWorkflowInput('blog', { topic: 'How it works', repo_id: 'cero-web' }),
      buildWorkflowInput('video_script', { topic: 'Teaser', duration_s: 30, aspect: '9:16' }),
      buildWorkflowInput('repurpose', { content_id: 'cnt_1', targets: ['x_thread'] }),
      buildWorkflowInput('weekly_plan', { platforms: ['linkedin'], posts_per_week: 3, week_start: '2026-11-30' }),
    ];
    expect(cases.map((c) => c.target_id)).toEqual(['post', 'blog', 'video_script', 'repurpose', 'weekly_plan']);
    expect(cases[0].payload).toMatchObject({ with_image: true });
    for (const input of cases) {
      expect(() => validateActionInput(input), JSON.stringify(input)).not.toThrow();
      expect(enqueueAction(input, UID, RULES).state).toBe('waiting');
    }
  });

  it('never carries a field the laptop would refuse', () => {
    const input = buildWorkflowInput('blog', { topic: 'x', repo_id: '', length_words: '5000', product_id: 'secretproj' } as never);
    expect(Object.keys(input.payload).sort()).toEqual(['topic']);
  });

  it('queues research as a request to the researcher agent', () => {
    const input = buildResearchInput('What do developers say about local-first tools?');
    expect(input).toMatchObject({ kind: 'agent_task', target_id: 'researcher' });
    expect(enqueueAction(input, UID, RULES).state).toBe('waiting');
    expect(() => enqueueAction(buildResearchInput('Research ProjectNightfall'), UID, RULES)).toThrow(/confidential/i);
  });

  it('lists only the teams repos that are not confidential, for the picker, and never edits them', async () => {
    const calls: Array<{ method: string; url: string }> = [];
    const bridge = createPreviewNativeBridge({
      httpHandler: async (req) => {
        calls.push({ method: req.method, url: req.url });
        return ok([
          { id: 'cero-web', name: 'Cero web', confidential: false, status: 'released' },
          { id: 'nightfall', name: 'Nightfall core', confidential: true, status: 'secret' },
          { id: 'cero-app', name: 'Cero app', confidential: false, status: 'secret' },
          { id: '../etc', name: 'bad', confidential: false, status: 'released' },
          { id: 'x', name: 5 },
          null,
        ]);
      },
    });
    await bridge.secureStore.set(SESSION_SECRET_KEY, JSON.stringify({ access_token: 'jwt', refresh_token: 'r', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }));
    const repos = await fetchTeamRepos(UID, bridge);
    expect(repos.map((r) => r.id)).toEqual(['cero-web']);
    expect(calls.every((c) => c.method === 'GET')).toBe(true);
    expect(calls[0].url).toContain('/rest/v1/team_repos');
  });
});
