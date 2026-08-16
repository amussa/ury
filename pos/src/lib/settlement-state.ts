import type {
  SettlementContext,
  SettlementCustomerInput,
  SettlementDiscountType,
  SettlementPayload,
  SettlementPaymentInput,
  SettlementPreview,
  SettlementTotals,
} from './settlement-api';

export interface SettlementDiscountDraft {
  type: SettlementDiscountType;
  value: string;
}

interface BuildSettlementPayloadOptions {
  context: SettlementContext;
  customer: SettlementCustomerInput;
  reason: string;
  itemDiscounts: Record<string, SettlementDiscountDraft>;
  invoiceDiscount: SettlementDiscountDraft | null;
  houseOffer: boolean;
  autoPaymentMode: string | null;
  creditEnabled: boolean;
  dueDate: string;
  paymentInputs: Record<string, string>;
}

interface SettlementAllocation {
  tenderedAmount: number;
  paidNow: number;
  creditAmount: number;
}

export function parseSettlementNumber(value: string): number {
  if (!value.trim()) return 0;
  return Number(value);
}

export function settlementItemKey(item: {
  pos_invoice: string;
  item_row: string;
}): string {
  return `${item.pos_invoice}\u0000${item.item_row}`;
}

/**
 * Convert editable checkout fields into the exact server payload.
 *
 * House Offer is deliberately normalised here as an exclusive operation. This
 * keeps stale UI state from leaking discounts, payments, or credit into a
 * zero-value settlement even if a caller forgets to clear an input first.
 */
export function buildSettlementPayload({
  context,
  customer,
  reason,
  itemDiscounts,
  invoiceDiscount,
  houseOffer,
  autoPaymentMode,
  creditEnabled,
  dueDate,
  paymentInputs,
}: BuildSettlementPayloadOptions): SettlementPayload {
  const items = houseOffer
    ? []
    : context.items.flatMap((item) => {
        const discount = itemDiscounts[settlementItemKey(item)];
        if (!discount) return [];
        return [{
          pos_invoice: item.pos_invoice,
          item_row: item.item_row,
          type: discount.type,
          value: parseSettlementNumber(discount.value),
        }];
      });

  const payments: SettlementPaymentInput[] = houseOffer
    ? []
    : context.payment_modes.flatMap((mode) => {
        const amount = parseSettlementNumber(paymentInputs[mode.id] ?? '');
        if (!Number.isFinite(amount) || amount <= 0) return [];
        return [{ mode_of_payment: mode.id, amount }];
      });

  const effectiveCredit = creditEnabled && !houseOffer;

  return {
    invoice: context.invoice,
    revision: context.revision,
    customer,
    reason: reason.trim(),
    discounts: {
      items,
      invoice: invoiceDiscount && !houseOffer
        ? {
            type: invoiceDiscount.type,
            value: parseSettlementNumber(invoiceDiscount.value),
          }
        : null,
    },
    house_offer: houseOffer,
    ...(autoPaymentMode && !effectiveCredit && !houseOffer
      ? { auto_payment_mode: autoPaymentMode }
      : {}),
    credit: {
      enabled: effectiveCredit,
      ...(effectiveCredit ? { due_date: dueDate } : {}),
    },
    payments,
  };
}

export function settlementPreviewKey(payload: SettlementPayload): string {
  return JSON.stringify(payload);
}

/** A preview is authoritative only for the byte-for-byte current payload. */
export function isSettlementPreviewCurrent(
  preview: SettlementPreview | null,
  previewKey: string,
  payload: SettlementPayload | null,
  isLoading: boolean
): boolean {
  return Boolean(
    preview
    && payload
    && !isLoading
    && previewKey === settlementPreviewKey(payload)
  );
}

/**
 * Calculate the values shown before a preview arrives, while preferring the
 * server allocation as soon as it is available.
 */
export function calculateSettlementAllocation(
  payments: SettlementPaymentInput[],
  grandTotal: number,
  creditEnabled: boolean,
  authoritative?: Pick<SettlementTotals, 'paid_now' | 'credit_amount'>
): SettlementAllocation {
  const tenderedAmount = payments.reduce((sum, payment) => sum + payment.amount, 0);
  const paidNow = authoritative?.paid_now ?? tenderedAmount;
  const creditAmount = authoritative?.credit_amount
    ?? (creditEnabled ? Math.max(0, grandTotal - paidNow) : 0);

  return { tenderedAmount, paidNow, creditAmount };
}
