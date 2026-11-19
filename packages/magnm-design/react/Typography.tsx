import type { HTMLAttributes } from 'react';
import { cx } from './cx';

export interface WordmarkProps extends HTMLAttributes<HTMLElement> {
  /** Element tag to render (defaults to span). */
  as?: 'h1' | 'span' | 'div';
  /** Compact scale for sidebars and top bars. */
  compact?: boolean;
}

/** Lowercase Instrument Serif "magnm" wordmark. */
export function Wordmark({
  as: Tag = 'span',
  compact = false,
  className,
  children = 'magnm',
  ...rest
}: WordmarkProps) {
  return (
    <Tag className={cx('mg-wordmark', compact && 'mg-wordmark--compact', className)} {...rest}>
      {children}
    </Tag>
  );
}

export interface LabelProps extends HTMLAttributes<HTMLElement> {
  /** Element tag to render (defaults to span). */
  as?: 'span' | 'div' | 'p';
}

/** Uppercase DM Mono section or metadata label. */
export function Label({ as: Tag = 'span', className, children, ...rest }: LabelProps) {
  return (
    <Tag className={cx('mg-label', className)} {...rest}>
      {children}
    </Tag>
  );
}

export interface DisplayProps extends HTMLAttributes<HTMLHeadingElement> {
  /** Large hero scale matching Pitch's h2.big. */
  big?: boolean;
  /** Heading tag level (defaults to h2). */
  as?: 'h1' | 'h2' | 'h3';
}

/** Instrument Serif display heading. */
export function Display({
  big = false,
  as: Tag = 'h2',
  className,
  children,
  ...rest
}: DisplayProps) {
  return (
    <Tag className={cx('mg-display', big && 'mg-display--big', className)} {...rest}>
      {children}
    </Tag>
  );
}
