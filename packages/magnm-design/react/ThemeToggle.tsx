import { useCallback, useEffect, useState } from 'react';
import { cx } from './cx';

export type ThemeChoice = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'magnm-theme';

/** Window event fired when any useTheme() instance changes the choice, so all toggles stay in sync. */
const THEME_EVENT = 'magnm-theme-change';

const CHOICES: ThemeChoice[] = ['light', 'dark', 'system'];
const LABELS: Record<ThemeChoice, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
};

function isThemeChoice(value: string | null): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system';
}

function readStoredChoice(): ThemeChoice {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return 'light';
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeChoice(raw) ? raw : 'light';
  } catch {
    return 'light';
  }
}

function writeStoredChoice(choice: ThemeChoice): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    }
  } catch {
    // Ignore storage errors (private mode or restricted context).
  }
}

/** Resolve 'light' | 'dark' | 'system' to an explicit 'light' or 'dark' theme. */
export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice !== 'system') return choice;
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return 'light';
}

/** Read the stored theme preference and apply data-theme before React mounts. */
export function applyStoredTheme(): ResolvedTheme {
  const choice = readStoredChoice();
  const resolved = resolveTheme(choice);
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = resolved;
  }
  return resolved;
}

export interface UseThemeResult {
  theme: ThemeChoice;
  resolvedTheme: ResolvedTheme;
  setTheme: (next: ThemeChoice) => void;
}

/** Manage theme preference ('light' | 'dark' | 'system') in localStorage and documentElement. */
export function useTheme(): UseThemeResult {
  const [theme, setThemeState] = useState<ThemeChoice>(() => readStoredChoice());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() =>
    resolveTheme(readStoredChoice()),
  );

  const apply = useCallback((choice: ThemeChoice) => {
    const resolved = resolveTheme(choice);
    setResolvedTheme(resolved);
    if (typeof document !== 'undefined') {
      document.documentElement.dataset.theme = resolved;
    }
  }, []);

  const setTheme = useCallback(
    (next: ThemeChoice) => {
      setThemeState(next);
      writeStoredChoice(next);
      apply(next);
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent<ThemeChoice>(THEME_EVENT, { detail: next }));
      }
    },
    [apply],
  );

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onOtherChange = (event: Event) => {
      const next = (event as CustomEvent<ThemeChoice>).detail;
      if (isThemeChoice(next)) setThemeState(next);
    };
    window.addEventListener(THEME_EVENT, onOtherChange);
    return () => window.removeEventListener(THEME_EVENT, onOtherChange);
  }, []);

  useEffect(() => {
    apply(theme);
    if (theme !== 'system' || typeof window === 'undefined' || !window.matchMedia) {
      return undefined;
    }
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => apply('system');
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, [theme, apply]);

  return { theme, resolvedTheme, setTheme };
}

export interface ThemeToggleProps {
  className?: string;
  ariaLabel?: string;
}

/** Segmented Light / Dark / System selector bound to useTheme(). */
export function ThemeToggle({ className, ariaLabel = 'Theme' }: ThemeToggleProps) {
  const { theme, setTheme } = useTheme();

  return (
    <div className={cx('mg-theme-toggle', className)} role="group" aria-label={ariaLabel}>
      {CHOICES.map((choice) => (
        <button
          key={choice}
          type="button"
          aria-pressed={theme === choice}
          onClick={() => setTheme(choice)}
        >
          {LABELS[choice]}
        </button>
      ))}
    </div>
  );
}
