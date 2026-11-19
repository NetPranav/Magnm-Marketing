import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';
import { Display } from './Typography';

export interface SheetHeaderProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  page?: number | string;
  total?: number | string;
  subtitle?: ReactNode;
  title: ReactNode;
  lead?: ReactNode;
}

/** Page header with mono page counter, large serif title, and optional lead text. */
export function SheetHeader({
  page,
  total,
  subtitle,
  title,
  lead,
  className,
  ...rest
}: SheetHeaderProps) {
  const hasPage = page !== undefined;
  const pageLabel =
    hasPage && total !== undefined ? `Page ${page} of ${total}` : hasPage ? `Page ${page}` : null;

  return (
    <header className={cx('mg-sheet-header', className)} {...rest}>
      {(pageLabel || subtitle) && (
        <div className="mg-sheet-pageno">
          <span>{pageLabel}</span>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
      )}
      <Display big>{title}</Display>
      {lead ? <p className="mg-lead">{lead}</p> : null}
    </header>
  );
}
