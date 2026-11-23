import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { TextInput } from '@magnm/design/react';

interface SectionEntry {
  id: string;
  title: string;
}

/** Everything a person could search a section by: its title and the labels, buttons and field names inside it. */
function searchableText(section: HTMLElement, title: string): string {
  const parts: string[] = [title];
  section.querySelectorAll<HTMLElement>('label, legend, .mg-label, .studio-card-title, button, [aria-label]').forEach((el) => {
    parts.push(el.textContent ?? '');
    const aria = el.getAttribute('aria-label');
    if (aria) parts.push(aria);
  });
  return parts.join(' ').toLowerCase();
}

function sectionTitle(section: HTMLElement): string {
  const headingId = section.getAttribute('aria-labelledby');
  const heading = headingId ? section.ownerDocument.getElementById(headingId) : null;
  return (heading?.textContent ?? '').trim();
}

function matches(haystack: string, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return words.every((word) => haystack.includes(word));
}

export interface SettingsIndexProps {
  /** The element that holds the Settings sections. */
  bodyRef: RefObject<HTMLElement | null>;
}

/**
 * Sticky index for the long Settings page: jump links to every section and a search box that
 * hides sections whose title or inner labels do not match. It reads the sections from the page,
 * so panels can be added or change their contents without touching this file.
 */
export function SettingsIndex({ bodyRef }: SettingsIndexProps) {
  const [sections, setSections] = useState<SectionEntry[]>([]);
  const [query, setQuery] = useState('');
  const [visibleIds, setVisibleIds] = useState<Set<string> | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;
  const rootRef = useRef<HTMLDivElement | null>(null);

  const collect = useCallback(() => {
    const body = bodyRef.current;
    if (!body) return;
    const found: SectionEntry[] = [];
    const visible = new Set<string>();
    const active = queryRef.current.trim();
    body.querySelectorAll<HTMLElement>('section.studio-section[aria-labelledby]').forEach((section) => {
      const title = sectionTitle(section);
      const id = section.getAttribute('aria-labelledby') ?? '';
      if (!title || !id) return;
      found.push({ id, title });
      const show = !active || matches(searchableText(section, title), active);
      section.hidden = !show;
      if (show) visible.add(id);
    });
    setSections((prev) =>
      prev.length === found.length && prev.every((p, i) => p.id === found[i].id && p.title === found[i].title) ? prev : found,
    );
    setVisibleIds(active ? visible : null);
  }, [bodyRef]);

  // Re-run the filter when the search changes, and when panels add or change their content.
  useLayoutEffect(() => {
    collect();
  }, [collect, query]);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body || typeof MutationObserver === 'undefined') return undefined;
    const observer = new MutationObserver(() => collect());
    observer.observe(body, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [bodyRef, collect]);

  // Keep the bar just under the phone-width section tabs, which are sticky too.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const apply = () => {
      const tabs = document.querySelector<HTMLElement>('.studio-mobile-nav');
      root.style.setProperty('--settings-index-top', tabs ? `${tabs.offsetHeight}px` : '0px');
    };
    apply();
    window.addEventListener('resize', apply);
    return () => window.removeEventListener('resize', apply);
  }, []);

  const jumpTo = (id: string) => {
    const heading = document.getElementById(id);
    const section = heading?.closest('section') ?? heading;
    if (!heading || !section) return;
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    heading.setAttribute('tabindex', '-1');
    heading.focus({ preventScroll: true });
  };

  const onSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape' && query) {
      event.preventDefault();
      setQuery('');
    }
  };

  const shown = visibleIds ? sections.filter((s) => visibleIds.has(s.id)) : sections;
  const searching = query.trim().length > 0;

  return (
    <div className="studio-settings-index" ref={rootRef}>
      <div className="studio-settings-index-bar">
        <TextInput
          type="search"
          className="studio-settings-search"
          aria-label="Search settings"
          placeholder="Search settings"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onSearchKeyDown}
        />
        <nav aria-label="Settings sections" className="studio-settings-jump">
          <ul>
            {shown.map((entry) => (
              <li key={entry.id}>
                <button type="button" className="studio-settings-jump-link" onClick={() => jumpTo(entry.id)}>
                  {entry.title}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </div>
      <div className="studio-settings-index-status" role="status" aria-live="polite">
        {searching
          ? shown.length === 0
            ? `No settings match "${query.trim()}".`
            : `${shown.length} of ${sections.length} sections shown`
          : ''}
      </div>
    </div>
  );
}
