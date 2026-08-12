import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import { Button, Textarea, cn } from '@ury/ui';
import { formatMoney } from '@/lib/format';
import type { DraftOrderItem, WaiterMenuItem, WaiterPriceOption } from '@/types';
import { MobileSheet } from './MobileSheet';
import { QuantityStepper } from './QuantityStepper';

export interface ItemEditorValue {
  qty: number;
  comment: string;
  priceOption: string | null;
}

interface ItemEditorSheetProps {
  open: boolean;
  item: WaiterMenuItem | null;
  line: DraftOrderItem | null;
  maximumQuantity: (priceOption: string | null) => number;
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
  const [selectedPriceOption, setSelectedPriceOption] = useState<string | null>(null);

  const priceOptions = useMemo(() => {
    if (!item) return [];
    const options = [...item.price_options];
    if (line?.price_option && !options.some((option) => option.id === line.price_option)) {
      options.push({
        id: line.price_option,
        label: line.price_option_label || 'Preço anterior',
        rate: line.rate,
        available_qty: 0,
        is_default: false,
      });
    }
    return options;
  }, [item, line]);

  useEffect(() => {
    if (!open || !item) return;
    setQuantity(line?.qty ?? 1);
    setComment(line?.comment ?? '');
    if (line) {
      setSelectedPriceOption(
        line.price_option
        ?? item.price_options.find((option) => option.is_default)?.id
        ?? null,
      );
      return;
    }
    const availableOptions = item.price_options.filter(
      (option) => maximumQuantity(option.id) > 0,
    );
    setSelectedPriceOption(availableOptions.length === 1 ? availableOptions[0].id : null);
  }, [item, line, maximumQuantity, open]);

  if (!item) return null;
  const selectedOption = priceOptions.find((option) => option.id === selectedPriceOption) ?? null;
  const selectedRate = selectedOption?.rate ?? item.rate;
  const selectedMaximum = maximumQuantity(selectedPriceOption);
  const effectiveMaximum = Math.max(line?.qty ?? 1, selectedMaximum);
  const optionRequired = item.price_options.length > 0 && !selectedOption;
  const hasStockConflict = !optionRequired && quantity > selectedMaximum;
  const optionSelectionLocked = Boolean(line && (item.price_options.length > 0 || line.price_option));

  const selectOption = (option: WaiterPriceOption) => {
    if (optionSelectionLocked || maximumQuantity(option.id) <= 0) return;
    setSelectedPriceOption(option.id);
    setQuantity((current) => Math.max(1, Math.min(current, maximumQuantity(option.id))));
  };

  return (
    <MobileSheet
      open={open}
      title={line ? 'Editar produto' : 'Adicionar produto'}
      description={item.item_name}
      onClose={onClose}
      footer={(
        <Button
          className="mb-3 h-12 w-full text-base font-bold"
          disabled={optionRequired || hasStockConflict}
          onClick={() => onSave({
            qty: quantity,
            comment: comment.trim(),
            priceOption: selectedOption?.id ?? null,
          })}
        >
          {line
            ? 'Guardar alterações'
            : optionRequired
              ? 'Escolha um preço'
              : `Adicionar · ${formatMoney(selectedRate * quantity, currency, currencySymbol)}`}
        </Button>
      )}
    >
      <div className="space-y-5">
        {priceOptions.length > 0 ? (
          <fieldset>
            <legend className="mb-2 text-sm font-bold text-slate-800">Escolha o preço</legend>
            <div className="space-y-2">
              {priceOptions.map((option) => {
                const optionMaximum = maximumQuantity(option.id);
                const selected = option.id === selectedPriceOption;
                const unavailable = optionMaximum <= 0;
                return (
                  <button
                    key={option.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={unavailable || (optionSelectionLocked && !selected)}
                    className={cn(
                      'flex min-h-16 w-full items-center gap-3 rounded-2xl border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                      selected ? 'border-primary bg-blue-50' : 'border-slate-200 bg-white',
                      (unavailable || (optionSelectionLocked && !selected)) && 'opacity-55',
                    )}
                    onClick={() => selectOption(option)}
                  >
                    <span className={cn(
                      'grid h-6 w-6 shrink-0 place-items-center rounded-full border',
                      selected ? 'border-primary bg-primary text-white' : 'border-slate-300 bg-white',
                    )}>
                      {selected ? <Check className="h-4 w-4" aria-hidden="true" /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-extrabold text-slate-900">{option.label}</span>
                      <span className={cn('mt-0.5 block text-xs', unavailable ? 'font-bold text-red-600' : 'text-slate-500')}>
                        {`Stock: ${optionMaximum} ${item.stock_uom || 'un.'}${unavailable ? ' · Indisponível' : ''}`}
                      </span>
                    </span>
                    <span className="shrink-0 font-extrabold text-primary">
                      {formatMoney(option.rate, currency, currencySymbol)}
                    </span>
                  </button>
                );
              })}
            </div>
            {optionSelectionLocked ? (
              <p className="mt-2 text-xs leading-5 text-slate-500">
                O preço desta linha é mantido. Para escolher outro, remova a linha e adicione o produto novamente.
              </p>
            ) : null}
          </fieldset>
        ) : null}
        <div className="flex items-center justify-between gap-4 rounded-2xl bg-slate-50 p-4">
          <div>
            <p className="text-sm text-slate-500">Quantidade</p>
            <p className="mt-1 font-bold text-primary">{formatMoney(selectedRate, currency, currencySymbol)} cada</p>
          </div>
          <QuantityStepper
            label={`quantidade de ${item.item_name}`}
            value={quantity}
            decreaseDisabled={quantity <= 1}
            increaseDisabled={optionRequired || quantity >= effectiveMaximum}
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
