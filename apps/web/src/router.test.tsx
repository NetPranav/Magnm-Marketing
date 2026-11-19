import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SECTIONS, parseHash, useRoute } from './router';

function RouteProbe() {
  const { section, param, navigate } = useRoute();
  return (
    <div>
      <span data-testid="section">{section}</span>
      <span data-testid="param">{param ?? 'none'}</span>
      <button type="button" onClick={() => navigate('review', 'post 42')}>
        Go Review
      </button>
    </div>
  );
}

describe('router', () => {
  it('parses known routes, parameters, and defaults unknown routes to today', () => {
    expect(SECTIONS.map((s) => s.id)).toEqual([
      'today',
      'team',
      'research',
      'plan',
      'create',
      'review',
      'library',
      'repos',
      'settings',
    ]);

    expect(parseHash('')).toEqual({ section: 'today', param: null });
    expect(parseHash('#/')).toEqual({ section: 'today', param: null });
    expect(parseHash('#/today')).toEqual({ section: 'today', param: null });
    expect(parseHash('#/review/item-99')).toEqual({ section: 'review', param: 'item-99' });
    expect(parseHash('#/review/hello%20world')).toEqual({
      section: 'review',
      param: 'hello world',
    });
    expect(parseHash('#/TEAM')).toEqual({ section: 'team', param: null });
    expect(parseHash('#/nonexistent/path')).toEqual({ section: 'today', param: null });
  });

  it('navigates and updates on hashchange', () => {
    window.location.hash = '#/today';
    render(<RouteProbe />);
    expect(screen.getByTestId('section').textContent).toBe('today');

    fireEvent.click(screen.getByText('Go Review'));
    expect(window.location.hash).toBe('#/review/post%2042');
    fireEvent(window, new HashChangeEvent('hashchange'));
    expect(screen.getByTestId('section').textContent).toBe('review');
    expect(screen.getByTestId('param').textContent).toBe('post 42');
  });
});
