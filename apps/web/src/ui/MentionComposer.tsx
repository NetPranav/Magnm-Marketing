import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { PillButton, Select, TextArea } from '@magnm/design/react';

export interface MentionOption {
  id: string;
  name: string;
  role?: string;
}

export interface AttachmentOption {
  id: string;
  name: string;
  file_group?: string;
}

export interface MentionComposerProps {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  agents: MentionOption[];
  placeholder?: string;
  submitLabel?: string;
  sending?: boolean;
  disabled?: boolean;
  assets?: AttachmentOption[];
  selectedAssetId?: string;
  onSelectAsset?: (assetId: string) => void;
  enableMentions?: boolean;
  ariaLabel?: string;
}

interface MentionMatch {
  start: number;
  end: number;
  query: string;
}

function findActiveMention(text: string, caret: number): MentionMatch | null {
  const before = text.slice(0, caret);
  const match = /(^|\s)@([a-zA-Z0-9_-]*)$/.exec(before);
  if (!match) return null;
  const query = match[2];
  const start = caret - query.length - 1;
  return { start, end: caret, query };
}

/** Chat composer with Enter-to-send, Shift+Enter newline, and keyboard @mention autocomplete. */
export function MentionComposer({
  value,
  onChange,
  onSubmit,
  agents,
  placeholder = 'Write a message...',
  submitLabel = 'Send',
  sending = false,
  disabled = false,
  assets = [],
  selectedAssetId = '',
  onSelectAsset,
  enableMentions = true,
  ariaLabel = 'Message composer',
}: MentionComposerProps) {
  const listboxId = useId();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [caret, setCaret] = useState(0);
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);

  const activeMention = useMemo(() => {
    if (!enableMentions) return null;
    const found = findActiveMention(value, caret);
    if (!found || dismissedAt === found.start) return null;
    return found;
  }, [enableMentions, value, caret, dismissedAt]);

  const filteredAgents = useMemo(() => {
    if (!activeMention) return [];
    const q = activeMention.query.toLowerCase();
    return agents.filter(
      (ag) => ag.id.toLowerCase().includes(q) || ag.name.toLowerCase().includes(q),
    );
  }, [activeMention, agents]);

  const menuOpen = Boolean(activeMention && filteredAgents.length > 0);
  const safeIndex = filteredAgents.length > 0 ? Math.min(highlightIndex, filteredAgents.length - 1) : 0;

  const syncCaret = () => {
    const el = textareaRef.current;
    if (!el) return;
    setCaret(el.selectionStart ?? value.length);
  };

  const applyMention = (agentId: string) => {
    if (!activeMention) return;
    const insertion = `@${agentId} `;
    const nextValue =
      value.slice(0, activeMention.start) + insertion + value.slice(activeMention.end);
    const nextCaret = activeMention.start + insertion.length;
    onChange(nextValue);
    setDismissedAt(null);
    setHighlightIndex(0);
    setCaret(nextCaret);

    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(nextCaret, nextCaret);
      }
    });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setHighlightIndex((prev) => (prev + 1) % filteredAgents.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setHighlightIndex((prev) => (prev - 1 + filteredAgents.length) % filteredAgents.length);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        const chosen = filteredAgents[safeIndex];
        if (chosen) {
          applyMention(chosen.id);
        }
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        if (activeMention) {
          setDismissedAt(activeMention.start);
        }
        return;
      }
    }

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!sending && !disabled && value.trim()) {
        onSubmit();
      }
    }
  };

  return (
    <div className="studio-composer">
      {menuOpen ? (
        <div
          id={listboxId}
          role="listbox"
          aria-label="Mention an agent"
          className="studio-mention-menu"
        >
          {filteredAgents.map((agent, idx) => {
            const isActive = idx === safeIndex;
            return (
              <button
                key={agent.id}
                id={`${listboxId}-${agent.id}`}
                type="button"
                role="option"
                aria-selected={isActive}
                className={
                  isActive ? 'studio-mention-item studio-mention-item--active' : 'studio-mention-item'
                }
                onMouseDown={(e) => {
                  e.preventDefault();
                  applyMention(agent.id);
                }}
              >
                <span className="studio-mono">@{agent.id}</span>
                <span className="studio-mention-name">{agent.name}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      <TextArea
        ref={textareaRef}
        rows={3}
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-autocomplete={enableMentions ? 'list' : undefined}
        aria-controls={menuOpen ? listboxId : undefined}
        aria-activedescendant={
          menuOpen && filteredAgents[safeIndex]
            ? `${listboxId}-${filteredAgents[safeIndex].id}`
            : undefined
        }
        disabled={disabled || sending}
        onChange={(event) => {
          const next = event.target.value;
          const pos = event.target.selectionStart ?? next.length;
          setDismissedAt(null);
          setHighlightIndex(0);
          setCaret(pos);
          onChange(next);
        }}
        onClick={syncCaret}
        onKeyUp={syncCaret}
        onKeyDown={handleKeyDown}
      />

      <div className="studio-composer-bar">
        <div className="studio-composer-tools">
          {onSelectAsset ? (
            <Select
              aria-label="Attach asset"
              value={selectedAssetId}
              onChange={(event) => onSelectAsset(event.target.value)}
              disabled={disabled || sending}
              className="studio-composer-select"
            >
              <option value="">No attachment</option>
              {assets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.name}
                  {asset.file_group ? ` (${asset.file_group})` : ''}
                </option>
              ))}
            </Select>
          ) : null}
          <span className="studio-hint">Enter to send, Shift+Enter for newline{enableMentions ? ', @ to mention' : ''}</span>
        </div>

        <PillButton
          size="sm"
          disabled={disabled || sending || !value.trim()}
          onClick={() => onSubmit()}
        >
          {sending ? 'Sending...' : submitLabel}
        </PillButton>
      </div>
    </div>
  );
}
