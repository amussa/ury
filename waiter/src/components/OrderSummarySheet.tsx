import { AlertTriangle, LockKeyhole, Pencil, Send, Trash2, Users } from 'lucide-react';
import { Button, Textarea } from '@ury/ui';
import { formatMoney } from '@/lib/format';
import type { DraftOrderItem, TableOrder } from '@/types';
import { MobileSheet } from './MobileSheet';
import { QuantityStepper } from './QuantityStepper';

interface OrderSummarySheetProps {
  open: boolean;
  tableName: string;
  existingOrder: TableOrder | null;
  draftItems: DraftOrderItem[];
  noOfPax: number;
  comments: string;
  currency: string;
  currencySymbol: string | null;
  submitting: boolean;
  submitError: string | null;
  mutationDisabled?: boolean;
  onClose: () => void;
  onNoOfPaxChange: (value: number) => void;
  onCommentsChange: (value: string) => void;
  onIncrease: (line: DraftOrderItem) => void;
  onDecrease: (line: DraftOrderItem) => void;
  onEdit: (line: DraftOrderItem) => void;
  onRemove: (line: DraftOrderItem) => void;
  onSubmit: () => void;
}

export function OrderSummarySheet({
  open,
  tableName,
  existingOrder,
  draftItems,
  noOfPax,
  comments,
  currency,
  currencySymbol,
  submitting,
  submitError,
  mutationDisabled = false,
  onClose,
  onNoOfPaxChange,
  onCommentsChange,
  onIncrease,
  onDecrease,
  onEdit,
  onRemove,
  onSubmit,
}: OrderSummarySheetProps) {
  const draftTotal = draftItems.reduce((sum, line) => sum + line.rate * line.qty, 0);
  const draftCount = draftItems.reduce((sum, line) => sum + line.qty, 0);
  const sentTotal = existingOrder?.sent_items.reduce((sum, line) => sum + line.amount, 0) ?? 0;

  return (
    <MobileSheet
      open={open}
      title="Resumo do pedido"
      description={tableName}
      onClose={onClose}
      closeDisabled={submitting}
      className="h-[92dvh] sm:h-auto"
      footer={(
        <div className="mb-3 space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-slate-600">Novos produtos ({draftCount})</span>
            <span className="font-extrabold text-slate-950">{formatMoney(draftTotal, currency, currencySymbol)}</span>
          </div>
          <Button
            className="h-14 w-full gap-2 rounded-2xl text-base font-extrabold"
            disabled={submitting || draftItems.length === 0}
            onClick={onSubmit}
          >
            {submitting ? (
              <span className="h-5 w-5 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true" />
            ) : (
              <Send className="h-5 w-5" aria-hidden="true" />
            )}
            {submitting ? 'A registar…' : mutationDisabled ? 'Repetir envio seguro' : 'Registar pedido'}
          </Button>
          <p className="text-center text-[11px] leading-4 text-slate-500">
            O backend do URY valida o pedido e decide o encaminhamento dos KOTs.
          </p>
        </div>
      )}
    >
      <div className="space-y-6">
        {submitError ? (
          <div className="flex gap-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
            <div>
              <p className="font-bold">O pedido não foi confirmado</p>
              <p className="mt-1 leading-5">{submitError}</p>
              <p className="mt-1 text-xs">Os novos produtos continuam neste resumo. Pode tentar novamente.</p>
            </div>
          </div>
        ) : null}

        {existingOrder && existingOrder.sent_items.length > 0 ? (
          <section aria-labelledby="sent-items-title">
            <div className="mb-3 flex items-start gap-2">
              <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
              <div>
                <h3 className="text-sm font-extrabold text-slate-800" id="sent-items-title">Já registado</h3>
                <p className="text-xs leading-5 text-slate-500">Estes produtos são apenas para consulta e não podem ser alterados aqui.</p>
              </div>
            </div>
            <div className="divide-y divide-slate-200 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
              {existingOrder.sent_items.map((line) => (
                <div key={line.name} className="flex items-start gap-3 px-3 py-3">
                  <span className="grid h-8 min-w-8 place-items-center rounded-lg bg-slate-200 px-2 text-sm font-extrabold text-slate-700">{line.qty}×</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-bold text-slate-800">{line.item_name}</p>
                    {line.comment ? <p className="mt-1 text-xs italic text-slate-500">{line.comment}</p> : null}
                  </div>
                  <span className="shrink-0 text-sm font-bold text-slate-700">{formatMoney(line.amount, currency, currencySymbol)}</span>
                </div>
              ))}
              <div className="flex justify-between bg-slate-100 px-3 py-2 text-xs font-bold text-slate-600">
                <span>Total já registado</span>
                <span>{formatMoney(sentTotal, currency, currencySymbol)}</span>
              </div>
            </div>
          </section>
        ) : null}

        <section aria-labelledby="draft-items-title">
          <h3 className="mb-3 text-sm font-extrabold text-slate-800" id="draft-items-title">Novos produtos</h3>
          {draftItems.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-300 px-4 py-8 text-center">
              <p className="font-bold text-slate-700">Ainda não adicionou produtos</p>
              <p className="mt-1 text-sm text-slate-500">Feche este resumo e escolha produtos no menu.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {draftItems.map((line) => (
                <article key={line.id} className="rounded-2xl border border-blue-200 bg-blue-50/40 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h4 className="text-sm font-extrabold text-slate-950">{line.item_name}</h4>
                      <p className="mt-1 text-sm font-bold text-primary">{formatMoney(line.rate * line.qty, currency, currencySymbol)}</p>
                      {line.comment ? <p className="mt-1 text-xs italic leading-5 text-slate-600">{line.comment}</p> : null}
                    </div>
                    <button
                      type="button"
                      className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                      aria-label={`Remover ${line.item_name}`}
                      disabled={submitting || mutationDisabled}
                      onClick={() => onRemove(line)}
                    >
                      <Trash2 className="h-5 w-5" aria-hidden="true" />
                    </button>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                    <QuantityStepper
                      compact
                      label={`quantidade de ${line.item_name}`}
                      value={line.qty}
                      disabled={submitting || mutationDisabled}
                      decreaseDisabled={line.qty <= 1}
                      onDecrease={() => onDecrease(line)}
                      onIncrease={() => onIncrease(line)}
                    />
                    <button
                      type="button"
                      className="flex h-11 items-center gap-2 rounded-xl px-3 text-sm font-bold text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      disabled={submitting || mutationDisabled}
                      onClick={() => onEdit(line)}
                    >
                      <Pencil className="h-4 w-4" aria-hidden="true" />
                      {line.comment ? 'Editar observação' : 'Adicionar observação'}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-4 border-t border-slate-200 pt-5" aria-labelledby="order-details-title">
          <h3 className="text-sm font-extrabold text-slate-800" id="order-details-title">Detalhes da mesa</h3>
          <div className="flex items-center justify-between gap-4 rounded-2xl bg-slate-50 p-3">
            <div className="flex items-center gap-2">
              <Users className="h-5 w-5 text-slate-500" aria-hidden="true" />
              <span className="text-sm font-bold text-slate-800">Número de pessoas</span>
            </div>
            <QuantityStepper
              compact
              label="número de pessoas"
              value={noOfPax}
              disabled={submitting || mutationDisabled}
              decreaseDisabled={noOfPax <= 1}
              increaseDisabled={noOfPax >= 99}
              onDecrease={() => onNoOfPaxChange(Math.max(1, noOfPax - 1))}
              onIncrease={() => onNoOfPaxChange(Math.min(99, noOfPax + 1))}
            />
          </div>
          <label className="block">
            <span className="mb-2 block text-sm font-bold text-slate-800">Observação geral do pedido</span>
            <Textarea
              className="min-h-24 rounded-xl text-base"
              maxLength={500}
              placeholder="Observação opcional para este pedido…"
              disabled={submitting || mutationDisabled}
              value={comments}
              onChange={(event) => onCommentsChange(event.target.value)}
            />
            <span className="mt-1 block text-right text-xs text-slate-400">{comments.length}/500</span>
          </label>
        </section>
      </div>
    </MobileSheet>
  );
}
