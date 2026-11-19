import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SafeMarkdown } from './SafeMarkdown';

describe('SafeMarkdown', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders paragraphs, lists, code, and safe http/https links', () => {
    const md = [
      'First paragraph with `inline_code` and **bold text**.',
      '',
      '- Bullet one',
      '- Bullet two with [Docs](https://example.com/docs)',
      '',
      '1. Step one',
      '2. Step two',
      '',
      '```ts',
      'const x = 1;',
      '```',
    ].join('\n');

    const { container } = render(<SafeMarkdown text={md} />);
    expect(screen.getByText('inline_code').tagName).toBe('CODE');
    expect(screen.getByText('bold text').tagName).toBe('STRONG');
    expect(screen.getByText('Bullet one').closest('ul')).toBeTruthy();
    expect(screen.getByText('Step one').closest('ol')).toBeTruthy();

    const link = screen.getByRole('link', { name: 'Docs' }) as HTMLAnchorElement;
    expect(link.href).toBe('https://example.com/docs');
    expect(link.target).toBe('_blank');
    expect(link.rel).toContain('noopener');
    expect(container.querySelector('pre code')?.textContent).toBe('const x = 1;');
  });

  it('prevents HTML injection and blocks javascript: or data: links', () => {
    const hostile = [
      '<script>window.__xss = true;</script>',
      '<img src=x onerror="alert(1)" />',
      'Click [bad](javascript:alert(1)) or [also bad](data:text/html,<script>alert(1)</script>)',
    ].join('\n\n');

    const { container } = render(<SafeMarkdown text={hostile} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container.textContent).toContain('<script>window.__xss = true;</script>');
    expect(container.textContent).toContain('bad');
  });
});
