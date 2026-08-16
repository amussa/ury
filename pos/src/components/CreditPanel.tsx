import { CalendarDays, CreditCard } from 'lucide-react';
import { Input, cn } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import { t } from '../i18n';

interface CreditPanelProps {
  available: boolean;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  dueDate: string;
  onDueDateChange: (dueDate: string) => void;
  creditAmount: number;
  paidNow: number;
  disabled?: boolean;
}

export function CreditPanel({
  available,
  enabled,
  onEnabledChange,
  dueDate,
  onDueDateChange,
  creditAmount,
  paidNow,
  disabled,
}: CreditPanelProps) {
  const now = new Date();
  const minimumDueDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

  if (!available) {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
        {t('settlement.credit.disabled')}
      </div>
    );
  }

  return (
    <div className={cn(
      'rounded-lg border p-4 transition-colors',
      enabled ? 'border-violet-300 bg-violet-50/50' : 'border-gray-200 bg-white'
    )}>
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onEnabledChange(event.target.checked)}
          disabled={disabled}
          className="mt-1 h-4 w-4 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
        />
        <CreditCard className="mt-0.5 h-5 w-5 text-violet-700" />
        <span>
          <span className="block font-semibold text-gray-900">{t('settlement.credit.title')}</span>
          <span className="mt-1 block text-xs text-gray-600">{t('settlement.credit.description')}</span>
        </span>
      </label>

      {enabled && (
        <div className="mt-4 space-y-4 border-t border-violet-200 pt-4">
          <label className="block space-y-1 text-sm font-medium text-gray-700">
            <span className="flex items-center gap-2">
              <CalendarDays className="h-4 w-4" />
              {t('settlement.credit.due_date')} *
            </span>
            <Input
              type="date"
              value={dueDate}
              onChange={(event) => onDueDateChange(event.target.value)}
              disabled={disabled}
              min={minimumDueDate}
            />
          </label>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-md bg-white p-3">
              <span className="block text-xs text-gray-500">{t('settlement.summary.paid_now')}</span>
              <strong className="mt-1 block text-gray-900">{formatCurrency(paidNow)}</strong>
            </div>
            <div className="rounded-md bg-white p-3">
              <span className="block text-xs text-gray-500">{t('settlement.summary.credit_balance')}</span>
              <strong className="mt-1 block text-violet-800">{formatCurrency(creditAmount)}</strong>
            </div>
          </div>

          <p className="text-xs font-medium text-violet-800">
            {t('settlement.credit.no_change')}
          </p>
        </div>
      )}
    </div>
  );
}
