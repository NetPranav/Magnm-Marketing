import { expect, it } from 'vitest';
import { dumpStorage, failStorageWrites } from './test-storage';

it('storage helpers see stored secrets and really make writes fail', () => {
  window.localStorage.clear();
  window.localStorage.setItem('k', 'leaked-secret');
  expect(dumpStorage()).toContain('leaked-secret');
  const spy = failStorageWrites();
  expect(() => window.localStorage.setItem('k2', 'v')).toThrow('QuotaExceededError');
  spy.mockRestore();
  window.localStorage.setItem('k3', 'v');
  expect(dumpStorage()).toContain('k3=v');
  window.localStorage.clear();
});
