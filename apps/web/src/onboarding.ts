/** First-run onboarding state for the laptop app: remembered in this browser profile. */

const SEEN_KEY = 'magnm-onboarding-seen';

/** Window event Settings > Account fires to bring the welcome screen back. */
export const SHOW_ONBOARDING_EVENT = 'magnm:show-onboarding';

export function hasSeenOnboarding(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === '1';
  } catch {
    return false;
  }
}

export function markOnboardingSeen(): void {
  try {
    window.localStorage.setItem(SEEN_KEY, '1');
  } catch {
    // Storage may be unavailable; the welcome screen then simply shows again next time.
  }
}

/** Forget that onboarding was seen and ask the app to show it now. */
export function resetOnboarding(): void {
  try {
    window.localStorage.removeItem(SEEN_KEY);
  } catch {
    // Ignore storage errors.
  }
}

export function requestOnboarding(): void {
  resetOnboarding();
  window.dispatchEvent(new CustomEvent(SHOW_ONBOARDING_EVENT));
}
