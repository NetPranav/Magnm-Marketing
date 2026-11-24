import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { enqueueAction, loadActions } from './actions';
import { ALL_CLASSIFIED_TOOLS, decideRoute, getConfiguredAgent } from './agentTools';
import { DraftsTab } from './components/DraftsTab';
import { buildRenderVideoInput } from './createPhone';
import type { DraftItem, ProductRulesConfig } from './types';

const UID = 'uid-alpha';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [{ id: 'quillby', name: 'Quillby', status: 'released', show: 'everything', confidential: false }],
};

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => cleanup());

describe('rendering a video from the phone', () => {
  it('the video producer has render_video and the phone knows it needs the laptop', () => {
    expect(getConfiguredAgent('video-producer')?.tools).toContain('render_video');
    expect(ALL_CLASSIFIED_TOOLS).toContain('render_video');
    expect(decideRoute('video-producer', 'Render the finished video for cnt_20261125_ab12cd').route).toBe('laptop');
    expect(decideRoute('video-producer', 'Render the finished video for cnt_20261125_ab12cd').tools).toContain('render_video');
    expect(decideRoute('video-producer', 'Suggest a hook for a reel').route).toBe('phone');
    expect(decideRoute('writer', 'Render the finished video').tools).not.toContain('render_video');
  });

  it('builds an agent task for the video producer that names the script and the tool', () => {
    const input = buildRenderVideoInput('cnt_20261125_ab12cd', 'Quillby 15s reel');
    expect(input.kind).toBe('agent_task');
    expect(input.target_id).toBe('video-producer');
    expect((input.payload as { message: string }).message).toContain('cnt_20261125_ab12cd');
    expect((input.payload as { message: string }).message).toContain('render_video');
    expect(input.label).toContain('Quillby 15s reel');
    expect(() => buildRenderVideoInput('../etc', 'x')).toThrow();
    expect(() => buildRenderVideoInput('  ', 'x')).toThrow();
  });

  it('is accepted by the queue and waits for the laptop', () => {
    const action = enqueueAction(buildRenderVideoInput('cnt_20261125_ab12cd', 'Reel'), UID, RULES);
    expect(action.kind).toBe('agent_task');
    expect(loadActions(UID).map((a) => a.id)).toContain(action.id);
  });
});

function script(overrides: Partial<DraftItem> = {}): DraftItem {
  return {
    id: 'cnt_vid_1', type: 'script', platform: 'instagram', status: 'ready', title: 'Quillby 15s reel', body: 'Scene 1',
    current_version: 1, files: [], versions: [], comments: [], ...overrides,
  };
}

function tab(draft: DraftItem, extra: Record<string, unknown> = {}) {
  const onRenderVideo = vi.fn().mockResolvedValue(true);
  render(
    <DraftsTab
      drafts={[draft]}
      selectedDraft={draft}
      laptopReachable={false}
      savedCopyLabel={null}
      canQueueOffline={false}
      canQueue
      busyAction={null}
      errorMessage={null}
      onSelectDraft={() => undefined}
      onCloseDraft={() => undefined}
      onAddComment={async () => true}
      onMarkPosted={async () => true}
      onCopyText={async () => true}
      onShareOut={async () => true}
      onCreateOfflineItem={async () => true}
      onRenderVideo={onRenderVideo}
      {...extra}
    />,
  );
  return onRenderVideo;
}

describe('the Finished video card in a script draft', () => {
  it('queues a render for the laptop and then shows it is queued', async () => {
    const onRenderVideo = tab(script());
    fireEvent.click(screen.getByRole('button', { name: 'Render video on laptop' }));
    await waitFor(() => expect(onRenderVideo).toHaveBeenCalledWith('cnt_vid_1', 'Quillby 15s reel'));
    await screen.findByRole('button', { name: 'Queued for your laptop' });
    expect((screen.getByRole('button', { name: 'Queued for your laptop' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('says Render again and lists the rendered video under Files with Get on phone', () => {
    tab(
      script({
        files: [
          { kind: 'video', path: 'outputs/video/cnt_vid_1/video-9x16.mp4' },
          { kind: 'poster', path: 'outputs/video/cnt_vid_1/poster.jpg' },
        ],
      }),
    );
    expect(screen.getByRole('button', { name: 'Render again on laptop' })).toBeTruthy();
    expect(screen.getByText('video-9x16.mp4')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Get on phone' }).length).toBeGreaterThan(0);
  });

  it('is not shown for other drafts, and asks to sign in when nothing can be queued', () => {
    tab(script({ type: 'post' }));
    expect(screen.queryByText('Finished video')).toBeNull();
    cleanup();
    tab(script(), { canQueue: false });
    expect((screen.getByRole('button', { name: 'Render video on laptop' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Sign in to ask your laptop to render the video/)).toBeTruthy();
  });
});
