import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import agentsConfig from '@magnm/config/agents.json';
import { loadActions } from './actions';
import { App } from './App';
import { SESSION_SECRET_KEY } from './auth';
import { saveCachedProductRules } from './cache';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';

const SUPA = getPublicSupabaseConfig();
const UID = 'uid-alpha';
const RULES = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released' as const, show: 'everything' as const, confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret' as const, show: 'nothing' as const, confidential: true },
  ],
};

interface Cloud {
  posts: Array<{ url: string; body: Record<string, unknown> }>;
  aiCalls: Array<Record<string, unknown>>;
  actionRows: Array<Record<string, unknown>>;
  chatRows: Array<Record<string, unknown>>;
  chatUrls: string[];
  signedInSeen: boolean;
}

function makeCloud(): Cloud {
  return { posts: [], aiCalls: [], actionRows: [], chatRows: [], chatUrls: [], signedInSeen: false };
}

const json = (body: unknown, status = 200): NativeHttpResponse => ({ status, headers: {}, body: JSON.stringify(body) });

function handler(cloud: Cloud) {
  return async (req: NativeHttpRequest): Promise<NativeHttpResponse> => {
    if (req.url.startsWith('http://100.')) throw new Error('Network request failed');
    if (req.url.includes('/rest/v1/profiles')) {
      cloud.signedInSeen = true;
      return json([{ id: UID, email: 'a@example.com', display_name: 'Alpha', avatar_url: null, role: 'member', active: true }]);
    }
    if (req.url.includes('/rest/v1/shared_knowledge')) return json([{ key: 'product_rules', data: RULES, updated_at: null }]);
    if (req.url.includes('/rest/v1/mirror_records')) {
      if (req.url.includes('kind=eq.chat_session')) {
        cloud.chatUrls.push(req.url);
        return json(cloud.chatRows);
      }
      return json([]);
    }
    if (req.url.includes('/rest/v1/phone_files')) return json([]);
    if (req.url.includes('/rest/v1/phone_actions')) {
      if (req.method === 'POST') {
        cloud.posts.push({ url: req.url, body: JSON.parse(req.body || '{}') });
        return { status: 201, headers: {}, body: '' };
      }
      return json(cloud.actionRows);
    }
    if (req.url.includes('/functions/v1/ai-proxy')) {
      cloud.aiCalls.push(JSON.parse(req.body || '{}'));
      return json({ choices: [{ message: { content: 'Here is a draft for Cero.' } }] });
    }
    return json({}, 404);
  };
}

async function start(cloud: Cloud, signedIn = true) {
  const bridge = createPreviewNativeBridge({ httpHandler: handler(cloud) });
  if (signedIn) {
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt-alpha', refresh_token: 'ref', expires_at: 9_999_999_999, user_id: UID, email: 'a@example.com' }),
    );
    saveCachedProductRules(UID, RULES);
  }
  render(<App bridge={bridge} initialTab="chat" />);
  if (signedIn) await waitFor(() => expect(cloud.signedInSeen).toBe(true));
  return bridge;
}

async function send(agent: string, text: string) {
  fireEvent.change(screen.getByLabelText('Agent'), { target: { value: agent } });
  fireEvent.change(screen.getByLabelText('Message or voice brief'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
}

describe('chatting with every agent from the phone', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => cleanup());

  it('offers all seven agents by their configured names and says what happens with tools', async () => {
    await start(makeCloud());
    const select = (await screen.findByLabelText('Agent')) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(agentsConfig.agents.map((a) => a.id));
    expect([...select.options].map((o) => o.textContent)).toEqual(agentsConfig.agents.map((a) => a.name));
    expect(screen.getByText(/queued for your laptop/i)).toBeTruthy();
  });

  it('answers a writing request on the phone with the chosen agent, no queue, no tools', async () => {
    const cloud = makeCloud();
    await start(cloud);
    await send('designer', 'Which colour palette suits a calm launch tone?');
    expect(await screen.findByText('Here is a draft for Cero.')).toBeTruthy();
    expect(cloud.aiCalls).toHaveLength(1);
    expect(cloud.aiCalls[0].tools).toBeUndefined();
    expect(JSON.stringify(cloud.aiCalls[0])).toContain('Designer');
    expect(cloud.posts).toHaveLength(0);
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('queues a request that needs a laptop tool and says "Queued for your laptop", without asking any model', async () => {
    const cloud = makeCloud();
    await start(cloud);
    await send('designer', 'Make a quote card image for tomorrow');
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    expect(cloud.posts[0].url).toBe(`${SUPA.url}/rest/v1/phone_actions?on_conflict=id`);
    expect(cloud.posts[0].body).toMatchObject({
      kind: 'agent_task', target_id: 'designer', payload: { message: 'Make a quote card image for tomorrow' },
    });
    expect(Object.keys(cloud.posts[0].body).sort()).toEqual(['id', 'kind', 'payload', 'target_id']);
    expect((await screen.findAllByText('Queued for your laptop')).length).toBeGreaterThan(0);
    expect(cloud.aiCalls).toHaveLength(0);
    expect((screen.getByLabelText('Message or voice brief') as HTMLTextAreaElement).value).toBe('');
    expect(loadActions(UID)).toMatchObject([{ kind: 'agent_task', target_id: 'designer', state: 'waiting' }]);
  });

  it('never queues or sends text that names a confidential product', async () => {
    const cloud = makeCloud();
    await start(cloud);
    await send('researcher', 'Search the web for news about ProjectNightfall');
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/confidential/i));
    expect(cloud.posts).toHaveLength(0);
    expect(cloud.aiCalls).toHaveLength(0);
    expect(loadActions(UID)).toHaveLength(0);
    expect((screen.getByLabelText('Message or voice brief') as HTMLTextAreaElement).value).toContain('ProjectNightfall');
  });

  it('asks to sign in instead of queueing when nobody is signed in', async () => {
    const cloud = makeCloud();
    await start(cloud, false);
    await send('researcher', 'Search the web for offline notes apps');
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/sign in/i));
    expect(cloud.posts).toHaveLength(0);
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('shows the agent\'s reply from the cloud mirror when the laptop answered, and a reason when it did not', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await send('researcher', 'Search the web for offline notes apps');
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    const id = cloud.posts[0].body.id as string;

    cloud.actionRows = [{ id, owner_id: UID, status: 'applied', result: 'Replied. session:sess_9', expires_at: '2999-01-01T00:00:00Z' }];
    cloud.chatRows = [{
      id: 'm1', owner_id: UID, kind: 'chat_session', local_id: 'sess_9', title: 'Search the web',
      data: { agent_id: 'researcher', status: 'done', messages: [{ role: 'user', text: 'Search the web for offline notes apps' }, { role: 'assistant', text: 'Three apps lead the field.' }] },
      local_updated_at: '2026-11-23T10:00:00Z',
    }];
    await act(async () => {
      bridge.emitResume();
    });
    expect(await screen.findByText('Three apps lead the field.')).toBeTruthy();
    expect(cloud.chatUrls.length).toBeGreaterThan(0);
    expect(cloud.chatUrls.every((u) => u.includes(`owner_id=eq.${UID}`) && u.includes('local_id=eq.sess_9'))).toBe(true);
    expect(loadActions(UID)[0]).toMatchObject({ state: 'applied', reply: 'Three apps lead the field.' });
    expect(screen.getAllByText(/Researcher replied/).length).toBeGreaterThan(0);

    await send('writer', 'Save this as a draft');
    await waitFor(() => expect(cloud.posts).toHaveLength(2));
    cloud.actionRows = [
      ...cloud.actionRows,
      { id: cloud.posts[1].body.id, owner_id: UID, status: 'rejected', result: 'Not applied: the text names a confidential product.', expires_at: '2999-01-01T00:00:00Z' },
    ];
    await act(async () => {
      bridge.emitResume();
    });
    expect(await screen.findByText('Not applied: the text names a confidential product.')).toBeTruthy();
  });

  it('keeps a confidential reply on the laptop and never fetches it', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await send('researcher', 'Search the web for offline notes apps');
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    cloud.actionRows = [{ id: cloud.posts[0].body.id, owner_id: UID, status: 'applied', result: 'Replied on the laptop only.', expires_at: '2999-01-01T00:00:00Z' }];
    await act(async () => {
      bridge.emitResume();
    });
    expect(await screen.findByText(/on your laptop only/i)).toBeTruthy();
    expect(cloud.chatUrls).toEqual([]);
    expect(loadActions(UID)[0]).toMatchObject({ laptop_only: true, reply: null });
  });

  it('shows a confidential mirror record as a stub and never copies its text', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await send('researcher', 'Search the web for offline notes apps');
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    cloud.actionRows = [{ id: cloud.posts[0].body.id, owner_id: UID, status: 'applied', result: 'Replied. session:sess_c', expires_at: '2999-01-01T00:00:00Z' }];
    cloud.chatRows = [{
      id: 'm2', owner_id: UID, kind: 'chat_session', local_id: 'sess_c', title: 'Confidential chat',
      data: { confidential: true, agent_id: 'researcher', status: 'done' }, local_updated_at: '2026-11-23T10:00:00Z',
    }];
    await act(async () => {
      bridge.emitResume();
    });
    expect(await screen.findByText(/on your laptop only/i)).toBeTruthy();
    expect(loadActions(UID)[0].reply).toBeNull();
  });

  it('ignores a reply that is another agent\'s, another owner\'s or not a chat record', async () => {
    const cloud = makeCloud();
    const bridge = await start(cloud);
    await send('researcher', 'Search the web for offline notes apps');
    await waitFor(() => expect(cloud.posts).toHaveLength(1));
    cloud.actionRows = [{ id: cloud.posts[0].body.id, owner_id: UID, status: 'applied', result: 'Replied. session:sess_9', expires_at: '2999-01-01T00:00:00Z' }];
    const good = { id: 'm', owner_id: UID, kind: 'chat_session', local_id: 'sess_9', data: { agent_id: 'researcher', messages: [{ role: 'assistant', text: 'REPLY' }] }, local_updated_at: 'x' };
    for (const bad of [
      { ...good, owner_id: 'uid-beta' },
      { ...good, kind: 'content_item' },
      { ...good, local_id: 'sess_other' },
      { ...good, data: { ...good.data, agent_id: 'writer' } },
      { ...good, data: { agent_id: 'researcher', messages: 'nope' } },
    ]) {
      cloud.chatRows = [bad];
      await act(async () => {
        bridge.emitResume();
      });
      await waitFor(() => expect(cloud.chatUrls.length).toBeGreaterThan(0));
      expect(screen.queryByText('REPLY')).toBeNull();
      expect(loadActions(UID)[0].reply ?? null).toBeNull();
    }
    expect(within(document.body).queryByText('REPLY')).toBeNull();
  });
});
