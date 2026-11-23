import { beforeEach, describe, expect, it } from 'vitest';
import { SESSION_SECRET_KEY } from './auth';
import { createPersonalCloud } from './fixtures/personalCloud';
import { createPreviewNativeBridge, type NativeBridge } from './native';
import {
  RepoRefused,
  addGithubRepo,
  editPersonalRepo,
  listPersonalRepos,
  normalizeGithub,
  removePersonalRepo,
  slugifyId,
  validateRepoFields,
} from './personalRepos';
import type { ProductRulesConfig } from './types';

const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'a', text_only: 'b', teasers_only: 'c', nothing: 'd' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', blocked_terms: [], confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', blocked_terms: [], confidential: true },
  ],
} as ProductRulesConfig;

async function setup(uid = 'user-a') {
  const cloud = createPersonalCloud();
  const bridge = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
  await signIn(bridge, uid);
  return { cloud, bridge };
}

async function signIn(bridge: NativeBridge, uid: string) {
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: `jwt-${uid}`, refresh_token: 'r', expires_at: 9_999_999_999, user_id: uid, email: `${uid}@example.com` }),
  );
}

describe('github input and field checks', () => {
  it('accepts slugs and github urls only', () => {
    expect(normalizeGithub('acme/demo-app')).toBe('acme/demo-app');
    expect(normalizeGithub(' https://github.com/acme/demo-app ')).toBe('acme/demo-app');
    expect(normalizeGithub('https://www.github.com/acme/demo-app.git')).toBe('acme/demo-app');
    expect(normalizeGithub('https://github.com/acme/demo-app/')).toBe('acme/demo-app');
    expect(normalizeGithub('git@github.com:acme/demo-app.git')).toBe('acme/demo-app');
    for (const bad of ['', 'acme', 'acme/', '/demo', 'a/b/c', 'acme/..', 'acme/.', '-acme/demo', 'ac me/demo', 'acme/demo;rm',
      'http://evil.example/acme/demo', 'https://github.com.evil.example/acme/demo', 'javascript:alert(1)', 'file:///etc/passwd',
      'https://user@github.com/acme/demo', 'acme/demo?x=1', 'acme/demo#f', 'acme/de\nmo', 'https://github.com/acme', 'ftp://github.com/acme/demo']) {
      expect(normalizeGithub(bad), bad).toBeNull();
    }
  });

  it('makes safe ids', () => {
    expect(slugifyId('Demo App!')).toBe('demo-app');
    expect(slugifyId('../../etc')).toBe('etc');
    expect(slugifyId('')).toBe('repo');
    expect(slugifyId('x'.repeat(100)).length).toBeLessThanOrEqual(63);
  });

  it('refuses hostile field values', () => {
    const bad: Array<Record<string, unknown>> = [
      { name: '' }, { name: 'x'.repeat(81) }, { name: 'a\nb' }, { id: 'Up' }, { id: 'a/b' }, { id: '../x' }, { id: '' },
      { branch: '--upload-pack=x' }, { branch: '../main' }, { branch: 'a..b' }, { branch: 'x.lock' }, { branch: 'with space' },
      { status: 'live' }, { marketing_angle: 'x'.repeat(501) }, { marketing_angle: 'bad\u0007bell' }, { confidential: 'yes' },
      { local_path: '../../etc' }, { local_path: '/a/../b' }, { local_path: 'file:///etc' }, { local_path: '\\\\srv\\share' },
      { local_path: '/tmp/x\ny' }, { local_path: '' }, { local_path: '/' + 'a'.repeat(500) }, { local_path: 5 }, { owner_id: 'x' }, { uid: 'x' },
      { rev: 5 }, { path_status: 'ok' },
    ];
    for (const fields of bad) {
      expect(() => validateRepoFields(fields, 'local'), JSON.stringify(fields)).toThrow();
    }
    expect(() => validateRepoFields({ local_path: '/x' }, 'github')).toThrow();
    expect(validateRepoFields({ name: ' Demo ', branch: '', status: 'released', marketing_angle: 'Calm.\nFast.', confidential: true, local_path: '~/code/x', id: 'demo-2' }, 'local'))
      .toEqual({ name: 'Demo', branch: null, status: 'released', marketing_angle: 'Calm.\nFast.', confidential: true, local_path: '~/code/x', id: 'demo-2' });
  });
});

describe('personal repos on the phone', () => {
  beforeEach(() => window.localStorage.clear());

  it('adds a GitHub repo without the laptop, and lists it', async () => {
    const { cloud, bridge } = await setup();
    const row = await addGithubRepo(bridge, { github: 'https://github.com/Acme/Demo-App', name: 'Demo App', branch: 'main' }, RULES, []);
    expect(row).toMatchObject({ id: 'demo-app', name: 'Demo App', kind: 'github', github: 'Acme/Demo-App', branch: 'main', status: 'in_development' });
    expect(cloud.rowsOf('user-a')).toHaveLength(1);
    const body = cloud.state.seen.at(-1)!.body as { p_uid: null; p_fields: Record<string, unknown> };
    expect(body.p_uid).toBeNull();
    expect(Object.keys(body.p_fields).sort()).toEqual(['branch', 'github', 'id', 'kind', 'name', 'status']);
    expect((await listPersonalRepos(bridge)).map((r) => r.id)).toEqual(['demo-app']);
  });

  it('defaults the name from the repo and numbers a taken id', async () => {
    const { bridge } = await setup();
    const first = await addGithubRepo(bridge, { github: 'acme/tool' }, RULES, []);
    expect(first.name).toBe('tool');
    const second = await addGithubRepo(bridge, { github: 'other/tool' }, RULES, ['tool']);
    expect(second.id).toBe('tool-2');
  });

  it('refuses local repos, bad input and a duplicate', async () => {
    const { bridge } = await setup();
    await expect(addGithubRepo(bridge, { github: '/home/me/code' }, RULES, [])).rejects.toThrow(/GitHub/);
    await expect(addGithubRepo(bridge, { github: 'acme/x', name: 'x'.repeat(81) }, RULES, [])).rejects.toThrow();
    await addGithubRepo(bridge, { github: 'acme/dup' }, RULES, []);
    await expect(addGithubRepo(bridge, { github: 'ACME/dup', name: 'Other' }, RULES, ['dup'])).rejects.toThrow(/already/);
  });

  it('never sends confidential text, and fails closed without the product rules', async () => {
    const { cloud, bridge } = await setup();
    for (const input of [
      { github: 'acme/projectnightfall-app' }, { github: 'acme/ok', name: 'ProjectNightfall mobile' },
      { github: 'acme/ok2', marketing_angle: 'Works with ProjectNightfall' }, { github: 'acme/ok3', name: 'secretproj' },
    ]) {
      await expect(addGithubRepo(bridge, input, RULES, [])).rejects.toBeInstanceOf(RepoRefused);
    }
    await expect(addGithubRepo(bridge, { github: 'acme/fine' }, null, [])).rejects.toBeInstanceOf(RepoRefused);
    expect(cloud.state.seen.filter((s) => s.method === 'POST')).toHaveLength(0);
  });

  it('shows only my own rows and rebuilds each from checked fields', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'mine', name: 'Mine', kind: 'github', github: 'acme/mine' });
    cloud.addRow('user-b', { id: 'theirs', name: 'Theirs', kind: 'github', github: 'acme/theirs' });
    const good = cloud.rowsOf('user-a')[0];
    const bad = [
      { ...good, uid: 'not-a-uuid', id: 'bad-1' }, { ...good, uid: '00000000-0000-4000-8000-0000000000aa', id: '../evil' },
      { ...good, uid: '00000000-0000-4000-8000-0000000000ab', id: 'evil-2', github: 'https://evil.example/x/y' },
      { ...good, uid: '00000000-0000-4000-8000-0000000000ac', id: 'evil-3', owner_id: 'user-b' },
      { ...good, uid: '00000000-0000-4000-8000-0000000000ad', id: 'evil-4', name: 'x'.repeat(81) },
      { ...good, uid: '00000000-0000-4000-8000-0000000000ae', id: 'evil-5', status: 'live' },
      { ...good, uid: '00000000-0000-4000-8000-0000000000af', id: 'evil-6', confidential: true },
      { ...good, uid: '00000000-0000-4000-8000-0000000000b0', id: 'evil-7', kind: 'local', github: null, local_path: '/a/../b' },
      'text', 5, null,
    ];
    cloud.state.extraRows = bad;
    expect((await listPersonalRepos(bridge)).map((r) => r.id)).toEqual(['mine']);
  });

  it('edits only what changed; a path edit shows as pending until the laptop checks it', async () => {
    const { cloud, bridge } = await setup();
    const row = cloud.addRow('user-a', { id: 'tool', name: 'Tool', kind: 'local', local_path: '/work/tool', field_rev: {}, field_at: {} });
    const repo = (await listPersonalRepos(bridge))[0];
    const saved = await editPersonalRepo(bridge, repo, { name: 'Tool 2', local_path: '/work/tool-2', status: 'in_development' }, RULES);
    expect(saved).toMatchObject({ name: 'Tool 2', local_path: '/work/tool-2', path_status: 'pending' });
    const body = cloud.state.seen.at(-1)!.body as { p_uid: string; p_fields: Record<string, unknown>; p_checked: boolean };
    expect(body.p_uid).toBe(row.uid);
    expect(body.p_fields).toEqual({ name: 'Tool 2', local_path: '/work/tool-2' });
    expect(body.p_checked).toBe(false);
  });

  it('refuses hostile edits before sending anything, and confidential text', async () => {
    const { cloud, bridge } = await setup();
    cloud.addRow('user-a', { id: 'tool', name: 'Tool', kind: 'local', local_path: '/work/tool' });
    const repo = (await listPersonalRepos(bridge))[0];
    const before = cloud.state.seen.length;
    for (const changes of [{ local_path: '../../etc' }, { branch: '--x' }, { id: 'A/B' }, { marketing_angle: 'x'.repeat(501) }]) {
      await expect(editPersonalRepo(bridge, repo, changes, RULES)).rejects.toThrow();
    }
    await expect(editPersonalRepo(bridge, repo, { name: 'ProjectNightfall tool' }, RULES)).rejects.toBeInstanceOf(RepoRefused);
    await expect(editPersonalRepo(bridge, repo, { marketing_angle: 'about ProjectNightfall' }, null)).rejects.toBeInstanceOf(RepoRefused);
    expect(cloud.state.seen.length).toBe(before);
    const gh = { ...repo, kind: 'github' as const, github: 'acme/x', local_path: null };
    await expect(editPersonalRepo(bridge, gh, { local_path: '/x' }, RULES)).rejects.toThrow();
  });

  it('removes a repo and cannot touch another member\'s', async () => {
    const { cloud, bridge } = await setup();
    const mine = cloud.addRow('user-a', { id: 'mine', name: 'Mine', github: 'acme/mine' });
    const theirs = cloud.addRow('user-b', { id: 'theirs', name: 'Theirs', github: 'acme/theirs' });
    await removePersonalRepo(bridge, theirs.uid);
    expect(cloud.rowsOf('user-b')).toHaveLength(1);
    await removePersonalRepo(bridge, mine.uid);
    expect(cloud.rowsOf('user-a')).toHaveLength(0);
    await expect(removePersonalRepo(bridge, 'not-a-uuid')).rejects.toThrow();
  });

  it('cloud errors give short messages that never echo what was sent', async () => {
    const { cloud, bridge } = await setup();
    cloud.state.down = true;
    await expect(addGithubRepo(bridge, { github: 'acme/tool', marketing_angle: 'SECRET-ANGLE' }, RULES, [])).rejects.toThrow(/problem|reach/i);
    await expect(listPersonalRepos(bridge)).rejects.toThrow(/problem|reach/i);
    try {
      await addGithubRepo(bridge, { github: 'acme/tool', marketing_angle: 'SECRET-ANGLE' }, RULES, []);
    } catch (err) {
      expect(String((err as Error).message)).not.toContain('SECRET-ANGLE');
    }
  });

  it('needs a signed-in account', async () => {
    const cloud = createPersonalCloud();
    const bridge = createPreviewNativeBridge({ httpHandler: (req) => cloud.handler(req).then((r) => r ?? { status: 404, headers: {}, body: '{}' }) });
    await expect(listPersonalRepos(bridge)).rejects.toThrow(/Sign in/);
    expect(cloud.state.seen).toHaveLength(0);
  });
});
