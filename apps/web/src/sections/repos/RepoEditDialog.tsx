import { useEffect, useState } from 'react';
import { Checkbox, Dialog, Field, PillButton, Select, TextArea, TextInput } from '@magnm/design/react';
import { updateRepo, type RepoEditFields } from '../../api/repos';
import type { RepoRecord } from '../../types';

const STATUS_OPTIONS: Array<[string, string]> = [
  ['in_development', 'In development'],
  ['released', 'Released'],
  ['secret', 'Secret'],
];

interface FormState {
  name: string;
  id: string;
  branch: string;
  status: string;
  angle: string;
  confidential: boolean;
  path: string;
}

function startState(repo: RepoRecord): FormState {
  return {
    name: repo.name || '',
    id: repo.id,
    branch: repo.branch || '',
    status: repo.status || 'in_development',
    angle: repo.marketing_angle || '',
    confidential: Boolean(repo.confidential),
    path: repo.local_path || '',
  };
}

/** Only the fields the person changed. The engine checks every one of them again. */
export function changedFields(repo: RepoRecord, form: FormState): RepoEditFields {
  const start = startState(repo);
  const out: RepoEditFields = {};
  if (form.name.trim() !== start.name) out.name = form.name.trim();
  if (form.id.trim() !== start.id) out.id = form.id.trim();
  if (form.branch.trim() !== start.branch) out.branch = form.branch.trim();
  if (form.status !== start.status) out.status = form.status;
  if (form.angle.trim() !== start.angle.trim()) out.marketing_angle = form.angle.trim();
  if (form.confidential !== start.confidential) out.confidential = form.confidential;
  if (repo.kind === 'local' && form.path.trim() !== start.path) out.local_path = form.path.trim();
  return out;
}

interface Props {
  repo: RepoRecord | null;
  onClose: () => void;
  onSaved: (repo: RepoRecord) => void;
}

/** The Edit form for one of your own repositories (team repos are managed by an admin and have no Edit). */
export function RepoEditDialog({ repo, onClose, onSaved }: Props) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm(repo ? startState(repo) : null);
    setError(null);
    setBusy(false);
  }, [repo]);

  if (!repo || !form) {
    return null;
  }

  const changes = changedFields(repo, form);
  const idChanged = changes.id !== undefined;
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const handleSave = async () => {
    if (Object.keys(changes).length === 0) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await updateRepo(repo.id, changes);
      onSaved(res.repo);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the changes.');
    } finally {
      setBusy(false);
    }
  };

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
          <PillButton
            size="sm"
            disabled={busy || Object.keys(changes).length === 0}
            onClick={() => void handleSave()}
          >
            {busy ? 'Saving...' : 'Save changes'}
          </PillButton>
        </>
      }
    >
      <div className="studio-stack-sm">
        {error ? <div className="mg-notice" role="alert">{error}</div> : null}
        <Field label="Name" htmlFor="repo-edit-name">
          <TextInput id="repo-edit-name" value={form.name} onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field
          label="Id"
          htmlFor="repo-edit-id"
          hint={
            idChanged
              ? 'Changing the id moves this repo\'s snapshots, facts and references to the new id.'
              : 'Lowercase letters, digits and dashes.'
          }
        >
          <TextInput id="repo-edit-id" value={form.id} onChange={(e) => set('id', e.target.value)} />
        </Field>
        {repo.kind === 'local' ? (
          <Field
            label="Local folder"
            htmlFor="repo-edit-path"
            hint={
              repo.cloud?.path_rejected
                ? 'A folder sent from your phone was refused. Pick a git folder on this computer.'
                : 'A git folder on this computer. It is checked before it is used.'
            }
          >
            <TextInput id="repo-edit-path" value={form.path} onChange={(e) => set('path', e.target.value)} />
          </Field>
        ) : null}
        <Field label="Branch" htmlFor="repo-edit-branch">
          <TextInput id="repo-edit-branch" value={form.branch} onChange={(e) => set('branch', e.target.value)} />
        </Field>
        <Field label="Status" htmlFor="repo-edit-status">
          <Select id="repo-edit-status" value={form.status} onChange={(e) => set('status', e.target.value)}>
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Marketing angle" htmlFor="repo-edit-angle">
          <TextArea id="repo-edit-angle" rows={3} value={form.angle} onChange={(e) => set('angle', e.target.value)} />
        </Field>
        <Checkbox
          checked={form.confidential}
          onChange={(e) => set('confidential', e.target.checked)}
          label="Confidential (stays on this laptop, never sent to the phone or the cloud)"
        />
      </div>
    </Dialog>
  );
}
