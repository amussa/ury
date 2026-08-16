import { AlertCircle, CheckCircle2, Clock3 } from 'lucide-react';
import { Badge, Spinner } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import type { SettlementPreview, SettlementTotals } from '../lib/settlement-api';
import { t } from '../i18n';

interface SettlementSummaryProps {
  preview: SettlementPreview | null;
  fallbackTotals: Pick<SettlementTotals, 'total_catalogue' | 'total_before_manual_discount' | 'grand_total'>;
  customerLabel?: string;
  dueDate?: string;
  tableLabel?: string | null;
  isLoading: boolean;
  isCurrent: boolean;
  error?: string | null;
}

function SummaryRow({
  label,
  value,
  className = '',
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <div className={`flex items-center justify-between gap-4 text-sm ${className}`}>
      <span className="text-gray-600">{label}</span>
      <span className="font-medium tabular-nums">{formatCurrency(value)}</span>
    </div>
  );
}

export function SettlementSummary({
  preview,
  fallbackTotals,
  customerLabel,
  dueDate,
  tableLabel,
  isLoading,
  isCurrent,
  error,
}: SettlementSummaryProps) {
  const totals: SettlementTotals = preview?.totals ?? {
    total_catalogue: fallbackTotals.total_catalogue,
    price_option_reduction: 0,
    total_before_manual_discount: fallbackTotals.total_before_manual_discount,
    item_discount: 0,
    invoice_discount: 0,
    manual_discount_total: 0,
    grand_total: fallbackTotals.grand_total,
    paid_now: 0,
    change_amount: 0,
    credit_amount: 0,
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold text-gray-900">{t('settlement.summary.title')}</h3>
        {preview?.settlement_type && (
          <Badge variant="outline">{t(`settlement.types.${preview.settlement_type.toLowerCase().replace(/ /g, '_')}`)}</Badge>
        )}
      </div>

      {tableLabel && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-600">{t('tables.table_name')}</span>
          <span className="font-medium text-gray-900">{tableLabel}</span>
        </div>
      )}

      <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-4">
        <SummaryRow label={t('settlement.summary.original_subtotal')} value={totals.total_catalogue} />
        {(totals.price_option_reduction ?? 0) > 0 && (
          <SummaryRow
            label={t('settlement.summary.price_option_reduction')}
            value={-(totals.price_option_reduction ?? 0)}
            className="text-green-700"
          />
        )}
        <SummaryRow
          label={t('settlement.summary.before_manual_discount')}
          value={totals.total_before_manual_discount}
        />
        {totals.manual_discount_total > 0 && (
          <SummaryRow
            label={t('settlement.summary.manual_discount_effective')}
            value={-totals.manual_discount_total}
            className="text-green-700"
          />
        )}
        {(totals.taxes ?? 0) !== 0 && (
          <SummaryRow label={t('settlement.summary.taxes')} value={totals.taxes ?? 0} />
        )}
        {(totals.adjustment ?? 0) !== 0 && (
          <SummaryRow label={t('settlement.summary.adjustment')} value={totals.adjustment ?? 0} />
        )}
        <div className="border-t border-gray-200 pt-2">
          <SummaryRow
            label={t('settlement.summary.final_total')}
            value={totals.grand_total}
            className="text-lg font-semibold text-gray-900"
          />
        </div>
        <SummaryRow
          label={t('settlement.summary.paid_now')}
          value={totals.paid_now}
          className="text-green-700"
        />
        <SummaryRow label={t('settlement.summary.change')} value={totals.change_amount} />
        {totals.credit_amount > 0 && (
          <SummaryRow
            label={t('settlement.summary.credit_balance')}
            value={totals.credit_amount}
            className="font-semibold text-violet-800"
          />
        )}
      </div>

      {(customerLabel || dueDate) && (
        <div className="space-y-2 rounded-lg border border-gray-200 p-4 text-sm">
          {customerLabel && (
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-600">{t('settlement.summary.customer')}</span>
              <span className="text-end font-medium text-gray-900">{customerLabel}</span>
            </div>
          )}
          {dueDate && (
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-600">{t('settlement.credit.due_date')}</span>
              <span className="font-medium text-violet-800">{dueDate}</span>
            </div>
          )}
        </div>
      )}

      <div aria-live="polite">
        {isLoading ? (
          <div className="flex items-center gap-2 rounded-md bg-blue-50 p-3 text-sm text-blue-800">
            <Spinner hideMessage message={t('settlement.preview.calculating')} className="h-4 w-4" />
            {t('settlement.preview.calculating')}
          </div>
        ) : error ? (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : isCurrent ? (
          <div className="flex items-center gap-2 rounded-md bg-green-50 p-3 text-sm text-green-800">
            <CheckCircle2 className="h-4 w-4" />
            {t('settlement.preview.current')}
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-800">
            <Clock3 className="h-4 w-4" />
            {t('settlement.preview.waiting')}
          </div>
        )}
      </div>
    </div>
  );
}
