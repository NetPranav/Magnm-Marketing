import { useRef, type HTMLAttributes, type KeyboardEvent } from 'react';
import { cx } from './cx';

export interface TabItem {
  id: string;
  label: string;
}

export interface NumberedTabsProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onSelect'> {
  items: TabItem[];
  current: string;
  onSelect: (id: string) => void;
  orientation?: 'horizontal' | 'vertical';
  ariaLabel: string;
}

/** Numbered tablist with roving tabindex and arrow-key navigation. */
export function NumberedTabs({
  items,
  current,
  onSelect,
  orientation = 'horizontal',
  ariaLabel,
  className,
  onKeyDown,
  ...rest
}: NumberedTabsProps) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = Math.max(
    0,
    items.findIndex((item) => item.id === current),
  );

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented || items.length === 0) return;

    const prevKey = orientation === 'vertical' ? 'ArrowUp' : 'ArrowLeft';
    const nextKey = orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight';
    let targetIndex: number | null = null;

    if (event.key === nextKey) {
      targetIndex = (activeIndex + 1) % items.length;
    } else if (event.key === prevKey) {
      targetIndex = (activeIndex - 1 + items.length) % items.length;
    } else if (event.key === 'Home') {
      targetIndex = 0;
    } else if (event.key === 'End') {
      targetIndex = items.length - 1;
    }

    if (targetIndex === null) return;
    event.preventDefault();
    const target = items[targetIndex];
    onSelect(target.id);
    buttonRefs.current[targetIndex]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation={orientation}
      className={cx('mg-tabs', orientation === 'vertical' && 'mg-tabs--vertical', className)}
      onKeyDown={handleKeyDown}
      {...rest}
    >
      {items.map((item, idx) => {
        const selected = item.id === current;
        return (
          <button
            key={item.id}
            ref={(el) => {
              buttonRefs.current[idx] = el;
            }}
            type="button"
            role="tab"
            className="mg-tab"
            aria-selected={selected}
            tabIndex={selected || (activeIndex === idx && items[idx]?.id !== current) ? 0 : -1}
            onClick={() => onSelect(item.id)}
          >
            <i>{idx + 1}</i> {item.label}
          </button>
        );
      })}
    </div>
  );
}
