import { useCallback, useEffect, useState } from 'react';

export type SectionId =
  | 'today'
  | 'team'
  | 'research'
  | 'plan'
  | 'create'
  | 'review'
  | 'pitch'
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
  { id: 'pitch', label: 'Pitch', layout: 'wide', coming: 'Client proposals, quote forms, present mode, and PDF exports.' },
  { id: 'library', label: 'Library', layout: 'read', coming: 'Uploaded brand assets and generated outputs are coming here.' },
  { id: 'repos', label: 'Repos', layout: 'read', coming: 'Connected repositories, recent changes, and product rules are coming here.' },
  { id: 'settings', label: 'Settings', layout: 'read', coming: 'Provider configuration, model defaults, and engine checks are coming here.' },
];

export interface Route {
  section: SectionId;
  param: string | null;
}

const SECTION_SET = new Set<string>(SECTIONS.map((s) => s.id));

/** Parse a URL hash such as "#/review/abc" or "#/pitch/prop_1/present" into a valid Route, defaulting to Today. */
export function parseHash(hash: string): Route {
  const cleaned = hash.replace(/^#\/?/, '').trim();
  if (!cleaned) return { section: 'today', param: null };
  const [rawSection, ...rest] = cleaned.split('/');
  const section = rawSection.toLowerCase();
  if (!SECTION_SET.has(section)) return { section: 'today', param: null };
  const decodedParam = rest
    .filter((seg) => seg.length > 0)
    .map((seg) => {
      try {
        return decodeURIComponent(seg);
      } catch {
        return seg;
      }
    })
    .join('/')
    .trim();
  if (!decodedParam) return { section: section as SectionId, param: null };
  return { section: section as SectionId, param: decodedParam };
}

/** Parse a pitch route param ("<id>" or "<id>/present") into proposalId and present flag. */
export function parsePitchParam(param: string | null): { proposalId: string | null; present: boolean } {
  if (!param) return { proposalId: null, present: false };
  const parts = param.split('/').filter(Boolean);
  if (parts.length === 0) return { proposalId: null, present: false };
  if (parts.length >= 2 && parts[parts.length - 1].toLowerCase() === 'present') {
    const id = parts.slice(0, -1).join('/').trim();
    return { proposalId: id || null, present: Boolean(id) };
  }
  return { proposalId: parts.join('/').trim() || null, present: false };
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
    const encodedParam = param
      ? param
          .split('/')
          .filter(Boolean)
          .map((seg) => encodeURIComponent(seg))
          .join('/')
      : '';
    const nextHash = encodedParam ? `#/${section}/${encodedParam}` : `#/${section}`;
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
    setRoute({ section, param: param ?? null });
  }, []);

  return { ...route, navigate };
}
