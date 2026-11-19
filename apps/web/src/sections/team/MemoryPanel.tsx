import { useCallback, useEffect, useState } from 'react';
import { Card, Checkbox, PillButton, Row, RowList, TextInput } from '@magnm/design/react';
import {
  createMemory,
  deleteMemory,
  listMemory,
  updateMemory,
  type AgentMemoryItem,
} from '../../api/team';

export interface MemoryPanelProps {
  agentId: string;
  agentName: string;
}

/** Small per-agent memory panel to view, add, edit, pin, and delete memory notes. */
export function MemoryPanel({ agentId, agentName }: MemoryPanelProps) {
  const [items, setItems] = useState<AgentMemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newText, setNewText] = useState('');
  const [newPinned, setNewPinned] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listMemory(agentId);
      setItems(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agent memory.');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleAdd = async () => {
    const clean = newText.trim();
    if (!clean) return;
    setBusy(true);
    setError(null);
    try {
      await createMemory(agentId, clean, newPinned);
      setNewText('');
      setNewPinned(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save memory.');
    } finally {
      setBusy(false);
    }
  };

  const handleTogglePin = async (item: AgentMemoryItem) => {
    setBusy(true);
    setError(null);
    try {
      await updateMemory(item.id, { pinned: !item.pinned });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update memory.');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveEdit = async (memoryId: string) => {
    const clean = editingText.trim();
    if (!clean) return;
    setBusy(true);
    setError(null);
    try {
      await updateMemory(memoryId, { text: clean });
      setEditingId(null);
      setEditingText('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update memory.');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (memoryId: string) => {
    setBusy(true);
    setError(null);
    try {
      await deleteMemory(memoryId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete memory.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card outline className="studio-memory-panel">
      <div className="studio-card-head">
        <div>
          <div className="mg-label">Agent memory</div>
          <div className="studio-card-title">{agentName} preferences &amp; feedback</div>
        </div>
        <span className="mg-chip">{items.length} notes</span>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <div className="studio-memory-add">
        <TextInput
          aria-label={`New memory for ${agentName}`}
          placeholder={`Remember something for @${agentId}...`}
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
        />
        <div className="studio-memory-add-actions">
          <Checkbox
            label="Pin"
            checked={newPinned}
            onChange={(e) => setNewPinned(e.target.checked)}
          />
          <PillButton
            size="sm"
            disabled={busy || !newText.trim()}
            onClick={() => void handleAdd()}
          >
            Add note
          </PillButton>
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="mg-empty">Loading memory notes.</div>
      ) : items.length === 0 ? (
        <div className="mg-empty">No memory notes yet for @{agentId}.</div>
      ) : (
        <RowList aria-label={`${agentName} memory notes`}>
          {items.map((item) => (
            <Row
              key={item.id}
              title={
                editingId === item.id ? (
                  <div className="studio-inline-form">
                    <TextInput
                      aria-label="Edit memory note"
                      value={editingText}
                      onChange={(e) => setEditingText(e.target.value)}
                    />
                    <PillButton
                      size="sm"
                      disabled={busy || !editingText.trim()}
                      onClick={() => void handleSaveEdit(item.id)}
                    >
                      Save
                    </PillButton>
                    <PillButton
                      variant="quiet"
                      size="sm"
                      onClick={() => {
                        setEditingId(null);
                        setEditingText('');
                      }}
                    >
                      Cancel
                    </PillButton>
                  </div>
                ) : (
                  item.text
                )
              }
              description={
                <span className="studio-mono">
                  {item.source}
                  {item.pinned ? ' · pinned' : ''}
                </span>
              }
              meta={
                <div className="studio-row-actions">
                  <PillButton
                    variant="quiet"
                    size="sm"
                    disabled={busy}
                    onClick={() => void handleTogglePin(item)}
                  >
                    {item.pinned ? 'Unpin' : 'Pin'}
                  </PillButton>
                  <PillButton
                    variant="quiet"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setEditingId(item.id);
                      setEditingText(item.text);
                    }}
                  >
                    Edit
                  </PillButton>
                  <PillButton
                    variant="danger"
                    size="sm"
                    disabled={busy}
                    onClick={() => void handleDelete(item.id)}
                  >
                    Delete
                  </PillButton>
                </div>
              }
            />
          ))}
        </RowList>
      )}
    </Card>
  );
}
