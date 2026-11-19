import { useState } from 'react';
import { Card, PillButton, TextInput } from '@magnm/design/react';
import { postFeedback } from '../../api/team';
import { formatTimestamp } from '../../ui/format';
import { SafeMarkdown } from '../../ui/SafeMarkdown';

export interface DisplayMessage {
  key: string;
  sender: string;
  authorLabel: string;
  timestamp?: string;
  text: string;
  modelUsed?: string | null;
  attachedAssets?: string[];
  contentIds?: string[];
  waitingUser?: boolean;
  redirectTo?: string;
}

export interface MessageListProps {
  messages: DisplayMessage[];
  emptyText: string;
  onSelectAgent?: (agentId: string) => void;
}

/** Thread view rendering author/timestamps in mono, safe markdown, and optional feedback. */
export function MessageList({ messages, emptyText, onSelectAgent }: MessageListProps) {
  const [feedbackOpenKey, setFeedbackOpenKey] = useState<string | null>(null);
  const [feedbackTone, setFeedbackTone] = useState<'up' | 'down'>('up');
  const [feedbackNote, setFeedbackNote] = useState('');
  const [feedbackDone, setFeedbackDone] = useState<Record<string, string>>({});
  const [feedbackErr, setFeedbackErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (messages.length === 0) {
    return <div className="mg-empty">{emptyText}</div>;
  }

  const submitFeedback = async (msg: DisplayMessage) => {
    const contentId = msg.contentIds?.[0];
    if (!contentId) return;
    const prefix = feedbackTone === 'up' ? 'Thumbs up' : 'Thumbs down';
    const note = feedbackNote.trim();
    const text = note ? `${prefix}: ${note}` : `${prefix} on ${msg.authorLabel} output`;

    setSubmitting(true);
    setFeedbackErr(null);
    try {
      await postFeedback(contentId, text, msg.sender);
      setFeedbackDone((prev) => ({ ...prev, [msg.key]: prefix }));
      setFeedbackOpenKey(null);
      setFeedbackNote('');
    } catch (err) {
      setFeedbackErr(err instanceof Error ? err.message : 'Failed to save feedback.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="studio-messages" aria-label="Messages">
      {feedbackErr ? (
        <div className="mg-notice" role="alert">
          {feedbackErr}
        </div>
      ) : null}

      {messages.map((msg) => {
        const isUser = msg.sender === 'CEO' || msg.sender === 'user';
        const canFeedback = !isUser && Array.isArray(msg.contentIds) && msg.contentIds.length > 0;
        const savedTone = feedbackDone[msg.key];

        return (
          <Card
            key={msg.key}
            outline={!isUser}
            className={isUser ? 'studio-msg studio-msg--user' : 'studio-msg'}
          >
            <div className="studio-msg-head">
              <div className="studio-msg-author-row">
                <span className="mg-label studio-msg-author">{msg.authorLabel}</span>
                {msg.modelUsed ? (
                  <span className="mg-chip">{msg.modelUsed}</span>
                ) : null}
                {msg.waitingUser ? <span className="mg-chip">Waiting for answer</span> : null}
              </div>
              {msg.timestamp ? (
                <span className="studio-mono studio-msg-time">{formatTimestamp(msg.timestamp)}</span>
              ) : null}
            </div>

            <SafeMarkdown text={msg.text} />

            {msg.attachedAssets && msg.attachedAssets.length > 0 ? (
              <div className="studio-msg-meta-row">
                {msg.attachedAssets.map((aid) => (
                  <span key={aid} className="mg-chip">
                    Asset: {aid}
                  </span>
                ))}
              </div>
            ) : null}

            {msg.redirectTo && onSelectAgent ? (
              <div className="studio-msg-meta-row">
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={() => onSelectAgent(msg.redirectTo!)}
                >
                  Open @{msg.redirectTo}
                </PillButton>
              </div>
            ) : null}

            {canFeedback ? (
              <div className="studio-msg-feedback">
                {savedTone ? (
                  <span className="studio-mono studio-hint">Feedback saved: {savedTone}</span>
                ) : (
                  <div className="studio-btn-group">
                    <PillButton
                      variant="quiet"
                      size="sm"
                      onClick={() => {
                        setFeedbackOpenKey(msg.key);
                        setFeedbackTone('up');
                      }}
                    >
                      Thumbs up
                    </PillButton>
                    <PillButton
                      variant="quiet"
                      size="sm"
                      onClick={() => {
                        setFeedbackOpenKey(msg.key);
                        setFeedbackTone('down');
                      }}
                    >
                      Thumbs down
                    </PillButton>
                  </div>
                )}

                {feedbackOpenKey === msg.key ? (
                  <div className="studio-inline-form">
                    <TextInput
                      aria-label="Feedback note"
                      placeholder={
                        feedbackTone === 'up'
                          ? 'What worked well? (optional)'
                          : 'What should change next time?'
                      }
                      value={feedbackNote}
                      onChange={(e) => setFeedbackNote(e.target.value)}
                    />
                    <PillButton
                      size="sm"
                      disabled={submitting}
                      onClick={() => void submitFeedback(msg)}
                    >
                      Save
                    </PillButton>
                    <PillButton
                      variant="quiet"
                      size="sm"
                      onClick={() => {
                        setFeedbackOpenKey(null);
                        setFeedbackNote('');
                      }}
                    >
                      Cancel
                    </PillButton>
                  </div>
                ) : null}
              </div>
            ) : null}
          </Card>
        );
      })}
    </div>
  );
}
