import { AlertCircle, CheckCircle2, LockKeyhole, RefreshCw, Users } from 'lucide-react';
import { Button, Card, cn } from '@ury/ui';
import type { WaiterRoom, WaiterTable } from '@/types';

interface TableSelectionProps {
  rooms: WaiterRoom[];
  selectedRoom: string;
  tables: WaiterTable[];
  loading: boolean;
  error: string | null;
  onRoomChange: (room: string) => void;
  onTableSelect: (table: WaiterTable) => void;
  onRetry: () => void;
}

const tableMeta = {
  free: {
    label: 'Livre',
    detail: 'Pode registar um pedido',
    icon: CheckCircle2,
    badge: 'bg-emerald-100 text-emerald-800',
    card: 'border-emerald-200 bg-emerald-50/40',
  },
  mine: {
    label: 'Minha mesa',
    detail: 'Pode acrescentar produtos',
    icon: Users,
    badge: 'bg-blue-100 text-blue-800',
    card: 'border-blue-300 bg-blue-50/50',
  },
  locked: {
    label: 'Ocupada',
    detail: 'Pertence a outro atendente',
    icon: LockKeyhole,
    badge: 'bg-slate-200 text-slate-700',
    card: 'border-slate-200 bg-slate-100 opacity-75',
  },
} as const;

function lockedTableCopy(table: WaiterTable): { label: string; detail: string } {
  if (table.ownership === 'other' || table.reason === 'other_waiter') {
    return { label: 'Ocupada', detail: 'Pertence a outro atendente' };
  }
  if (table.reason === 'sent_for_billing') {
    return { label: 'Conta bloqueada', detail: 'Já foi enviada para facturação' };
  }
  if (table.reason === 'merged_or_split') {
    return { label: 'Conta combinada', detail: 'Deve ser tratada no POS padrão' };
  }
  if (table.reason === 'till_closed') {
    return { label: 'Indisponível', detail: 'Aguarda a abertura do caixa' };
  }
  return { label: 'Indisponível', detail: 'Não pode ser alterada nesta página' };
}

export function TableSelection({
  rooms,
  selectedRoom,
  tables,
  loading,
  error,
  onRoomChange,
  onTableSelect,
  onRetry,
}: TableSelectionProps) {
  return (
    <main className="mx-auto w-full max-w-5xl px-3 pb-10 pt-5">
      <div className="mb-4">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-primary">Atendimento de mesa</p>
        <h1 className="mt-1 text-2xl font-extrabold tracking-tight text-slate-950">Escolha uma mesa</h1>
        <p className="mt-1 text-sm text-slate-600">Só pode alterar mesas livres ou atribuídas a si.</p>
      </div>

      <nav className="-mx-3 mb-5 overflow-x-auto px-3 pb-1 scrollbar-none" aria-label="Salas">
        <div className="flex w-max gap-2">
          {rooms.map((room) => {
            const selected = room.name === selectedRoom;
            return (
              <button
                key={room.name}
                type="button"
                aria-pressed={selected}
                className={cn(
                  'h-11 shrink-0 rounded-full border px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                  selected
                    ? 'border-primary bg-primary text-white'
                    : room.is_open
                      ? 'border-slate-200 bg-white text-slate-700'
                      : 'border-slate-200 bg-slate-100 text-slate-400',
                )}
                disabled={!room.is_open}
                onClick={() => onRoomChange(room.name)}
              >
                {room.label}
                {!room.is_open ? ' · fechada' : ''}
              </button>
            );
          })}
        </div>
      </nav>

      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4" role="status" aria-label="A carregar mesas">
          {Array.from({ length: 6 }, (_, index) => (
            <div key={index} className="h-32 animate-pulse rounded-2xl border border-slate-200 bg-white p-4">
              <div className="h-5 w-2/3 rounded bg-slate-200" />
              <div className="mt-7 h-4 w-1/2 rounded bg-slate-100" />
              <div className="mt-2 h-4 w-4/5 rounded bg-slate-100" />
            </div>
          ))}
        </div>
      ) : error ? (
        <Card className="flex flex-col items-center px-5 py-8 text-center">
          <AlertCircle className="h-9 w-9 text-red-600" aria-hidden="true" />
          <h2 className="mt-3 font-bold text-slate-950">Não foi possível carregar as mesas</h2>
          <p className="mt-1 text-sm text-slate-600">{error}</p>
          <Button className="mt-5 h-12 gap-2" onClick={onRetry}>
            <RefreshCw className="h-5 w-5" aria-hidden="true" />
            Tentar novamente
          </Button>
        </Card>
      ) : tables.length === 0 ? (
        <Card className="px-5 py-9 text-center">
          <Users className="mx-auto h-9 w-9 text-slate-400" aria-hidden="true" />
          <h2 className="mt-3 font-bold text-slate-950">Sem mesas nesta sala</h2>
          <p className="mt-1 text-sm text-slate-600">Actualize a lista ou escolha outra sala.</p>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
          {tables.map((table) => {
            const meta = tableMeta[table.status];
            const copy = table.status === 'locked' ? lockedTableCopy(table) : meta;
            const Icon = meta.icon;
            const canOpen = table.editable && table.status !== 'locked';
            return (
              <button
                key={table.name}
                type="button"
                className={cn(
                  'min-h-32 rounded-2xl border p-3 text-left shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
                  meta.card,
                  canOpen && 'active:scale-[0.98]',
                )}
                disabled={!canOpen}
                onClick={() => onTableSelect(table)}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="line-clamp-2 text-base font-extrabold text-slate-950">{table.name}</span>
                  <Icon className="h-5 w-5 shrink-0 text-slate-600" aria-hidden="true" />
                </div>
                <span className={cn('mt-3 inline-flex rounded-full px-2 py-1 text-[11px] font-bold', meta.badge)}>{copy.label}</span>
                <p className="mt-2 text-xs leading-4 text-slate-600">{copy.detail}</p>
              </button>
            );
          })}
        </div>
      )}
    </main>
  );
}
