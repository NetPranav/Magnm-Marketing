import { useMemo, useState } from 'react';
import { Card, Dialog, Field, PillButton, Select, TextInput } from '@magnm/design/react';
import { contentFileUrl } from '../../api/review';
import type { ContentItem, PostingKitResult } from '../../types';
import { buildCopyableKitFields } from './reviewUtils';

export interface PostingKitDialogProps {
  open: boolean;
  onClose: () => void;
  kit: PostingKitResult | null;
  item: ContentItem;
  body: string;
}

/** Modal displaying the generated posting kit with per-field Copy buttons and file download links. */
export function PostingKitDialog({ open, onClose, kit, item, body }: PostingKitDialogProps) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copyErr, setCopyErr] = useState<string | null>(null);

  const fields = useMemo(
    () => (kit ? buildCopyableKitFields(kit.text, item, body) : []),
    [kit, item, body],
  );

  const handleCopy = async (fieldId: string, text: string) => {
    setCopyErr(null);
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(fieldId);
    } catch {
      setCopyErr('Could not copy to clipboard.');
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      subtitle={kit?.path || 'Posting kit'}
      title="Posting kit"
      className="studio-kit-dialog"
      actions={
        <PillButton variant="outline" size="sm" onClick={onClose}>
          Close
        </PillButton>
      }
    >
      {kit ? (
        <div className="studio-stack-sm">
          {copyErr ? (
            <div className="mg-notice" role="alert">
              {copyErr}
            </div>
          ) : null}

          {fields.map((field) => (
            <Card key={field.id} className="studio-stack-xs">
              <div className="studio-section-bar">
                <span className="mg-label">{field.label}</span>
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={() => void handleCopy(field.id, field.value)}
                >
                  {copiedId === field.id ? 'Copied' : `Copy ${field.label.toLowerCase()}`}
                </PillButton>
              </div>
              <pre className="studio-pre">{field.value}</pre>
            </Card>
          ))}

          <Card outline className="studio-stack-xs">
            <span className="mg-label">Files</span>
            {kit.files.length === 0 ? (
              <span className="studio-hint">No attached files.</span>
            ) : (
              <div className="studio-stack-xs">
                {kit.files.map((filePath) => (
                  <a
                    key={filePath}
                    href={contentFileUrl(item.id, filePath)}
                    download
                    className="studio-inline-link"
                  >
                    Download {filePath}
                  </a>
                ))}
              </div>
            )}
          </Card>
        </div>
      ) : null}
    </Dialog>
  );
}

export interface MarkPostedDialogProps {
  open: boolean;
  onClose: () => void;
  defaultPlatform: string;
  submitting: boolean;
  onSubmit: (url: string, platform: string) => void;
}

/** Modal to mark a content item as posted with live URL and platform. */
export function MarkPostedDialog({
  open,
  onClose,
  defaultPlatform,
  submitting,
  onSubmit,
}: MarkPostedDialogProps) {
  const [url, setUrl] = useState('');
  const [platform, setPlatform] = useState(defaultPlatform || 'linkedin');

  return (
    <Dialog
      open={open}
      onClose={onClose}
      subtitle="Publish log"
      title="Mark as posted"
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            disabled={submitting}
            onClick={() => onSubmit(url, platform)}
          >
            {submitting ? 'Saving...' : 'Confirm posted'}
          </PillButton>
        </>
      }
    >
      <div className="studio-stack-sm">
        <Field label="Published URL (optional)" htmlFor="mark-posted-url">
          <TextInput
            id="mark-posted-url"
            placeholder="https://..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
        <Field label="Platform" htmlFor="mark-posted-platform">
          <Select
            id="mark-posted-platform"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
          >
            <option value="linkedin">linkedin</option>
            <option value="x">x</option>
            <option value="instagram">instagram</option>
            <option value="youtube">youtube</option>
            <option value="blog">blog</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}

export interface RestoreConfirmDialogProps {
  version: number | null;
  onClose: () => void;
  submitting: boolean;
  onConfirm: (version: number) => void;
}

/** Confirmation modal before restoring a previous content version. */
export function RestoreConfirmDialog({
  version,
  onClose,
  submitting,
  onConfirm,
}: RestoreConfirmDialogProps) {
  return (
    <Dialog
      open={version !== null}
      onClose={onClose}
      subtitle="Version history"
      title={version !== null ? `Restore v${version}?` : 'Restore version'}
      actions={
        <>
          <PillButton variant="quiet" size="sm" onClick={onClose}>
            Cancel
          </PillButton>
          <PillButton
            size="sm"
            disabled={submitting || version === null}
            onClick={() => {
              if (version !== null) onConfirm(version);
            }}
          >
            {submitting ? 'Restoring...' : 'Confirm restore'}
          </PillButton>
        </>
      }
    >
      <p className="studio-hint">
        Restoring creates a new version containing the body from v{version}. Existing history and
        comments are preserved.
      </p>
    </Dialog>
  );
}

export interface ShortcutsDialogProps {
  open: boolean;
  onClose: () => void;
}

const SHORTCUT_ITEMS = [
  { key: 'J', desc: 'Move to next comment' },
  { key: 'K', desc: 'Move to previous comment' },
  { key: 'R', desc: 'Reply to focused comment' },
  { key: 'E', desc: 'Resolve or reopen focused comment' },
  { key: 'Esc', desc: 'Close popovers and dialogs' },
  { key: '?', desc: 'Toggle this shortcuts help' },
] as const;

/** Small help popover listing keyboard navigation shortcuts. */
export function ShortcutsDialog({ open, onClose }: ShortcutsDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      subtitle="Keyboard"
      title="Shortcuts"
      actions={
        <PillButton variant="outline" size="sm" onClick={onClose}>
          Close
        </PillButton>
      }
    >
      <div className="studio-stack-xs">
        <p className="studio-hint">
          Shortcuts are disabled while typing in any input or editor. Every control is also
          reachable by Tab.
        </p>
        <div className="studio-shortcut-list">
          {SHORTCUT_ITEMS.map((item) => (
            <div key={item.key} className="studio-shortcut-row">
              <kbd className="mg-chip">{item.key}</kbd>
              <span>{item.desc}</span>
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
