import { useCallback, useEffect, useState } from 'react';

export type SectionId =
  | 'today'
  | 'team'
  | 'research'
  | 'plan'
  | 'create'
  | 'review'
  | 'library'
  | 'repos'
  | 'settings';

export type PageLayout = 'read' | 'wide';

export interface SectionSpec {
  id: SectionId;
  label: string;
  layout: PageLayout;
  coming: string;
}

export const SECTIONS: SectionSpec[] = [
  { id: 'today', label: 'Today', layout: 'read', coming: '' },
  { id: 'team', label: 'Team', layout: 'wide', coming: 'Team room, agent chats, and editable agent memory are coming here.' },
  { id: 'research', label: 'Research', layout: 'read', coming: 'Web research, market radar, and watch topics are coming here.' },
  { id: 'plan', label: 'Plan', layout: 'read', coming: 'Briefs, weekly plans, and task splitting are coming here.' },
  { id: 'create', label: 'Create', layout: 'read', coming: 'Drafting posts, blogs, visual cards, and videos is coming here.' },
  { id: 'review', label: 'Review', layout: 'wide', coming: 'Review Studio with comments, versions, platform previews, and posting kits is coming here.' },
  { id: 'library', label: 'Library', layout: 'read', coming: 'Uploaded brand assets and generated outputs are coming here.' },
  { id: 'repos', label: 'Repos', layout: 'read', coming: 'Connected repositories, recent changes, and product rules are coming here.' },
  { id: 'settings', label: 'Settings', layout: 'read', coming: 'Provider configuration, model defaults, and engine checks are coming here.' },
];

export interface Route {
  section: SectionId;
  param: string | null;
}

const SECTION_SET = new Set<string>(SECTIONS.map((s) => s.id));

/** Parse a URL hash such as "#/review/abc" into a valid Route, defaulting to Today. */
export function parseHash(hash: string): Route {
  const cleaned = hash.replace(/^#\/?/, '').trim();
  if (!cleaned) return { section: 'today', param: null };
  const [rawSection, ...rest] = cleaned.split('/');
  const section = rawSection.toLowerCase();
  if (!SECTION_SET.has(section)) return { section: 'today', param: null };
  const rawParam = rest.join('/').trim();
  if (!rawParam) return { section: section as SectionId, param: null };
  try {
    return { section: section as SectionId, param: decodeURIComponent(rawParam) };
  } catch {
    return { section: section as SectionId, param: rawParam };
  }
}

/** Subscribe to window.location.hash and provide section navigation. */
export function useRoute(): Route & { navigate: (section: SectionId, param?: string | null) => void } {
  const [route, setRoute] = useState<Route>(() =>
    typeof window !== 'undefined' ? parseHash(window.location.hash) : { section: 'today', param: null },
  );

  useEffect(() => {
    const onHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = useCallback((section: SectionId, param?: string | null) => {
    const nextHash = param ? `#/${section}/${encodeURIComponent(param)}` : `#/${section}`;
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
    setRoute({ section, param: param ?? null });
  }, []);

  return { ...route, navigate };
}
