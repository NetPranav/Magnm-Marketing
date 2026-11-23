import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Checkbox, Dialog, Field, Label, PillButton, Row, RowList, Select, TextArea, TextInput } from '@magnm/design/react';
import { getCachedProductRules } from '../cache';
import type { NativeBridge } from '../native';
import {
  PersonalReposError,
  REPO_STATUSES,
  addGithubRepo,
  editPersonalRepo,
  listPersonalRepos,
  removePersonalRepo,
  type PersonalRepo,
  type RepoFieldChanges,
} from '../personalRepos';

const STATUS_LABELS: Record<string, string> = {
  in_development: 'In development',
  released: 'Released',
  secret: 'Secret',
};

export interface ReposPanelProps {
  bridge: NativeBridge;
  /** The signed-in account, or null. Repos come from the cloud, so the laptop does not need to be reachable. */
  userId: string | null;
}

function describe(repo: PersonalRepo): string {
  const where = repo.kind === 'github' ? repo.github : repo.local_path;
  return `${where ?? repo.id} · ${STATUS_LABELS[repo.status] ?? repo.status}${repo.branch ? ` · ${repo.branch}` : ''}`;
}

function pathNote(repo: PersonalRepo): string | null {
  if (repo.kind !== 'local') return null;
  if (repo.path_status === 'pending') return 'Waiting for your laptop to check this folder.';
  if (repo.path_status === 'rejected') return `Your laptop did not accept this folder${repo.path_reason ? `: ${repo.path_reason}` : '.'}`;
  return null;
}

/** My repositories: list, add a GitHub repo, edit, remove. Folder paths are checked by the laptop. */
export function ReposPanel({ bridge, userId }: ReposPanelProps) {
  const [repos, setRepos] = useState<PersonalRepo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A failed load is a quiet status line; only a failed action (add, edit, remove) is an alert.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<PersonalRepo | null>(null);
  const [removing, setRemoving] = useState<PersonalRepo | null>(null);
  const alive = useRef(true);

  const rules = () => (userId ? getCachedProductRules(userId) : null);

  const refresh = useCallback(async () => {
    if (!userId) return;
    try {
      const next = await listPersonalRepos(bridge);
      if (!alive.current) return;
      setRepos(next);
      setLoadError(null);
    } catch (err) {
      if (alive.current) setLoadError(err instanceof Error ? err.message : 'Could not load your repos.');
    } finally {
      if (alive.current) setLoaded(true);
    }
  }, [bridge, userId]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  if (!userId) {
    return (
      <Card>
        <div className="mob-stack-sm">
          <Label>My repos</Label>
          <p className="mob-body-text">Sign in to see and add your repos here.</p>
        </div>
      </Card>
    );
  }

  const run = async (task: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await task());
    } catch (err) {
      setError(err instanceof PersonalReposError ? err.message : 'Something went wrong. Try again.');
      throw err;
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <Card>
      <div className="mob-stack-sm">
        <div className="mob-row-between">
          <Label>My repos</Label>
          <div className="mob-actions">
            <PillButton variant="outline" size="sm" disabled={busy} onClick={() => void refresh()}>
              Refresh
            </PillButton>
            <PillButton size="sm" disabled={busy} onClick={() => setAdding(true)}>
              Add GitHub repo
            </PillButton>
          </div>
        </div>
        {error ? (
          <div className="mob-banner-error" role="alert">
            {error}
          </div>
        ) : null}
        {loadError ? (
          <div className="mob-mono" role="status">
            {loadError}
          </div>
        ) : null}
        {notice ? <span className="mob-mono">{notice}</span> : null}
        {loaded && repos.length === 0 && !loadError ? (
          <p className="mob-mono">No repos yet. Add a GitHub repo here, or a folder from your laptop.</p>
        ) : null}
        {repos.length > 0 ? (
          <RowList>
            {repos.map((repo) => (
              <Row
                key={repo.uid}
                title={repo.name}
                description={[describe(repo), pathNote(repo)].filter(Boolean).join(' — ')}
                meta={
                  <div className="mob-actions">
                    <PillButton variant="outline" size="sm" disabled={busy} aria-label={`Edit ${repo.name}`} onClick={() => setEditing(repo)}>
                      Edit
                    </PillButton>
                    <PillButton variant="outline" size="sm" disabled={busy} aria-label={`Remove ${repo.name}`} onClick={() => setRemoving(repo)}>
                      Remove
                    </PillButton>
                  </div>
                }
              />
            ))}
          </RowList>
        ) : null}
      </div>

      {adding ? (
        <AddRepoDialog
          busy={busy}
          onClose={() => setAdding(false)}
          onAdd={async (input) => {
            await run(async () => {
              const created = await addGithubRepo(bridge, input, rules(), repos.map((r) => r.id));
              setRepos((prev) => [...prev, created]);
              setAdding(false);
              return `Added ${created.name}. Your laptop picks it up on its next sync.`;
            });
          }}
        />
      ) : null}

      {editing ? (
        <EditRepoDialog
          repo={editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={async (changes) => {
            await run(async () => {
              const saved = await editPersonalRepo(bridge, editing, changes, rules());
              setRepos((prev) => prev.map((r) => (r.uid === saved.uid ? saved : r)));
              setEditing(null);
              return changes.local_path !== undefined
                ? `Saved ${saved.name}. Your laptop will check the folder.`
                : `Saved ${saved.name}.`;
            });
          }}
        />
      ) : null}

      <Dialog
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        title="Remove repository"
        subtitle="Repos"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setRemoving(null)}>
              Cancel
            </PillButton>
            <PillButton
              size="sm"
              disabled={busy}
              onClick={() => {
                const target = removing;
                if (!target) return;
                void run(async () => {
                  await removePersonalRepo(bridge, target.uid);
                  setRepos((prev) => prev.filter((r) => r.uid !== target.uid));
                  setRemoving(null);
                  return `Removed ${target.name}. Your laptop drops it on its next sync.`;
                }).catch(() => setRemoving(null));
              }}
            >
              Remove repo
            </PillButton>
          </>
        }
      >
        <p className="mob-body-text">
          Remove {removing?.name} from your list? Nothing is deleted from GitHub or your laptop's disk.
        </p>
      </Dialog>
    </Card>
  );
}

function AddRepoDialog({
  busy,
  onClose,
  onAdd,
}: {
  busy: boolean;
  onClose: () => void;
  onAdd: (input: { github: string; name?: string; branch?: string; status?: string; marketing_angle?: string }) => Promise<void>;
}) {
  const [github, setGithub] = useState('');
  const [name, setName] = useState('');
  const [branch, setBranch] = useState('');
  const [status, setStatus] = useState('in_development');
  const [angle, setAngle] = useState('');
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a GitHub repo"
      subtitle="Repos"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            disabled={busy || !github.trim()}
            onClick={() => void onAdd({ github, name, branch, status, marketing_angle: angle }).catch(() => undefined)}
          >
            Add repo
          </PillButton>
        </>
      }
    >
      <div className="mob-stack-sm">
        <Field label="GitHub repository" htmlFor="repo-add-github" hint="owner/repo or its github.com address. Folders are added on your laptop.">
          <TextInput id="repo-add-github" value={github} onChange={(e) => setGithub(e.target.value)} placeholder="owner/repo" autoComplete="off" />
        </Field>
        <Field label="Name (optional)" htmlFor="repo-add-name">
          <TextInput id="repo-add-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Branch (optional)" htmlFor="repo-add-branch">
          <TextInput id="repo-add-branch" value={branch} onChange={(e) => setBranch(e.target.value)} />
        </Field>
        <Field label="Status" htmlFor="repo-add-status">
          <Select id="repo-add-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {REPO_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Marketing angle (optional)" htmlFor="repo-add-angle">
          <TextArea id="repo-add-angle" rows={2} value={angle} onChange={(e) => setAngle(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

function EditRepoDialog({
  repo,
  busy,
  onClose,
  onSave,
}: {
  repo: PersonalRepo;
  busy: boolean;
  onClose: () => void;
  onSave: (changes: RepoFieldChanges) => Promise<void>;
}) {
  const [name, setName] = useState(repo.name);
  const [id, setId] = useState(repo.id);
  const [branch, setBranch] = useState(repo.branch ?? '');
  const [status, setStatus] = useState<string>(repo.status);
  const [angle, setAngle] = useState(repo.marketing_angle ?? '');
  const [confidential, setConfidential] = useState(repo.confidential);
  const [path, setPath] = useState(repo.local_path ?? '');

  const changes: RepoFieldChanges = {};
  if (name.trim() !== repo.name) changes.name = name.trim();
  if (id.trim() !== repo.id) changes.id = id.trim();
  if (branch.trim() !== (repo.branch ?? '')) changes.branch = branch.trim();
  if (status !== repo.status) changes.status = status;
  if (angle.trim() !== (repo.marketing_angle ?? '').trim()) changes.marketing_angle = angle.trim();
  if (confidential !== repo.confidential) changes.confidential = confidential;
  if (repo.kind === 'local' && path.trim() !== (repo.local_path ?? '')) changes.local_path = path.trim();
  const dirty = Object.keys(changes).length > 0;

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${repo.name}`}
      subtitle="Repos"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose}>
            Cancel
          </PillButton>
          <PillButton size="sm" disabled={busy || !dirty} onClick={() => void onSave(changes).catch(() => undefined)}>
            Save changes
          </PillButton>
        </>
      }
    >
      <div className="mob-stack-sm">
        <Field label="Name" htmlFor="repo-edit-name">
          <TextInput id="repo-edit-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Id" htmlFor="repo-edit-id" hint="Lowercase letters, digits and dashes. Your laptop moves its saved data to the new id.">
          <TextInput id="repo-edit-id" value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" />
        </Field>
        {repo.kind === 'local' ? (
          <Field label="Folder on your laptop" htmlFor="repo-edit-path" hint="Your laptop checks this folder before it uses it.">
            <TextInput id="repo-edit-path" value={path} onChange={(e) => setPath(e.target.value)} autoComplete="off" />
          </Field>
        ) : null}
        <Field label="Branch" htmlFor="repo-edit-branch">
          <TextInput id="repo-edit-branch" value={branch} onChange={(e) => setBranch(e.target.value)} />
        </Field>
        <Field label="Status" htmlFor="repo-edit-status">
          <Select id="repo-edit-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {REPO_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Marketing angle" htmlFor="repo-edit-angle">
          <TextArea id="repo-edit-angle" rows={3} value={angle} onChange={(e) => setAngle(e.target.value)} />
        </Field>
        <Checkbox
          checked={confidential}
          onChange={(e) => setConfidential(e.target.checked)}
          label="Confidential (stays on your laptop and leaves this list)"
        />
      </div>
    </Dialog>
  );
}
