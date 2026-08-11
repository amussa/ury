import { auth, call } from '@ury/core';
import type {
  MenuCategory,
  RegisterOrderRequest,
  RegisterOrderResult,
  SentOrderItem,
  TableOrder,
  WaiterContext,
  WaiterMenu,
  WaiterMenuItem,
  WaiterRoom,
  WaiterTable,
} from '@/types';

const METHODS = {
  context: 'ury.ury_pos.waiter_api.get_context',
  tables: 'ury.ury_pos.waiter_api.get_tables',
  menu: 'ury.ury_pos.waiter_api.get_menu',
  tableOrder: 'ury.ury_pos.waiter_api.get_table_order',
  registerOrder: 'ury.ury_pos.waiter_api.register_order',
} as const;

type UnknownRecord = Record<string, unknown>;
interface FrappeResponse<T> {
  message: T;
}

const record = (value: unknown): UnknownRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : {};

const stringValue = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback;

const nullableString = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value : null;

const numberValue = (value: unknown, fallback = 0): number => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const booleanValue = (value: unknown): boolean =>
  value === true || value === 1 || value === '1';

const arrayValue = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

function unwrap<T>(response: FrappeResponse<T>): T {
  return response.message;
}

function normalizeRoom(value: unknown): WaiterRoom {
  const row = record(value);
  const name = stringValue(row.name ?? row.room);
  return {
    name,
    label: stringValue(row.label ?? row.room_name, name),
    is_open: row.is_open === undefined ? true : booleanValue(row.is_open),
  };
}

function normalizeContext(value: unknown): WaiterContext {
  const row = record(value);
  const rawUser = record(row.user);
  const userName = stringValue(rawUser.name ?? row.user);
  const rawOpening = record(row.opening);
  const isOpen = booleanValue(rawOpening.is_open ?? row.has_opening ?? row.is_open);

  const currency = stringValue(row.currency, 'MZN');
  return {
    user: {
      name: userName,
      full_name: stringValue(rawUser.full_name ?? row.full_name, userName),
    },
    branch: stringValue(row.branch),
    pos_profile: stringValue(row.pos_profile),
    currency,
    currency_symbol: nullableString(row.currency_symbol) ?? (currency === 'MZN' ? 'MT' : null),
    rooms: arrayValue(row.rooms).map(normalizeRoom).filter((room) => room.name),
    opening: {
      is_open: isOpen,
      message: nullableString(rawOpening.reason ?? rawOpening.message ?? row.opening_message),
    },
    can_register: row.can_register === undefined ? isOpen : booleanValue(row.can_register),
  };
}

function normalizeTable(value: unknown, fallbackRoom = ''): WaiterTable {
  const row = record(value);
  const rawStatus = stringValue(row.state ?? row.status).toLowerCase();
  const editable = booleanValue(row.editable ?? row.can_edit);
  const occupied = booleanValue(row.occupied) || rawStatus === 'mine' || rawStatus === 'locked' || rawStatus === 'occupied';
  const status = rawStatus === 'mine'
    ? 'mine'
    : rawStatus === 'locked' || rawStatus === 'occupied'
      ? 'locked'
      : occupied
        ? editable ? 'mine' : 'locked'
        : 'free';

  return {
    name: stringValue(row.name ?? row.table),
    room: stringValue(row.room ?? row.restaurant_room, fallbackRoom),
    status,
    editable: status === 'free' ? row.editable === undefined ? true : editable : editable,
    occupied,
    ownership: nullableString(row.ownership),
    reason: nullableString(row.reason),
    waiter: nullableString(row.waiter),
    waiter_name: nullableString(row.waiter_name ?? row.waiter_full_name),
    invoice: nullableString(row.invoice ?? row.pos_invoice),
    no_of_pax: Math.max(1, Math.round(numberValue(row.no_of_pax, 1))),
    modified: nullableString(row.modified),
  };
}

function normalizeMenuItem(value: unknown): WaiterMenuItem {
  const row = record(value);
  const itemCode = stringValue(row.item_code ?? row.item);
  const category = stringValue(row.category ?? row.item_group ?? row.course, 'Outros');
  const rawAvailableQty = row.available_qty;

  return {
    item_code: itemCode,
    item_name: stringValue(row.item_name ?? row.name, itemCode),
    description: stringValue(row.description),
    image: nullableString(row.image ?? row.item_image),
    rate: numberValue(row.rate),
    category,
    category_label: stringValue(row.category_label ?? row.course_label, category),
    available_qty: rawAvailableQty === null || rawAvailableQty === undefined
      ? null
      : numberValue(rawAvailableQty),
    is_stock_item: row.is_stock_item === undefined ? true : booleanValue(row.is_stock_item),
    negative_stock_allowed: booleanValue(row.negative_stock_allowed),
    stock_uom: nullableString(row.stock_uom),
  };
}

function normalizeMenu(value: unknown): WaiterMenu {
  const row = record(value);
  const rawItems = Array.isArray(value) ? value : row.items;
  const items = arrayValue(rawItems).map(normalizeMenuItem).filter((item) => item.item_code);
  const suppliedCategories = arrayValue(row.categories).map((entry): MenuCategory => {
    const category = record(entry);
    const name = typeof entry === 'string' ? entry : stringValue(category.name);
    return { name, label: typeof entry === 'string' ? entry : stringValue(category.label, name) };
  }).filter((category) => category.name);

  const categories = suppliedCategories.length > 0
    ? suppliedCategories
    : Array.from(new Map(items.map((item) => [
        item.category,
        { name: item.category, label: item.category_label },
      ])).values());

  return { items, categories };
}

function normalizeSentItem(value: unknown): SentOrderItem {
  const row = record(value);
  const itemCode = stringValue(row.item_code ?? row.item);
  const qty = numberValue(row.qty);
  const rate = numberValue(row.rate);
  return {
    name: stringValue(row.line_id ?? row.name, itemCode),
    item_code: itemCode,
    item_name: stringValue(row.item_name, itemCode),
    qty,
    rate,
    amount: numberValue(row.amount, qty * rate),
    comment: stringValue(row.comment),
  };
}

function normalizeOrder(value: unknown): TableOrder {
  const row = record(value);
  const rawItems = row.sent_items ?? row.items;
  return {
    invoice: nullableString(row.invoice ?? row.name),
    modified: nullableString(row.modified),
    waiter: nullableString(row.waiter),
    waiter_name: nullableString(row.waiter_name ?? row.waiter_full_name),
    no_of_pax: Math.max(1, Math.round(numberValue(row.no_of_pax, 1))),
    comments: stringValue(row.comments ?? row.custom_comments),
    sent_items: arrayValue(rawItems).map(normalizeSentItem).filter((item) => item.item_code),
  };
}

export function getRenderedSessionUser(): string | null {
  const user = document.getElementById('root')?.dataset.sessionUser?.trim();
  return user && user !== 'Guest' ? user : null;
}

export interface WaiterLoginResult {
  requiresVerification: boolean;
}

export async function loginWaiter(username: string, password: string): Promise<WaiterLoginResult> {
  const response = await auth.loginWithUsernamePassword({ username, password });
  return {
    requiresVerification: Boolean(response.tmp_id || response.verification),
  };
}

export async function logoutWaiter(): Promise<void> {
  await auth.logout();
}

export async function getWaiterContext(): Promise<WaiterContext> {
  const response = await call.get<FrappeResponse<unknown>>(METHODS.context);
  return normalizeContext(unwrap(response));
}

export async function getWaiterTables(room?: string): Promise<WaiterTable[]> {
  const response = await call.get<FrappeResponse<unknown>>(METHODS.tables, room ? { room } : undefined);
  const value = unwrap(response);
  const rows = Array.isArray(value) ? value : arrayValue(record(value).tables);
  return rows.map((table) => normalizeTable(table, room)).filter((table) => table.name);
}

export async function getWaiterMenu(room: string): Promise<WaiterMenu> {
  const response = await call.get<FrappeResponse<unknown>>(METHODS.menu, { room });
  return normalizeMenu(unwrap(response));
}

export async function getWaiterTableOrder(table: string, allowReadOnly = false): Promise<TableOrder | null> {
  const response = await call.get<FrappeResponse<unknown>>(METHODS.tableOrder, { table });
  const value = unwrap(response);
  if (!value) return null;
  const row = record(value);
  if (!allowReadOnly && row.editable !== undefined && !booleanValue(row.editable)) {
    throw new Error('Esta mesa já não pode ser alterada no atendimento móvel. Actualize a lista de mesas.');
  }
  if (row.order === null) return null;
  return normalizeOrder(row.order ?? value);
}

export async function registerWaiterOrder(request: RegisterOrderRequest): Promise<RegisterOrderResult> {
  const response = await call.post<FrappeResponse<unknown>>(METHODS.registerOrder, {
    table: request.table,
    room: request.room,
    items: request.items,
    no_of_pax: request.no_of_pax,
    comments: request.comments || undefined,
    expected_modified: request.expected_modified || undefined,
    request_id: request.request_id,
  });
  const row = record(unwrap(response));
  const warnings = arrayValue(row.kot_warnings).filter((warning): warning is string => typeof warning === 'string');
  const kots = arrayValue(row.kots).map((value) => {
    const kot = record(value);
    return {
      name: stringValue(kot.name),
      production: stringValue(kot.production ?? kot.production_unit),
    };
  }).filter((kot) => kot.name && kot.production);
  return {
    status: stringValue(row.status),
    idempotent: booleanValue(row.idempotent ?? row.replayed),
    request_id: stringValue(row.request_id, request.request_id),
    invoice: stringValue(row.invoice ?? row.name),
    modified: nullableString(row.modified),
    kot_warning: nullableString(row.kot_warning) ?? (warnings.length ? warnings.join(' ') : null),
    message: nullableString(row.message),
    kots,
    order: row.order ? normalizeOrder(row.order) : null,
  };
}
