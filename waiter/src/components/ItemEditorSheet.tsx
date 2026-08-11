import { useEffect, useState } from 'react';
import { Button, Textarea } from '@ury/ui';
import { formatMoney } from '@/lib/format';
import type { DraftOrderItem, WaiterMenuItem } from '@/types';
import { MobileSheet } from './MobileSheet';
import { QuantityStepper } from './QuantityStepper';

export interface ItemEditorValue {
  qty: number;
  comment: string;
}

interface ItemEditorSheetProps {
  open: boolean;
  item: WaiterMenuItem | null;
  line: DraftOrderItem | null;
  maximumQuantity: number;
  currency: string;
  currencySymbol: string | null;
  onClose: () => void;
  onSave: (value: ItemEditorValue) => void;
}

export function ItemEditorSheet({
  open,
  item,
  line,
  maximumQuantity,
  currency,
  currencySymbol,
  onClose,
  onSave,
}: ItemEditorSheetProps) {
  const [quantity, setQuantity] = useState(1);
  const [comment, setComment] = useState('');

  useEffect(() => {
    if (!open) return;
    setQuantity(line?.qty ?? 1);
    setComment(line?.comment ?? '');
  }, [line, open]);

  if (!item) return null;
  const effectiveMaximum = Math.max(line?.qty ?? 1, maximumQuantity);
  const hasStockConflict = quantity > maximumQuantity;

  return (
    <MobileSheet
      open={open}
      title={line ? 'Editar produto' : 'Adicionar produto'}
      description={item.item_name}
      onClose={onClose}
      footer={(
        <Button
          className="mb-3 h-12 w-full text-base font-bold"
          disabled={hasStockConflict}
          onClick={() => onSave({ qty: quantity, comment: comment.trim() })}
        >
          {line ? 'Guardar alterações' : `Adicionar · ${formatMoney(item.rate * quantity, currency, currencySymbol)}`}
        </Button>
      )}
    >
      <div className="space-y-5">
        <div className="flex items-center justify-between gap-4 rounded-2xl bg-slate-50 p-4">
          <div>
            <p className="text-sm text-slate-500">Quantidade</p>
            <p className="mt-1 font-bold text-primary">{formatMoney(item.rate, currency, currencySymbol)} cada</p>
          </div>
          <QuantityStepper
            label={`quantidade de ${item.item_name}`}
            value={quantity}
            decreaseDisabled={quantity <= 1}
            increaseDisabled={quantity >= effectiveMaximum}
            onDecrease={() => setQuantity((current) => Math.max(1, current - 1))}
            onIncrease={() => setQuantity((current) => Math.min(effectiveMaximum, current + 1))}
          />
        </div>
        {hasStockConflict ? (
          <p className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm font-medium leading-5 text-red-800" role="alert">
            A disponibilidade mudou. Reduza a quantidade ou remova este produto do resumo antes de registar.
          </p>
        ) : null}
        <label className="block">
          <span className="mb-2 block text-sm font-bold text-slate-800">Observação para este produto</span>
          <Textarea
            className="min-h-28 rounded-xl text-base"
            maxLength={240}
            placeholder="Ex.: sem açúcar, servir depois…"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <span className="mt-1 block text-right text-xs text-slate-400">{comment.length}/240</span>
        </label>
      </div>
    </MobileSheet>
  );
}
