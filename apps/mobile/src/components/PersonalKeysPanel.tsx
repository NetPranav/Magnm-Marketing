import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Field, Label, PillButton, Row, RowList, TextInput } from '@magnm/design/react';
import type { NativeBridge } from '../native';
import {
  PersonalKeysError,
  addPersonalKey,
  listPersonalKeys,
  removePersonalKey,
  syncPersonalKeys,
  type PersonalKeyRow,
} from '../personalKeys';

const SYNC_EVERY_MS = 60_000;

export interface PersonalKeysPanelProps {
  bridge: NativeBridge;
  signedIn: boolean;
  /** Called after the keys on this phone changed (saved, removed or synced), with whether one is left. */
  onChanged?: (hasKey: boolean) => void;
}

/** Personal NVIDIA keys: add or remove on either device and both have them. Saving or removing never leaves the screen. */
export function PersonalKeysPanel({ bridge, signedIn, onChanged }: PersonalKeysPanelProps) {
  const [rows, setRows] = useState<PersonalKeyRow[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  const refresh = useCallback(async () => {
    const next = await listPersonalKeys(bridge);
    if (!alive.current) return;
    setRows(next);
    changed.current?.(next.length > 0);
  }, [bridge]);

  useEffect(() => {
    alive.current = true;
    void refresh().catch(() => undefined);
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    if (!signedIn) return undefined;
    let stopped = false;
    const tick = async () => {
      const out = await syncPersonalKeys(bridge);
      if (!stopped && out) await refresh().catch(() => undefined);
    };
    void tick();
    const timer = setInterval(() => void tick(), SYNC_EVERY_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [bridge, signedIn, refresh]);

  const handleAdd = async () => {
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      const res = await addPersonalKey(bridge, input.trim());
      setInput('');
      await refresh();
      setNote(
        !signedIn
          ? 'Key saved on this phone. Sign in to share it with your laptop.'
          : res.uploaded
            ? 'Key saved and shared with your laptop.'
            : 'Key saved on this phone. It will be shared when you are online.',
      );
    } catch (err) {
      setError(err instanceof PersonalKeysError ? err.message : 'Could not save the key.');
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const handleRemove = async (slot: string) => {
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      const res = await removePersonalKey(bridge, slot);
      await refresh();
      setNote(res.removedInCloud || !signedIn ? 'Key removed.' : 'Key removed here. It will be removed on your laptop when you are online.');
    } catch {
      setError('Could not remove the key.');
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <Card>
      <div className="mob-stack-sm">
        <Label>Personal NVIDIA NIM key</Label>
        <span className="mob-mono">
          {rows.length > 0
            ? signedIn
              ? 'Stored in secure store (never shown). Shared with your laptop.'
              : 'Stored in secure store (never shown).'
            : 'Not configured — Phone Lite uses shared team AI when signed in.'}
        </span>
        {error ? (
          <div className="mob-banner-error" role="alert">
            {error}
          </div>
        ) : null}
        {note ? <span className="mob-mono">{note}</span> : null}
        {rows.length > 0 ? (
          <RowList>
            {rows.map((row) => (
              <Row
                key={row.name}
                title={`nvapi-${row.label}`}
                description={
                  !row.shareable
                    ? 'This phone only (unusual length, not shared)'
                    : row.synced
                      ? 'Shared with your laptop'
                      : signedIn
                        ? 'Waiting to share'
                        : 'This phone only'
                }
                meta={
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    aria-label={rows.length === 1 ? 'Remove key' : `Remove key ${row.label}`}
                    onClick={() => void handleRemove(row.name)}
                  >
                    {rows.length === 1 ? 'Remove key' : 'Remove'}
                  </PillButton>
                }
              />
            ))}
          </RowList>
        ) : null}
        <Field
          label={rows.length > 0 ? 'Add another key' : 'Update personal key'}
          htmlFor="nim-personal-key"
          hint="Stored only in the native secure keystore."
        >
          <TextInput
            id="nim-personal-key"
            type="password"
            autoComplete="off"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="nvapi-..."
          />
        </Field>
        <div className="mob-actions">
          <PillButton size="sm" disabled={!input.trim() || busy} onClick={() => void handleAdd()}>
            Save key
          </PillButton>
        </div>
      </div>
    </Card>
  );
}
