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
import type { ChatTurn, PhoneAgentInfo } from '../types';
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
  socialResearch?: Omit<SocialResearchPanelProps, 'idPrefix'>;
}

export const DEFAULT_LITE_AGENTS: PhoneAgentInfo[] = [
  { id: 'writer', name: 'Writer', role: 'Posts, captions, threads, hooks' },
  { id: 'cmo', name: 'CMO', role: 'Strategy, campaign ideas, messaging' },
];

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
                Phone Lite mode: writing and quick actions only, no tools. Confidential products stay on your laptop.
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

      {socialResearch ? <SocialResearchPanel idPrefix="chat" {...socialResearch} /> : null}
    </section>
  );
}
