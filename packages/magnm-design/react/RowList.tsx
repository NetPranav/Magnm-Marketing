import type { ButtonHTMLAttributes, HTMLAttributes, MouseEventHandler, ReactNode } from 'react';
import { cx } from './cx';

export interface RowListProps extends HTMLAttributes<HTMLDivElement> {}

/** Vertical list of hairline-separated rows. */
export function RowList({ className, children, ...rest }: RowListProps) {
  return (
    <div className={cx('mg-rows', className)} {...rest}>
      {children}
    </div>
  );
}

export interface RowProps extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'onClick'> {
  selected?: boolean;
  meta?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  onClick?: MouseEventHandler<HTMLElement>;
  disabled?: boolean;
}

/** Single row item; renders as a button when onClick is provided. `selected` sets aria-current. */
export function Row({
  selected = false,
  meta,
  title,
  description,
  onClick,
  disabled,
  className,
  children,
  ...rest
}: RowProps) {
  const content = (
    <>
      <div className="mg-row-main">
        {title ? <span className="mg-row-title">{title}</span> : null}
        {description ? <span className="mg-row-desc">{description}</span> : null}
        {children}
      </div>
      {meta !== undefined && meta !== null ? <span className="mg-row-meta">{meta}</span> : null}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={cx('mg-row', className)}
        aria-current={selected ? 'true' : undefined}
        disabled={disabled}
        onClick={onClick}
        {...(rest as ButtonHTMLAttributes<HTMLButtonElement>)}
      >
        {content}
      </button>
    );
  }

  return (
    <div
      className={cx('mg-row', className)}
      aria-current={selected ? 'true' : undefined}
      {...rest}
    >
      {content}
    </div>
  );
}
