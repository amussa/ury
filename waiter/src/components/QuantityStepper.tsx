import { Minus, Plus } from 'lucide-react';
import { cn } from '@ury/ui';

interface QuantityStepperProps {
  value: number;
  onDecrease: () => void;
  onIncrease: () => void;
  decreaseDisabled?: boolean;
  increaseDisabled?: boolean;
  disabled?: boolean;
  compact?: boolean;
  label: string;
}

export function QuantityStepper({
  value,
  onDecrease,
  onIncrease,
  decreaseDisabled = false,
  increaseDisabled = false,
  disabled = false,
  compact = false,
  label,
}: QuantityStepperProps) {
  const buttonClass = compact ? 'h-10 w-10' : 'h-12 w-12';
  return (
    <div className="inline-flex items-center rounded-xl border border-slate-200 bg-white" role="group" aria-label={label}>
      <button
        type="button"
        aria-label={`Diminuir ${label}`}
        className={cn('grid place-items-center rounded-l-xl text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', buttonClass)}
        disabled={disabled || decreaseDisabled}
        onClick={onDecrease}
      >
        <Minus className="h-4 w-4" aria-hidden="true" />
      </button>
      <output className={cn('grid min-w-10 place-items-center border-x border-slate-200 px-2 font-extrabold text-slate-950', compact ? 'h-10' : 'h-12')} aria-live="polite">
        {value}
      </output>
      <button
        type="button"
        aria-label={`Aumentar ${label}`}
        className={cn('grid place-items-center rounded-r-xl text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', buttonClass)}
        disabled={disabled || increaseDisabled}
        onClick={onIncrease}
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}
