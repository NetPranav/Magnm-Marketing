import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MentionComposer } from './MentionComposer';

const AGENTS = [
  { id: 'cmo', name: 'CMO' },
  { id: 'researcher', name: 'Researcher' },
  { id: 'writer', name: 'Writer' },
];

function Harness({ onSubmit }: { onSubmit: (val: string) => void }) {
  const [val, setVal] = useState('');
  return (
    <MentionComposer
      value={val}
      onChange={setVal}
      onSubmit={() => onSubmit(val)}
      agents={AGENTS}
    />
  );
}

describe('MentionComposer', () => {
  afterEach(() => {
    cleanup();
  });

  it('opens @mention autocomplete, navigates with keyboard, and inserts chosen agent', () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    const textarea = screen.getByRole('textbox', { name: 'Message composer' }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Hello @', selectionStart: 7 } });

    const listbox = screen.getByRole('listbox', { name: 'Mention an agent' });
    expect(listbox).toBeTruthy();
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);

    // Move down to @researcher and press Enter to insert (should NOT submit message)
    fireEvent.keyDown(textarea, { key: 'ArrowDown' });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(textarea.value).toBe('Hello @researcher ');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('filters mentions, closes on Escape, and submits on Enter while keeping Shift+Enter for newline', () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    const textarea = screen.getByRole('textbox', { name: 'Message composer' }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: '@wr', selectionStart: 3 } });

    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option').textContent).toContain('@writer');

    fireEvent.keyDown(textarea, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();

    // Shift+Enter does not submit
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();

    // Enter submits when autocomplete is closed
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledWith('@wr');
  });
});
