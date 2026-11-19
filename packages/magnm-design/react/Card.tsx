import type { HTMLAttributes } from 'react';
import { cx } from './cx';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Highlight with selected tint-2 background. */
  selected?: boolean;
  /** Render with bg fill and hairline outline border. */
  outline?: boolean;
}

/** Tinted surface container with 14px rounded corners. */
export function Card({
  selected = false,
  outline = false,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <div
      className={cx(
        'mg-card',
        selected && 'mg-card--selected',
        outline && 'mg-card--outline',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
