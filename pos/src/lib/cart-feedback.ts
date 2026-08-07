import { showToast } from '@ury/ui';
import { t } from '../i18n';
import type { CartMutationResult } from '../store/pos-store';

export function getCartMutationErrorMessage(result: CartMutationResult): string | null {
  if (result.ok) return null;

  const item = result.itemName || result.itemCode || t('stock.selected_items');
  switch (result.code) {
    case 'invalid_quantity':
      return t('errors.invalid_item_quantity', { item });
    case 'quantity_limit':
      return t('errors.item_quantity_limit', { item, limit: 99 });
    case 'insufficient_stock':
      if ((result.availableQuantity ?? 0) <= 0) {
        return t('errors.item_out_of_stock', { item });
      }
      return t('errors.insufficient_item_stock', {
        item,
        available: result.availableQuantity ?? 0,
        requested: result.requestedQuantity ?? 0,
        uom: result.stockUom || '',
      });
    case 'stock_check_failed':
      return t('errors.stock_check_failed', { item });
  }
}

export function showCartMutationError(result: CartMutationResult): boolean {
  const message = getCartMutationErrorMessage(result);
  if (!message) return false;
  showToast.error(message);
  return true;
}
