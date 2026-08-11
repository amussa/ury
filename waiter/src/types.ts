export interface WaiterUser {
  name: string;
  full_name: string;
}

export interface WaiterRoom {
  name: string;
  label: string;
  is_open: boolean;
}

export interface OpeningState {
  is_open: boolean;
  message: string | null;
}

export interface WaiterContext {
  user: WaiterUser;
  branch: string;
  pos_profile: string;
  currency: string;
  currency_symbol: string | null;
  rooms: WaiterRoom[];
  opening: OpeningState;
  can_register: boolean;
}

export type TableStatus = 'free' | 'mine' | 'locked';

export interface WaiterTable {
  name: string;
  room: string;
  status: TableStatus;
  editable: boolean;
  occupied: boolean;
  ownership: string | null;
  reason: string | null;
  waiter: string | null;
  waiter_name: string | null;
  invoice: string | null;
  no_of_pax: number;
  modified: string | null;
}

export interface MenuCategory {
  name: string;
  label: string;
}

export interface WaiterMenuItem {
  item_code: string;
  item_name: string;
  description: string;
  image: string | null;
  rate: number;
  category: string;
  category_label: string;
  available_qty: number | null;
  is_stock_item: boolean;
  negative_stock_allowed: boolean;
  stock_uom: string | null;
}

export interface WaiterMenu {
  items: WaiterMenuItem[];
  categories: MenuCategory[];
}

export interface SentOrderItem {
  name: string;
  item_code: string;
  item_name: string;
  qty: number;
  rate: number;
  amount: number;
  comment: string;
}

export interface TableOrder {
  invoice: string | null;
  modified: string | null;
  waiter: string | null;
  waiter_name: string | null;
  no_of_pax: number;
  comments: string;
  sent_items: SentOrderItem[];
}

export interface DraftOrderItem {
  id: string;
  item_code: string;
  item_name: string;
  qty: number;
  rate: number;
  comment: string;
  available_qty: number | null;
  is_stock_item: boolean;
  negative_stock_allowed: boolean;
  stock_uom: string | null;
}

export interface RegisterOrderItem {
  item_code: string;
  qty: number;
  expected_rate: number;
  comment?: string;
}

export interface RegisterOrderRequest {
  table: string;
  room: string;
  items: RegisterOrderItem[];
  no_of_pax: number;
  comments?: string | null;
  expected_modified?: string | null;
  request_id: string;
}

export interface RegisterOrderResult {
  status: string;
  idempotent: boolean;
  request_id: string;
  invoice: string;
  modified: string | null;
  kot_warning: string | null;
  message: string | null;
  kots: Array<{ name: string; production: string }>;
  order: TableOrder | null;
}
