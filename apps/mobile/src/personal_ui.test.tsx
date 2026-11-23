import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SESSION_SECRET_KEY, getPersonalNimKey } from './auth';
import { saveCachedProductRules } from './cache';
import { PersonalKeysPanel } from './components/PersonalKeysPanel';
import { ReposPanel } from './components/ReposPanel';
import { createPersonalCloud } from './fixtures/personalCloud';
import { createPreviewNativeBridge } from './native';

const KEY = 'nvapi-FAKEKEYUIPHONE000000000000000001';

async function setup(signedIn = true) {
  const cloud = createPersonalCloud();
  const bridge = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
  if (signedIn) {
    await bridge.secureStore.set(
      SESSION_SECRET_KEY,
      JSON.stringify({ access_token: 'jwt-user-a', refresh_token: 'r', expires_at: 9_999_999_999, user_id: 'user-a', email: 'a@example.com' }),
    );
  }
  saveCachedProductRules('user-a', {
    version: 1,
    show_levels: { everything: 'a', text_only: 'b', teasers_only: 'c', nothing: 'd' },
    products: [{ id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', blocked_terms: [], confidential: true }],
  } as never);
  return { cloud, bridge };
}

describe('PersonalKeysPanel', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => cleanup());

  it('saves a key, shows it by its last four characters only, shares it and removes it', async () => {
    const { cloud, bridge } = await setup();
    const changes: boolean[] = [];
    render(<PersonalKeysPanel bridge={bridge} signedIn onChanged={(has) => changes.push(has)} />);
    fireEvent.change(screen.getByPlaceholderText('nvapi-...'), { target: { value: KEY } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await screen.findByText('Shared with your laptop');
    expect(screen.getByText(`nvapi-...${KEY.slice(-4)}`)).toBeTruthy();
    expect(document.body.textContent).not.toContain('FAKEKEY');
    expect(cloud.keysOf('user-a')).toEqual({ NVIDIA_NIM_API_KEY: KEY });
    expect(changes.at(-1)).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Remove key' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove key' })).toBeNull());
    expect(cloud.keysOf('user-a')).toEqual({});
    expect(changes.at(-1)).toBe(false);
  });

  it('shows keys that arrive from the laptop', async () => {
    const { cloud, bridge } = await setup();
    cloud.putKey('user-a', 'NVIDIA_NIM_API_KEY2', KEY);
    render(<PersonalKeysPanel bridge={bridge} signedIn />);
    await screen.findByText(`nvapi-...${KEY.slice(-4)}`);
    expect(await getPersonalNimKey(bridge)).toBe(KEY);
  });

  it('says so when the key cannot be shared yet, and refuses a malformed one', async () => {
    const { cloud, bridge } = await setup();
    cloud.state.down = true;
    render(<PersonalKeysPanel bridge={bridge} signedIn />);
    fireEvent.change(screen.getByPlaceholderText('nvapi-...'), { target: { value: KEY } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await screen.findByText(/It will be shared when you are online/);
    fireEvent.change(screen.getByPlaceholderText('nvapi-...'), { target: { value: 'sk-nope' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/does not look like an NVIDIA key/);
  });

  it('signed out: keeps the key on this phone and says to sign in to share it', async () => {
    const { cloud, bridge } = await setup(false);
    render(<PersonalKeysPanel bridge={bridge} signedIn={false} />);
    fireEvent.change(screen.getByPlaceholderText('nvapi-...'), { target: { value: KEY } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await screen.findByText(/Sign in to share it with your laptop/);
    expect(cloud.state.seen).toHaveLength(0);
  });
});

describe('ReposPanel', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => cleanup());

  it('asks to sign in when signed out', async () => {
    const { bridge } = await setup(false);
    render(<ReposPanel bridge={bridge} userId={null} />);
    expect(screen.getByText(/Sign in to see and add your repos/)).toBeTruthy();
  });

  it('lists my repos with the laptop folder check state', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'site', name: 'Site', kind: 'github', github: 'acme/site', branch: 'main' });
    cloud.addRow('user-a', { id: 'tool', name: 'Tool', kind: 'local', local_path: '/work/tool', path_status: 'pending' });
    cloud.addRow('user-a', { id: 'bad', name: 'Bad', kind: 'local', local_path: '/etc', path_status: 'rejected', path_reason: 'That folder is too broad.' });
    cloud.addRow('user-b', { id: 'theirs', name: 'Theirs', kind: 'github', github: 'acme/theirs' });
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    await screen.findByText('Site');
    expect(screen.getByText(/acme\/site · In development · main/)).toBeTruthy();
    expect(screen.getByText(/Waiting for your laptop to check this folder/)).toBeTruthy();
    expect(screen.getByText(/did not accept this folder: That folder is too broad/)).toBeTruthy();
    expect(screen.queryByText('Theirs')).toBeNull();
  });

  it('adds a GitHub repo without the laptop', async () => {
    const { cloud, bridge } = await setup();
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    await screen.findByText(/No repos yet/);
    fireEvent.click(screen.getByRole('button', { name: 'Add GitHub repo' }));
    fireEvent.change(screen.getByLabelText(/GitHub repository/), { target: { value: 'https://github.com/acme/new-site' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add repo' }));
    await screen.findByText(/Added new-site/);
    expect(cloud.rowsOf('user-a').map((r) => r.github)).toEqual(['acme/new-site']);
  });

  it('refuses a local folder and a confidential name with a clear message', async () => {
    const { cloud, bridge } = await setup();
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    await screen.findByText(/No repos yet/);
    fireEvent.click(screen.getByRole('button', { name: 'Add GitHub repo' }));
    fireEvent.change(screen.getByLabelText(/GitHub repository/), { target: { value: '/home/me/code' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add repo' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Folders are added on your laptop/);
    fireEvent.change(screen.getByLabelText(/GitHub repository/), { target: { value: 'acme/ok' } });
    fireEvent.change(screen.getByLabelText('Name (optional)'), { target: { value: 'ProjectNightfall app' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add repo' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/confidential product/));
    expect(cloud.rowsOf('user-a')).toHaveLength(0);
  });

  it('edits a repo (only the changed fields) and tells the laptop will check a new folder', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'tool', name: 'Tool', kind: 'local', local_path: '/work/tool' });
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Tool' }));
    const save = screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change((document.getElementById('repo-edit-path') as HTMLInputElement), { target: { value: '/work/tool-2' } });
    fireEvent.change((document.getElementById('repo-edit-name') as HTMLInputElement), { target: { value: 'Tool Two' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/Your laptop will check the folder/);
    const row = cloud.rowsOf('user-a')[0];
    expect([row.name, row.local_path, row.path_status]).toEqual(['Tool Two', '/work/tool-2', 'pending']);
    expect(screen.getByText(/Waiting for your laptop to check this folder/)).toBeTruthy();
  });

  it('shows hostile edits as an error and sends nothing', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'tool', name: 'Tool', kind: 'local', local_path: '/work/tool' });
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Tool' }));
    const before = cloud.state.seen.length;
    fireEvent.change((document.getElementById('repo-edit-path') as HTMLInputElement), { target: { value: '../../etc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/cannot contain/);
    expect(cloud.state.seen.length).toBe(before);
  });

  it('removes a repo after asking', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'site', name: 'Site', kind: 'github', github: 'acme/site' });
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Site' }));
    const dialog = await screen.findByText(/from your list/);
    expect(dialog).toBeTruthy();
    fireEvent.click(within(document.body).getByRole('button', { name: 'Remove repo' }));
    await waitFor(() => expect(cloud.rowsOf('user-a')).toHaveLength(0));
    await screen.findByText(/Removed Site/);
  });

  it('a failed load is a quiet status, not an alert', async () => {
    const { cloud, bridge } = await setup();
    cloud.state.down = true;
    render(<ReposPanel bridge={bridge} userId="user-a" />);
    expect((await screen.findByRole('status')).textContent).toMatch(/problem|reach/i);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
