import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { BoardTab } from './components/BoardTab';
import type { ContextCacheState } from './cache';
import type { MirrorRecord, PhoneAction } from './types';

const UID = 'uid-alpha';
const RUN = 'wf_post_1795464083916_312cad';

function rec(kind: string, id: string, data: Record<string, unknown>, title = id): MirrorRecord {
  return { id: `m-${kind}-${id}`, owner_id: UID, kind, local_id: id, title, data, local_updated_at: '2026-11-24T08:00:00+00:00', cached_at: Date.now() };
}

const ITEM = {
  kind: 'workflow', column: 'running', status: 'running', agents: ['writer'], repo_name: 'Alpha app', scope: 'personal', age_days: 0,
  confidential: false, automatic: false, detail: { steps: [{ name: 'write_drafts', status: 'done' }, { name: 'review', status: 'current' }] }, actions: ['pause'],
};

function cache(records: MirrorRecord[]): ContextCacheState {
  return { ownerUid: UID, mirrorRecords: records, productRules: null, laptop: null } as unknown as ContextCacheState;
}

function setup(records: MirrorRecord[], actions: PhoneAction[] = []) {
  const queue = vi.fn(async () => true);
  render(<BoardTab signedIn cache={cache(records)} phoneActions={actions} onQueueAction={queue} />);
  return queue;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => cleanup());

describe('Board on the phone', () => {
  it('groups items by column and shows the repo, scope and steps', () => {
    setup([rec('board_item', `run:${RUN}`, ITEM, 'post: Share an update')]);
    const running = screen.getByRole('region', { name: 'Running' });
    fireEvent.click(within(running).getByText('post: Share an update'));
    expect(within(running).getByText(/Alpha app \(personal\)/)).toBeTruthy();
    expect(within(running).getByRole('list', { name: 'Steps' })).toBeTruthy();
  });

  it('queues a pause for a run and shows it as waiting for the laptop', () => {
    const queue = setup([rec('board_item', `run:${RUN}`, ITEM, 'post: Share an update')]);
    fireEvent.click(screen.getByText('post: Share an update'));
    fireEvent.click(screen.getByRole('button', { name: /Pause post: Share an update/ }));
    expect(queue).toHaveBeenCalledWith(expect.objectContaining({ kind: 'run_control', target_id: RUN, payload: { action: 'pause' } }), expect.any(String));
  });

  it('disables a second pause while the first is waiting', () => {
    const waiting: PhoneAction = {
      id: '99999999-9999-4999-8999-999999999999', kind: 'run_control', target_id: RUN, payload: { action: 'pause' }, label: null,
      created_at: 't', owner_uid: UID, sent: false, state: 'waiting', result: null,
    };
    setup([rec('board_item', `run:${RUN}`, ITEM, 'post: Share an update')], [waiting]);
    fireEvent.click(screen.getByText('post: Share an update'));
    expect(screen.getByText(/Waiting for the laptop: pause/)).toBeTruthy();
    expect((screen.getByRole('button', { name: /Pause post: Share an update/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers Resume for a paused run and nothing for a confidential stub', () => {
    const queue = setup([
      rec('board_item', `run:${RUN}`, { ...ITEM, column: 'paused', status: 'paused', actions: ['resume'] }, 'post: Share an update'),
      rec('board_item', 'run:wf_blog_2_abcdef', { ...ITEM, confidential: true, actions: [] }, 'Moonshot plan'),
    ]);
    expect(screen.queryByText(/Moonshot/)).toBeNull();
    fireEvent.click(screen.getByText('post: Share an update'));
    fireEvent.click(screen.getByRole('button', { name: /Resume post: Share an update/ }));
    expect(queue).toHaveBeenCalledWith(expect.objectContaining({ payload: { action: 'resume' } }), expect.any(String));
    fireEvent.click(screen.getByText('Confidential item'));
    expect(screen.getByText(/stays on the laptop/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Pause Confidential/ })).toBeNull();
  });

  it('flips the global switch through the queue and lists missed runs without running them', () => {
    const state = rec('automation', 'state', {
      paused: true, paused_by: 'laptop', missed: [{ id: 'market_scan', name: 'Market scan', due_at: 'x' }],
      pipeline: { enabled: false, kinds: ['post'], skip_recent_days: 7, last_run: null },
    });
    const queue = setup([state]);
    expect(screen.getByText(/Paused from the laptop/)).toBeTruthy();
    expect(screen.getByText(/Missed while paused/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Resume automatic work' }));
    expect(queue).toHaveBeenCalledWith(expect.objectContaining({ kind: 'automation_control', target_id: 'all', payload: { action: 'resume' } }), expect.any(String));
  });

  it('filters by agent and kind', () => {
    setup([
      rec('board_item', `run:${RUN}`, ITEM, 'post: Share an update'),
      rec('board_item', 'job:j1', { ...ITEM, kind: 'job', agents: [], repo_name: '', actions: [] }, 'Market scan'),
    ]);
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'job' } });
    expect(screen.queryByText('post: Share an update')).toBeNull();
    expect(screen.getByText('Market scan')).toBeTruthy();
  });

  it('says so when the board is empty', () => {
    setup([]);
    expect(screen.getByText(/board is empty/)).toBeTruthy();
  });
});
