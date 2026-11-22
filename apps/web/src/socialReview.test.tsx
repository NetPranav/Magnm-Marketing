import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { MessageList } from './sections/team/MessageList';
import type { SocialRunRecord } from './types';

afterEach(cleanup);

it('shows one run card per thread, not one under every message', () => {
  const run = { id: 'r1', platform: 'x', recipe: 'search', argument: 'Cero topic', status: 'awaiting_start', session_id: 's1', pages_used: 0 } as SocialRunRecord;
  const messages = ['ask', 'answer'].map((text) => ({ key: text, sender: 'researcher', authorLabel: 'Researcher', text, sessionId: 's1' }));
  render(<MessageList messages={messages} emptyText="none" socialRuns={[run]} />);
  expect(screen.getAllByText('Cero topic')).toHaveLength(1);
});
