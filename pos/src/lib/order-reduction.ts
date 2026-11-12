export interface PreviousOrderLine {
  item_code: string;
  item_name: string;
  qty: number;
  comment?: string | null;
}

export interface CurrentOrderLine {
  item: string;
  item_name?: string;
  name?: string;
  quantity: number;
  comment?: string | null;
}

export interface OrderItemReduction {
  itemCode: string;
  itemName: string;
  previousQuantity: number;
  currentQuantity: number;
  cancelledQuantity: number;
}

const itemKey = (itemCode: string, comment?: string | null) =>
  `${itemCode}\u0000${comment || ''}`;

export function findOrderItemReductions(
  previousLines: PreviousOrderLine[],
  currentLines: CurrentOrderLine[],
): OrderItemReduction[] {
  const previous = new Map<string, { itemCode: string; itemName: string; quantity: number }>();
  const current = new Map<string, number>();

  for (const line of previousLines) {
    const key = itemKey(line.item_code, line.comment);
    const existing = previous.get(key);
    previous.set(key, {
      itemCode: line.item_code,
      itemName: line.item_name,
      quantity: (existing?.quantity || 0) + Number(line.qty || 0),
    });
  }

  for (const line of currentLines) {
    const key = itemKey(line.item, line.comment);
    current.set(key, (current.get(key) || 0) + Number(line.quantity || 0));
  }

  return [...previous.entries()].flatMap(([key, line]) => {
    const currentQuantity = current.get(key) || 0;
    const cancelledQuantity = line.quantity - currentQuantity;
    if (cancelledQuantity <= Number.EPSILON) return [];
    return [{
      itemCode: line.itemCode,
      itemName: line.itemName,
      previousQuantity: line.quantity,
      currentQuantity,
      cancelledQuantity,
    }];
  });
}
