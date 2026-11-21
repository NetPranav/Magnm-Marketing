import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AdminSection } from './AdminSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ADMIN_ID = '11111111-1111-1111-1111-111111111111';
const MEMBER_ID = '22222222-2222-2222-2222-222222222222';

describe('AdminSection', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function setupFetchMocks(options?: { storageBytes?: number; storageWarning?: boolean }) {
    let members = [
      {
        id: ADMIN_ID,
        email: 'admin@example.com',
        display_name: 'Pranav',
        avatar_url: 'https://lh3.googleusercontent.com/a/admin',
        role: 'admin',
        active: true,
        created_at: '2026-11-01T00:00:00Z',
        last_seen_at: new Date(Date.now() - 60_000).toISOString(),
        device_last_seen_at: new Date(Date.now() - 60_000).toISOString(),
        latest_activity_ts: new Date(Date.now() - 120_000).toISOString(),
        last_active_at: new Date(Date.now() - 60_000).toISOString(),
        app_version: '0.1.0',
        presence: 'online',
        status: 'online',
        running_agents: [],
        devices: [
          {
            device_id: 'dev-admin',
            device_kind: 'desktop',
            status: 'online',
            running_agents: [],
            app_version: '0.1.0',
            last_seen_at: new Date(Date.now() - 60_000).toISOString(),
          },
        ],
      },
      {
        id: MEMBER_ID,
        email: 'maya@example.com',
        display_name: 'Maya <script>alert(1)</script>',
        avatar_url: null,
        role: 'member',
        active: true,
        created_at: '2026-11-02T00:00:00Z',
        last_seen_at: new Date(Date.now() - 300_000).toISOString(),
        device_last_seen_at: new Date(Date.now() - 30_000).toISOString(),
        latest_activity_ts: new Date(Date.now() - 10_000).toISOString(),
        last_active_at: new Date(Date.now() - 300_000).toISOString(),
        app_version: '0.1.0',
        presence: 'working',
        status: 'working',
        running_agents: ['writer'],
        devices: [
          {
            device_id: 'dev-maya',
            device_kind: 'desktop',
            status: 'working',
            running_agents: ['writer'],
            app_version: '0.1.0',
            last_seen_at: new Date(Date.now() - 30_000).toISOString(),
          },
        ],
      },
    ];

    let previewFetches = 0;

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();

      if (url === '/api/admin/team') {
        return jsonResponse({ members, current_user_id: ADMIN_ID });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/role` && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { role: string };
        members = members.map((m) => (m.id === MEMBER_ID ? { ...m, role: body.role } : m));
        return jsonResponse({ member: members[1] });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/active` && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { active: boolean };
        members = members.map((m) => (m.id === MEMBER_ID ? { ...m, active: body.active } : m));
        return jsonResponse({ member: members[1] });
      }
      if (url.startsWith('/api/admin/activity/daily')) {
        const series = Array.from({ length: 14 }, (_, idx) => ({
          date: `2026-11-${String(8 + idx).padStart(2, '0')}`,
          total: idx === 13 ? 5 : 1,
          by_member: {
            [ADMIN_ID]: 1,
            [MEMBER_ID]: idx === 13 ? 4 : 0,
          },
        }));
        return jsonResponse({ days: 14, dates: series.map((s) => s.date), series, members: [] });
      }
      if (url.startsWith('/api/admin/activity')) {
        const qs = new URL(url, 'http://localhost').searchParams;
        const member = qs.get('member');
        const allEvents = [
          {
            id: 101,
            owner_id: MEMBER_ID,
            member_name: 'Maya <script>alert(1)</script>',
            ts: '2026-11-21T10:00:00Z',
            kind: 'activity',
            agent_id: 'writer',
            summary: '<b>Drafted post</b>',
            data: {},
          },
          {
            id: 100,
            owner_id: ADMIN_ID,
            member_name: 'Pranav',
            ts: '2026-11-21T09:00:00Z',
            kind: 'outbox.pitch_proposal',
            agent_id: null,
            summary: 'Published proposal',
            data: {},
          },
        ];
        const filtered = member ? allEvents.filter((e) => e.owner_id === member) : allEvents;
        return jsonResponse({ events: filtered, next_before: null });
      }
      if (url.startsWith(`/api/admin/members/${MEMBER_ID}/records?`)) {
        const qs = new URL(url, 'http://localhost').searchParams;
        const kind = qs.get('kind');
        const q = qs.get('q') || '';
        if (kind === 'chat_session') {
          return jsonResponse({
            records: [
              {
                id: 'r-chat',
                kind: 'chat_session',
                local_id: 'chat-1',
                parent_local_id: null,
                title: 'Writer brainstorm',
                local_updated_at: '2026-11-21T10:00:00Z',
                synced_at: '2026-11-21T10:01:00Z',
              },
              {
                id: 'r-conf',
                kind: 'chat_session',
                local_id: 'chat-conf',
                parent_local_id: null,
                title: 'Confidential chat',
                local_updated_at: '2026-11-21T09:00:00Z',
                synced_at: '2026-11-21T09:01:00Z',
              },
            ],
            next_before: null,
          });
        }
        if (kind === 'posted_link') {
          return jsonResponse({
            records: [
              {
                id: 'r-bad-link',
                kind: 'posted_link',
                local_id: 'bad-1',
                parent_local_id: null,
                title: 'Unsafe link',
                local_updated_at: '2026-11-21T10:00:00Z',
                synced_at: '2026-11-21T10:01:00Z',
              },
              {
                id: 'r-good-link',
                kind: 'posted_link',
                local_id: 'good-1',
                parent_local_id: null,
                title: 'Safe link',
                local_updated_at: '2026-11-21T09:00:00Z',
                synced_at: '2026-11-21T09:01:00Z',
              },
            ],
            next_before: null,
          });
        }
        const drafts = [
          {
            id: 'r-draft',
            kind: 'content_item',
            local_id: 'draft-1',
            parent_local_id: null,
            title: 'Cero Launch <img src=x onerror=alert(1)>',
            local_updated_at: '2026-11-21T11:00:00Z',
            synced_at: '2026-11-21T11:01:00Z',
          },
        ];
        const filtered = q ? drafts.filter((d) => d.title.toLowerCase().includes(q.toLowerCase())) : drafts;
        return jsonResponse({ records: filtered, next_before: null });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/content_item/draft-1`) {
        return jsonResponse({
          record: {
            id: 'r-draft',
            kind: 'content_item',
            local_id: 'draft-1',
            parent_local_id: null,
            title: 'Cero Launch <img src=x onerror=alert(1)>',
            data: { type: 'post', platform: 'linkedin', status: 'ready' },
            local_updated_at: '2026-11-21T11:00:00Z',
            synced_at: '2026-11-21T11:01:00Z',
          },
          children: [
            {
              id: 'cv-1',
              kind: 'content_version',
              local_id: 'v1',
              parent_local_id: 'draft-1',
              title: 'v1',
              local_updated_at: '2026-11-21T10:30:00Z',
              synced_at: '2026-11-21T10:31:00Z',
            },
            {
              id: 'rc-1',
              kind: 'review_comment',
              local_id: 'c1',
              parent_local_id: 'draft-1',
              title: 'Tighten hook',
              local_updated_at: '2026-11-21T10:40:00Z',
              synced_at: '2026-11-21T10:41:00Z',
            },
            {
              id: 'm-1',
              kind: 'metric',
              local_id: 'met1',
              parent_local_id: 'draft-1',
              title: 'linkedin',
              local_updated_at: '2026-11-21T10:50:00Z',
              synced_at: '2026-11-21T10:51:00Z',
            },
          ],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/content_version/v1`) {
        return jsonResponse({
          record: {
            id: 'cv-1',
            kind: 'content_version',
            local_id: 'v1',
            parent_local_id: 'draft-1',
            title: 'v1',
            data: { version: 1, body: 'Version one body <script>bad()</script>', created_by: 'Maya' },
            local_updated_at: '2026-11-21T10:30:00Z',
            synced_at: '2026-11-21T10:31:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/review_comment/c1`) {
        return jsonResponse({
          record: {
            id: 'rc-1',
            kind: 'review_comment',
            local_id: 'c1',
            parent_local_id: 'draft-1',
            title: 'Tighten hook',
            data: { author: 'Reviewer', version: 1, status: 'open', body: 'Shorten opening line.' },
            local_updated_at: '2026-11-21T10:40:00Z',
            synced_at: '2026-11-21T10:41:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/metric/met1`) {
        return jsonResponse({
          record: {
            id: 'm-1',
            kind: 'metric',
            local_id: 'met1',
            parent_local_id: 'draft-1',
            title: 'linkedin',
            data: { metrics: { impressions: 420 }, source: 'manual' },
            local_updated_at: '2026-11-21T10:50:00Z',
            synced_at: '2026-11-21T10:51:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/chat_session/chat-1`) {
        return jsonResponse({
          record: {
            id: 'r-chat',
            kind: 'chat_session',
            local_id: 'chat-1',
            parent_local_id: null,
            title: 'Writer brainstorm',
            data: {
              agent_id: 'writer',
              status: 'idle',
              messages: [
                { role: 'user', text: 'Write a hook for Cero' },
                { role: 'assistant', text: 'Here is a clean hook.' },
              ],
            },
            local_updated_at: '2026-11-21T10:00:00Z',
            synced_at: '2026-11-21T10:01:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/chat_session/chat-conf`) {
        return jsonResponse({
          record: {
            id: 'r-conf',
            kind: 'chat_session',
            local_id: 'chat-conf',
            parent_local_id: null,
            title: 'Confidential chat',
            data: { confidential: true, agent_id: 'cmo', secret_leak: 'MUST_NOT_APPEAR' },
            local_updated_at: '2026-11-21T09:00:00Z',
            synced_at: '2026-11-21T09:01:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/posted_link/bad-1`) {
        return jsonResponse({
          record: {
            id: 'r-bad-link',
            kind: 'posted_link',
            local_id: 'bad-1',
            parent_local_id: null,
            title: 'Unsafe link',
            data: { platform: 'x', posted_url: 'javascript:alert(1)' },
            local_updated_at: '2026-11-21T10:00:00Z',
            synced_at: '2026-11-21T10:01:00Z',
          },
          children: [],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/records/posted_link/good-1`) {
        return jsonResponse({
          record: {
            id: 'r-good-link',
            kind: 'posted_link',
            local_id: 'good-1',
            parent_local_id: null,
            title: 'Safe link',
            data: { platform: 'linkedin', posted_url: 'https://linkedin.com/posts/123' },
            local_updated_at: '2026-11-21T09:00:00Z',
            synced_at: '2026-11-21T09:01:00Z',
          },
          children: [],
        });
      }
      if (url.startsWith(`/api/admin/members/${MEMBER_ID}/previews`)) {
        previewFetches += 1;
        const suffix = previewFetches === 1 ? 'tok1' : 'tok2';
        return jsonResponse({
          previews: [
            {
              id: 'p1',
              owner_id: MEMBER_ID,
              local_source_id: 'src-img',
              source_kind: 'content_file',
              parent_local_id: 'draft-1',
              kind: 'thumbnail',
              storage_path: `${MEMBER_ID}/thumbnail/img.webp`,
              content_type: 'image/webp',
              bytes: 1200,
              width: 320,
              height: 240,
              duration_s: null,
              created_at: '2026-11-21T10:00:00Z',
              expires_at: null,
              signed_url: `https://example.supabase.co/storage/v1/object/sign/previews/thumb.webp?t=${suffix}`,
            },
            {
              id: 'p2',
              owner_id: MEMBER_ID,
              local_source_id: 'src-img',
              source_kind: 'content_file',
              parent_local_id: 'draft-1',
              kind: 'image_preview',
              storage_path: `${MEMBER_ID}/image_preview/img.webp`,
              content_type: 'image/webp',
              bytes: 15000,
              width: 1080,
              height: 810,
              duration_s: null,
              created_at: '2026-11-21T10:00:00Z',
              expires_at: null,
              signed_url: `https://example.supabase.co/storage/v1/object/sign/previews/full.webp?t=${suffix}`,
            },
            {
              id: 'p3',
              owner_id: MEMBER_ID,
              local_source_id: 'src-vid',
              source_kind: 'content_file',
              parent_local_id: 'draft-1',
              kind: 'thumbnail',
              storage_path: `${MEMBER_ID}/thumbnail/vid.webp`,
              content_type: 'image/webp',
              bytes: 1400,
              width: 320,
              height: 180,
              duration_s: null,
              created_at: '2026-11-21T10:00:00Z',
              expires_at: null,
              signed_url: `https://example.supabase.co/storage/v1/object/sign/previews/vthumb.webp?t=${suffix}`,
            },
            {
              id: 'p4',
              owner_id: MEMBER_ID,
              local_source_id: 'src-vid',
              source_kind: 'content_file',
              parent_local_id: 'draft-1',
              kind: 'video_preview',
              storage_path: `${MEMBER_ID}/video_preview/vid.mp4`,
              content_type: 'video/mp4',
              bytes: 2200000,
              width: 854,
              height: 480,
              duration_s: 12,
              created_at: '2026-11-21T10:00:00Z',
              expires_at: '2027-01-20T10:00:00Z',
              signed_url: `https://example.supabase.co/storage/v1/object/sign/previews/vid.mp4?t=${suffix}`,
            },
          ],
        });
      }
      if (url === `/api/admin/members/${MEMBER_ID}/pitch` || url === `/api/admin/members/${ADMIN_ID}/pitch`) {
        return jsonResponse({
          proposals: [
            {
              share_id: 'AbCdEf',
              local_proposal_id: 'p-1',
              kind: 'proposal',
              status: 'live',
              client_name: 'Asha',
              business_name: 'Acme',
              updated_at: '2026-11-21T10:00:00Z',
            },
          ],
          submissions: [
            {
              id: 'sub-1',
              share_id: 'AbCdEf',
              name: 'Priya',
              business: 'Northstar',
              phone: '999',
              notes: 'Call tomorrow',
              items: ['Web Frontend'],
              total_shown: 7500,
              created_at: '2026-11-21T11:00:00Z',
              seen_at: null,
            },
          ],
        });
      }
      if (url === '/api/admin/storage') {
        const bytes = options?.storageBytes ?? 120 * 1024 * 1024;
        const warning = options?.storageWarning ?? false;
        return jsonResponse({
          bytes,
          files: 18,
          by_kind: {
            thumbnail: { bytes: 1024 * 1024, files: 10 },
            video_preview: { bytes: bytes - 1024 * 1024, files: 8 },
          },
          limit_bytes: 1073741824,
          percent: (bytes / 1073741824) * 100,
          warning,
        });
      }
      if (url.startsWith('/api/admin/audit')) {
        return jsonResponse({
          entries: [
            {
              id: 1,
              actor_id: ADMIN_ID,
              actor_name: 'Pranav',
              action: 'invite.created',
              target: 'new@example.com',
              target_label: 'new@example.com',
              data: { role: 'member' },
              created_at: '2026-11-21T12:00:00Z',
            },
          ],
          next_before: null,
        });
      }
      return jsonResponse({});
    });

    return { fetchMock, getPreviewFetches: () => previewFetches };
  }

  it('renders team cards, enforces no self-actions, confirms role/active changes, and shows activity bars', async () => {
    const { fetchMock } = setupFetchMocks();
    const onOpenSettings = vi.fn();
    render(<AdminSection onOpenSettings={onOpenSettings} />);

    await waitFor(() => {
      expect(screen.getByText('Pranav')).toBeTruthy();
      expect(screen.getByText('Maya <script>alert(1)</script>')).toBeTruthy();
    });

    // HTML in member display_name is shown literally, never executed or injected as <script>
    expect(document.querySelectorAll('script')).toHaveLength(0);
    expect(screen.getByText('Running agents: writer')).toBeTruthy();
    expect(screen.getByText('Working')).toBeTruthy();
    expect(screen.getByText('Online')).toBeTruthy();

    // Only 1 Make admin/member and 1 Deactivate button (none on own card)
    expect(screen.getAllByRole('button', { name: 'Make admin' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Deactivate' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Make member' })).toBeNull();

    // Make admin requires confirm dialog
    fireEvent.click(screen.getByRole('button', { name: 'Make admin' }));
    const dialog = screen.getByRole('dialog');
    expect(fetchMock).not.toHaveBeenCalledWith(
      `/api/admin/members/${MEMBER_ID}/role`,
      expect.anything(),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/admin/members/${MEMBER_ID}/role`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ role: 'admin' }),
        }),
      );
    });

    // Invite people link navigates to Settings
    fireEvent.click(screen.getByRole('link', { name: 'Invite people' }));
    expect(onOpenSettings).toHaveBeenCalled();

    // Switch to Activity tab: renders 14 bars and filters by member
    fireEvent.click(screen.getByRole('tab', { name: /Activity/ }));
    await waitFor(() => {
      expect(screen.getAllByTestId('admin-activity-bar')).toHaveLength(14);
      expect(screen.getByText('Total: 18')).toBeTruthy();
      expect(screen.getByText('<b>Drafted post</b>')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Filter by member'), { target: { value: MEMBER_ID } });
    await waitFor(() => {
      expect(screen.getByText('Total: 4')).toBeTruthy();
    });
  });

  it('browses member workspace tabs, search, chat, draft versions, confidential, safe links, and previews', async () => {
    const { fetchMock, getPreviewFetches } = setupFetchMocks();
    render(<AdminSection onOpenSettings={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Maya <script>alert(1)</script>')).toBeTruthy();
    });

    // Open Maya's workspace from her card (second Open workspace button)
    const openBtns = screen.getAllByRole('button', { name: 'Open workspace' });
    fireEvent.click(openBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Read-only copy. Ask the member for full quality files.')).toBeTruthy();
      expect(screen.getByText('Cero Launch <img src=x onerror=alert(1)>')).toBeTruthy();
    });

    // All 9 workspace kind tabs exist
    for (const label of ['Chats', 'Drafts', 'Research', 'Reviews', 'Briefs', 'Tasks', 'Posted links', 'Results', 'Team room']) {
      expect(screen.getByRole('tab', { name: new RegExp(label) })).toBeTruthy();
    }

    // Search titles
    fireEvent.change(screen.getByLabelText('Search titles'), { target: { value: 'Cero' } });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/api/admin/members/${MEMBER_ID}/records?kind=content_item&q=Cero`),
        expect.anything(),
      );
    });

    // Click draft -> versions preformatted, comments, metrics, previews
    fireEvent.click(screen.getByText('Cero Launch <img src=x onerror=alert(1)>'));
    await waitFor(() => {
      expect(screen.getByText('Version one body <script>bad()</script>')).toBeTruthy();
      expect(screen.getByText('Shorten opening line.')).toBeTruthy();
      expect(screen.getByText(/420/)).toBeTruthy();
      expect(screen.getByLabelText('Draft previews')).toBeTruthy();
    });

    // Signed URL image error refreshes previews once (not in a loop)
    const thumbImg = screen.getByAltText('Preview src-img') as HTMLImageElement;
    expect(thumbImg.getAttribute('src')).toContain('t=tok1');
    fireEvent.error(thumbImg);
    await waitFor(() => {
      expect(getPreviewFetches()).toBe(2);
      expect((screen.getByAltText('Preview src-img') as HTMLImageElement).getAttribute('src')).toContain('t=tok2');
    });
    fireEvent.error(screen.getByAltText('Preview src-img'));
    expect(getPreviewFetches()).toBe(2);

    // Clicking video thumbnail opens dialog with <video controls preload="none">
    fireEvent.click(screen.getByRole('button', { name: 'Open preview for src-vid' }));
    const videoEl = screen.getByTestId('admin-preview-video') as HTMLVideoElement;
    expect(videoEl.hasAttribute('controls')).toBe(true);
    expect(videoEl.getAttribute('preload')).toBe('none');
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));

    // Switch to Chats tab -> conversation and confidential placeholder
    fireEvent.click(screen.getByRole('tab', { name: /Chats/ }));
    await waitFor(() => {
      expect(screen.getByText('Writer brainstorm')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('Writer brainstorm'));
    await waitFor(() => {
      expect(screen.getByText('Write a hook for Cero')).toBeTruthy();
      expect(screen.getByText('Here is a clean hook.')).toBeTruthy();
    });

    fireEvent.click(screen.getByText('Confidential chat'));
    await waitFor(() => {
      expect(screen.getByTestId('confidential-record')).toBeTruthy();
      expect(document.body.textContent).not.toContain('MUST_NOT_APPEAR');
    });

    // Switch to Posted links tab -> javascript: rendered as plain text, https: as rel=noopener noreferrer
    fireEvent.click(screen.getByRole('tab', { name: /Posted links/ }));
    await waitFor(() => {
      expect(screen.getByText('Unsafe link')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('Unsafe link'));
    await waitFor(() => {
      expect(screen.getByText('javascript:alert(1)')).toBeTruthy();
      expect(screen.queryByRole('link', { name: 'javascript:alert(1)' })).toBeNull();
    });

    fireEvent.click(screen.getByText('Safe link'));
    await waitFor(() => {
      const safeAnchor = screen.getByRole('link', { name: 'https://linkedin.com/posts/123' });
      expect(safeAnchor.getAttribute('rel')).toContain('noopener');
      expect(safeAnchor.getAttribute('rel')).toContain('noreferrer');
    });
  });

  it('renders Pitch, Storage bar with 80% notice, and Audit log table', async () => {
    setupFetchMocks({ storageBytes: 860 * 1024 * 1024, storageWarning: true });
    render(<AdminSection onOpenSettings={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Pranav')).toBeTruthy();
    });

    // Pitch tab
    fireEvent.click(screen.getByRole('tab', { name: /Pitch/ }));
    await waitFor(() => {
      expect(screen.getByText('Acme · Asha')).toBeTruthy();
      expect(screen.getByText('ID: AbCdEf')).toBeTruthy();
      expect(screen.getByText('Northstar · Priya')).toBeTruthy();
      expect(screen.getByText('Items: Web Frontend')).toBeTruthy();
    });

    // Storage tab
    fireEvent.click(screen.getByRole('tab', { name: /Storage/ }));
    await waitFor(() => {
      expect(screen.getByText('860 MB of 1 GB')).toBeTruthy();
      expect(screen.getByRole('progressbar', { name: 'Team storage usage' })).toBeTruthy();
      expect(screen.getByText('Thumbnails')).toBeTruthy();
      expect(
        screen.getByText('Storage is almost full. Video previews will move to Backblaze B2.'),
      ).toBeTruthy();
    });

    // Audit log tab
    fireEvent.click(screen.getByRole('tab', { name: /Audit log/ }));
    await waitFor(() => {
      const table = screen.getByRole('table', { name: 'Admin audit log' });
      expect(within(table).getByText('Pranav')).toBeTruthy();
      expect(within(table).getByText('invite.created')).toBeTruthy();
      expect(within(table).getByText('new@example.com')).toBeTruthy();
    });
  });
});
