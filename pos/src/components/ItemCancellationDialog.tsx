import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';
import { Button } from '@ury/ui';
import { t } from '../i18n';
import type { OrderItemReduction } from '../lib/order-reduction';

interface ItemCancellationDialogProps {
  isOpen: boolean;
  isSubmitting: boolean;
  reductions: OrderItemReduction[];
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}

const ItemCancellationDialog = ({
  isOpen,
  isSubmitting,
  reductions,
  onClose,
  onConfirm,
}: ItemCancellationDialogProps) => {
  const [reason, setReason] = useState('');
  const [showRequired, setShowRequired] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setReason('');
      setShowRequired(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleConfirm = async () => {
    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setShowRequired(true);
      return;
    }
    await onConfirm(trimmedReason);
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="item-cancellation-title"
        className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 flex-none text-amber-600" />
            <div>
              <h2 id="item-cancellation-title" className="text-lg font-semibold text-gray-900">
                {t('item_cancellation.title')}
              </h2>
              <p className="mt-1 text-sm text-gray-600">
                {t('item_cancellation.description')}
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label={t('common.cancel')}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="mb-2 text-sm font-medium text-amber-900">
            {t('item_cancellation.items_heading')}
          </p>
          <ul className="space-y-1 text-sm text-amber-900">
            {reductions.map((reduction) => (
              <li key={`${reduction.itemCode}-${reduction.previousQuantity}-${reduction.currentQuantity}`}>
                {t('item_cancellation.item_change', {
                  item: reduction.itemName,
                  previous: reduction.previousQuantity,
                  current: reduction.currentQuantity,
                  cancelled: reduction.cancelledQuantity,
                })}
              </li>
            ))}
          </ul>
        </div>

        <label htmlFor="item-cancellation-reason" className="block text-sm font-medium text-gray-700">
          {t('item_cancellation.reason_label')}
        </label>
        <textarea
          id="item-cancellation-reason"
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
            if (event.target.value.trim()) setShowRequired(false);
          }}
          placeholder={t('item_cancellation.reason_placeholder')}
          className="mt-2 h-24 w-full resize-none rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
          disabled={isSubmitting}
          autoFocus
        />
        {showRequired && (
          <p className="mt-2 text-sm text-red-600">{t('item_cancellation.reason_required')}</p>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            {t('common.cancel')}
          </Button>
          <Button type="button" onClick={handleConfirm} disabled={isSubmitting}>
            {isSubmitting && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
            {isSubmitting
              ? t('item_cancellation.saving')
              : t('item_cancellation.confirm')}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default ItemCancellationDialog;
