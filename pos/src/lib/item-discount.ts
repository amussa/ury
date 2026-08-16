export type ItemDiscountType = 'Percent' | 'Amount';

export interface ItemManualDiscount {
  type: ItemDiscountType;
  /** Percentage, or the total MT discount for the complete line. */
  value: number;
  reason: string;
}

interface PersistedItemManualDiscount {
  custom_ury_manual_discount_type?: ItemDiscountType | null;
  custom_ury_manual_discount_input?: number | null;
  custom_ury_manual_discount_reason?: string | null;
}

export function getPersistedItemManualDiscount(
  item: PersistedItemManualDiscount,
): ItemManualDiscount | undefined {
  const value = Number(item.custom_ury_manual_discount_input);
  if (!item.custom_ury_manual_discount_type || !Number.isFinite(value) || value <= 0) {
    return undefined;
  }
  return {
    type: item.custom_ury_manual_discount_type,
    value,
    reason: item.custom_ury_manual_discount_reason || '',
  };
}

export function calculateItemDiscountAmount(
  unitRate: number,
  quantity: number,
  discount?: ItemManualDiscount | null,
): number {
  const eligible = Math.max(0, Number(unitRate) * Number(quantity));
  if (!discount || eligible <= 0 || !Number.isFinite(discount.value)) return 0;

  const requested = discount.type === 'Percent'
    ? eligible * discount.value / 100
    : discount.value;
  if (!Number.isFinite(requested)) return 0;
  return Math.min(eligible, Math.max(0, requested));
}

export function calculateDiscountedLineTotal(
  unitRate: number,
  quantity: number,
  discount?: ItemManualDiscount | null,
): number {
  return Math.max(
    0,
    Number(unitRate) * Number(quantity)
      - calculateItemDiscountAmount(unitRate, quantity, discount),
  );
}
