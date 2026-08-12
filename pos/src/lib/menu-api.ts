import { call } from '@ury/core';

const getServerMessage = (error: unknown): string | null => {
  if (!error || typeof error !== 'object' || !('_server_messages' in error)) return null;
  const serverMessages = (error as { _server_messages?: unknown })._server_messages;
  if (typeof serverMessages !== 'string') return null;

  try {
    const messages = JSON.parse(serverMessages) as string[];
    const firstMessage = messages[0] ? JSON.parse(messages[0]) as { message?: string } : null;
    return firstMessage?.message || null;
  } catch {
    return null;
  }
};

export interface PriceOption {
  id: string;
  label: string;
  rate: number;
  available_qty: number;
  is_default: boolean;
}

export interface MenuItem {
  item: string;
  item_name: string;
  item_image: string | null;
  rate: number | string;
  course: string;
  course_label?: string;
  trending?: boolean;
  popular?: boolean;
  recommended?: boolean;
  description?: string;
  special_dish?: 1 | 0;
  available_qty?: number;
  is_stock_item?: boolean;
  stock_uom?: string | null;
  negative_stock_allowed?: boolean;
  total_available_qty?: number;
  price_options?: PriceOption[];
}

export interface GetMenuResponse {
  message: {
    items: MenuItem[];
  };
}

export interface GetAggregatorMenuResponse {
  message: MenuItem[];
}

export const getRestaurantMenu = async (posProfile: string, room: string | null, order_type: string | null) => {
  try {
    const response = await call.get<GetMenuResponse>(
      'ury.ury_pos.api.getRestaurantMenu',
      {
        pos_profile: posProfile,
        room: room,
        order_type: order_type
      }
    );
    return response.message.items;
  } catch (error) {
    const message = getServerMessage(error);
    if (message) throw new Error(message);
    throw error;
  }
};

export const getAggregatorMenu = async (aggregator: string, posProfile?: string) => {
  try {
    const response = await call.get<GetAggregatorMenuResponse>(
      'ury.ury_pos.api.getAggregatorItem',
      {
        aggregator,
        ...(posProfile ? { pos_profile: posProfile } : {}),
      }
    );
    return response.message;
  } catch (error) {
    const message = getServerMessage(error);
    if (message) throw new Error(message);
    throw error;
  }
};
