import type { HTMLAttributes } from 'react';
import { cx } from './cx';

export type StatusState = 'online' | 'working' | 'idle' | 'off' | 'error';

export interface StatusDotProps extends HTMLAttributes<HTMLSpanElement> {
  state: StatusState;
  label: string;
  /** Set true to add role="status" for live status announcements. */
  live?: boolean;
}

/** Monochrome dot/square indicator paired with a DM Mono status label. */
export function StatusDot({ state, label, live = false, className, ...rest }: StatusDotProps) {
  return (
    <span
      className={cx('mg-status', className)}
      data-state={state}
      role={live ? 'status' : undefined}
      {...rest}
    >
      {label}
    </span>
  );
}
