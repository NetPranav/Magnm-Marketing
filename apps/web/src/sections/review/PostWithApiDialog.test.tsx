import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { STUDIO_EVENT_NAMES } from '../../events';
import type { ContentItem, PostingAttemptRecord } from '../../types';
import { PostWithApiDialog } from './PostWithApiDialog';

const item = { id: 'c1', platform: 'linkedin', status: 'draft' } as ContentItem;
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
const preview = (platform: string, media: unknown[] = []) => ({
  attempt_id: `a-${platform}`,
  content_id: 'c1',
  confirmation_id: `c-${platform}`,
  payload_hash: 'h',
  platform,
  account: { account_id: `${platform}-account` },
  final_text: 'Cero is live.',
  media,
  ai_generated: false,
  warnings: [],
  cost: { summary: 'cost' },
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('PostWithApiDialog review regressions', () => {
  it('never offers to post to a platform other than the selected one', async () => {
    let release: () => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const { platform } = JSON.parse(String(init?.body)) as { platform: string };
      if (platform === 'linkedin') await new Promise<void>((resolve) => (release = resolve));
      return json(preview(platform));
    });
    render(<PostWithApiDialog open item={item} onClose={() => {}} onPosted={() => {}} />);
    await waitFor(() => expect(vi.mocked(globalThis.fetch)).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Target platform'), { target: { value: 'x' } });
    await screen.findByText('(x-account)');
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('(linkedin-account)')).toBeNull();
  });

  it('keeps an unknown result visible after newer previews and posts elsewhere', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(preview('linkedin')));
    const attempts = [
      { id: '3', platform: 'linkedin', status: 'prepared' },
      { id: '2', platform: 'x', status: 'posted' },
      { id: '1', platform: 'linkedin', status: 'unknown' },
    ] as PostingAttemptRecord[];
    render(<PostWithApiDialog open item={item} attempts={attempts} onClose={() => {}} onPosted={() => {}} />);
    expect(await screen.findByText(/I checked the platform/)).toBeTruthy();
  });

  it('shows the image that will be published and is honest about the AI label', async () => {
    const uri = 'data:image/png;base64,iVBORw0KGgo=';
    const media = [{ path: 'outputs/a.png', bytes: 8, sha256: 's', mime_type: 'image/png', preview_data_uri: uri }];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(preview('linkedin', media)));
    render(<PostWithApiDialog open item={item} onClose={() => {}} onPosted={() => {}} />);
    expect(((await screen.findByAltText('Image that will be published')) as HTMLImageElement).src).toBe(uri);
    expect(screen.getByText(/LinkedIn's API has no AI label/)).toBeTruthy();
  });

  it('subscribes to posting updates', () => {
    expect(STUDIO_EVENT_NAMES as readonly string[]).toContain('posting.updated');
  });
});
