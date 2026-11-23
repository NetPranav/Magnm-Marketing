import { useState } from 'react';
import {
  Card,
  Field,
  Label,
  PillButton,
  Select,
  StatusDot,
  TextArea,
} from '@magnm/design/react';
import { stateLabel } from '../actions';
import { configuredAgents, getConfiguredAgent } from '../agentTools';
import type { ChatTurn, PhoneAction, PhoneAgentInfo } from '../types';
import {
  SocialResearchPanel,
  type SocialResearchPanelProps,
} from './SocialResearchPanel';

export interface ChatTabProps {
  laptopReachable: boolean;
  agents: PhoneAgentInfo[];
  selectedAgentId: string;
  onSelectAgent: (id: string) => void;
  messages: ChatTurn[];
  hasPersonalNimKey: boolean;
  signedIn: boolean;
  hasProductRules: boolean;
  busy: boolean;
  errorMessage: string | null;
  onSendMessage: (text: string, agentId: string) => Promise<boolean>;
  onDictate: () => Promise<string | null>;
  /** Requests queued on this phone for the laptop (this account's only). */
  phoneActions?: PhoneAction[];
  socialResearch?: Omit<SocialResearchPanelProps, 'idPrefix'>;
}

/** Phone Lite talks to all seven configured agents, by their configured names and roles. */
export const DEFAULT_LITE_AGENTS: PhoneAgentInfo[] = configuredAgents().map((a) => ({
  id: a.id,
  name: a.name,
  code: a.code,
  role: a.role,
}));

const QUICK_PROMPTS = [
  { label: 'Shorten', prompt: 'Shorten this post while keeping the core takeaway:\n\n' },
  { label: 'Tighten hook', prompt: 'Write 3 sharper opening hooks for:\n\n' },
  { label: 'Hashtags', prompt: 'Suggest 5 relevant hashtags for:\n\n' },
  { label: 'Rewrite tone', prompt: 'Rewrite this in a calmer, factual engineering tone:\n\n' },
];

export function ChatTab({
  laptopReachable,
  agents,
  selectedAgentId,
  onSelectAgent,
  messages,
  hasPersonalNimKey,
  signedIn,
  hasProductRules,
  busy,
  errorMessage,
  onSendMessage,
  onDictate,
  phoneActions = [],
  socialResearch,
}: ChatTabProps) {
  const [draftText, setDraftText] = useState('');
  const [dictating, setDictating] = useState(false);
  const [dictateError, setDictateError] = useState<string | null>(null);

  const agentOptions =
    laptopReachable && agents.length > 0 ? agents : DEFAULT_LITE_AGENTS;
  const activeAgent = agentOptions.some((a) => a.id === selectedAgentId)
    ? selectedAgentId
    : agentOptions[0].id;

  const liteSourceLabel = hasPersonalNimKey
    ? 'Personal NIM key'
    : signedIn
      ? 'Shared team AI (ai-proxy)'
      : 'No AI key or sign-in';

  async function handleDictate() {
    setDictateError(null);
    setDictating(true);
    try {
      const text = await onDictate();
      if (text && text.trim()) {
        // Fill editable text box only; never auto-send.
        setDraftText((prev) => (prev.trim() ? `${prev.trim()}\n${text.trim()}` : text.trim()));
      }
    } catch (err) {
      setDictateError(err instanceof Error ? err.message : 'Dictation failed.');
    } finally {
      setDictating(false);
    }
  }

  async function handleSubmit() {
    const clean = draftText.trim();
    if (!clean || busy) return;
    const ok = await onSendMessage(clean, activeAgent);
    if (ok) {
      setDraftText('');
    }
  }

  const displayError = errorMessage || dictateError;
  const agentTasks = phoneActions.filter((a) => a.kind === 'agent_task').slice(-10).reverse();

  return (
    <section aria-label="Chat" className="mob-stack">
      <Card selected={!laptopReachable}>
        <div className="mob-stack-sm">
          <div className="mob-row-between">
            <Label>{laptopReachable ? 'Laptop AI team' : 'Phone Lite'}</Label>
            <StatusDot
              state={laptopReachable ? 'online' : 'idle'}
              label={laptopReachable ? 'Laptop connected' : 'Phone Lite (offline)'}
            />
          </div>
          {!laptopReachable ? (
            <div className="mob-stack-sm">
              <p className="mob-body-text">
                Phone Lite mode: every agent answers in text here, without tools. A request that needs a laptop tool
                (web or social research, images, repos, saving numbers, delegation) is queued for your laptop and its
                reply comes back here. Confidential products stay on your laptop.
              </p>
              <span className="mob-mono">
                Key: {liteSourceLabel} · Product rules: {hasProductRules ? 'Loaded' : 'Not cached'}
              </span>
            </div>
          ) : null}
        </div>
      </Card>

      <Field label="Agent" htmlFor="chat-agent-select">
        <Select
          id="chat-agent-select"
          value={activeAgent}
          onChange={(e) => onSelectAgent(e.target.value)}
        >
          {agentOptions.map((ag) => (
            <option key={ag.id} value={ag.id}>
              {ag.name}
            </option>
          ))}
        </Select>
      </Field>

      {!laptopReachable ? (
        <div className="mob-actions" aria-label="Quick actions">
          {QUICK_PROMPTS.map((qp) => (
            <PillButton
              key={qp.label}
              variant="outline"
              size="sm"
              onClick={() => setDraftText((prev) => `${qp.prompt}${prev}`)}
            >
              {qp.label}
            </PillButton>
          ))}
        </div>
      ) : null}

      {displayError ? (
        <div className="mob-banner-error" role="alert">
          {displayError}
        </div>
      ) : null}

      <div className="mob-chat-log" aria-live="polite">
        {messages.length === 0 ? (
          <div className="mg-empty">
            {laptopReachable
              ? 'Ask your laptop agent for a draft, research, or summary.'
              : 'Phone Lite ready for writing and quick rewrites.'}
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`mob-msg ${msg.role === 'user' ? 'mob-msg--user' : ''}`}
            >
              <div className="mob-row-between">
                <span className="mob-mono">
                  {msg.role === 'user' ? 'You' : msg.agentId} · {msg.mode}
                </span>
              </div>
              <p className="mob-body-text">{msg.text}</p>
              {msg.waitingUser && msg.question ? (
                <div className="mob-stack-sm">
                  <div className="mg-notice">{msg.question}</div>
                  {(msg.options ?? []).length > 0 ? (
                    <div className="mob-actions">
                      {(msg.options ?? []).map((opt) => (
                        <PillButton
                          key={opt}
                          variant="outline"
                          size="sm"
                          onClick={() => setDraftText(opt)}
                        >
                          {opt}
                        </PillButton>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>

      <Card>
        <div className="mob-stack-sm">
          <Field label="Message or voice brief" htmlFor="chat-input-box">
            <TextArea
              id="chat-input-box"
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              placeholder="Write a brief or tap Dictate..."
            />
          </Field>
          <div className="mob-actions">
            <PillButton
              variant="outline"
              size="sm"
              disabled={dictating || busy}
              onClick={() => void handleDictate()}
            >
              {dictating ? 'Listening...' : 'Dictate'}
            </PillButton>
            <PillButton
              size="sm"
              disabled={!draftText.trim() || busy}
              onClick={() => void handleSubmit()}
            >
              {busy ? 'Sending...' : 'Send'}
            </PillButton>
          </div>
        </div>
      </Card>

      {agentTasks.length > 0 ? (
        <Card>
          <div className="mob-stack-sm" aria-label="Requests for your laptop">
            <Label>Requests for your laptop</Label>
            {agentTasks.map((task) => (
              <AgentTaskEntry key={task.id} task={task} />
            ))}
          </div>
        </Card>
      ) : null}

      {socialResearch ? <SocialResearchPanel idPrefix="chat" {...socialResearch} /> : null}
    </section>
  );
}

function AgentTaskEntry({ task }: { task: PhoneAction }) {
  const agent = getConfiguredAgent(task.target_id);
  const name = agent?.name ?? task.target_id;
  let status: string;
  if (task.state === 'waiting') status = 'Queued for your laptop';
  else if (task.state === 'rejected') status = task.result || 'Not applied.';
  else if (task.laptop_only) status = 'Replied on your laptop only. Open it there.';
  else if (task.reply) status = `${name} replied`;
  else status = `${name} replied. Loading the reply...`;
  return (
    <div className="mob-msg">
      <div className="mob-row-between">
        <span className="mob-mono">{name}</span>
        <span className="mg-chip">{task.state === 'waiting' ? stateLabel(task) : task.state === 'applied' ? 'Applied' : 'Not applied'}</span>
      </div>
      <p className="mob-body-text">{task.payload.message}</p>
      <p className="mob-mono">{status}</p>
      {task.state === 'applied' && task.reply ? <p className="mob-body-text">{task.reply}</p> : null}
    </div>
  );
}
