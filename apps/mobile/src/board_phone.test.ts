import { beforeEach, describe, expect, it } from 'vitest';
import { ACTION_KINDS, PhoneActionError, enqueueAction, kindLabel, loadActions, validateActionInput } from './actions';
import { boardFromMirror, buildAutomationControl, buildRunControl, pendingControl } from './boardPhone';
import type { ContextCacheState } from './cache';
import { WORK_MIRROR_QUERIES } from './workMirror';
import type { MirrorRecord, PhoneAction, ProductRulesConfig } from './types';

const UID = 'uid-alpha';
const RUN = 'wf_post_1795464083916_312cad';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [{ id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false }],
};

function rec(kind: string, id: string, data: Record<string, unknown>, extra: Partial<MirrorRecord> = {}): MirrorRecord {
  return { id: `m-${kind}-${id}`, owner_id: UID, kind, local_id: id, title: String(data.title ?? id), data, local_updated_at: '2026-11-24T08:00:00+00:00', cached_at: Date.now(), ...extra };
}

function cache(records: MirrorRecord[], ownerUid: string | null = UID): ContextCacheState {
  return { ownerUid, mirrorRecords: records, productRules: null, laptop: null } as unknown as ContextCacheState;
}

const ITEM = {
  kind: 'workflow', column: 'running', status: 'running', agents: ['writer'], repo_name: 'Alpha app', scope: 'team', age_days: 0,
  confidential: false, automatic: true, detail: { steps: [{ name: 'write_drafts', status: 'done' }], error: '' }, actions: ['pause'],
};

beforeEach(() => window.localStorage.clear());

describe('the pause and resume action kinds', () => {
  it('keeps every older kind and adds run_control and automation_control', () => {
    for (const kind of ['draft_comment', 'draft_posted', 'reminder_done', 'agent_task', 'pitch_create', 'library_upload', 'schedule_update', 'draft_create', 'workflow_request']) {
      expect(ACTION_KINDS).toContain(kind);
    }
    expect(ACTION_KINDS).toContain('run_control');
    expect(ACTION_KINDS).toContain('automation_control');
    expect(kindLabel('run_control')).toBe('Pause or resume a run');
  });

  it('accepts exactly the two shapes', () => {
    expect(validateActionInput(buildRunControl(RUN, 'pause'))).toEqual({ kind: 'run_control', target_id: RUN, payload: { action: 'pause' } });
    expect(validateActionInput(buildAutomationControl('resume'))).toEqual({ kind: 'automation_control', target_id: 'all', payload: { action: 'resume' } });
  });

  it('refuses hostile payloads and targets before anything is queued', () => {
    const bad = [
      { kind: 'run_control', target_id: RUN, payload: {} },
      { kind: 'run_control', target_id: RUN, payload: { action: 'cancel' } },
      { kind: 'run_control', target_id: RUN, payload: { action: 'pause', extra: 1 } },
      { kind: 'run_control', target_id: RUN, payload: { action: ['pause'] } },
      { kind: 'run_control', target_id: 'all', payload: { action: 'pause' } },
      { kind: 'run_control', target_id: '../etc/passwd', payload: { action: 'pause' } },
      { kind: 'run_control', target_id: `${RUN}' OR 1=1`, payload: { action: 'pause' } },
      { kind: 'run_control', target_id: 'WF_POST_1_ABCDEF', payload: { action: 'pause' } },
      { kind: 'automation_control', target_id: RUN, payload: { action: 'pause' } },
      { kind: 'automation_control', target_id: 'all', payload: { action: 'stop' } },
      { kind: 'automation_control', target_id: 'all', payload: { paused: true } },
      { kind: 'automation_control', target_id: 'all', payload: [] },
    ];
    for (const input of bad) {
      expect(() => validateActionInput(input as never), JSON.stringify(input)).toThrow(PhoneActionError);
    }
  });

  it('queues a pause with no confidential rules needed, once, for this account only', () => {
    const a = enqueueAction(buildRunControl(RUN, 'pause'), UID, null);
    expect(a.state).toBe('waiting');
    expect(loadActions(UID)).toHaveLength(1);
    expect(loadActions('uid-beta')).toHaveLength(0);
    expect(() => enqueueAction({ ...buildRunControl(RUN, 'pause'), targetConfidential: true }, UID, RULES)).toThrow(PhoneActionError);
  });
});

describe('the board from the cloud copy', () => {
  it('asks the cloud for the board and the automation record', () => {
    expect(WORK_MIRROR_QUERIES.map((q) => q.kind)).toEqual(expect.arrayContaining(['board_item', 'automation']));
  });

  it('shows an item with its steps and only offers pause for a running run', () => {
    const board = boardFromMirror(cache([rec('board_item', `run:${RUN}`, ITEM, { title: 'post: Share an update' })]));
    expect(board.items).toHaveLength(1);
    const item = board.items[0];
    expect(item).toMatchObject({ runId: RUN, column: 'running', repoName: 'Alpha app', scope: 'team', canPause: true, canResume: false, automatic: true });
    expect(item.steps).toEqual([{ name: 'write_drafts', status: 'done' }]);
    expect(board.filters).toEqual({ agents: ['writer'], repos: ['Alpha app'], kinds: ['workflow'] });
  });

  it('shows a confidential stub as a stub with no actions and no filter entry', () => {
    const board = boardFromMirror(
      cache([rec('board_item', `run:${RUN}`, { ...ITEM, confidential: true, repo_name: 'Moonshot', agents: ['writer'] }, { title: 'Moonshot launch' })]),
    );
    const item = board.items[0];
    expect(item.title).toBe('Confidential item');
    expect(item).toMatchObject({ runId: null, canPause: false, canResume: false, repoName: '', agents: [] });
    expect(JSON.stringify(board)).not.toContain('Moonshot');
  });

  it('ignores another account, junk ids, junk columns and tampered actions', () => {
    const records = [
      rec('board_item', `run:${RUN}`, ITEM, { owner_id: 'uid-beta' }),
      rec('board_item', 'run:../x', ITEM),
      rec('board_item', 'nope:1', ITEM),
      rec('board_item', 'job:j1', { ...ITEM, column: 'exploding' }),
      rec('board_item', 'job:j2', { ...ITEM, kind: 'job', actions: ['pause', 'cancel', 'retry'] }),
      rec('board_item', 'run:wf_post_1_nothex', { ...ITEM, actions: ['pause'] }),
    ];
    const board = boardFromMirror(cache(records));
    expect(board.items.map((i) => i.id)).toEqual(['job:j2', 'run:wf_post_1_nothex']);
    expect(board.items.every((i) => !i.canPause && i.runId === null)).toBe(true);
  });

  it('strips control characters and caps long text', () => {
    const board = boardFromMirror(cache([rec('board_item', 'job:j1', { ...ITEM, kind: 'job', detail: { error: 'x\u0000y'.repeat(500) } }, { title: 'a'.repeat(900) })]));
    expect(board.items[0].title.length).toBeLessThanOrEqual(200);
    expect(board.items[0].error).not.toContain('\u0000');
    expect(board.items[0].error.length).toBeLessThanOrEqual(160);
  });

  it('reads the automatic-work state: the switch, missed runs, the pipeline and a confidential last run', () => {
    const state = (lastRun: unknown) =>
      rec('automation', 'state', {
        paused: true, paused_since: '2026-11-24T08:00:00+00:00', paused_by: 'phone',
        missed: [{ id: 'market_scan', name: 'Market scan', due_at: 'x' }, { id: '../bad', name: 'Bad', due_at: 'x' }],
        pipeline: { enabled: true, cron: '30 10 * * *', kinds: ['post'], skip_recent_days: 7, last_run: lastRun },
      });
    const open = boardFromMirror(cache([state({ status: 'ready', started_at: 't', confidential: false, repo_name: 'Alpha app', scope: 'team', why: 'Random pick.' })])).automation;
    expect(open).toMatchObject({ paused: true, pausedBy: 'phone', pipeline: { enabled: true, kinds: ['post'] } });
    expect(open?.missed).toEqual([{ id: 'market_scan', name: 'Market scan', dueAt: 'x' }]);
    expect(open?.pipeline.lastRun?.repoName).toBe('Alpha app');
    const hidden = boardFromMirror(cache([state({ status: 'ready', started_at: 't', confidential: true, repo_name: 'Moonshot' })])).automation;
    expect(JSON.stringify(hidden)).not.toContain('Moonshot');
    expect(boardFromMirror(cache([rec('automation', 'state', { paused: true }, { owner_id: 'uid-beta' })])).automation).toBeNull();
  });

  it('finds the control still waiting for the laptop', () => {
    const waiting: PhoneAction = {
      id: '99999999-9999-4999-8999-999999999999', kind: 'run_control', target_id: RUN, payload: { action: 'pause' }, label: null,
      created_at: 't', owner_uid: UID, sent: false, state: 'waiting', result: null,
    };
    expect(pendingControl([waiting], 'run_control', RUN)).toBe(waiting);
    expect(pendingControl([{ ...waiting, state: 'applied' }], 'run_control', RUN)).toBeNull();
    expect(pendingControl([waiting], 'automation_control', 'all')).toBeNull();
  });
});
