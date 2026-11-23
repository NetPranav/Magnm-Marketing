import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { usePersonalKeysSync } from './usePersonalKeysSync';

vi.mock('../../api/keys', () => ({ applyPendingKeys: vi.fn() }));
import { applyPendingKeys } from '../../api/keys';

function Probe({ enabled }: { enabled: boolean }) {
  usePersonalKeysSync(enabled);
  return null;
}

describe('usePersonalKeysSync', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(applyPendingKeys).mockReset();
    vi.mocked(applyPendingKeys).mockResolvedValue(0);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('checks on start and then every 15 seconds while signed in', async () => {
    render(<Probe enabled />);
    expect(applyPendingKeys).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(applyPendingKeys).toHaveBeenCalledTimes(2);
  });

  it('does nothing when signed out and stops on unmount', async () => {
    const { unmount } = render(<Probe enabled={false} />);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(applyPendingKeys).not.toHaveBeenCalled();
    cleanup();
    const second = render(<Probe enabled />);
    second.unmount();
    vi.mocked(applyPendingKeys).mockClear();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(applyPendingKeys).not.toHaveBeenCalled();
    unmount();
  });

  it('a failing try does not stop later tries', async () => {
    vi.mocked(applyPendingKeys).mockRejectedValueOnce(new Error('keychain locked'));
    render(<Probe enabled />);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(applyPendingKeys).toHaveBeenCalledTimes(2);
  });
});
