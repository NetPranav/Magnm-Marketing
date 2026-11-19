import type { ButtonHTMLAttributes } from 'react';
import { cx } from './cx';

export type PillButtonVariant = 'primary' | 'outline' | 'quiet' | 'danger';
export type PillButtonSize = 'md' | 'sm';

export interface PillButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: PillButtonVariant;
  size?: PillButtonSize;
}

/** Pill-shaped action button following Magnm's monochrome palette. */
export function PillButton({
  variant = 'primary',
  size = 'md',
  type = 'button',
  className,
  children,
  ...rest
}: PillButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'mg-btn',
        variant !== 'primary' && `mg-btn--${variant}`,
        size === 'sm' && 'mg-btn--sm',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
