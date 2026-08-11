import type { ReactNode } from 'react';
import { AlertTriangle, LockKeyhole, RefreshCw, Utensils } from 'lucide-react';
import { Button, Spinner } from '@ury/ui';

interface FullPageLoadingProps {
  message?: string;
}

export function FullPageLoading({ message = 'A preparar o atendimento…' }: FullPageLoadingProps) {
  return (
    <main className="grid min-h-dvh min-w-[320px] place-items-center bg-slate-50 px-6">
      <div className="text-center" role="status" aria-live="polite">
        <div className="mx-auto mb-5 grid h-16 w-16 place-items-center rounded-2xl bg-primary text-white shadow-lg shadow-blue-200">
          <Utensils className="h-8 w-8" aria-hidden="true" />
        </div>
        <Spinner hideMessage className="h-9 w-9" />
        <p className="mt-4 font-medium text-slate-700">{message}</p>
      </div>
    </main>
  );
}

interface StatePanelProps {
  icon?: ReactNode;
  title: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
  actionLoading?: boolean;
  actionIcon?: ReactNode;
  secondaryActionLabel?: string;
  onSecondaryAction?: () => void;
}

export function StatePanel({
  icon,
  title,
  message,
  actionLabel,
  onAction,
  actionLoading = false,
  actionIcon,
  secondaryActionLabel,
  onSecondaryAction,
}: StatePanelProps) {
  return (
    <div className="mx-auto flex min-h-[55dvh] max-w-sm flex-col items-center justify-center px-5 py-10 text-center">
      <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-amber-100 text-amber-700">
        {icon ?? <AlertTriangle className="h-7 w-7" aria-hidden="true" />}
      </div>
      <h2 className="text-xl font-bold text-slate-950">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">{message}</p>
      {actionLabel && onAction ? (
        <Button className="mt-6 h-12 min-w-40 gap-2 text-base" disabled={actionLoading} onClick={onAction}>
          {actionIcon ?? <RefreshCw className={`h-5 w-5 ${actionLoading ? 'animate-spin' : ''}`} aria-hidden="true" />}
          {actionLabel}
        </Button>
      ) : null}
      {secondaryActionLabel && onSecondaryAction ? (
        <button
          type="button"
          className="mt-3 min-h-11 rounded-xl px-4 text-sm font-semibold text-slate-600 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary hover:underline"
          disabled={actionLoading}
          onClick={onSecondaryAction}
        >
          {secondaryActionLabel}
        </button>
      ) : null}
    </div>
  );
}

interface OpeningBlockedProps {
  message?: string | null;
  onRetry: () => void;
  loading: boolean;
}

export function OpeningBlocked({ message, onRetry, loading }: OpeningBlockedProps) {
  return (
    <StatePanel
      icon={<LockKeyhole className="h-7 w-7" aria-hidden="true" />}
      title="O caixa está fechado"
      message={message || 'Não existe um caixa aberto para registar pedidos. Contacte o responsável pelo caixa.'}
      actionLabel="Verificar novamente"
      onAction={onRetry}
      actionLoading={loading}
    />
  );
}
