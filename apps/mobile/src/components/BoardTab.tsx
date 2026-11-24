import { useMemo, useState } from 'react';
import { Field, PillButton, Select } from '@magnm/design/react';
import type { ActionInput } from '../actions';
import type { ContextCacheState } from '../cache';
import {
  BOARD_COLUMNS,
  BOARD_KIND_LABELS,
  boardFromMirror,
  buildAutomationControl,
  buildRunControl,
  pendingControl,
  type PhoneBoardItem,
} from '../boardPhone';
import type { PhoneAction } from '../types';

export interface BoardTabProps {
  signedIn: boolean;
  cache: ContextCacheState;
  phoneActions: PhoneAction[];
  onQueueAction: (input: ActionInput, okMessage: string) => Promise<boolean>;
}

const QUEUED = 'Queued for your laptop. It applies this when it is back.';

function whenText(item: PhoneBoardItem): string {
  if (item.ageDays >= 1) return `${item.ageDays}d ago`;
  const t = Date.parse(item.updatedAt);
  return Number.isNaN(t) ? '' : new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function ItemCard({ item, phoneActions, signedIn, onQueueAction }: { item: PhoneBoardItem; phoneActions: PhoneAction[]; signedIn: boolean; onQueueAction: BoardTabProps['onQueueAction'] }) {
  const [open, setOpen] = useState(false);
  const pending = item.runId ? pendingControl(phoneActions, 'run_control', item.runId) : null;
  return (
    <div className="mob-asset" data-grey={item.column === 'done' && item.ageDays >= 3 ? 'true' : undefined}>
      <button type="button" className="mob-board-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="mob-asset-meta">
          {BOARD_KIND_LABELS[item.kind] ?? item.kind}
          {item.automatic ? ' · automatic' : ''}
          {item.confidential ? ' · confidential' : ''}
        </span>
        <span className="mob-wrap-text">{item.title}</span>
        <span className="mob-asset-meta">
          {[item.repoName ? `${item.repoName} (${item.scope || 'personal'})` : '', item.agents.join(', '), whenText(item)].filter(Boolean).join(' · ')}
        </span>
      </button>
      {pending ? <div className="mg-notice">Waiting for the laptop: {pending.payload.action}.</div> : null}
      {open ? (
        <div className="mob-stack-sm">
          {item.confidential ? <div className="mob-asset-meta">Confidential: it stays on the laptop.</div> : null}
          {item.steps.length > 0 ? (
            <ol className="mob-board-steps" aria-label="Steps">
              {item.steps.map((s) => (
                <li key={s.name}>
                  {s.name.replace(/_/g, ' ')} <span className="mob-asset-meta">{s.status.replace(/_/g, ' ')}</span>
                </li>
              ))}
            </ol>
          ) : null}
          {item.error ? <div className="mg-notice">{item.error}</div> : null}
          {item.runId && (item.canPause || item.canResume) ? (
            <div className="mob-actions">
              <PillButton
                variant="outline"
                size="sm"
                disabled={!signedIn || pending !== null}
                aria-label={`${item.canPause ? 'Pause' : 'Resume'} ${item.title}`}
                onClick={() => void onQueueAction(buildRunControl(item.runId as string, item.canPause ? 'pause' : 'resume', `${item.canPause ? 'Pause' : 'Resume'}: ${item.title}`.slice(0, 80)), QUEUED)}
              >
                {item.canPause ? 'Pause' : 'Resume'}
              </PillButton>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Board on the phone: what the team is doing, from the cloud copy. Pause and resume are queued for the laptop. */
export function BoardTab({ signedIn, cache, phoneActions, onQueueAction }: BoardTabProps) {
  const board = useMemo(() => boardFromMirror(cache), [cache]);
  const [agent, setAgent] = useState('');
  const [repo, setRepo] = useState('');
  const [kind, setKind] = useState('');
  const auto = board.automation;
  const switchPending = pendingControl(phoneActions, 'automation_control', 'all');
  const items = board.items.filter(
    (i) => (!agent || i.agents.includes(agent)) && (!repo || i.repoName === repo) && (!kind || i.kind === kind),
  );
  return (
    <div className="mob-stack">
      <div className="mob-asset">
        <div className="mob-wrap-text">Automatic work</div>
        <div className="mob-asset-meta">
          {auto ? (auto.paused ? `Paused${auto.pausedBy ? ` from the ${auto.pausedBy}` : ''}. Schedules do not fire and background jobs wait.` : 'On. Schedules fire and background jobs run.') : 'No automatic work set up on the laptop yet.'}
        </div>
        {switchPending ? <div className="mg-notice">Waiting for the laptop: {switchPending.payload.action}.</div> : null}
        <div className="mob-actions">
          <PillButton
            variant="outline"
            size="sm"
            disabled={!signedIn || switchPending !== null}
            onClick={() => void onQueueAction(buildAutomationControl(auto?.paused ? 'resume' : 'pause'), QUEUED)}
          >
            {auto?.paused ? 'Resume automatic work' : 'Pause automatic work'}
          </PillButton>
        </div>
        {auto && auto.missed.length > 0 ? (
          <div className="mob-asset-meta">Missed while paused (run them on the laptop): {auto.missed.map((m) => m.name).join(', ')}.</div>
        ) : null}
        {auto?.pipeline.enabled || auto?.pipeline.lastRun ? (
          <div className="mob-asset-meta">
            Content pipeline: {auto.pipeline.enabled ? 'on' : 'off'}
            {auto.pipeline.kinds.length > 0 ? ` · makes ${auto.pipeline.kinds.join(', ')}` : ''}
            {auto.pipeline.lastRun
              ? auto.pipeline.lastRun.confidential
                ? ` · last run: ${auto.pipeline.lastRun.status} (confidential, on the laptop)`
                : ` · last run: ${auto.pipeline.lastRun.status}${auto.pipeline.lastRun.repoName ? ` for ${auto.pipeline.lastRun.repoName} (${auto.pipeline.lastRun.scope || 'personal'})` : ''}`
              : ''}
          </div>
        ) : null}
      </div>

      <div className="mob-asset-edit">
        <Field label="Agent" htmlFor="mob-board-agent">
          <Select id="mob-board-agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">All agents</option>
            {board.filters.agents.map((a) => <option key={a} value={a}>{a}</option>)}
          </Select>
        </Field>
        <Field label="Repo" htmlFor="mob-board-repo">
          <Select id="mob-board-repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
            <option value="">All repos</option>
            {board.filters.repos.map((r) => <option key={r} value={r}>{r}</option>)}
          </Select>
        </Field>
        <Field label="Kind" htmlFor="mob-board-kind">
          <Select id="mob-board-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All kinds</option>
            {board.filters.kinds.map((k) => <option key={k} value={k}>{BOARD_KIND_LABELS[k] ?? k}</option>)}
          </Select>
        </Field>
      </div>

      {board.items.length === 0 ? <div className="mg-notice">The board is empty, or the laptop has not shared it yet.</div> : null}
      {BOARD_COLUMNS.map((col) => {
        const inCol = items.filter((i) => i.column === col.id);
        if (inCol.length === 0) return null;
        return (
          <section key={col.id} aria-label={col.label} className="mob-stack-sm">
            <h3 className="mob-board-col">{col.label} ({inCol.length})</h3>
            {inCol.map((item) => (
              <ItemCard key={item.id} item={item} phoneActions={phoneActions} signedIn={signedIn} onQueueAction={onQueueAction} />
            ))}
          </section>
        );
      })}
    </div>
  );
}
