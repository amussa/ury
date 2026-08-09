import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Spinner,
  Textarea,
  showToast,
} from '@ury/ui';
import { formatCurrency } from '@ury/core';
import {
  correctPaymentMethods,
  getPaymentCorrectionDetails,
  type PaymentCorrectionDetails,
} from '../lib/invoice-api';
import { t } from '../i18n';


interface PaymentCorrectionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  invoice: string;
  onCorrected: () => Promise<void>;
}

const PaymentCorrectionDialog = ({
  open,
  onOpenChange,
  invoice,
  onCorrected,
}: PaymentCorrectionDialogProps) => {
  const [details, setDetails] = useState<PaymentCorrectionDetails | null>(null);
  const [paymentInputs, setPaymentInputs] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    setDetails(null);
    setPaymentInputs({});
    setReason('');
    setError(null);
    setIsLoading(true);

    getPaymentCorrectionDetails(invoice)
      .then((result) => {
        if (cancelled) return;
        const currentAmounts = Object.fromEntries(
          result.payments.map((payment) => [
            payment.mode_of_payment,
            String(payment.amount),
          ])
        );
        setDetails(result);
        setPaymentInputs(currentAmounts);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : t('payment_correction.load_failed'));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, invoice]);

  const payments = useMemo(() => {
    if (!details) return [];
    return details.payment_modes
      .map((mode) => ({
        mode_of_payment: mode,
        amount: Number.parseFloat(paymentInputs[mode] || '0'),
      }))
      .filter((payment) => Number.isFinite(payment.amount) && payment.amount > 0);
  }, [details, paymentInputs]);

  const enteredTotal = useMemo(
    () => payments.reduce((total, payment) => total + payment.amount, 0),
    [payments]
  );

  const precision = details?.precision ?? 2;
  const tolerance = 0.5 / 10 ** precision;
  const totalMatches = !!details && Math.abs(enteredTotal - details.total_paid) < tolerance;

  const hasChanges = useMemo(() => {
    if (!details) return false;
    const current = new Map(
      details.payments.map((payment) => [payment.mode_of_payment, payment.amount])
    );
    if (current.size !== payments.length) return true;
    return payments.some(
      (payment) =>
        Math.abs((current.get(payment.mode_of_payment) ?? 0) - payment.amount) >= tolerance
    );
  }, [details, payments, tolerance]);

  const handlePaymentFocus = (mode: string) => {
    if (!details || Number.parseFloat(paymentInputs[mode] || '0') > 0) return;
    const otherTotal = Object.entries(paymentInputs)
      .filter(([paymentMode]) => paymentMode !== mode)
      .reduce((total, [, value]) => total + (Number.parseFloat(value) || 0), 0);
    const remaining = Math.max(0, details.total_paid - otherTotal);
    if (remaining > 0) {
      setPaymentInputs((current) => ({ ...current, [mode]: remaining.toFixed(precision) }));
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (isSubmitting) return;
    onOpenChange(nextOpen);
  };

  const handleSubmit = async () => {
    if (!details) return;
    if (!reason.trim()) {
      setError(t('payment_correction.reason_required'));
      return;
    }
    if (!totalMatches) {
      setError(t('payment_correction.total_mismatch', { total: formatCurrency(details.total_paid) }));
      return;
    }
    if (!hasChanges) {
      setError(t('payment_correction.no_change'));
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      await correctPaymentMethods(invoice, payments, reason.trim());
      await onCorrected();
      showToast.success(t('payment_correction.success'));
      onOpenChange(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t('payment_correction.save_failed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-xl bg-white">
        <DialogHeader>
          <DialogTitle>{t('payment_correction.title')}</DialogTitle>
          <DialogDescription>
            {t('payment_correction.description', { invoice })}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex min-h-48 items-center justify-center">
            <Spinner message={t('common.loading')} />
          </div>
        ) : details ? (
          <div className="space-y-5 px-6 pb-3">
            {details.affected_invoices.length > 1 && (
              <div className="rounded-md border border-primary-200 bg-primary-50 p-3 text-sm text-primary-800">
                {t('payment_correction.affected_invoices', {
                  invoices: details.affected_invoices.join(', '),
                })}
              </div>
            )}

            <div>
              <p className="mb-2 text-sm font-medium text-gray-700">
                {t('payment_correction.current_payments')}
              </p>
              <div className="flex flex-wrap gap-2">
                {details.payments.map((payment) => (
                  <span
                    key={payment.mode_of_payment}
                    className="rounded-full bg-gray-100 px-3 py-1 text-sm text-gray-700"
                  >
                    {payment.mode_of_payment}: {formatCurrency(payment.amount)}
                  </span>
                ))}
              </div>
            </div>

            <div className="space-y-3">
              <p className="text-sm font-medium text-gray-700">
                {t('payment_correction.corrected_payments')}
              </p>
              {details.payment_modes.map((mode) => (
                <label key={mode} className="grid grid-cols-[1fr_10rem] items-center gap-4 text-sm">
                  <span className="font-medium text-gray-700">{mode}</span>
                  <Input
                    type="number"
                    min="0"
                    step={1 / 10 ** precision}
                    value={paymentInputs[mode] ?? ''}
                    onFocus={() => handlePaymentFocus(mode)}
                    onChange={(event) => {
                      setPaymentInputs((current) => ({ ...current, [mode]: event.target.value }));
                      setError(null);
                    }}
                    disabled={isSubmitting}
                    aria-label={`${mode} ${t('payment.amount_placeholder')}`}
                  />
                </label>
              ))}
            </div>

            <div className="flex items-center justify-between rounded-md bg-gray-50 p-3 text-sm">
              <span className="font-medium text-gray-700">{t('payment_correction.total_entered')}</span>
              <span className={totalMatches ? 'font-semibold text-green-700' : 'font-semibold text-red-600'}>
                {formatCurrency(enteredTotal)} / {formatCurrency(details.total_paid)}
              </span>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700" htmlFor="payment-correction-reason">
                {t('payment_correction.reason_label')}
              </label>
              <Textarea
                id="payment-correction-reason"
                value={reason}
                onChange={(event) => {
                  setReason(event.target.value);
                  setError(null);
                }}
                placeholder={t('payment_correction.reason_placeholder')}
                disabled={isSubmitting}
                rows={3}
              />
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}
          </div>
        ) : (
          <div className="px-6 pb-3">
            <p className="text-sm text-red-600">{error || t('payment_correction.load_failed')}</p>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={isSubmitting}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isLoading || isSubmitting || !details || !totalMatches || !hasChanges || !reason.trim()}
          >
            {isSubmitting ? t('payment_correction.saving') : t('payment_correction.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default PaymentCorrectionDialog;
