import { call } from '@ury/core';

export interface StockAvailability {
  item_code: string;
  available_qty: number;
  is_stock_item: boolean;
  stock_uom: string | null;
  negative_stock_allowed: boolean;
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
): Promise<Record<string, StockAvailability>> {
  const uniqueItemCodes = [...new Set(itemCodes.filter(Boolean))];
  if (uniqueItemCodes.length === 0) return {};

  const response = await call.get<StockAvailabilityResponse>(
    'ury.ury_pos.api.getStockAvailability',
    {
      pos_profile: posProfile,
      item_codes: JSON.stringify(uniqueItemCodes),
      ...(excludeInvoice ? { exclude_invoice: excludeInvoice } : {}),
    },
  );

  return response.message.stocks;
}
