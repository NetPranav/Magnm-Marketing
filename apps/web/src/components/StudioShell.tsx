import { useEffect, useState, type ReactNode } from 'react';
import {
  NumberedTabs,
  PillButton,
  StatusDot,
  ThemeToggle,
  Wordmark,
  type StatusState,
  type TabItem,
} from '@magnm/design/react';
import type { PageLayout, SectionId } from '../router';

export interface StudioShellProps {
  items: TabItem[];
  current: SectionId;
  onSelect: (id: SectionId) => void;
  layout: PageLayout;
  engineState: StatusState;
  engineLabel: string;
  children: ReactNode;
}

function useIsDesktop(): boolean {
  const query = '(min-width: 900px)';
  const [isDesktop, setIsDesktop] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return true;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mq = window.matchMedia(query);
    const update = () => setIsDesktop(mq.matches);
    update();
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', update);
      return () => mq.removeEventListener('change', update);
    }
    mq.addListener(update);
    return () => mq.removeListener(update);
  }, []);

  return isDesktop;
}

/** Responsive Studio shell: 220px sidebar on desktop (>= 900px), sticky top tabs on mobile. */
export function StudioShell({
  items,
  current,
  onSelect,
  layout,
  engineState,
  engineLabel,
  children,
}: StudioShellProps) {
  const isDesktop = useIsDesktop();
  const [menuOpen, setMenuOpen] = useState(false);

  const handleSelect = (id: string) => {
    onSelect(id as SectionId);
  };

  const mainContent = (
    <main className="studio-main">
      <div className={layout === 'wide' ? 'mg-wide' : 'mg-wrap'}>{children}</div>
    </main>
  );

  if (isDesktop) {
    return (
      <div className="mg-root studio-shell">
        <aside className="studio-sidebar">
          <div className="studio-sidebar-top">
            <Wordmark compact />
            <nav aria-label="Studio sections">
              <NumberedTabs
                items={items}
                current={current}
                onSelect={handleSelect}
                orientation="vertical"
                ariaLabel="Studio sections"
              />
            </nav>
          </div>
          <div className="studio-sidebar-foot">
            <StatusDot state={engineState} label={engineLabel} live />
            <ThemeToggle />
          </div>
        </aside>
        {mainContent}
      </div>
    );
  }

  return (
    <div className="mg-root studio-mobile-shell">
      <header className="studio-mobile-head">
        <Wordmark compact />
        <div className="studio-mobile-actions">
          <StatusDot state={engineState} label={engineLabel} live />
          <PillButton
            variant="outline"
            size="sm"
            aria-expanded={menuOpen}
            aria-controls="studio-mobile-menu"
            onClick={() => setMenuOpen((prev) => !prev)}
          >
            Theme
          </PillButton>
        </div>
      </header>

      {menuOpen ? (
        <div id="studio-mobile-menu" className="studio-mobile-menu">
          <span className="studio-mobile-menu-label">Appearance</span>
          <ThemeToggle />
        </div>
      ) : null}

      <nav aria-label="Studio sections" className="studio-mobile-nav">
        <NumberedTabs
          items={items}
          current={current}
          onSelect={handleSelect}
          orientation="horizontal"
          ariaLabel="Studio sections"
        />
      </nav>

      {mainContent}
    </div>
  );
}
