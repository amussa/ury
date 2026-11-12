import { describe, expect, it } from 'vitest';
import { findOrderItemReductions } from './order-reduction';

describe('findOrderItemReductions', () => {
  it('detects a quantity reduction after aggregating persisted lines', () => {
    expect(findOrderItemReductions(
      [
        { item_code: 'BOLO-005', item_name: 'Mil folhas', qty: 2, comment: '' },
        { item_code: 'BOLO-005', item_name: 'Mil folhas', qty: 1, comment: '' },
      ],
      [{ item: 'BOLO-005', item_name: 'Mil folhas', quantity: 1, comment: '' }],
    )).toEqual([{
      itemCode: 'BOLO-005',
      itemName: 'Mil folhas',
      previousQuantity: 3,
      currentQuantity: 1,
      cancelledQuantity: 2,
    }]);
  });

  it('detects a completely removed item', () => {
    expect(findOrderItemReductions(
      [{ item_code: 'ITEM-1', item_name: 'Item one', qty: 2 }],
      [],
    )[0]).toMatchObject({ currentQuantity: 0, cancelledQuantity: 2 });
  });

  it('does not treat additions or unchanged quantities as reductions', () => {
    expect(findOrderItemReductions(
      [{ item_code: 'ITEM-1', item_name: 'Item one', qty: 1 }],
      [
        { item: 'ITEM-1', item_name: 'Item one', quantity: 1 },
        { item: 'ITEM-2', item_name: 'Item two', quantity: 1 },
      ],
    )).toEqual([]);
  });

  it('keeps preparation comments as distinct kitchen lines', () => {
    expect(findOrderItemReductions(
      [
        { item_code: 'ITEM-1', item_name: 'Item one', qty: 1, comment: 'sem sal' },
        { item_code: 'ITEM-1', item_name: 'Item one', qty: 1, comment: 'picante' },
      ],
      [
        { item: 'ITEM-1', item_name: 'Item one', quantity: 1, comment: 'sem sal' },
      ],
    )).toEqual([{
      itemCode: 'ITEM-1',
      itemName: 'Item one',
      previousQuantity: 1,
      currentQuantity: 0,
      cancelledQuantity: 1,
    }]);
  });
});
