import { LoaderCircle, LogOut, RefreshCw, Utensils } from 'lucide-react';
import { InstallAppButton } from '@/components/InstallAppButton';

interface AppHeaderProps {
  userName: string;
  branch: string;
  onRefresh?: () => void;
  onLogout: () => void;
  refreshing?: boolean;
  loggingOut?: boolean;
}

export function AppHeader({ userName, branch, onRefresh, onLogout, refreshing = false, loggingOut = false }: AppHeaderProps) {
  return (
    <header className="safe-top sticky top-0 z-30 border-b border-slate-200 bg-white/95 px-3 pb-3 pt-3 backdrop-blur">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-3">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary text-white">
          <Utensils className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-bold text-slate-950">{userName || 'Atendente'}</p>
          <p className="truncate text-xs text-slate-500">{branch}</p>
        </div>
        {onRefresh ? (
          <button
            type="button"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-slate-200 bg-white text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            aria-label="Actualizar"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <RefreshCw className={`h-5 w-5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
          </button>
        ) : null}
        <InstallAppButton compact />
        <button
          type="button"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-slate-200 bg-white text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-label="Terminar sessão"
          disabled={loggingOut}
          onClick={onLogout}
        >
          {loggingOut
            ? <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden="true" />
            : <LogOut className="h-5 w-5" aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
}
