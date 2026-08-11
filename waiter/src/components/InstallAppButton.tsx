import { useEffect, useState } from 'react';
import { Download, LoaderCircle } from 'lucide-react';
import { cn } from '@ury/ui';
import {
  isWaiterInstallAvailable,
  promptWaiterInstall,
  subscribeToWaiterInstallAvailability,
} from '@/lib/pwa';

interface InstallAppButtonProps {
  compact?: boolean;
  className?: string;
}

export function InstallAppButton({ compact = false, className }: InstallAppButtonProps) {
  const [available, setAvailable] = useState(isWaiterInstallAvailable);
  const [prompting, setPrompting] = useState(false);

  useEffect(() => subscribeToWaiterInstallAvailability(() => {
    setAvailable(isWaiterInstallAvailable());
  }), []);

  if (!available) return null;

  const install = async () => {
    if (prompting) return;
    setPrompting(true);
    try {
      await promptWaiterInstall();
    } finally {
      setPrompting(false);
      setAvailable(isWaiterInstallAvailable());
    }
  };

  return (
    <button
      type="button"
      className={cn(
        compact
          ? 'grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-slate-200 bg-white text-slate-700'
          : 'flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 font-semibold text-blue-800',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:opacity-60',
        className,
      )}
      aria-label={compact ? 'Instalar aplicação no dispositivo' : undefined}
      disabled={prompting}
      onClick={() => void install()}
    >
      {prompting
        ? <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden="true" />
        : <Download className="h-5 w-5" aria-hidden="true" />}
      {compact ? null : <span>{prompting ? 'A abrir instalação…' : 'Instalar aplicação'}</span>}
    </button>
  );
}
