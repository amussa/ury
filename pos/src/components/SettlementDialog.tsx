import { useEffect, useMemo, useRef, useState } from 'react';
import { MessageSquareText, UserRound, WalletCards, X } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  Input,
  Spinner,
  Textarea,
  showToast,
} from '@ury/ui';
import { formatCurrency } from '@ury/core';
import { usePOSStore } from '../store/pos-store';
import {
  getSettlementContext,
  previewSettlement,
  settleInvoice,
  shouldRotateSettlementIdempotencyKey,
  type SettlementContext,
  type SettlementCustomerInput,
  type SettlementPayload,
  type SettlementPreview,
  type SettlementResult,
} from '../lib/settlement-api';
import {
  buildSettlementPayload,
  calculateRemainingPayment,
  calculateSettlementAllocation,
  isSettlementPreviewCurrent,
  parseSettlementNumber,
  settlementPricingKey,
  settlementPreviewKey,
} from '../lib/settlement-state';
import {
  DiscountEditor,
  type DiscountDraft,
} from './DiscountEditor';
import { CreditPanel } from './CreditPanel';
import { SettlementCustomerEditor } from './SettlementCustomerEditor';
import { SettlementSummary } from './SettlementSummary';
import { t } from '../i18n';

interface SettlementDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  invoice: string;
  tableLabel?: string | null;
  onSettled: (result: SettlementResult) => Promise<void>;
  printSettledInvoice: (browserPrintWindow: Window | null) => Promise<void>;
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `ury-pos-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function futureDate(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function selectedCustomerLabel(
  customer: SettlementCustomerInput,
  context: SettlementContext
): string {
  if ('new' in customer) {
    return customer.new.customer_name || t('settlement.customer.new_title');
  }
  if (customer.existing === context.customer.id) return context.customer.name;
  return customer.existing;
}

export default function SettlementDialog({
  open,
  onOpenChange,
  invoice,
  tableLabel,
  onSettled,
  printSettledInvoice,
}: SettlementDialogProps) {
  const { posProfile } = usePOSStore();
  const [context, setContext] = useState<SettlementContext | null>(null);
  const [customer, setCustomer] = useState<SettlementCustomerInput>({ existing: '' });
  const [invoiceDiscount, setInvoiceDiscount] = useState<DiscountDraft | null>(null);
  const [houseOffer, setHouseOffer] = useState(false);
  const [creditEnabled, setCreditEnabled] = useState(false);
  const [dueDate, setDueDate] = useState('');
  const [reason, setReason] = useState('');
  const [paymentInputs, setPaymentInputs] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<SettlementPreview | null>(null);
  const [previewKey, setPreviewKey] = useState('');
  const [previewPricingKey, setPreviewPricingKey] = useState('');
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isSettling, setIsSettling] = useState(false);
  const [hasSettlementAttempt, setHasSettlementAttempt] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewSequence = useRef(0);
  const idempotencyKey = useRef(createIdempotencyKey());

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    previewSequence.current += 1;
    idempotencyKey.current = createIdempotencyKey();
    setContext(null);
    setPreview(null);
    setPreviewKey('');
    setPreviewPricingKey('');
    setContextError(null);
    setPreviewError(null);
    setInvoiceDiscount(null);
    setHouseOffer(false);
    setCreditEnabled(false);
    setReason('');
    setPaymentInputs({});
    setHasSettlementAttempt(false);
    setIsLoadingContext(true);

    getSettlementContext(invoice)
      .then((result) => {
        if (cancelled) return;
        setContext(result);
        setCustomer({ existing: result.customer.id });
        setDueDate(result.suggested_due_date || futureDate(30));
      })
      .catch((error) => {
        if (!cancelled) {
          setContextError(error instanceof Error ? error.message : t('settlement.errors.context_failed'));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingContext(false);
      });

    return () => {
      cancelled = true;
    };
  }, [invoice, open]);

  const payload = useMemo<SettlementPayload | null>(() => {
    if (!context) return null;
    return buildSettlementPayload({
      context,
      customer,
      reason,
      itemDiscounts: {},
      invoiceDiscount,
      houseOffer,
      autoPaymentMode: null,
      creditEnabled,
      dueDate,
      paymentInputs,
    });
  }, [context, creditEnabled, customer, dueDate, houseOffer, invoiceDiscount, paymentInputs, reason]);

  const payments = useMemo(() => payload?.payments ?? [], [payload]);

  const validationError = useMemo(() => {
    if (!context || !payload) return null;
    if (!context.flags.commercial_checkout) return t('settlement.errors.feature_disabled');

    if ('new' in customer) {
      if (!customer.new.customer_name.trim()) return t('customer.name_required');
      if (!customer.new.mobile_no.trim()) return t('customer.phone_required');
    } else if (!customer.existing) {
      return t('errors.select_customer');
    }

    if (creditEnabled) {
      if (!context.flags.credit) return t('settlement.errors.credit_disabled');
      if ('existing' in customer && customer.existing === context.default_customer) {
        return t('settlement.customer.real_customer_required');
      }
      if (!dueDate) return t('settlement.errors.due_date_required');
    }

    const hasSpecialOperation = houseOffer
      || creditEnabled
      || !!invoiceDiscount;
    if (hasSpecialOperation && !reason.trim()) return t('settlement.errors.reason_required');

    if (invoiceDiscount && !houseOffer) {
      const value = parseSettlementNumber(invoiceDiscount.value);
      if (!Number.isFinite(value) || value <= 0) return t('settlement.errors.discount_value_required');
      if (
        invoiceDiscount.type === 'Percent'
        && value > context.limits.max_discount_percentage
      ) {
        return t('settlement.errors.discount_exceeds_limit', {
          limit: String(context.limits.max_discount_percentage),
        });
      }
      if (
        invoiceDiscount.type === 'Amount'
        && value > context.totals.total_before_manual_discount
      ) {
        return t('settlement.errors.invoice_discount_exceeds_total');
      }
    }

    for (const value of Object.values(paymentInputs)) {
      if (!value.trim()) continue;
      const amount = parseSettlementNumber(value);
      if (!Number.isFinite(amount) || amount < 0) return t('settlement.errors.invalid_payment');
    }

    return null;
  }, [context, creditEnabled, customer, dueDate, houseOffer, invoiceDiscount, payload, paymentInputs, reason]);

  const payloadKey = useMemo(() => payload ? settlementPreviewKey(payload) : '', [payload]);
  const payloadPricingKey = useMemo(
    () => payload ? settlementPricingKey(payload) : '',
    [payload]
  );
  const previewIsCurrent = isSettlementPreviewCurrent(
    preview,
    previewKey,
    payload,
    isLoadingPreview
  );

  useEffect(() => {
    if (!open || !payload || validationError || isSettling || hasSettlementAttempt) {
      previewSequence.current += 1;
      setIsLoadingPreview(false);
      setPreviewError(validationError);
      return;
    }

    const sequence = ++previewSequence.current;
    setIsLoadingPreview(true);
    setPreviewError(null);

    const timer = window.setTimeout(() => {
      previewSettlement(payload)
        .then((result) => {
          if (sequence !== previewSequence.current) return;
          setPreview(result);
          setPreviewKey(payloadKey);
          setPreviewPricingKey(payloadPricingKey);
        })
        .catch((error) => {
          if (sequence !== previewSequence.current) return;
          setPreviewError(error instanceof Error ? error.message : t('settlement.errors.preview_failed'));
        })
        .finally(() => {
          if (sequence === previewSequence.current) setIsLoadingPreview(false);
        });
    }, 350);

    return () => window.clearTimeout(timer);
  }, [hasSettlementAttempt, isSettling, open, payload, payloadKey, payloadPricingKey, validationError]);

  const handleHouseOfferChange = (enabled: boolean) => {
    setHouseOffer(enabled);
    setPreviewError(null);
    if (enabled) {
      setInvoiceDiscount(null);
      setCreditEnabled(false);
      if (context) setCustomer({ existing: context.customer.id });
      setPaymentInputs({});
    } else if (context) {
      setPreview(null);
      setPreviewKey('');
    }
  };

  const handleCreditChange = (enabled: boolean) => {
    setCreditEnabled(enabled);
    setPreviewError(null);
    if (enabled) {
      setPaymentInputs({});
    } else if (context) {
      setCustomer({ existing: context.customer.id });
    }
  };

  const handlePaymentFocus = (mode: string) => {
    if (!context || creditEnabled || paymentInputs[mode]) return;
    const pricingPreviewIsCurrent = preview && previewPricingKey === payloadPricingKey;
    const total = (pricingPreviewIsCurrent ? preview.totals.grand_total : undefined)
      ?? context.totals.grand_total;
    const remaining = calculateRemainingPayment(paymentInputs, mode, total);
    if (remaining > 0) {
      setPaymentInputs((current) => ({
        ...current,
        [mode]: remaining.toFixed(context.precision),
      }));
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (isSettling) return;
    onOpenChange(nextOpen);
  };

  const handleSettle = async () => {
    const hasValidSettlementMethod = houseOffer || creditEnabled || payments.length > 0;
    if (
      !payload
      || !previewIsCurrent
      || validationError
      || isSettling
      || !hasValidSettlementMethod
    ) return;

    setIsSettling(true);
    setHasSettlementAttempt(true);
    setPreviewError(null);
    const browserPrintWindow = posProfile?.print_type === 'socket'
      ? window.open('', '_blank')
      : null;
    if (browserPrintWindow) browserPrintWindow.opener = null;

    let committed = false;
    try {
      const result = await settleInvoice({
        ...payload,
        idempotency_key: idempotencyKey.current,
      });
      committed = true;
      showToast.success(t(`settlement.success.${result.settlement_type.toLowerCase().replace(/ /g, '_')}`));

      try {
        await printSettledInvoice(browserPrintWindow);
        showToast.success(t('success.printed'));
      } catch (printError) {
        browserPrintWindow?.close();
        const reasonText = printError instanceof Error ? printError.message : String(printError);
        showToast.error(t('errors.print_failed', { reason: reasonText }));
      }

      try {
        await onSettled(result);
      } catch (refreshError) {
        showToast.error(refreshError instanceof Error
          ? refreshError.message
          : t('settlement.errors.refresh_failed'));
      }
      onOpenChange(false);
    } catch (error) {
      browserPrintWindow?.close();
      setPreviewError(error instanceof Error ? error.message : t('settlement.errors.settle_failed'));
      if (shouldRotateSettlementIdempotencyKey(error)) {
        idempotencyKey.current = createIdempotencyKey();
        setHasSettlementAttempt(false);
      }
    } finally {
      if (!committed) setIsSettling(false);
    }
  };

  const previewTotals = previewIsCurrent ? preview?.totals : undefined;
  const { tenderedAmount, paidNow, creditAmount } = calculateSettlementAllocation(
    payments,
    context?.totals.grand_total ?? 0,
    creditEnabled,
    previewTotals
  );
  const customerLabel = context
    ? (previewIsCurrent && preview?.customer?.name
      ? preview.customer.name
      : selectedCustomerLabel(customer, context))
    : '';
  const actionLabel = houseOffer
    ? t('settlement.actions.house_offer')
    : creditEnabled
      ? t('settlement.actions.credit')
      : t('settlement.actions.pay');
  const formLocked = isSettling || hasSettlementAttempt;
  const hasValidSettlementMethod = houseOffer || creditEnabled || payments.length > 0;
  const hasSpecialOperation = houseOffer || creditEnabled || !!invoiceDiscount;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        variant="xlarge"
        className="flex max-h-[94vh] w-[min(96vw,88rem)] max-w-none flex-col overflow-hidden bg-white p-0"
        showCloseButton={false}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settlement-dialog-title"
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 id="settlement-dialog-title" className="text-xl font-bold text-gray-900">{t('settlement.title')}</h2>
            <p className="mt-1 text-sm text-gray-500">{t('settlement.description', { invoice })}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => handleOpenChange(false)}
            disabled={isSettling}
            aria-label={t('common.cancel')}
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        {isLoadingContext ? (
          <div className="flex min-h-[32rem] items-center justify-center">
            <Spinner message={t('settlement.loading')} />
          </div>
        ) : contextError || !context ? (
          <div className="flex min-h-[24rem] flex-col items-center justify-center gap-4 p-8 text-center">
            <p className="max-w-xl text-red-700">{contextError || t('settlement.errors.context_failed')}</p>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1.6fr)_minmax(20rem,0.8fr)]">
            <div className="min-h-0 overflow-y-auto px-6 py-5">
              <div className="space-y-7">
                <section>
                  <DiscountEditor
                    invoiceDiscount={invoiceDiscount}
                    onInvoiceDiscountChange={setInvoiceDiscount}
                    houseOffer={houseOffer}
                    onHouseOfferChange={handleHouseOfferChange}
                    enabled={context.flags.discount}
                    disabled={formLocked}
                    currency={context.currency}
                    maxPercentage={context.limits.max_discount_percentage}
                  />
                </section>

                <section className="space-y-4 border-t border-gray-200 pt-6" aria-labelledby="settlement-payments-title">
                  <div>
                    <h3 id="settlement-payments-title" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
                      <WalletCards className="h-5 w-5" />
                      {t('settlement.payment.title')}
                    </h3>
                    <p className="mt-1 text-xs text-gray-500">{t('settlement.payment.description')}</p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {context.payment_modes.map((mode) => (
                      <label key={mode.id} className="space-y-1 text-sm font-medium text-gray-700">
                        <span>{mode.name}</span>
                        <Input
                          type="number"
                          min="0"
                          step={1 / 10 ** context.precision}
                          value={paymentInputs[mode.id] ?? ''}
                          onFocus={() => handlePaymentFocus(mode.id)}
                          onChange={(event) => {
                            setPaymentInputs((current) => ({
                              ...current,
                              [mode.id]: event.target.value,
                            }));
                          }}
                          placeholder={t('payment.amount_placeholder')}
                          disabled={formLocked || houseOffer}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="flex items-center justify-between rounded-md bg-gray-50 p-3 text-sm">
                    <span className="text-gray-600">{t('payment.total_entered')}</span>
                    <strong className="tabular-nums text-gray-900">{formatCurrency(tenderedAmount)}</strong>
                  </div>
                </section>

                <section className="space-y-4 border-t border-gray-200 pt-6">
                  <CreditPanel
                    available={context.flags.credit}
                    enabled={creditEnabled}
                    onEnabledChange={handleCreditChange}
                    dueDate={dueDate}
                    onDueDateChange={setDueDate}
                    creditAmount={creditAmount}
                    paidNow={paidNow}
                    disabled={formLocked || houseOffer}
                  >
                    {creditEnabled && (
                      <div className="space-y-3 rounded-md border border-violet-200 bg-white p-4" aria-labelledby="settlement-customer-title">
                        <h3 id="settlement-customer-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
                          <UserRound className="h-5 w-5 text-violet-700" />
                          {t('settlement.customer.credit_title')}
                        </h3>
                        <SettlementCustomerEditor
                          value={customer}
                          currentCustomer={context.customer}
                          onChange={setCustomer}
                          disabled={formLocked}
                          requiredRealCustomer
                          defaultCustomer={context.default_customer}
                        />
                      </div>
                    )}
                  </CreditPanel>
                </section>

                {hasSpecialOperation && (
                <section className="space-y-2 border-t border-gray-200 pt-6">
                  <label htmlFor="settlement-reason" className="flex items-center gap-2 text-sm font-semibold text-gray-900">
                    <MessageSquareText className="h-4 w-4" />
                    {t('settlement.reason.label')}
                  </label>
                  <Textarea
                    id="settlement-reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder={t('settlement.reason.placeholder')}
                    disabled={formLocked}
                    rows={3}
                  />
                  <p className="text-xs text-gray-500">{t('settlement.reason.help')}</p>
                </section>
                )}
              </div>
            </div>

            <aside className="min-h-0 overflow-y-auto border-t border-gray-200 bg-white px-6 py-5 lg:border-s lg:border-t-0">
              <SettlementSummary
                preview={previewIsCurrent ? preview : null}
                fallbackTotals={{
                  total_catalogue: context.totals.total_catalogue,
                  total_before_manual_discount: context.totals.total_before_manual_discount,
                  grand_total: context.totals.grand_total,
                }}
                customerLabel={creditEnabled ? customerLabel : undefined}
                dueDate={creditEnabled ? dueDate : undefined}
                tableLabel={tableLabel}
                isLoading={isLoadingPreview}
                isCurrent={previewIsCurrent}
                error={previewError}
              />

              <div className="mt-6 space-y-3">
                <Button
                  type="button"
                  className="w-full"
                  onClick={handleSettle}
                  disabled={
                    isSettling
                    || !previewIsCurrent
                    || !!validationError
                    || !hasValidSettlementMethod
                  }
                >
                  {isSettling
                    ? t('settlement.actions.processing')
                    : hasSettlementAttempt
                      ? t('settlement.actions.retry')
                      : actionLabel}
                </Button>
                <p className="text-center text-xs text-gray-500">
                  {t('settlement.actions.server_authority')}
                </p>
              </div>
            </aside>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
