import { Gift, Percent, ReceiptText } from 'lucide-react';
import { Button, Input, cn } from '@ury/ui';
import type { SettlementDiscountType } from '../lib/settlement-api';
import { t } from '../i18n';

export interface DiscountDraft {
  type: SettlementDiscountType;
  value: string;
}

interface DiscountTypeButtonsProps {
  value: SettlementDiscountType;
  onChange: (type: SettlementDiscountType) => void;
  disabled?: boolean;
  amountLabel: string;
}

function DiscountTypeButtons({ value, onChange, disabled, amountLabel }: DiscountTypeButtonsProps) {
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
  invoiceDiscount: DiscountDraft | null;
  onInvoiceDiscountChange: (discount: DiscountDraft | null) => void;
  houseOffer: boolean;
  onHouseOfferChange: (enabled: boolean) => void;
  enabled: boolean;
  disabled?: boolean;
  currency: string;
  maxPercentage: number;
}

/** Checkout-level actions only. Item discounts are applied before checkout. */
export function DiscountEditor({
  invoiceDiscount,
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

  if (!enabled) {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
        {t('settlement.discount.disabled')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
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
          <ReceiptText className="mt-0.5 h-5 w-5 text-green-700" />
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
