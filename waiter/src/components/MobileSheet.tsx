import { useEffect, useId, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@ury/ui';

interface MobileSheetProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  closeDisabled?: boolean;
  className?: string;
}

export function MobileSheet({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  closeDisabled = false,
  className,
}: MobileSheetProps) {
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !closeDisabled) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [closeDisabled, onClose, open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" role="presentation">
      <button
        type="button"
        aria-label="Fechar"
        className="absolute inset-0 h-full w-full cursor-default bg-slate-950/55"
        onClick={closeDisabled ? undefined : onClose}
        disabled={closeDisabled}
      />
      <section
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className={cn(
          'relative z-10 flex max-h-[92dvh] w-full max-w-xl flex-col overflow-hidden rounded-t-3xl bg-white shadow-sheet sm:rounded-3xl',
          className,
        )}
        role="dialog"
      >
        <div className="mx-auto mt-2 h-1.5 w-12 shrink-0 rounded-full bg-slate-300 sm:hidden" aria-hidden="true" />
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-100 px-4 py-4">
          <div className="min-w-0">
            <h2 className="text-lg font-bold text-slate-950" id={titleId}>{title}</h2>
            {description ? (
              <p className="mt-1 text-sm text-slate-600" id={descriptionId}>{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            aria-label="Fechar"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            disabled={closeDisabled}
            onClick={onClose}
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">{children}</div>
        {footer ? <footer className="safe-bottom shrink-0 border-t border-slate-200 bg-white px-4 pt-3">{footer}</footer> : null}
      </section>
    </div>
  );
}
