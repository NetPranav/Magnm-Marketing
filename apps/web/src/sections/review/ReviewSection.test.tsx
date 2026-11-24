import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { ReviewSection } from './ReviewSection';
import type { ReviewPayload } from '../../types';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const SAMPLE_BODY = 'Plan costs ₹12,500 per release. Second sentence covers local AI checks.';

function makeReviewPayload(overrides: Partial<ReviewPayload> = {}): ReviewPayload {
  return {
    item: {
      id: 'cnt-rev-1',
      type: 'post',
      platform: 'linkedin',
      title: 'Cero v2.2 release',
      status: 'draft',
      product_id: 'cero',
      created_by: 'writer',
      current_version: 2,
      created_at: '2026-11-19T08:00:00Z',
      updated_at: '2026-11-19T09:00:00Z',
      files: [
        { kind: 'card', path: 'outputs/posts/cnt-rev-1/4x5.png', alt_text: 'Cero card' },
        { kind: 'video', path: 'outputs/posts/cnt-rev-1/demo.mp4' },
      ],
    },
    body: SAMPLE_BODY,
    current_version: 2,
    versions: [
      {
        number: 1,
        version: 1,
        created_by: 'writer',
        notes: 'Initial draft',
        created_at: '2026-11-19T08:00:00Z',
      },
      {
        number: 2,
        version: 2,
        created_by: 'user',
        notes: 'Added pricing check',
        created_at: '2026-11-19T09:00:00Z',
      },
    ],
    comments: [
      {
        id: 'cmt-1',
        content_id: 'cnt-rev-1',
        version: 2,
        kind: 'text',
        anchor: { start: 0, end: 17, quote: 'Plan costs ₹12,500' },
        body: 'Verify pricing figure.',
        author: 'reviewer',
        status: 'open',
        created_at: '2026-11-19T09:05:00Z',
        replies: [
          {
            id: 'cmt-1-r1',
            content_id: 'cnt-rev-1',
            version: 2,
            kind: 'text',
            anchor: {},
            body: 'Confirmed from pitch sheet.',
            author: 'writer',
            parent_id: 'cmt-1',
            status: 'open',
            created_at: '2026-11-19T09:06:00Z',
            replies: [],
          },
        ],
      },
      {
        id: 'cmt-2',
        content_id: 'cnt-rev-1',
        version: 1,
        kind: 'text',
        anchor: { start: 0, end: 10, quote: 'Old phrase', orphaned: true },
        body: 'Old anchor note',
        author: 'user',
        status: 'open',
        created_at: '2026-11-19T08:30:00Z',
        replies: [],
      },
    ],
    suggestions: [
      {
        id: 'sug-1',
        content_id: 'cnt-rev-1',
        base_version: 2,
        start: 33,
        end: 69,
        original: 'Second sentence covers local AI checks.',
        replacement: 'Local AI checks run before every release.',
        author: 'writer',
        reason: 'ai_action:rephrase',
        status: 'pending',
        created_at: '2026-11-19T09:10:00Z',
      },
    ],
    checklist: [
      {
        item_key: 'auto:zero_emoji',
        label: 'Zero-emoji policy',
        source: 'auto',
        checked: true,
      },
      {
        item_key: 'platform:see_more',
        label: 'First line works without See more (210 chars)',
        source: 'platform',
        checked: false,
      },
      {
        item_key: 'manual:ceo',
        label: 'CEO final signoff',
        source: 'manual',
        checked: false,
      },
    ],
    warnings: [
      {
        code: 'platform_hook',
        severity: 'warn',
        message: 'Check opening hook length.',
      },
    ],
    platform_check: {
      platform: 'linkedin',
      limits: { platform: 'linkedin', max_chars: 3000, hook_chars: 210, max_hashtags: 5 },
      warnings: [],
      ok: true,
      char_count: SAMPLE_BODY.length,
      word_count: 11,
      hashtag_count: 0,
    },
    product: {
      id: 'cero',
      name: 'Cero',
      status: 'released',
      show: 'everything',
      confidential: false,
    },
    confidential: false,
    ...overrides,
  };
}

const MOCK_ROSTER = [
  { id: 'writer', name: 'Writer', code: 'WRT', role: 'Drafts', status: 'idle' },
  { id: 'reviewer', name: 'Reviewer', code: 'REV', role: 'Checks', status: 'idle' },
];

describe('ReviewSection', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('groups drafts and ready items on the list screen and opens a workspace', async () => {
    const onNavigate = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.startsWith('/api/content?')) {
        return jsonResponse([
          {
            id: 'cnt-d1',
            type: 'post',
            platform: 'linkedin',
            title: 'Draft announcement',
            status: 'draft',
            open_comments_count: 3,
            warnings_count: 1,
            created_at: '2026-11-19T08:00:00Z',
            updated_at: '2026-11-19T09:00:00Z',
          },
          {
            id: 'cnt-r1',
            type: 'thread',
            platform: 'x',
            title: 'Ready launch thread',
            status: 'ready',
            open_comments_count: 0,
            warnings_count: 0,
            created_at: '2026-11-19T07:00:00Z',
            updated_at: '2026-11-19T08:30:00Z',
          },
        ]);
      }
      return jsonResponse({});
    });

    render(<ReviewSection contentId={null} onNavigate={onNavigate} />);

    await waitFor(() => {
      expect(screen.getByText('Draft announcement')).toBeTruthy();
    });

    const draftsSection = screen.getByRole('region', { name: 'Drafts' });
    const readySection = screen.getByRole('region', { name: 'Ready' });

    expect(within(draftsSection).getByText('Draft announcement')).toBeTruthy();
    expect(within(draftsSection).getByText('post · linkedin')).toBeTruthy();
    expect(within(draftsSection).getByText('3 open comments')).toBeTruthy();
    expect(within(draftsSection).getByText('1 warning')).toBeTruthy();

    expect(within(readySection).getByText('Ready launch thread')).toBeTruthy();
    expect(within(readySection).getByText('thread · x')).toBeTruthy();

    fireEvent.click(within(draftsSection).getByText('Draft announcement'));
    expect(onNavigate).toHaveBeenCalledWith('cnt-d1');
  });

  it('renders comments, suggestions, checklist, versions, and handles suggestions, apply_all, diff, and posting kit', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    const payload = makeReviewPayload();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1' && (!init?.method || init.method === 'GET')) {
        return jsonResponse(payload);
      }
      if (url === '/api/roster') {
        return jsonResponse(MOCK_ROSTER);
      }
      if (url === '/api/review/cnt-rev-1/suggestions/sug-1/decide' && init?.method === 'POST') {
        return jsonResponse({ content_id: 'cnt-rev-1', version_created: true, accepted: [], rejected: [] });
      }
      if (url === '/api/review/cnt-rev-1/suggestions/decide_all' && init?.method === 'POST') {
        return jsonResponse({ content_id: 'cnt-rev-1', version_created: false, accepted: [], rejected: [] });
      }
      if (url === '/api/review/cnt-rev-1/apply_all' && init?.method === 'POST') {
        return jsonResponse({
          job_id: 'job-apply-1',
          job: { id: 'job-apply-1', name: 'review_action', status: 'queued', progress: 0, message: 'Queued' },
        });
      }
      if (url === '/api/review/cnt-rev-1/diff?from=1&to=2') {
        return jsonResponse({
          content_id: 'cnt-rev-1',
          from_version: 1,
          to_version: 2,
          ops: [
            { op: 'equal', text: 'Plan costs ' },
            { op: 'delete', text: '₹10,000' },
            { op: 'insert', text: '₹12,500' },
          ],
        });
      }
      if (url === '/api/review/cnt-rev-1/posting_kit' && init?.method === 'POST') {
        return jsonResponse({
          content_id: 'cnt-rev-1',
          path: 'outputs/content/cnt-rev-1/POSTING-KIT.md',
          text: '# Posting Kit\n\nPlan costs ₹12,500 per release.',
          files: ['outputs/content/cnt-rev-1/POSTING-KIT.md', 'outputs/posts/cnt-rev-1/4x5.png'],
        });
      }
      return jsonResponse({});
    });

    render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Cero v2.2 release')).toBeTruthy();
    });

    // Comments, suggestions, checklist, versions rendered from /api/review/<id>
    expect(screen.getByText('Verify pricing figure.')).toBeTruthy();
    expect(screen.getByText('Confirmed from pitch sheet.')).toBeTruthy();
    expect(screen.getByText(/Orphaned/)).toBeTruthy();
    expect(screen.getByText('ai_action:rephrase')).toBeTruthy();
    expect(screen.getByText('Zero-emoji policy')).toBeTruthy();
    expect(screen.getByText('Initial draft')).toBeTruthy();
    expect(screen.getByText('Added pricing check')).toBeTruthy();

    // Accept and Reject suggestion calls
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/suggestions/sug-1/decide',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ decision: 'accept' }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Reject all' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/suggestions/decide_all',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ decision: 'reject' }),
        }),
      );
    });

    // Apply all with both modes (new version and as suggestions)
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/apply_all',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ as_suggestions: false, agent: 'writer' }),
        }),
      );
    });

    fireEvent.change(screen.getByLabelText('Apply all mode'), {
      target: { value: 'suggestions' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/apply_all',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ as_suggestions: true, agent: 'writer' }),
        }),
      );
    });

    // Diff rendering
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    await waitFor(() => {
      expect(screen.getByTestId('version-diff-view')).toBeTruthy();
    });
    const diffView = screen.getByTestId('version-diff-view');
    expect(diffView.querySelector('del')?.textContent).toBe('₹10,000');
    expect(diffView.querySelector('ins')?.textContent).toBe('₹12,500');

    // Posting kit build + copy button
    fireEvent.click(screen.getByRole('button', { name: 'Build posting kit' }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Copy title' }));
    expect(writeText).toHaveBeenCalledWith('Cero v2.2 release');
    expect(screen.getByRole('link', { name: /POSTING-KIT\.md/ })).toBeTruthy();
  });

  it('posts exact text anchor offsets including multi-byte rupee sign, image pins, and video current time', async () => {
    const payload = makeReviewPayload({ suggestions: [], comments: [] });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1') return jsonResponse(payload);
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url === '/api/review/cnt-rev-1/comments' && init?.method === 'POST') {
        return jsonResponse({
          id: 'cmt-new',
          content_id: 'cnt-rev-1',
          version: 2,
          kind: 'general',
          anchor: {},
          body: 'ok',
          author: 'user',
          status: 'open',
          created_at: '2026-11-19T09:30:00Z',
        });
      }
      return jsonResponse({});
    });

    render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId('review-body-view')).toBeTruthy();
    });

    // 1. Select text spanning the rupee sign directly in the annotated Review view
    const bodyView = screen.getByTestId('review-body-view');
    const firstSpan = bodyView.querySelector('[data-raw-start="0"]');
    expect(firstSpan?.firstChild).toBeTruthy();
    const range = document.createRange();
    range.setStart(firstSpan!.firstChild!, 0);
    // "Plan costs ₹12,500" -> length 18
    range.setEnd(firstSpan!.firstChild!, 18);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    fireEvent.mouseUp(bodyView);

    const toolbar = screen.getByRole('toolbar', { name: 'Selection actions' });
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Comment' }));

    const composer = screen.getByRole('textbox', { name: 'New comment composer' });
    fireEvent.change(composer, { target: { value: 'Check INR format' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/comments',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            kind: 'text',
            anchor: { start: 0, end: 18, quote: 'Plan costs ₹12,500' },
            body: 'Check INR format',
            parent_id: null,
          }),
        }),
      );
    });

    // Also verify Edit text saves a new version
    fireEvent.click(screen.getByRole('button', { name: 'Edit text' }));
    const editor = screen.getByLabelText('Editing v2') as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: 'Plan costs ₹15,000 per release.' } });
    fireEvent.change(screen.getByLabelText('Version note'), { target: { value: 'Updated price' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save new version' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/versions',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            body: 'Plan costs ₹15,000 per release.',
            notes: 'Updated price',
          }),
        }),
      );
    });

    // 2. Click image to drop a normalized pin
    const img = screen.getByAltText('Cero card') as HTMLImageElement;
    vi.spyOn(img, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 50,
      width: 200,
      height: 100,
      right: 300,
      bottom: 150,
      x: 100,
      y: 50,
      toJSON: () => ({}),
    });
    fireEvent.click(img, { clientX: 150, clientY: 125 });

    fireEvent.change(screen.getByRole('textbox', { name: 'New comment composer' }), {
      target: { value: 'Pin note here' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/comments',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            kind: 'image',
            anchor: { file: 'outputs/posts/cnt-rev-1/4x5.png', x: 0.25, y: 0.75 },
            body: 'Pin note here',
            parent_id: null,
          }),
        }),
      );
    });

    // 3. Video current time comment
    const videoEl = document.querySelector('video') as HTMLVideoElement;
    expect(videoEl).toBeTruthy();
    Object.defineProperty(videoEl, 'currentTime', { configurable: true, writable: true, value: 12 });
    fireEvent.timeUpdate(videoEl);

    const videoBtn = screen.getByRole('button', { name: 'Comment at 0:12' });
    fireEvent.click(videoBtn);
    fireEvent.change(screen.getByRole('textbox', { name: 'New comment composer' }), {
      target: { value: 'Cut pause at 12s' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/comments',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            kind: 'video',
            anchor: { file: 'outputs/posts/cnt-rev-1/demo.mp4', t: 12 },
            body: 'Cut pause at 12s',
            parent_id: null,
          }),
        }),
      );
    });
  });

  it('does not trigger keyboard shortcuts while typing in an input, and shows numbered sub-tabs at 390px', async () => {
    const payload = makeReviewPayload();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1') return jsonResponse(payload);
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url.includes('/comments/cmt-1') && init?.method === 'POST') {
        return jsonResponse({ ...payload.comments[0], status: 'resolved' });
      }
      return jsonResponse({});
    });

    // Simulate 390px mobile viewport
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 390 });
    render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId('review-narrow-layout')).toBeTruthy();
    });

    const tabs = within(screen.getByTestId('review-narrow-layout')).getAllByRole('tab');
    expect(tabs.map((t) => t.textContent?.trim())).toEqual([
      '1 Content',
      '2 Comments',
      '3 Checklist',
      '4 Versions',
      '5 Preview',
    ]);

    // Switch to Comments tab
    fireEvent.click(tabs[1]);
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'New comment composer' })).toBeTruthy();
    });

    // Typing 'e' or 'r' inside an input must NOT fire shortcuts
    const composer = screen.getByRole('textbox', { name: 'New comment composer' });
    fireEvent.keyDown(composer, { key: 'e' });
    fireEvent.keyDown(composer, { key: 'r' });
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/review/cnt-rev-1/comments/cmt-1',
      expect.anything(),
    );
    expect(screen.queryByRole('textbox', { name: 'Reply composer' })).toBeNull();

    // Pressing 'j' then 'e' on document resolves focused comment
    fireEvent.keyDown(document, { key: 'j' });
    fireEvent.keyDown(document, { key: 'e' });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/review/cnt-rev-1/comments/cmt-1',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ status: 'resolved' }),
        }),
      );
    });
  });

  it('gates Send to my phone on paired devices and confidentiality, posting /api/phone/send when allowed', async () => {
    const publicPayload = makeReviewPayload();
    const sendCalls: Array<{ kind: string; id: string }> = [];
    let currentDevices: Array<{ id: string; name: string }> = [{ id: 'pdev-1', name: 'Pixel 9' }];

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1') return jsonResponse(publicPayload);
      if (url === '/api/review/cnt-conf-1') {
        return jsonResponse(
          makeReviewPayload({
            item: { ...publicPayload.item, id: 'cnt-conf-1', title: 'Confidential draft' },
            confidential: true,
          }),
        );
      }
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url === '/api/phone') {
        return jsonResponse({ enabled: true, running: true, devices: currentDevices });
      }
      if (url === '/api/phone/send' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { kind: string; id: string };
        sendCalls.push(body);
        return jsonResponse({ ok: true, route: `#/review/${body.id}` });
      }
      return jsonResponse({});
    });

    const { rerender } = render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);

    const phoneBtn = (await screen.findByRole('button', {
      name: 'Send to my phone',
    })) as HTMLButtonElement;
    await waitFor(() => {
      expect(phoneBtn.disabled).toBe(false);
    });

    fireEvent.click(phoneBtn);
    await waitFor(() => {
      expect(sendCalls).toEqual([{ kind: 'content', id: 'cnt-rev-1' }]);
    });
    expect(await screen.findByText('Sent to your phone.')).toBeTruthy();

    currentDevices = [];
    rerender(<ReviewSection contentId="cnt-conf-1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      const confBtn = screen.getByRole('button', { name: 'Send to my phone' }) as HTMLButtonElement;
      expect(confBtn.disabled).toBe(true);
      expect(confBtn.title).toBe('Confidential items cannot be sent to your phone');
    });
  });

  it('places Post with API after manual posting kit, renders exact preview, blocks Enter confirm and unfocused default, and surfaces unknown status', async () => {
    const payload = makeReviewPayload();
    let confirmCalls = 0;
    let shouldFailUnknown = false;

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1') return jsonResponse(payload);
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url === '/api/phone') return jsonResponse({ enabled: false, running: false, devices: [] });
      if (url.startsWith('/api/posting/attempts')) {
        return jsonResponse({
          attempts: shouldFailUnknown
            ? [
                {
                  id: 'post-unk-1',
                  content_id: 'cnt-rev-1',
                  platform: 'linkedin',
                  payload_hash: 'h1',
                  status: 'unknown',
                  error: 'Post status is unknown; check the platform before trying again.',
                  prepared_at: '2026-11-22T10:00:00Z',
                  expires_at: '2026-11-22T10:05:00Z',
                  updated_at: '2026-11-22T10:01:00Z',
                },
              ]
            : [],
        });
      }
      if (url === '/api/posting/prepare' && init?.method === 'POST') {
        const req = JSON.parse(String(init.body || '{}')) as { ai_generated?: boolean };
        return jsonResponse({
          attempt_id: 'post-1',
          confirmation_id: 'pconf-1',
          payload_hash: 'hash-1',
          expires_at: '2026-11-22T12:05:00Z',
          expires_in_s: 300,
          content_id: 'cnt-rev-1',
          content_version: 2,
          item_status: 'draft',
          platform: 'linkedin',
          account: {
            account_id: 'urn:li:person:abc',
            display_name: 'Jane Doe',
          },
          final_text: SAMPLE_BODY,
          media: [
            {
              kind: 'image',
              source: 'local',
              path: 'outputs/posts/cnt-rev-1/4x5.png',
              bytes: 2048,
              sha256: 'deadbeef01234567',
              mime_type: 'image/png',
              alt_text: 'Cero card',
            },
          ],
          ai_generated: Boolean(req.ai_generated),
          warnings: [{ code: 'product_rule', severity: 'warn', message: 'Check blocked terms.' }],
          cost: { estimated_usd: 0, label: '$0.00', summary: 'No per-post fee on LinkedIn API.' },
        });
      }
      if (url === '/api/posting/confirm' && init?.method === 'POST') {
        confirmCalls += 1;
        if (shouldFailUnknown) {
          return jsonResponse(
            {
              error: 'Post status is unknown; check the platform before trying again.',
              error_code: 'provider_5xx',
              uncertain: true,
              attempt_status: 'unknown',
            },
            502,
          );
        }
        return jsonResponse({
          ok: true,
          post_id: 'urn:li:share:1',
          post_url: 'https://www.linkedin.com/feed/update/urn:li:share:1/',
          status: 'posted',
          item: { ...payload.item, status: 'posted' },
        });
      }
      return jsonResponse({});
    });

    render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);

    const kitBtn = await screen.findByRole('button', { name: 'Build posting kit' });
    const apiBtn = screen.getByRole('button', { name: 'Post with API' });
    // Manual posting kit button MUST come before Post with API in DOM order.
    expect(kitBtn.compareDocumentPosition(apiBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(apiBtn);

    const dialog = await screen.findByRole('dialog');
    const confirmBtn = await within(dialog).findByRole('button', { name: 'Post publicly now' });

    await waitFor(() => {
      expect(within(dialog).getByText(/Jane Doe/)).toBeTruthy();
      expect(within(dialog).getByText(/deadbeef01234567/)).toBeTruthy();
      expect(within(dialog).getByText('Check blocked terms.')).toBeTruthy();
      expect(within(dialog).getByText('No per-post fee on LinkedIn API.')).toBeTruthy();
      expect((confirmBtn as HTMLButtonElement).disabled).toBe(false);
    });

    // "Post publicly now" must NOT be focused by default.
    expect(document.activeElement).not.toBe(confirmBtn);

    // Pressing Enter inside the dialog or on the button itself must NEVER confirm.
    const aiBox = within(dialog).getByRole('checkbox', { name: /AI-generated/i }) as HTMLInputElement;
    expect(aiBox.checked).toBe(false);
    fireEvent.keyDown(aiBox, { key: 'Enter', code: 'Enter' });
    fireEvent.keyDown(confirmBtn, { key: 'Enter', code: 'Enter' });
    expect(confirmCalls).toBe(0);

    // Clicking "Post publicly now" when provider returns uncertain/5xx surfaces "unknown" clearly.
    shouldFailUnknown = true;
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(confirmCalls).toBe(1);
      expect(within(dialog).getByText(/Post status: unknown\./i)).toBeTruthy();
    });
  });
  it('shows a script\'s rendered video with its poster and a Render again button, and no render panel for other items', async () => {
    const script = makeReviewPayload({
      item: {
        ...makeReviewPayload().item,
        id: 'cnt-vid-1',
        type: 'script',
        platform: 'instagram',
        files: [
          { kind: 'video', path: 'outputs/video/cnt-vid-1/video-9x16.mp4', aspect: '9:16' },
          { kind: 'video', path: 'outputs/video/cnt-vid-1/video-16x9.mp4', aspect: '16:9' },
          { kind: 'poster', path: 'outputs/video/cnt-vid-1/poster.jpg' },
          { kind: 'captions', path: 'outputs/video/cnt-vid-1/captions.srt' },
        ],
      },
      suggestions: [],
      comments: [],
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-vid-1') return jsonResponse(script);
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      if (url === '/api/video/render/cnt-vid-1') return jsonResponse({ content_id: 'cnt-vid-1', state: 'idle' });
      return jsonResponse({});
    });
    render(<ReviewSection contentId="cnt-vid-1" onNavigate={vi.fn()} />);
    await screen.findByRole('button', { name: 'Render again' });
    const videos = Array.from(document.querySelectorAll('video'));
    expect(videos).toHaveLength(2);
    expect(videos[0].getAttribute('poster')).toContain('poster.jpg');
    expect(videos[0].getAttribute('src')).toContain('video-9x16.mp4');
    expect(screen.getAllByRole('button', { name: /^Comment at 0:00$/ })).toHaveLength(2);
    cleanup();

    const post = makeReviewPayload({ suggestions: [], comments: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/review/cnt-rev-1') return jsonResponse(post);
      if (url === '/api/roster') return jsonResponse(MOCK_ROSTER);
      return jsonResponse({});
    });
    render(<ReviewSection contentId="cnt-rev-1" onNavigate={vi.fn()} />);
    await screen.findByText('Cero v2.2 release');
    expect(screen.queryByRole('button', { name: /Render video|Render again/ })).toBeNull();
  });
});
