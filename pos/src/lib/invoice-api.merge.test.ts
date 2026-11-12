import { beforeEach, describe, expect, it, vi } from 'vitest';

const coreMocks = vi.hoisted(() => ({
  getDocList: vi.fn(),
}));

vi.mock('@ury/core', () => ({
  call: { get: vi.fn(), post: vi.fn() },
  db: { getDocList: coreMocks.getDocList },
}));

import { getMergeBillCandidates } from './invoice-api';

describe('getMergeBillCandidates', () => {
  beforeEach(() => {
    coreMocks.getDocList.mockReset();
    coreMocks.getDocList.mockResolvedValue([]);
  });

  it('uses the active in-memory profile branch without requiring session storage', async () => {
    await expect(getMergeBillCandidates({
      primaryInvoice: 'POL-2026-00340',
      branch: 'Polana',
      linkedSecondaries: [],
    })).resolves.toEqual({ data: [], hasMore: false });

    const options = coreMocks.getDocList.mock.calls[0][1] as {
      filters: unknown[];
    };
    expect(options.filters).toContainEqual(['branch', '=', 'Polana']);
  });

  it('fails explicitly when the active profile has no branch', async () => {
    await expect(getMergeBillCandidates({
      primaryInvoice: 'POL-2026-00340',
      branch: '',
      linkedSecondaries: [],
    })).rejects.toThrow('errors.pos_profile_not_loaded');

    expect(coreMocks.getDocList).not.toHaveBeenCalled();
  });
});
