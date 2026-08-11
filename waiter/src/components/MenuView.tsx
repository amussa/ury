import { ArrowLeft, ImageOff, MessageSquarePlus, PackageX, Plus, RefreshCw, Search } from 'lucide-react';
import { Button, Input, cn } from '@ury/ui';
import { formatMoney, resolveAssetUrl } from '@/lib/format';
import type { MenuCategory, WaiterMenuItem } from '@/types';

interface MenuViewProps {
  tableName: string;
  roomName: string;
  items: WaiterMenuItem[];
  categories: MenuCategory[];
  selectedCategory: string;
  search: string;
  draftQuantityByCode: Record<string, number>;
  currency: string;
  currencySymbol: string | null;
  loading: boolean;
  error: string | null;
  interactionDisabled?: boolean;
  onBack: () => void;
  onSearchChange: (value: string) => void;
  onCategoryChange: (value: string) => void;
  onQuickAdd: (item: WaiterMenuItem) => void;
  onConfigure: (item: WaiterMenuItem) => void;
  onRetry: () => void;
}

function stockDetails(item: WaiterMenuItem, draftQuantity: number) {
  const constrained = item.is_stock_item && !item.negative_stock_allowed && item.available_qty !== null;
  if (!constrained) {
    return { unavailable: false, label: 'Disponível', remaining: null };
  }

  const remaining = Math.max(0, (item.available_qty ?? 0) - draftQuantity);
  return {
    unavailable: remaining <= 0,
    remaining,
    label: remaining <= 0
      ? 'Indisponível'
      : `${remaining} ${item.stock_uom || 'un.'} disponível${remaining === 1 ? '' : 's'}`,
  };
}

export function MenuView({
  tableName,
  roomName,
  items,
  categories,
  selectedCategory,
  search,
  draftQuantityByCode,
  currency,
  currencySymbol,
  loading,
  error,
  interactionDisabled = false,
  onBack,
  onSearchChange,
  onCategoryChange,
  onQuickAdd,
  onConfigure,
  onRetry,
}: MenuViewProps) {
  const normalizedSearch = search.trim().toLocaleLowerCase('pt');
  const filteredItems = items.filter((item) => {
    const matchesCategory = !selectedCategory || item.category === selectedCategory;
    const matchesSearch = !normalizedSearch
      || item.item_name.toLocaleLowerCase('pt').includes(normalizedSearch)
      || item.item_code.toLocaleLowerCase('pt').includes(normalizedSearch)
      || item.description.toLocaleLowerCase('pt').includes(normalizedSearch);
    return matchesCategory && matchesSearch;
  });

  return (
    <main className="mx-auto w-full max-w-5xl pb-32">
      <div className="border-b border-slate-200 bg-white px-3 py-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label="Voltar às mesas"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-slate-200 bg-white text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            onClick={onBack}
          >
            <ArrowLeft className="h-5 w-5" aria-hidden="true" />
          </button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-bold uppercase tracking-wider text-primary">{roomName}</p>
            <h1 className="truncate text-xl font-extrabold text-slate-950">{tableName}</h1>
          </div>
        </div>
        <label className="relative mt-4 block">
          <span className="sr-only">Pesquisar produtos</span>
          <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <Input
            type="search"
            inputMode="search"
            autoComplete="off"
            className="h-12 rounded-xl pl-11 text-base"
            placeholder="Pesquisar produto…"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
      </div>

      <nav className="overflow-x-auto border-b border-slate-200 bg-slate-50 px-3 py-3 scrollbar-none" aria-label="Categorias do menu">
        <div className="flex w-max gap-2">
          <button
            type="button"
            aria-pressed={!selectedCategory}
            className={cn(
              'h-11 rounded-full border px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              !selectedCategory ? 'border-primary bg-primary text-white' : 'border-slate-200 bg-white text-slate-700',
            )}
            onClick={() => onCategoryChange('')}
          >
            Todos
          </button>
          {categories.map((category) => (
            <button
              key={category.name}
              type="button"
              aria-pressed={selectedCategory === category.name}
              className={cn(
                'h-11 rounded-full border px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                selectedCategory === category.name
                  ? 'border-primary bg-primary text-white'
                  : 'border-slate-200 bg-white text-slate-700',
              )}
              onClick={() => onCategoryChange(category.name)}
            >
              {category.label}
            </button>
          ))}
        </div>
      </nav>

      <div className="px-3 py-4">
        {loading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4" role="status" aria-label="A carregar menu">
            {Array.from({ length: 8 }, (_, index) => (
              <div key={index} className="h-64 animate-pulse rounded-2xl border border-slate-200 bg-white p-3">
                <div className="h-24 rounded-xl bg-slate-200" />
                <div className="mt-3 h-5 w-4/5 rounded bg-slate-200" />
                <div className="mt-2 h-4 w-1/2 rounded bg-slate-100" />
                <div className="mt-5 h-11 rounded-xl bg-slate-100" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-8 text-center">
            <PackageX className="mx-auto h-9 w-9 text-red-600" aria-hidden="true" />
            <h2 className="mt-3 font-bold text-slate-950">Não foi possível carregar o menu</h2>
            <p className="mt-1 text-sm text-slate-600">{error}</p>
            <Button className="mt-5 h-12 gap-2" onClick={onRetry}>
              <RefreshCw className="h-5 w-5" aria-hidden="true" />
              Tentar novamente
            </Button>
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white px-5 py-10 text-center">
            <Search className="mx-auto h-9 w-9 text-slate-400" aria-hidden="true" />
            <h2 className="mt-3 font-bold text-slate-950">Nenhum produto encontrado</h2>
            <p className="mt-1 text-sm text-slate-600">Altere a pesquisa ou escolha outra categoria.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {filteredItems.map((item) => {
              const draftQuantity = draftQuantityByCode[item.item_code] ?? 0;
              const stock = stockDetails(item, draftQuantity);
              const imageUrl = resolveAssetUrl(item.image);
              return (
                <article key={item.item_code} className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                  <button
                    type="button"
                    className="min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    disabled={stock.unavailable || interactionDisabled}
                    onClick={() => onConfigure(item)}
                    aria-label={`Configurar ${item.item_name}`}
                  >
                    <div className="grid h-24 place-items-center overflow-hidden bg-slate-100 sm:h-28">
                      {imageUrl ? (
                        <img className="h-full w-full object-cover" src={imageUrl} alt="" loading="lazy" />
                      ) : (
                        <ImageOff className="h-7 w-7 text-slate-400" aria-hidden="true" />
                      )}
                    </div>
                    <div className="p-3 pb-2">
                      <h2 className="line-clamp-2 min-h-10 text-sm font-extrabold leading-5 text-slate-950">{item.item_name}</h2>
                      <p className="mt-1 text-sm font-bold text-primary">{formatMoney(item.rate, currency, currencySymbol)}</p>
                      <p className={cn('mt-1 line-clamp-2 min-h-8 text-[11px] leading-4', stock.unavailable ? 'font-bold text-red-600' : 'text-slate-500')}>
                        {stock.label}
                      </p>
                    </div>
                  </button>
                  <div className="mt-auto space-y-2 px-3 pb-3">
                    {draftQuantity > 0 ? (
                      <p className="rounded-lg bg-blue-50 px-2 py-1 text-center text-xs font-bold text-blue-800">No pedido: {draftQuantity}</p>
                    ) : null}
                    <Button
                      type="button"
                      className="h-11 w-full gap-1.5 px-2"
                      disabled={stock.unavailable || interactionDisabled}
                      onClick={() => onQuickAdd(item)}
                    >
                      <Plus className="h-4 w-4" aria-hidden="true" />
                      Adicionar
                    </Button>
                    <button
                      type="button"
                      className="flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-xs font-bold text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:text-slate-300"
                      disabled={stock.unavailable || interactionDisabled}
                      onClick={() => onConfigure(item)}
                    >
                      <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
                      Com observação
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}
