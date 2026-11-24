import { useCallback, useEffect, useMemo, useState } from 'react';
import { Dialog, Field, PillButton, Select, SheetHeader, StatusDot } from '@magnm/design/react';
import { setAutomationPaused } from '../../api/automation';
import { actOnBoard, getBoard, type BoardActionId, type BoardData, type BoardItem } from '../../api/board';
import { useStudioEvents } from '../../events';
import { formatTimestamp } from '../../ui/format';

const KIND_LABELS: Record<string, string> = {
  agent_task: 'Agent task',
  workflow: 'Workflow',
  approval: 'Approval',
  job: 'Job',
  reminder: 'Reminder',
};
const POLL_MS = 15000;

export interface BoardSectionProps {
  /** Open another screen, for example the approval screen ("#/create"). */
  onNavigate: (route: string) => void;
}

function ageLabel(item: BoardItem): string {
  if (item.age_days >= 1) return `${item.age_days}d ago`;
  return item.updated_at ? formatTimestamp(item.updated_at) : '';
}

/** The task board: everything the team is doing in six columns, with filters and the actions each item allows. */
export function BoardSection({ onNavigate }: BoardSectionProps) {
  const [data, setData] = useState<BoardData | null>(null);
  const [agent, setAgent] = useState('');
  const [repo, setRepo] = useState('');
  const [kind, setKind] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await getBoard({ agent, repo, kind });
      if (next && Array.isArray(next.items) && Array.isArray(next.columns)) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The board could not be loaded.');
    }
  }, [agent, repo, kind]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useStudioEvents(
    useCallback(
      (name) => {
        if (name.startsWith('workflow_') || name === 'job' || name.startsWith('agent_') || name === 'content_status') {
          void load();
        }
      },
      [load],
    ),
  );

  const open = useMemo(() => data?.items.find((i) => i.id === openId) ?? null, [data, openId]);

  const act = async (item: BoardItem, action: BoardActionId) => {
    setBusy(true);
    setError(null);
    try {
      const res = await actOnBoard(item.id, action);
      if (action === 'approve') {
        const route = res.route || item.route;
        setOpenId(null);
        if (route) onNavigate(route);
        return;
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(false);
    }
  };

  const togglePause = async () => {
    if (!data) return;
    setBusy(true);
    try {
      await setAutomationPaused(!data.paused);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the switch.');
    } finally {
      setBusy(false);
    }
  };

  const filters = data?.filters;
  return (
    <div className="studio-stack">
      <SheetHeader
        subtitle="Everything the team is doing"
        title="Board"
        lead="Agent tasks, workflow runs, approvals waiting, scheduled jobs and reminders. Finished items fade after 7 days."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <div className="board-bar">
        <div className="board-filters">
          <Field label="Agent" htmlFor="board-agent">
            <Select id="board-agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">All agents</option>
              {(filters?.agents ?? []).map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Repo" htmlFor="board-repo">
            <Select id="board-repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
              <option value="">All repos</option>
              {(filters?.repos ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Kind" htmlFor="board-kind">
            <Select id="board-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All kinds</option>
              {(filters?.kinds ?? []).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k] ?? k}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="studio-row-actions">
          <StatusDot
            state={data?.paused ? 'off' : 'online'}
            label={data?.paused ? 'Automatic work paused' : 'Automatic work on'}
          />
          <PillButton variant="outline" size="sm" disabled={busy || !data} onClick={() => void togglePause()}>
            {data?.paused ? 'Resume automatic work' : 'Pause automatic work'}
          </PillButton>
        </div>
      </div>

      {!data ? (
        <div className="mg-empty">Loading the board.</div>
      ) : (
        <div className="board-columns" aria-label="Task board">
          {data.columns.map((col) => {
            const items = data.items.filter((i) => i.column === col.id);
            return (
              <section key={col.id} className="board-column" aria-label={col.label}>
                <h2 className="studio-section-title board-column-title">
                  {col.label} <span className="board-count">{items.length}</span>
                </h2>
                {items.length === 0 ? <div className="board-empty">Nothing here</div> : null}
                {items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`board-card${item.age_days >= 3 && item.column === 'done' ? ' board-card--aged' : ''}`}
                    onClick={() => setOpenId(item.id)}
                  >
                    <span className="board-card-kind">
                      {KIND_LABELS[item.kind] ?? item.kind}
                      {item.automatic ? ' · automatic' : ''}
                      {item.confidential ? ' · confidential' : ''}
                    </span>
                    <span className="board-card-title">{item.title}</span>
                    <span className="board-card-meta">
                      {item.repo_name ? `${item.repo_name} (${item.scope || 'personal'})` : null}
                      {item.agents.length > 0 ? `${item.repo_name ? ' · ' : ''}${item.agents.join(', ')}` : null}
                    </span>
                    <span className="board-card-meta">{ageLabel(item)}</span>
                  </button>
                ))}
              </section>
            );
          })}
        </div>
      )}

      <Dialog
        open={Boolean(open)}
        onClose={() => setOpenId(null)}
        title={open?.title ?? 'Item'}
        subtitle={open ? `${KIND_LABELS[open.kind] ?? open.kind} · ${open.column}` : ''}
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setOpenId(null)}>
              Close
            </PillButton>
            {open?.actions.map((a) => (
              <PillButton
                key={a.id}
                size="sm"
                variant={a.id === 'cancel' ? 'outline' : 'primary'}
                disabled={busy}
                onClick={() => void act(open, a.id)}
              >
                {a.label}
              </PillButton>
            ))}
          </>
        }
      >
        {open ? (
          <div className="studio-stack-sm">
            {open.repo_name ? (
              <p className="mg-sub">
                Repo: {open.repo_name} ({open.scope || 'personal'})
              </p>
            ) : null}
            {open.agents.length > 0 ? <p className="mg-sub">Agents: {open.agents.join(', ')}</p> : null}
            {open.detail.paused_by ? <p className="mg-sub">Paused by: {open.detail.paused_by}</p> : null}
            {open.detail.steps && open.detail.steps.length > 0 ? (
              <ol className="board-steps" aria-label="Steps">
                {open.detail.steps.map((s) => (
                  <li key={s.name} data-status={s.status}>
                    <span>{s.name.replace(/_/g, ' ')}</span>
                    <span className="studio-mono">{s.status.replace(/_/g, ' ')}</span>
                  </li>
                ))}
              </ol>
            ) : null}
            {open.detail.options && open.detail.options.length > 0 ? (
              <p className="mg-sub">Options: {open.detail.options.join(', ')}</p>
            ) : null}
            {open.detail.message ? <p className="mg-sub">{open.detail.message}</p> : null}
            {open.detail.error ? (
              <div className="mg-notice" role="alert">
                {open.detail.error}
              </div>
            ) : null}
            {open.confidential ? <p className="studio-hint">Confidential: this stays on the laptop.</p> : null}
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
