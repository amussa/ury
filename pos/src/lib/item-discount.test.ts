import { describe, expect, it } from 'vitest';
import {
  calculateDiscountedLineTotal,
  calculateItemDiscountAmount,
} from './item-discount';

describe('item discount calculations', () => {
  it('calculates a percentage over the complete line', () => {
    const discount = { type: 'Percent' as const, value: 10, reason: 'Manager approval' };

    expect(calculateItemDiscountAmount(150, 3, discount)).toBe(45);
    expect(calculateDiscountedLineTotal(150, 3, discount)).toBe(405);
  });

  it('treats an amount as the total discount for the line', () => {
    const discount = { type: 'Amount' as const, value: 50, reason: 'Service recovery' };

    expect(calculateItemDiscountAmount(150, 3, discount)).toBe(50);
    expect(calculateDiscountedLineTotal(150, 3, discount)).toBe(400);
  });

  it('never lets malformed UI state make a line negative', () => {
    const discount = { type: 'Amount' as const, value: 999, reason: 'Invalid draft' };

    expect(calculateItemDiscountAmount(100, 2, discount)).toBe(200);
    expect(calculateDiscountedLineTotal(100, 2, discount)).toBe(0);
  });
});
