import { Gift, Percent, Tag } from 'lucide-react';
import { Button, Input, cn } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import type {
  SettlementContextItem,
  SettlementDiscountType,
} from '../lib/settlement-api';
import { t } from '../i18n';

export interface DiscountDraft {
  type: SettlementDiscountType;
  value: string;
}

function settlementItemKey(item: Pick<SettlementContextItem, 'pos_invoice' | 'item_row'>) {
  return `${item.pos_invoice}\u0000${item.item_row}`;
}

interface DiscountTypeButtonsProps {
  value: SettlementDiscountType;
  onChange: (type: SettlementDiscountType) => void;
  disabled?: boolean;
  amountLabel: string;
}

function DiscountTypeButtons({
  value,
  onChange,
  disabled,
  amountLabel,
}: DiscountTypeButtonsProps) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-gray-200" role="group">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={cn('rounded-none px-3', value === 'Percent' && 'bg-primary-50 text-primary-700')}
        onClick={() => onChange('Percent')}
        disabled={disabled}
        aria-pressed={value === 'Percent'}
      >
        %
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={cn('rounded-none border-s px-3', value === 'Amount' && 'bg-primary-50 text-primary-700')}
        onClick={() => onChange('Amount')}
        disabled={disabled}
        aria-pressed={value === 'Amount'}
      >
        {amountLabel}
      </Button>
    </div>
  );
}

interface DiscountEditorProps {
  items: SettlementContextItem[];
  itemDiscounts: Record<string, DiscountDraft>;
  invoiceDiscount: DiscountDraft | null;
  onItemDiscountsChange: (discounts: Record<string, DiscountDraft>) => void;
  onInvoiceDiscountChange: (discount: DiscountDraft | null) => void;
  houseOffer: boolean;
  onHouseOfferChange: (enabled: boolean) => void;
  enabled: boolean;
  disabled?: boolean;
  currency: string;
  maxPercentage: number;
}

export function DiscountEditor({
  items,
  itemDiscounts,
  invoiceDiscount,
  onItemDiscountsChange,
  onInvoiceDiscountChange,
  houseOffer,
  onHouseOfferChange,
  enabled,
  disabled,
  currency,
  maxPercentage,
}: DiscountEditorProps) {
  const amountLabel = currency === 'MZN'
    ? 'MT'
    : currency || t('settlement.discount.amount_short');
  const hasMultipleInvoices = new Set(items.map((item) => item.pos_invoice)).size > 1;

  if (!enabled) {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
        {t('settlement.discount.disabled')}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold text-gray-900">
            <Tag className="h-4 w-4" />
            {t('settlement.discount.items_title')}
          </h3>
          <p className="mt-1 text-xs text-gray-500">{t('settlement.discount.items_help')}</p>
        </div>
      </div>

      <div className="space-y-2">
        {items.map((item) => {
          const key = settlementItemKey(item);
          const discount = itemDiscounts[key];
          const selected = !!discount;
          const inputMaximum = discount?.type === 'Percent'
            ? maxPercentage
            : Math.max(0, item.amount);

          return (
            <div
              key={key}
              className={cn(
                'rounded-lg border p-3 transition-colors',
                selected ? 'border-primary-200 bg-primary-50/30' : 'border-gray-200'
              )}
            >
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={(event) => {
                    if (event.target.checked) {
                      onItemDiscountsChange({
                        ...itemDiscounts,
                        [key]: { type: 'Percent', value: '' },
                      });
                    } else {
                      const next = { ...itemDiscounts };
                      delete next[key];
                      onItemDiscountsChange(next);
                    }
                  }}
                  disabled={disabled || houseOffer}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                  aria-label={t('settlement.discount.select_item', { item: item.item_name })}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium text-gray-900">{item.item_name}</p>
                      <p className="text-xs text-gray-500">
                        {t('settlement.discount.item_base', {
                          qty: String(item.qty),
                          amount: formatCurrency(item.amount),
                        })}
                      </p>
                      {hasMultipleInvoices && (
                        <p className="text-xs text-gray-500">
                          {t('settlement.discount.invoice_source', { invoice: item.pos_invoice })}
                        </p>
                      )}
                      {item.price_option_label && (
                        <p className="text-xs font-medium text-blue-700">{item.price_option_label}</p>
                      )}
                    </div>
                    <span className="text-sm font-semibold text-gray-900">
                      {formatCurrency(item.amount)}
                    </span>
                  </div>

                  {discount && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <DiscountTypeButtons
                        value={discount.type}
                        onChange={(type) => onItemDiscountsChange({
                          ...itemDiscounts,
                          [key]: { type, value: '' },
                        })}
                        disabled={disabled || houseOffer}
                        amountLabel={amountLabel}
                      />
                      <Input
                        type="number"
                        min="0"
                        max={inputMaximum}
                        step="0.01"
                        value={discount.value}
                        onChange={(event) => onItemDiscountsChange({
                          ...itemDiscounts,
                          [key]: { ...discount, value: event.target.value },
                        })}
                        className="w-36"
                        placeholder={discount.type === 'Percent' ? '%' : amountLabel}
                        disabled={disabled || houseOffer}
                        aria-label={t('settlement.discount.item_value', { item: item.item_name })}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="rounded-lg border border-gray-200 p-4">
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={!!invoiceDiscount}
            onChange={(event) => onInvoiceDiscountChange(
              event.target.checked ? { type: 'Percent', value: '' } : null
            )}
            disabled={disabled || houseOffer}
            className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
            aria-label={t('settlement.discount.invoice_title')}
          />
          <div className="flex-1">
            <p className="font-medium text-gray-900">{t('settlement.discount.invoice_title')}</p>
            <p className="text-xs text-gray-500">{t('settlement.discount.invoice_help')}</p>
            {invoiceDiscount && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <DiscountTypeButtons
                  value={invoiceDiscount.type}
                  onChange={(type) => onInvoiceDiscountChange({ type, value: '' })}
                  disabled={disabled || houseOffer}
                  amountLabel={amountLabel}
                />
                <Input
                  type="number"
                  min="0"
                  max={invoiceDiscount.type === 'Percent' ? maxPercentage : undefined}
                  step="0.01"
                  value={invoiceDiscount.value}
                  onChange={(event) => onInvoiceDiscountChange({
                    ...invoiceDiscount,
                    value: event.target.value,
                  })}
                  className="w-36"
                  placeholder={invoiceDiscount.type === 'Percent' ? '%' : amountLabel}
                  disabled={disabled || houseOffer}
                  aria-label={t('settlement.discount.invoice_value')}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => onHouseOfferChange(!houseOffer)}
        disabled={disabled}
        aria-pressed={houseOffer}
        className={cn(
          'flex w-full items-start gap-3 rounded-lg border p-4 text-start transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50',
          houseOffer
            ? 'border-amber-400 bg-amber-50 text-amber-950'
            : 'border-gray-200 bg-white hover:border-amber-300 hover:bg-amber-50/50'
        )}
      >
        <Gift className="mt-0.5 h-5 w-5 shrink-0" />
        <span>
          <span className="block font-semibold">{t('settlement.house_offer.title')}</span>
          <span className="mt-1 block text-xs opacity-80">{t('settlement.house_offer.description')}</span>
        </span>
        {houseOffer && (
          <span className="ms-auto rounded-full bg-amber-200 px-2 py-1 text-xs font-semibold">
            <Percent className="me-1 inline h-3 w-3" />100
          </span>
        )}
      </button>
    </div>
  );
}
