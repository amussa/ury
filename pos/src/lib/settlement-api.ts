import { call } from '@ury/core';
import { t } from '../i18n';

export type SettlementDiscountType = 'Percent' | 'Amount';
export type SettlementType = 'Paid' | 'Partial Credit' | 'Full Credit' | 'House Offer';

export interface SettlementCustomerSummary {
  id: string;
  name: string;
  phone?: string | null;
}

export type SettlementCustomerInput =
  | { existing: string }
  | {
      new: {
        customer_name: string;
        mobile_no: string;
        tax_id?: string;
      };
    };

export interface SettlementContextItem {
  pos_invoice: string;
  name: string;
  item_row: string;
  item_code: string;
  item_name: string;
  qty: number;
  rate: number;
  amount: number;
  price_list_rate?: number;
  price_option?: string | null;
  price_option_label?: string | null;
}

export interface SettlementPaymentMode {
  id: string;
  name: string;
  type?: string | null;
  default?: boolean;
}

export interface SettlementContext {
  invoice: string;
  invoices: string[];
  revision: string;
  customer: SettlementCustomerSummary;
  default_customer: string | null;
  items: SettlementContextItem[];
  totals: {
    total_catalogue: number;
    total_before_manual_discount: number;
    grand_total: number;
  };
  payment_modes: SettlementPaymentMode[];
  flags: {
    commercial_checkout: boolean;
    discount: boolean;
    credit: boolean;
  };
  limits: {
    max_discount_percentage: number;
  };
  suggested_due_date: string | null;
  currency: string;
  precision: number;
}

export interface SettlementItemDiscountInput {
  pos_invoice: string;
  item_row: string;
  type: SettlementDiscountType;
  value: number;
}

export interface SettlementInvoiceDiscountInput {
  type: SettlementDiscountType;
  value: number;
}

export interface SettlementPaymentInput {
  mode_of_payment: string;
  amount: number;
}

export interface SettlementPayload {
  invoice: string;
  revision: string;
  customer: SettlementCustomerInput;
  reason: string;
  discounts: {
    items: SettlementItemDiscountInput[];
    invoice: SettlementInvoiceDiscountInput | null;
  };
  house_offer: boolean;
  auto_payment_mode?: string;
  credit: {
    enabled: boolean;
    due_date?: string;
  };
  payments: SettlementPaymentInput[];
}

export interface SettlementPreviewItem extends SettlementContextItem {
  original_amount?: number;
  manual_discount_amount?: number;
  final_amount?: number;
}

export interface SettlementTotals {
  total_catalogue: number;
  price_option_reduction?: number;
  total_before_manual_discount: number;
  item_discount: number;
  invoice_discount: number;
  manual_discount_total: number;
  taxes?: number;
  adjustment?: number;
  grand_total: number;
  tendered_amount?: number;
  paid_now: number;
  change_amount: number;
  credit_amount: number;
}

export interface SettlementPreview {
  invoice: string;
  invoices: string[];
  revision: string;
  customer: SettlementCustomerSummary;
  items: SettlementPreviewItem[];
  totals: SettlementTotals;
  payments: SettlementPaymentInput[];
  credit: {
    enabled: boolean;
    due_date?: string | null;
  };
  discounts: SettlementPayload['discounts'];
  settlement_type: SettlementType;
}

export interface SettlementResult extends SettlementPreview {
  settlement: string;
  status: 'completed';
  idempotent_replay: boolean;
}

type FrappeResponse<T> = { message: T };

export class SettlementRequestError extends Error {
  ambiguous: boolean;

  constructor(message: string, ambiguous: boolean) {
    super(message);
    this.name = 'SettlementRequestError';
    this.ambiguous = ambiguous;
  }
}

export function shouldRotateSettlementIdempotencyKey(error: unknown): boolean {
  return error instanceof SettlementRequestError && !error.ambiguous;
}

function hasAuthoritativeServerError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const source = error as Record<string, unknown>;
  return Boolean(
    source._server_messages
    || source.exc_type
    || source.exception
    || source.httpStatus
    || source.status
  );
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizePaymentModes(value: unknown): SettlementPaymentMode[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((mode): SettlementPaymentMode | null => {
      if (typeof mode === 'string') {
        return { id: mode, name: mode, default: false };
      }
      if (!mode || typeof mode !== 'object') return null;
      const source = mode as Record<string, unknown>;
      const id = String(source.id ?? source.mode_of_payment ?? source.name ?? '');
      if (!id) return null;
      return {
        id,
        name: String(source.name ?? source.label ?? id),
        type: source.type == null ? null : String(source.type),
        default: Boolean(source.default ?? source.is_default),
      };
    })
    .filter((mode): mode is SettlementPaymentMode => mode !== null);
}

function normalizeContext(context: SettlementContext): SettlementContext {
  return {
    ...context,
    invoices: Array.isArray(context.invoices) && context.invoices.length
      ? context.invoices.map(String)
      : [context.invoice],
    customer: {
      id: String(context.customer?.id ?? ''),
      name: String(context.customer?.name ?? context.customer?.id ?? ''),
      phone: context.customer?.phone ?? '',
    },
    default_customer: context.default_customer ? String(context.default_customer) : null,
    items: (context.items ?? []).map((item) => ({
      ...item,
      pos_invoice: String(item.pos_invoice ?? context.invoice),
      name: String(item.name ?? item.item_row),
      item_row: String(item.item_row ?? item.name),
      item_code: String(item.item_code ?? ''),
      item_name: String(item.item_name ?? item.item_code ?? ''),
      qty: asNumber(item.qty),
      rate: asNumber(item.rate),
      amount: asNumber(item.amount),
      price_list_rate: item.price_list_rate == null ? undefined : asNumber(item.price_list_rate),
    })),
    totals: {
      total_catalogue: asNumber(context.totals?.total_catalogue),
      total_before_manual_discount: asNumber(context.totals?.total_before_manual_discount),
      grand_total: asNumber(context.totals?.grand_total),
    },
    payment_modes: normalizePaymentModes(context.payment_modes),
    flags: {
      commercial_checkout: Boolean(context.flags?.commercial_checkout),
      discount: Boolean(context.flags?.discount),
      credit: Boolean(context.flags?.credit),
    },
    limits: {
      max_discount_percentage: asNumber(context.limits?.max_discount_percentage, 100),
    },
    precision: Math.max(0, Math.min(6, asNumber(context.precision, 2))),
  };
}

export function getFrappeErrorMessage(error: unknown, fallback: string): string {
  const source = error && typeof error === 'object'
    ? error as Record<string, unknown>
    : null;
  if (source && '_server_messages' in source) {
    const raw = source._server_messages;
    if (typeof raw === 'string') {
      try {
        const messages = JSON.parse(raw) as unknown[];
        for (let index = messages.length - 1; index >= 0; index -= 1) {
          let message = messages[index];
          if (typeof message === 'string') {
            try {
              message = JSON.parse(message);
            } catch {
              if (message.trim()) return localizeSettlementError(message);
            }
          }
          if (message && typeof message === 'object' && 'message' in message) {
            const text = (message as { message?: unknown }).message;
            if (typeof text === 'string' && text.trim()) return localizeSettlementError(text);
          }
        }
      } catch {
        // Fall back to the regular Error shape below.
      }
    }
  }
  // frappe-js-sdk rejects with a plain FrappeError object, not a native Error.
  // Its useful server message was previously discarded, leaving only the
  // generic preview fallback in the checkout.
  const plainMessage = source?.message;
  if (typeof plainMessage === 'string' && plainMessage.trim()
    && plainMessage !== 'There was an error.') {
    return localizeSettlementError(plainMessage);
  }
  const exception = source?.exception;
  if (typeof exception === 'string' && exception.trim()) {
    const separator = exception.indexOf(':');
    const detail = separator >= 0 ? exception.slice(separator + 1).trim() : '';
    if (detail) return localizeSettlementError(detail);
  }
  if (error instanceof Error && error.message) return localizeSettlementError(error.message);
  return fallback;
}

function localizeSettlementError(message: string): string {
  const plain = message.replace(/<[^>]*>/g, '').trim();
  if (/already uses this phone number|more than one customer uses this phone number/i.test(plain)) {
    return t('settlement.errors.duplicate_customer_phone');
  }
  if (/already uses this tax identifier/i.test(plain)) {
    return t('settlement.errors.duplicate_customer_tax_id');
  }
  return plain;
}

export async function getSettlementContext(invoice: string): Promise<SettlementContext> {
  try {
    const response = await call.get<FrappeResponse<SettlementContext>>(
      'ury.ury_pos.settlement.get_settlement_context',
      { invoice }
    );
    return normalizeContext(response.message);
  } catch (error) {
    throw new Error(getFrappeErrorMessage(error, t('settlement.errors.context_failed')));
  }
}

export async function previewSettlement(payload: SettlementPayload): Promise<SettlementPreview> {
  try {
    const response = await call.post<FrappeResponse<SettlementPreview>>(
      'ury.ury_pos.settlement.preview_settlement',
      { payload }
    );
    return response.message;
  } catch (error) {
    throw new Error(getFrappeErrorMessage(error, t('settlement.errors.preview_failed')));
  }
}

export async function settleInvoice(
  payload: SettlementPayload & { idempotency_key: string }
): Promise<SettlementResult> {
  try {
    const response = await call.post<FrappeResponse<SettlementResult>>(
      'ury.ury_pos.settlement.settle_invoice',
      { payload }
    );
    return response.message;
  } catch (error) {
    throw new SettlementRequestError(
      getFrappeErrorMessage(error, t('settlement.errors.settle_failed')),
      !hasAuthoritativeServerError(error)
    );
  }
}
