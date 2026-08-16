import { describe, expect, it } from 'vitest';
import {
  SettlementRequestError,
  getFrappeErrorMessage,
  shouldRotateSettlementIdempotencyKey,
  type SettlementContext,
  type SettlementPayload,
  type SettlementPreview,
} from './settlement-api';
import {
  buildSettlementPayload,
  calculateSettlementAllocation,
  isSettlementPreviewCurrent,
  settlementItemKey,
  settlementPreviewKey,
} from './settlement-state';

const context: SettlementContext = {
  invoice: 'ACC-POS-INV-0001',
  invoices: ['ACC-POS-INV-0001', 'ACC-POS-INV-0002'],
  revision: 'revision-1',
  customer: { id: 'Walk In Customer', name: 'Walk In Customer' },
  default_customer: 'Walk In Customer',
  items: [
    {
      pos_invoice: 'ACC-POS-INV-0001',
      name: 'row-1',
      item_row: 'row-1',
      item_code: 'ITEM-1',
      item_name: 'Item one',
      qty: 1,
      rate: 60,
      amount: 60,
    },
    {
      pos_invoice: 'ACC-POS-INV-0002',
      name: 'row-1',
      item_row: 'row-1',
      item_code: 'ITEM-2',
      item_name: 'Item two',
      qty: 1,
      rate: 40,
      amount: 40,
    },
  ],
  totals: {
    total_catalogue: 100,
    total_before_manual_discount: 100,
    grand_total: 100,
  },
  payment_modes: [
    { id: 'Cash', name: 'Cash', type: 'Cash', default: true },
    { id: 'Card', name: 'Card', type: 'Bank' },
  ],
  flags: { commercial_checkout: true, discount: true, credit: true },
  limits: { max_discount_percentage: 100 },
  suggested_due_date: '2026-09-15',
  currency: 'MZN',
  precision: 2,
};

function buildPayload(overrides: Partial<Parameters<typeof buildSettlementPayload>[0]> = {}) {
  return buildSettlementPayload({
    context,
    customer: { existing: 'CUST-0001' },
    reason: '  approved by manager  ',
    itemDiscounts: {},
    invoiceDiscount: null,
    houseOffer: false,
    autoPaymentMode: null,
    creditEnabled: false,
    dueDate: '2026-09-15',
    paymentInputs: {},
    ...overrides,
  });
}

function previewFor(payload: SettlementPayload): SettlementPreview {
  return {
    invoice: payload.invoice,
    invoices: context.invoices,
    revision: payload.revision,
    customer: context.customer,
    items: context.items,
    totals: {
      total_catalogue: 100,
      total_before_manual_discount: 100,
      item_discount: 0,
      invoice_discount: 0,
      manual_discount_total: 0,
      grand_total: 100,
      paid_now: 100,
      change_amount: 0,
      credit_amount: 0,
    },
    payments: payload.payments,
    credit: payload.credit,
    discounts: payload.discounts,
    settlement_type: 'Paid',
  };
}

describe('buildSettlementPayload', () => {
  it('normalises discounts and payments in authoritative context order', () => {
    const payload = buildPayload({
      itemDiscounts: {
        [settlementItemKey(context.items[0])]: { type: 'Percent', value: '10' },
        [settlementItemKey(context.items[1])]: { type: 'Amount', value: '5.50' },
      },
      invoiceDiscount: { type: 'Amount', value: '2' },
      paymentInputs: { Card: '40', Cash: '60', ignored: '999' },
    });

    expect(payload.reason).toBe('approved by manager');
    expect(payload.discounts.items).toEqual([
      {
        pos_invoice: 'ACC-POS-INV-0001',
        item_row: 'row-1',
        type: 'Percent',
        value: 10,
      },
      {
        pos_invoice: 'ACC-POS-INV-0002',
        item_row: 'row-1',
        type: 'Amount',
        value: 5.5,
      },
    ]);
    expect(payload.payments).toEqual([
      { mode_of_payment: 'Cash', amount: 60 },
      { mode_of_payment: 'Card', amount: 40 },
    ]);
  });

  it('makes House Offer exclusive even when stale form values are supplied', () => {
    const payload = buildPayload({
      itemDiscounts: {
        [settlementItemKey(context.items[0])]: { type: 'Percent', value: '25' },
      },
      invoiceDiscount: { type: 'Amount', value: '10' },
      houseOffer: true,
      autoPaymentMode: 'Cash',
      creditEnabled: true,
      paymentInputs: { Cash: '100' },
    });

    expect(payload.discounts).toEqual({ items: [], invoice: null });
    expect(payload.credit).toEqual({ enabled: false });
    expect(payload.payments).toEqual([]);
    expect(payload).not.toHaveProperty('auto_payment_mode');
  });

  it('keeps partial-credit payments but omits automatic full payment', () => {
    const payload = buildPayload({
      autoPaymentMode: 'Cash',
      creditEnabled: true,
      paymentInputs: { Cash: '30' },
    });

    expect(payload.credit).toEqual({ enabled: true, due_date: '2026-09-15' });
    expect(payload.payments).toEqual([{ mode_of_payment: 'Cash', amount: 30 }]);
    expect(payload).not.toHaveProperty('auto_payment_mode');
  });
});

describe('getFrappeErrorMessage', () => {
  it('turns a duplicate phone response into an actionable customer message', () => {
    const message = getFrappeErrorMessage(
      {
        message: 'Customer Existing Customer already uses this phone number. Select that customer.',
        httpStatus: 417,
      },
      'generic preview failure',
    );

    expect(message).toBe('settlement.errors.duplicate_customer_phone');
  });

  it('extracts duplicate phone details from Frappe server messages', () => {
    const message = getFrappeErrorMessage(
      {
        _server_messages: JSON.stringify([
          JSON.stringify({
            message: 'Customer <strong>Existing Customer</strong> already uses this phone number. Select that customer.',
          }),
        ]),
      },
      'generic preview failure',
    );

    expect(message).toBe('settlement.errors.duplicate_customer_phone');
  });

  it('extracts the detail from Frappe exception-only responses', () => {
    const message = getFrappeErrorMessage(
      {
        exception: 'ury.ury_pos.settlement.SettlementValidationError: Customer <strong>Existing Customer</strong> already uses this phone number. Select that customer.',
        exc_type: 'SettlementValidationError',
        httpStatus: 417,
      },
      'generic preview failure',
    );

    expect(message).toBe('settlement.errors.duplicate_customer_phone');
  });
});

describe('strict settlement preview identity', () => {
  it('accepts only the preview key for the complete current payload', () => {
    const payload = buildPayload({ paymentInputs: { Cash: '100' } });
    const preview = previewFor(payload);
    const key = settlementPreviewKey(payload);

    expect(isSettlementPreviewCurrent(preview, key, payload, false)).toBe(true);
    expect(isSettlementPreviewCurrent(preview, key, { ...payload, reason: 'changed' }, false)).toBe(false);
    expect(isSettlementPreviewCurrent(preview, key, payload, true)).toBe(false);
    expect(isSettlementPreviewCurrent(null, key, payload, false)).toBe(false);
  });
});

describe('credit allocation', () => {
  it('uses the unpaid balance for partial credit and never produces negative credit', () => {
    expect(calculateSettlementAllocation(
      [{ mode_of_payment: 'Cash', amount: 30 }, { mode_of_payment: 'Card', amount: 20 }],
      100,
      true
    )).toEqual({ tenderedAmount: 50, paidNow: 50, creditAmount: 50 });

    expect(calculateSettlementAllocation(
      [{ mode_of_payment: 'Cash', amount: 110 }],
      100,
      true
    ).creditAmount).toBe(0);
  });

  it('prefers the server allocation over the optimistic browser calculation', () => {
    expect(calculateSettlementAllocation(
      [{ mode_of_payment: 'Cash', amount: 60 }],
      100,
      true,
      { paid_now: 55, credit_amount: 45 }
    )).toEqual({ tenderedAmount: 60, paidNow: 55, creditAmount: 45 });
  });
});

describe('idempotency recovery', () => {
  it('retains the key for ambiguous outcomes and rotates after a definitive rejection', () => {
    expect(shouldRotateSettlementIdempotencyKey(
      new SettlementRequestError('connection ended', true)
    )).toBe(false);
    expect(shouldRotateSettlementIdempotencyKey(
      new SettlementRequestError('validation rejected', false)
    )).toBe(true);
    expect(shouldRotateSettlementIdempotencyKey(new Error('unknown'))).toBe(false);
  });
});
