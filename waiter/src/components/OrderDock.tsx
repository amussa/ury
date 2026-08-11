import { ClipboardList, ChevronUp } from 'lucide-react';
import { Button } from '@ury/ui';
import { formatMoney } from '@/lib/format';

interface OrderDockProps {
  itemCount: number;
  total: number;
  hasExistingOrder: boolean;
  currency: string;
  currencySymbol: string | null;
  onOpen: () => void;
}

export function OrderDock({ itemCount, total, hasExistingOrder, currency, currencySymbol, onOpen }: OrderDockProps) {
  const enabled = itemCount > 0 || hasExistingOrder;
  return (
    <div className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 px-3 pt-3 shadow-sheet backdrop-blur">
      <div className="mx-auto max-w-xl">
        <Button
          type="button"
          className="mb-3 flex h-14 w-full items-center justify-between rounded-2xl px-4 text-base"
          disabled={!enabled}
          onClick={onOpen}
        >
          <span className="flex items-center gap-2">
            <ClipboardList className="h-5 w-5" aria-hidden="true" />
            <span>
              Resumo do pedido
              {itemCount > 0 ? <span className="ml-1 rounded-full bg-white/20 px-2 py-0.5 text-sm">{itemCount}</span> : null}
            </span>
          </span>
          <span className="flex items-center gap-2">
            {itemCount > 0 ? formatMoney(total, currency, currencySymbol) : 'Ver pedido'}
            <ChevronUp className="h-5 w-5" aria-hidden="true" />
          </span>
        </Button>
      </div>
    </div>
  );
}
