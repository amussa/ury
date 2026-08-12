import { call } from '@ury/core';
import type { PriceOption } from './menu-api';

export interface StockAvailability {
  item_code: string;
  available_qty: number;
  is_stock_item: boolean;
  stock_uom: string | null;
  negative_stock_allowed: boolean;
  total_available_qty?: number;
  price_options?: PriceOption[];
}

interface StockAvailabilityResponse {
  message: {
    stocks: Record<string, StockAvailability>;
  };
}

export async function getStockAvailability(
  posProfile: string,
  itemCodes: string[],
  excludeInvoice?: string | null,
  room?: string | null,
  orderType?: string | null,
): Promise<Record<string, StockAvailability>> {
  const uniqueItemCodes = [...new Set(itemCodes.filter(Boolean))];
  if (uniqueItemCodes.length === 0) return {};

  const response = await call.get<StockAvailabilityResponse>(
    'ury.ury_pos.api.getStockAvailability',
    {
      pos_profile: posProfile,
      item_codes: JSON.stringify(uniqueItemCodes),
      ...(excludeInvoice ? { exclude_invoice: excludeInvoice } : {}),
      ...(room ? { room } : {}),
      ...(orderType ? { order_type: orderType } : {}),
    },
  );

  return response.message.stocks;
}
