import { FC } from 'react';
import { cn } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import { t } from '../i18n';

interface MenuCardProps {
  id: string;
  name: string;
  price: number;
  item_image: string | null;
  course?: string;
  item: string;
  onClick?: () => void;
  disabled?: boolean;
  available_qty?: number;
  is_stock_item?: boolean;
  stock_uom?: string | null;
}

const MenuCard: FC<MenuCardProps> = ({ 
  name, 
  price, 
  item_image, 
  course, 
  onClick,
  disabled,
  available_qty,
  is_stock_item,
  stock_uom,
}) => {
  const isOutOfStock = is_stock_item === true && (available_qty ?? 0) <= 0;
  const isDisabled = disabled || isOutOfStock;
  const stockLabel = is_stock_item === false
    ? t('stock.not_tracked')
    : t('stock.available', { qty: available_qty ?? 0, uom: stock_uom || '' });

  return (
    <div
      className={cn(
        "bg-white rounded-lg shadow-sm overflow-hidden hover:shadow-md transition-shadow cursor-pointer h-56 flex flex-col",
        isDisabled && "opacity-50 cursor-not-allowed pointer-events-none"
      )}
      onClick={isDisabled ? undefined : onClick}
      aria-disabled={isDisabled}
    >
      {/* Image section - fixed height */}
      <div className="h-24 relative">
        {item_image ? (
          <img
            src={item_image}
            alt={name}
            className="w-full h-full object-cover filter saturate-75 brightness-95"
            style={{ filter: 'saturate(0.7) brightness(0.95)' }}
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = 'none';
              const parent = target.parentElement;
              if (parent) {
                const placeholder = document.createElement('div');
                placeholder.className = 'w-full h-full bg-gray-200 flex items-center justify-center text-2xl text-gray-400 font-medium';
                placeholder.textContent = name.slice(0, 2).toUpperCase();
                parent.insertBefore(placeholder, target);
              }
            }}
          />
        ) : (
          <div className="w-full h-full bg-gray-200 flex items-center justify-center text-2xl text-gray-400 font-medium">
            {name.slice(0, 2).toUpperCase()}
          </div>
        )}
        {typeof is_stock_item === 'boolean' && (
          <span className={cn(
            "absolute top-2 end-2 max-w-[calc(100%-1rem)] truncate rounded-full px-2 py-1 text-[11px] font-semibold shadow-sm",
            isOutOfStock
              ? "bg-red-100 text-red-700"
              : "bg-white/90 text-gray-700"
          )} title={stockLabel}>
            {stockLabel}
          </span>
        )}
      </div>

      {/* Content section - flex grow with fixed padding */}
      <div className="flex-1 p-3 flex flex-col">
        {/* Name section - fixed height for 2 lines */}
        <div className="">
          <h3 className="font-medium text-gray-900 text-sm leading-5 line-clamp-2" title={name}>
            {name}
          </h3>
        </div>

        {/* Course section - fixed height for 1 line */}
        <div className="h-5 mt-1">
          <p className="text-xs text-gray-500 truncate" title={course}>
            {course || ' '}
          </p>
        </div>

        {/* Price section - pushed to bottom */}
        <div className="mt-auto pt-2">
          <span className="text-sm font-semibold text-gray-900 tabular-nums">
            {formatCurrency(price)}
          </span>
        </div>
      </div>
    </div>
  );
};

export default MenuCard;
