import { useEffect, useState } from 'react';
import { Card, Checkbox, PillButton, Select, TextInput } from '@magnm/design/react';
import type { RosterAgent } from '../../api/team';
import type {
  ReviewChecklistItem,
  ReviewDiffResponse,
  ReviewFinding,
  ReviewVersionSummary,
} from '../../types';
import { formatTimestamp } from '../../ui/format';
import { resolveAuthorName } from './reviewUtils';

export interface ChecklistPanelProps {
  checklist: ReviewChecklistItem[];
  warnings: ReviewFinding[];
  onToggleItem: (itemKey: string, nextChecked: boolean) => Promise<void>;
  onAddManualItem: (label: string) => Promise<void>;
}

/** Checklist rail: advisory Reviewer warnings plus auto, platform, and manual checklist items. */
export function ChecklistPanel({
  checklist,
  warnings,
  onToggleItem,
  onAddManualItem,
}: ChecklistPanelProps) {
  const [newLabel, setNewLabel] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleAdd = async () => {
    const clean = newLabel.trim();
    if (!clean) return;
    setSubmitting(true);
    try {
      await onAddManualItem(clean);
      setNewLabel('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="studio-stack-sm" aria-label="Checklist">
      <h2 className="studio-section-title">Checklist</h2>

      {warnings.length > 0 ? (
        <Card className="studio-stack-xs">
          <span className="mg-label">Advisory warnings ({warnings.length})</span>
          <span className="studio-hint">Warnings never block readiness or posting.</span>
          {warnings.map((w, idx) => (
            <div key={`warn-${idx}`} className="studio-warning-item">
              <span className="studio-mono">{w.code || w.product || 'warn'}</span>
              <span>{w.message}</span>
            </div>
          ))}
        </Card>
      ) : null}

      <Card outline className="studio-stack-xs">
        {checklist.length === 0 ? (
          <div className="studio-hint">No checklist items yet.</div>
        ) : (
          <div className="studio-checklist-items">
            {checklist.map((item) => (
              <div key={item.item_key} className="studio-checklist-row">
                <Checkbox
                  label={item.label}
                  checked={item.checked}
                  onChange={() => void onToggleItem(item.item_key, !item.checked)}
                />
                <span className="studio-mono">{item.source}</span>
              </div>
            ))}
          </div>
        )}

        <div className="studio-inline-form">
          <TextInput
            aria-label="New checklist item"
            placeholder="Add manual check..."
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                void handleAdd();
              }
            }}
          />
          <PillButton
            variant="outline"
            size="sm"
            disabled={submitting || !newLabel.trim()}
            onClick={() => void handleAdd()}
          >
            Add
          </PillButton>
        </div>
      </Card>
    </div>
  );
}

export interface VersionsPanelProps {
  versions: ReviewVersionSummary[];
  currentVersion: number;
  roster: RosterAgent[];
  diff: ReviewDiffResponse | null;
  diffLoading: boolean;
  onCompare: (fromVer: number, toVer: number) => void;
  onRequestRestore: (version: number) => void;
}

/** Versions rail: version history list, two-version word diff viewer, and restore trigger. */
export function VersionsPanel({
  versions,
  currentVersion,
  roster,
  diff,
  diffLoading,
  onCompare,
  onRequestRestore,
}: VersionsPanelProps) {
  const ordered = [...versions].sort((a, b) => b.version - a.version);
  const oldest = versions.length > 0 ? versions[0].version : 1;
  const newest = versions.length > 0 ? versions[versions.length - 1].version : 1;

  const [fromVer, setFromVer] = useState<number>(oldest);
  const [toVer, setToVer] = useState<number>(newest);

  useEffect(() => {
    if (versions.length >= 2) {
      setFromVer(versions[versions.length - 2].version);
      setToVer(versions[versions.length - 1].version);
    } else if (versions.length === 1) {
      setFromVer(versions[0].version);
      setToVer(versions[0].version);
    }
  }, [versions]);

  return (
    <div className="studio-stack-sm" aria-label="Versions">
      <h2 className="studio-section-title">Versions ({versions.length})</h2>

      <Card outline className="studio-stack-xs">
        <div className="studio-stack-xs">
          {ordered.map((ver) => {
            const isCurrent = ver.version === currentVersion;
            return (
              <div key={ver.version} className="studio-version-row">
                <div className="studio-stack-xs">
                  <div className="studio-msg-author-row">
                    <span className="mg-label">v{ver.version}</span>
                    {isCurrent ? <span className="mg-chip">Current</span> : null}
                    {ver.created_by ? (
                      <span className="studio-mono">
                        {resolveAuthorName(ver.created_by, roster)}
                      </span>
                    ) : null}
                  </div>
                  {ver.notes ? <span className="studio-hint">{ver.notes}</span> : null}
                  {ver.created_at ? (
                    <span className="studio-mono">{formatTimestamp(ver.created_at)}</span>
                  ) : null}
                </div>
                {!isCurrent ? (
                  <PillButton
                    variant="outline"
                    size="sm"
                    onClick={() => onRequestRestore(ver.version)}
                  >
                    Restore
                  </PillButton>
                ) : null}
              </div>
            );
          })}
        </div>

        {versions.length >= 2 ? (
          <div className="studio-diff-controls">
            <span className="mg-label">Word diff</span>
            <div className="studio-diff-selects">
              <Select
                aria-label="Compare from version"
                value={String(fromVer)}
                onChange={(e) => setFromVer(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={`from-${v.version}`} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </Select>
              <span className="studio-mono">to</span>
              <Select
                aria-label="Compare to version"
                value={String(toVer)}
                onChange={(e) => setToVer(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={`to-${v.version}`} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </Select>
              <PillButton
                variant="outline"
                size="sm"
                disabled={diffLoading}
                onClick={() => onCompare(fromVer, toVer)}
              >
                {diffLoading ? 'Comparing...' : 'Compare'}
              </PillButton>
            </div>
          </div>
        ) : null}

        {diff ? (
          <div className="studio-diff-box" data-testid="version-diff-view">
            <div className="studio-mono">
              Diff v{diff.from_version} to v{diff.to_version}
            </div>
            <div className="studio-diff-text">
              {diff.ops.map((op, idx) => {
                if (op.op === 'insert') {
                  return (
                    <ins key={`op-${idx}`} className="studio-diff-ins">
                      {op.text}
                    </ins>
                  );
                }
                if (op.op === 'delete') {
                  return (
                    <del key={`op-${idx}`} className="studio-diff-del">
                      {op.text}
                    </del>
                  );
                }
                return <span key={`op-${idx}`}>{op.text}</span>;
              })}
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
