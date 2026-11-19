import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';

export interface BottomBarProps extends HTMLAttributes<HTMLDivElement> {
  caption: ReactNode;
  value: ReactNode;
  children?: ReactNode;
}

/** Sticky translucent bottom bar with caption, serif value, and right-aligned actions. */
export function BottomBar({ caption, value, children, className, ...rest }: BottomBarProps) {
  return (
    <div className={cx('mg-bar', className)} {...rest}>
      <div className="mg-bar-inner">
        <div>
          <small className="mg-bar-caption">{caption}</small>
          <strong className="mg-bar-value">{value}</strong>
        </div>
        {children ? <div className="mg-bar-actions">{children}</div> : null}
      </div>
    </div>
  );
}
