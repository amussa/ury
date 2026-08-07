import { create } from 'zustand';
import { v4 as uuidv4 } from 'uuid';
import { storage } from '@ury/core';
import { getRestaurantMenu, getAggregatorMenu, MenuItem as APIMenuItem } from '../lib/menu-api';
import { getCurrencyInfo, PosProfileCombined, getCombinedPosProfile } from '../lib/pos-profile-api';
import { getMenuCourses } from '../lib/menu-course-api';
import { getCustomerGroups, getCustomerTerritories } from '../lib/customer-api';
import { DEFAULT_ORDER_TYPE, OrderType } from '../data/order-types';
import { getTableOrder, TableOrder } from '../lib/order-api';
import { getPaymentModes } from '../lib/payment-api';
import { getStockAvailability, StockAvailability } from '../lib/stock-api';

// Constants
const MAX_QUANTITY = 99;
const MIN_QUANTITY = 0;

// Extend the API MenuItem to include UI-specific properties
export interface MenuItem extends Omit<APIMenuItem, 'rate' | 'item_image'> {
  id: string;
  name: string;
  image: string | null;
  price: number;
  quantity?: number;
  description?: string;
  special_dish?: 1 | 0;
  category?: string;
  variants?: Array<{ id: string; name: string; price: number }>;
  addons?: Array<{ id: string; name: string; price: number; category: 'sides' | 'drinks' | 'desserts' }>;
  selectedVariant?: { id: string; name: string; price: number };
  selectedAddons?: Array<{ id: string; name: string; price: number }>;
  uniqueId?: string;
  tax_rate?: number;
}

export interface Customer {
  id: string;
  name: string;
  phone: string;
}

const getDefaultCustomer = (profile: PosProfileCombined | null): Customer | null =>
  profile?.customer
    ? { id: profile.customer, name: profile.customer, phone: '' }
    : null;

export interface OrderItem extends MenuItem {
  quantity: number;
  selectedVariant?: { id: string; name: string; price: number };
  selectedAddons?: { id: string; name: string; price: number }[];
  uniqueId?: string;
  comment?: string;
  configurationId?: string;
  configurationRole?: 'main' | 'addon';
  configuredAddons?: Array<{ id: string; name: string; price: number }>;
}

export type CartMutationFailureCode =
  | 'invalid_quantity'
  | 'quantity_limit'
  | 'stock_check_failed'
  | 'insufficient_stock';

export type CartMutationResult =
  | { ok: true }
  | {
      ok: false;
      code: CartMutationFailureCode;
      itemCode?: string;
      itemName?: string;
      requestedQuantity?: number;
      availableQuantity?: number;
      stockUom?: string | null;
    };

export interface PaymentMode {
  id: string;
  name: string;
  enabled: boolean;
}

export interface Category {
  name: string;
  label: string;
}

export interface Order {
  id: string;
  cartId: string;
  customerId?: string;
  paymentModeId: string;
  paymentMode: string;
  orderType: OrderType;
  status: 'pending' | 'paid' | 'preparing' | 'ready' | 'completed' | 'cancelled';
  totalAmount: number;
  paidAmount: number;
  createdAt: string;
  updatedAt: string;
}

interface CartTotals {
  subtotal: number;
  tax: number;
  total: number;
  itemCount: number;
}

interface Aggregator {
  customer: string;
}

interface POSState {
  menuItems: MenuItem[];
  categories: Category[];
  activeOrders: OrderItem[];
  selectedCategory: string;
  selectedTable: string | null;
  selectedRoom: string | null;
  searchQuery: string;
  selectedCustomer: Customer | null;
  selectedOrderType: OrderType;
  quickFilter: 'all' | 'special';
  selectedItem: MenuItem | null;
  cartId: string | null;
  loading: boolean;
  menuLoading: boolean;
  orderLoading: boolean;
  profileLoading: boolean;
  error: string | null;
  paymentModes: string[];
  orders: Order[];
  selectedAggregator: Aggregator | null;
  currency: string;
  currencySymbol: string | null;
  isUpdatingOrder: boolean;
  orderId: string | null;
  stockExcludeInvoice: string | null;
  cartRevision: number;
  posProfile: PosProfileCombined | null;
  customerGroups: string[];
  territories: string[];
  tableOrder: TableOrder | null;
  isInitializing: boolean;
  orderComment: string;
  stockByItem: Record<string, StockAvailability>;
}

interface POSStore extends POSState {
  fetchMenuItems: () => Promise<void>;
  fetchAggregatorMenu: (aggregator: string) => Promise<void>;
  fetchCategories: () => Promise<void>;
  fetchPaymentModes: () => Promise<void>;
  addToOrder: (item: OrderItem) => Promise<CartMutationResult>;
  applyOrderItems: (items: OrderItem[], replaceUniqueIds?: string[]) => Promise<CartMutationResult>;
  hydrateOrderItems: (items: OrderItem[], excludeInvoice?: string | null) => Promise<CartMutationResult>;
  refreshStockForItems: (itemCodes: string[], excludeInvoice?: string | null, expectedRevision?: number) => Promise<CartMutationResult>;
  removeFromOrder: (uniqueId: string) => Promise<void>;
  updateQuantity: (uniqueId: string, quantity: number) => Promise<CartMutationResult>;
  clearOrder: () => Promise<void>;
  setSelectedCategory: (category: string) => void;
  setSearchQuery: (query: string) => void;
  setSelectedCustomer: (customer: Customer | null) => void;
  setSelectedTable: (table: string | null, room: string | null, doNotLoadOrder?: boolean) => void;
  setSelectedOrderType: (type: OrderType) => void;
  setQuickFilter: (filter: 'all' | 'special') => void;
  setSelectedItem: (item: MenuItem | null) => void;
  initializeCart: () => Promise<void>;
  processPayment: (paymentMode: string, amount: number) => Promise<void>;
  updateOrderStatus: (orderId: string, status: Order['status']) => Promise<void>;
  fetchPosProfile: () => Promise<void>;
  fetchCustomerGroups: () => Promise<void>;
  fetchTerritories: () => Promise<void>;
  fetchCurrencySymbol: () => Promise<void>;
  getCartTotals: () => CartTotals;
  itemExistsInCart: (uniqueId: string) => boolean;
  validateQuantity: (quantity: number) => boolean;
  getItemPrice: (item: OrderItem) => number;
  getItemQuantityFromCart: (item: MenuItem) => number;
  getItemQuantityByCode: (itemCode: string) => number;
  loadTableOrder: (table: string) => Promise<void>;
  clearTableOrder: () => void;
  isMenuInteractionDisabled: () => boolean;
  isOrderInteractionDisabled: () => boolean;
  initializeApp: () => Promise<void>;
  setOrderForUpdate: (orderId: string | null, stockExcludeInvoice?: string | null) => void;
  resetOrderState: () => void;
  setSelectedAggregator: (aggregator: Aggregator | null) => void;
  setOrderComment: (comment: string) => void;
}

const generateUniqueId = (item: OrderItem): string => {
  const variantId = item.selectedVariant?.id || 'default';
  const addonIds = item.selectedAddons?.map(addon => addon.id).sort().join('-') || 'no-addons';
  return `${item.id}-${variantId}-${addonIds}`;
};

const calculateItemPrice = (item: OrderItem): number => {
  const basePrice = item.selectedVariant?.price || item.price;
  const addonsTotal = item.selectedAddons?.reduce((sum, addon) => sum + addon.price, 0) || 0;
  return basePrice + addonsTotal;
};

const SUCCESS_RESULT: CartMutationResult = { ok: true };

const getItemCode = (item: Pick<OrderItem, 'item' | 'id'>): string => item.item || item.id;

const getItemName = (item: Pick<OrderItem, 'item_name' | 'name'>): string =>
  item.item_name || item.name;

const normalizeStock = (stock: StockAvailability): StockAvailability => ({
  ...stock,
  available_qty: Number(stock.available_qty) || 0,
  is_stock_item: Boolean(stock.is_stock_item),
  negative_stock_allowed: Boolean(stock.negative_stock_allowed),
  stock_uom: stock.stock_uom || null,
});

const stockFromMenuItem = (item: MenuItem): StockAvailability | null => {
  if (typeof item.is_stock_item !== 'boolean' || typeof item.available_qty !== 'number') {
    return null;
  }

  return normalizeStock({
    item_code: item.item,
    available_qty: item.available_qty,
    is_stock_item: item.is_stock_item,
    stock_uom: item.stock_uom || null,
    negative_stock_allowed: Boolean(item.negative_stock_allowed),
  });
};

const withStock = <T extends MenuItem>(item: T, stock?: StockAvailability): T =>
  stock
    ? {
        ...item,
        available_qty: stock.available_qty,
        is_stock_item: stock.is_stock_item,
        stock_uom: stock.stock_uom,
        negative_stock_allowed: stock.negative_stock_allowed,
      }
    : item;

const getQuantitiesByItemCode = (items: OrderItem[]): Record<string, number> =>
  items.reduce<Record<string, number>>((totals, item) => {
    const itemCode = getItemCode(item);
    totals[itemCode] = (totals[itemCode] || 0) + item.quantity;
    return totals;
  }, {});

const addItemsToSnapshot = (orders: OrderItem[], items: OrderItem[]): OrderItem[] => {
  const nextOrders = [...orders];

  items.forEach((item) => {
    const uniqueId = item.uniqueId || generateUniqueId(item);
    const existingItemIndex = nextOrders.findIndex(orderItem => orderItem.uniqueId === uniqueId);

    if (existingItemIndex === -1) {
      nextOrders.push({ ...item, uniqueId });
      return;
    }

    const existingItem = nextOrders[existingItemIndex];
    nextOrders[existingItemIndex] = {
      ...existingItem,
      ...item,
      uniqueId,
      quantity: existingItem.quantity + item.quantity,
      comment: item.comment !== undefined ? item.comment : existingItem.comment,
    };
  });

  return nextOrders;
};

let cartMutationQueue: Promise<void> = Promise.resolve();

export const usePOSStore = create<POSStore>((set, get) => ({
  menuItems: [],
  categories: [],
  activeOrders: [],
  selectedCategory: '',
  selectedTable: null,
  selectedRoom: null,
  searchQuery: '',
  selectedCustomer: null,
  selectedOrderType: DEFAULT_ORDER_TYPE as OrderType,
  quickFilter: "all",
  selectedItem: null,
  cartId: null,
  loading: false,
  menuLoading: false,
  orderLoading: false,
  profileLoading: false,
  error: null,
  paymentModes: ['Cash'],
  orders: [],
  posProfile: null,
  customerGroups: [],
  territories: [],
  selectedAggregator: null,
  currency: storage.getItem('currency') || 'INR',
  currencySymbol: storage.getItem('currencySymbol') || null,
  tableOrder: null,
  isInitializing: true,
  isUpdatingOrder: false,
  orderId: null,
  stockExcludeInvoice: null,
  cartRevision: 0,
  orderComment: '',
  stockByItem: {},

  initializeApp: async () => {
    try {
      set({ isInitializing: true, error: null });
      
      const [profileResult, menuResult, categoriesResult, paymentModesResult] = await Promise.allSettled([
        get().fetchPosProfile(),
        get().fetchMenuItems(),
        get().fetchCategories(),
        get().fetchPaymentModes()
      ]);

      if (profileResult.status === 'rejected' || 
          menuResult.status === 'rejected' || 
          categoriesResult.status === 'rejected' ||
          paymentModesResult.status === 'rejected') {
        set({ 
          error: 'Failed to initialize app. Please refresh the page.',
          isInitializing: false 
        });
        return;
      }

      set({ isInitializing: false });
    } catch (error) {
      set({ 
        error: 'Failed to initialize app. Please refresh the page.',
        isInitializing: false 
      });
    }
  },

  fetchPosProfile: async () => {
    try {
      const cached = sessionStorage.getItem('posProfile');
      if (cached) {
        const profile = JSON.parse(cached);
        // Profiles cached before a default customer was configured must be
        // refreshed, otherwise the POS keeps asking for a customer forever.
        if (profile.customer) {
          set({
            posProfile: profile,
            selectedCustomer: get().selectedCustomer ?? getDefaultCustomer(profile),
            profileLoading: false,
            currency: profile.currency || 'INR'
          });
          if (!storage.getItem('currencySymbol')) {
            await get().fetchCurrencySymbol();
          }
          return;
        }
        sessionStorage.removeItem('posProfile');
      }

      set({ profileLoading: true, error: null });
      const combinedProfile = await getCombinedPosProfile();
      
      sessionStorage.setItem('posProfile', JSON.stringify(combinedProfile));
      set({ 
        posProfile: combinedProfile, 
        selectedCustomer: get().selectedCustomer ?? getDefaultCustomer(combinedProfile),
        profileLoading: false,
        currency: combinedProfile.currency || 'INR'
      });
      
      if (!storage.getItem('currencySymbol')) {
        await get().fetchCurrencySymbol();
      }
    } catch (error) {
      console.error('Error fetching POS profile:', error);
      set({ 
        error: 'Failed to fetch POS profile',
        profileLoading: false 
      });
    }
  },

  fetchCurrencySymbol: async () => {
    try {
      const currency = get().currency;
      const response = await getCurrencyInfo(currency);
      const { symbol } = response;
      
      set({ currencySymbol: symbol });
      storage.setItem('currencySymbol', symbol);
    } catch (error) {
      console.error('Error fetching currency symbol:', error);
      set({ currencySymbol: get().currency });
      storage.setItem('currencySymbol', get().currency);
    }
  },

  fetchMenuItems: async () => {
    const { posProfile, selectedRoom, selectedOrderType } = get();
    if (!posProfile?.restaurant) return;

    try {
      set({ menuLoading: true, error: null });
      const items = await getRestaurantMenu(posProfile.name, selectedRoom, selectedOrderType);
      
      const menuItems: MenuItem[] = items.map((item: any) => ({
        id: item.item,
        name: item.item_name,
        image: item.item_image || null,
        price: typeof item.rate === 'string' ? parseFloat(item.rate) : item.rate || 0,
        item: item.item,
        item_name: item.item_name,
        item_image: item.item_image,
        course: item.course,
        course_label: item.course_label || item.course,
        description: item.description || '',
        special_dish: item.special_dish || 0,
        tax_rate: 0,
        available_qty: Number(item.available_qty) || 0,
        is_stock_item: Boolean(item.is_stock_item),
        stock_uom: item.stock_uom || null,
        negative_stock_allowed: Boolean(item.negative_stock_allowed),
      }));

      const stockByItem = menuItems.reduce<Record<string, StockAvailability>>((stocks, item) => {
        const stock = stockFromMenuItem(item);
        if (stock) stocks[item.item] = stock;
        return stocks;
      }, {});

      set((state) => {
        const effectiveStockByItem = { ...stockByItem };
        if (state.isUpdatingOrder && state.orderId) {
          state.activeOrders.forEach((item) => {
            const itemCode = getItemCode(item);
            if (state.stockByItem[itemCode]) {
              effectiveStockByItem[itemCode] = state.stockByItem[itemCode];
            }
          });
        }
        return {
          menuItems: menuItems.map(item => withStock(item, effectiveStockByItem[item.item])),
          stockByItem: effectiveStockByItem,
          activeOrders: state.activeOrders.map(item => withStock(item, effectiveStockByItem[getItemCode(item)])),
        };
      });

      const currentState = get();
      if (currentState.isUpdatingOrder && currentState.activeOrders.length > 0) {
        await currentState.refreshStockForItems(
          currentState.activeOrders.map(getItemCode),
          currentState.stockExcludeInvoice,
          currentState.cartRevision,
        );
      }
    } catch (error) {
      set({ error: 'Failed to load menu items' });
      console.error('Error loading menu items:', error);
    } finally {
      set({ menuLoading: false });
    }
  },

  fetchAggregatorMenu: async (aggregator: string) => {
    try {
      set({ menuLoading: true, error: null });
      const items = await getAggregatorMenu(aggregator, get().posProfile?.name);
      
      const menuItems: MenuItem[] = items.map((item: any) => ({
        ...item,
        id: item.item,
        name: item.item_name,
        image: item.item_image || null,
        price: typeof item.rate === 'string' ? parseFloat(item.rate) : item.rate || 0,
        category: item.course,
        available_qty: Number(item.available_qty) || 0,
        is_stock_item: Boolean(item.is_stock_item),
        stock_uom: item.stock_uom || null,
        negative_stock_allowed: Boolean(item.negative_stock_allowed),
      }));

      const stockByItem = menuItems.reduce<Record<string, StockAvailability>>((stocks, item) => {
        const stock = stockFromMenuItem(item);
        if (stock) stocks[item.item] = stock;
        return stocks;
      }, {});

      set((state) => {
        const effectiveStockByItem = { ...stockByItem };
        if (state.isUpdatingOrder && state.orderId) {
          state.activeOrders.forEach((item) => {
            const itemCode = getItemCode(item);
            if (state.stockByItem[itemCode]) {
              effectiveStockByItem[itemCode] = state.stockByItem[itemCode];
            }
          });
        }
        return {
          menuItems: menuItems.map(item => withStock(item, effectiveStockByItem[item.item])),
          stockByItem: effectiveStockByItem,
          activeOrders: state.activeOrders.map(item => withStock(item, effectiveStockByItem[getItemCode(item)])),
          menuLoading: false,
        };
      });

      const currentState = get();
      if (currentState.isUpdatingOrder && currentState.activeOrders.length > 0) {
        await currentState.refreshStockForItems(
          currentState.activeOrders.map(getItemCode),
          currentState.stockExcludeInvoice,
          currentState.cartRevision,
        );
      }
    } catch (error) {
      set({ error: 'Failed to load aggregator menu', menuLoading: false });
      console.error('Error loading aggregator menu:', error);
    }
  },

  fetchCategories: async () => {
    try {
      const cached = sessionStorage.getItem('menuCategories');
      if (cached) {
        const categories = JSON.parse(cached);
        set({ categories });
        return;
      }

      const courses = await getMenuCourses();
      sessionStorage.setItem('menuCategories', JSON.stringify(courses));
      set({ categories: courses });
    } catch (error) {
      set({ error: 'Failed to load menu categories' });
      throw error;
    }
  },

  fetchPaymentModes: async () => {
    try {
      const modes = await getPaymentModes();
      set({ paymentModes: modes });
    } catch (error) {
      console.error('Failed to fetch payment modes:', error);
    }
  },

  initializeCart: async () => {
    set({ cartId: uuidv4() });
  },

  addToOrder: async (item: OrderItem) => {
    return get().applyOrderItems([item]);
  },

  refreshStockForItems: async (itemCodes, excludeInvoice, expectedRevision) => {
    const uniqueItemCodes = [...new Set(itemCodes.filter(Boolean))];
    if (uniqueItemCodes.length === 0) return SUCCESS_RESULT;

    const posProfile = get().posProfile;
    if (!posProfile?.name) {
      return { ok: false, code: 'stock_check_failed' };
    }

    try {
      const responseStocks = await getStockAvailability(
        posProfile.name,
        uniqueItemCodes,
        excludeInvoice,
      );
      const stocks = Object.fromEntries(
        Object.entries(responseStocks).map(([itemCode, stock]) => [itemCode, normalizeStock(stock)]),
      );

      if (expectedRevision !== undefined && get().cartRevision !== expectedRevision) {
        return { ok: false, code: 'stock_check_failed' };
      }

      const missingItemCode = uniqueItemCodes.find(itemCode => !stocks[itemCode]);
      if (missingItemCode) {
        return { ok: false, code: 'stock_check_failed', itemCode: missingItemCode };
      }

      set((state) => ({
        stockByItem: { ...state.stockByItem, ...stocks },
        menuItems: state.menuItems.map(item => withStock(item, stocks[item.item])),
        activeOrders: state.activeOrders.map(item => withStock(item, stocks[getItemCode(item)])),
        selectedItem: state.selectedItem
          ? withStock(state.selectedItem, stocks[state.selectedItem.item])
          : null,
      }));

      return SUCCESS_RESULT;
    } catch (error) {
      console.error('Failed to refresh stock availability:', error);
      return { ok: false, code: 'stock_check_failed' };
    }
  },

  applyOrderItems: (items, replaceUniqueIds = []) => {
    const requestedRevision = get().cartRevision;
    const operation = cartMutationQueue.then(async (): Promise<CartMutationResult> => {
      if (get().cartRevision !== requestedRevision) {
        return { ok: false, code: 'stock_check_failed' };
      }
      const invalidItem = items.find(item => !get().validateQuantity(item.quantity) || item.quantity <= 0);
      if (invalidItem) {
        return {
          ok: false,
          code: 'invalid_quantity',
          itemCode: getItemCode(invalidItem),
          itemName: getItemName(invalidItem),
          requestedQuantity: invalidItem.quantity,
        };
      }

      const replacedIds = new Set(replaceUniqueIds.filter(Boolean));
      const buildProposal = () => {
        const currentOrders = get().activeOrders;
        const baseOrders = currentOrders.filter(item => !item.uniqueId || !replacedIds.has(item.uniqueId));
        const proposedOrders = addItemsToSnapshot(baseOrders, items);
        const currentQuantities = getQuantitiesByItemCode(currentOrders);
        const proposedQuantities = getQuantitiesByItemCode(proposedOrders);
        const increasedItemCodes = Object.keys(proposedQuantities).filter(
          itemCode => proposedQuantities[itemCode] > (currentQuantities[itemCode] || 0) + Number.EPSILON,
        );
        return { proposedOrders, proposedQuantities, increasedItemCodes };
      };

      let proposal = buildProposal();
      let overLimitItem = proposal.proposedOrders.find(item => !get().validateQuantity(item.quantity));
      if (overLimitItem) {
        return {
          ok: false,
          code: 'quantity_limit',
          itemCode: getItemCode(overLimitItem),
          itemName: getItemName(overLimitItem),
          requestedQuantity: overLimitItem.quantity,
        };
      }

      if (proposal.increasedItemCodes.length > 0) {
        const { stockExcludeInvoice } = get();
        const initiallyRequestedCodes = [...proposal.increasedItemCodes];
        const refreshResult = await get().refreshStockForItems(
          initiallyRequestedCodes,
          stockExcludeInvoice,
          requestedRevision,
        );
        if (!refreshResult.ok) {
          const failedCode = refreshResult.itemCode || proposal.increasedItemCodes[0];
          const failedItem = items.find(item => getItemCode(item) === failedCode);
          return {
            ...refreshResult,
            itemCode: failedCode,
            itemName: failedItem ? getItemName(failedItem) : failedCode,
          };
        }

        // Stock requests are asynchronous. Rebuild from the latest cart so a
        // removal/clear performed while the request was in flight is preserved.
        proposal = buildProposal();
        overLimitItem = proposal.proposedOrders.find(item => !get().validateQuantity(item.quantity));
        if (overLimitItem) {
          return {
            ok: false,
            code: 'quantity_limit',
            itemCode: getItemCode(overLimitItem),
            itemName: getItemName(overLimitItem),
            requestedQuantity: overLimitItem.quantity,
          };
        }

        const refreshedCodes = new Set(initiallyRequestedCodes);
        const missingCodes = proposal.increasedItemCodes.filter(itemCode => !refreshedCodes.has(itemCode));
        if (missingCodes.length > 0) {
          const secondRefresh = await get().refreshStockForItems(
            missingCodes,
            stockExcludeInvoice,
            requestedRevision,
          );
          if (!secondRefresh.ok) return secondRefresh;
          proposal = buildProposal();
        }

        const stockByItem = get().stockByItem;
        for (const itemCode of proposal.increasedItemCodes) {
          const stock = stockByItem[itemCode];
          const requestedQuantity = proposal.proposedQuantities[itemCode];
          if (!stock) {
            return { ok: false, code: 'stock_check_failed', itemCode, itemName: itemCode };
          }
          if (stock.is_stock_item && requestedQuantity > stock.available_qty + Number.EPSILON) {
            const failedItem = items.find(item => getItemCode(item) === itemCode);
            return {
              ok: false,
              code: 'insufficient_stock',
              itemCode,
              itemName: failedItem ? getItemName(failedItem) : itemCode,
              requestedQuantity,
              availableQuantity: stock.available_qty,
              stockUom: stock.stock_uom,
            };
          }
        }
      }

      const stockByItem = get().stockByItem;
      if (get().cartRevision !== requestedRevision) {
        return { ok: false, code: 'stock_check_failed' };
      }
      set({
        activeOrders: proposal.proposedOrders.map(item => withStock(item, stockByItem[getItemCode(item)])),
      });
      return SUCCESS_RESULT;
    });

    cartMutationQueue = operation.then(() => undefined, () => undefined);
    return operation;
  },

  hydrateOrderItems: async (items, excludeInvoice) => {
    const usedUniqueIds = new Set<string>();
    const hydratedItems = items.map((item) => {
      const baseUniqueId = item.uniqueId || generateUniqueId(item);
      const uniqueId = usedUniqueIds.has(baseUniqueId) ? `${baseUniqueId}-${uuidv4()}` : baseUniqueId;
      usedUniqueIds.add(uniqueId);
      return { ...item, uniqueId };
    });

    set((state) => ({
      activeOrders: hydratedItems,
      cartRevision: state.cartRevision + 1,
    }));
    const revision = get().cartRevision;
    return get().refreshStockForItems(
      hydratedItems.map(getItemCode),
      excludeInvoice,
      revision,
    );
  },

  removeFromOrder: async (uniqueId: string) => {
    try {
      const newOrders = get().activeOrders.filter(item => item.uniqueId !== uniqueId);
      set({ activeOrders: newOrders });
    } catch (error) {
      set({ error: 'Failed to remove item from cart' });
    }
  },

  updateQuantity: async (uniqueId: string, quantity: number) => {
    const item = get().activeOrders.find(orderItem => orderItem.uniqueId === uniqueId);
    if (!item || !get().validateQuantity(quantity)) {
      return {
        ok: false,
        code: 'invalid_quantity',
        itemCode: item ? getItemCode(item) : undefined,
        itemName: item ? getItemName(item) : undefined,
        requestedQuantity: quantity,
      };
    }

    if (quantity === 0) {
      set({ activeOrders: get().activeOrders.filter(orderItem => orderItem.uniqueId !== uniqueId) });
      return SUCCESS_RESULT;
    }

    return get().applyOrderItems([{ ...item, quantity }], [uniqueId]);
  },

  clearOrder: async () => {
    try {
      set((state) => ({ activeOrders: [], cartRevision: state.cartRevision + 1 }));
    } catch (error) {
      set({ error: 'Failed to clear cart' });
    }
  },

  setSelectedCategory: (category) => set({ selectedCategory: category }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setSelectedCustomer: (customer) => set({ selectedCustomer: customer }),
  setSelectedTable: (table: string | null, room: string | null, doNotLoadOrder: boolean = false) => {
    set((state) => ({
      selectedTable: table,
      selectedRoom: room,
      cartRevision: state.selectedTable === table ? state.cartRevision : state.cartRevision + 1,
    }));
    if (table ) {
      if (!doNotLoadOrder) 
        get().loadTableOrder(table);
    } else {
      get().clearTableOrder();
    }
    if (room) {
      get().fetchMenuItems();
    }
  },
  setSelectedOrderType: (type) => {
    const { fetchMenuItems } = get();
    
    set((state) => ({
      activeOrders: [],
      selectedOrderType: type,
      isUpdatingOrder: false,
      orderId: null,
      stockExcludeInvoice: null,
      cartRevision: state.cartRevision + 1,
    }));
    
    if (type !== 'Aggregators') {
      fetchMenuItems();
    }
  },
  setQuickFilter: (filter) => set({ quickFilter: filter }),
  setSelectedItem: (item) => set({ selectedItem: item }),
  setSelectedAggregator: (aggregator) => set({ selectedAggregator: aggregator }),
  setOrderComment: (comment: string) => set({ orderComment: comment }),

  processPayment: async (paymentMode: string, amount: number) => {
    try {
      const { cartId, selectedCustomer, selectedOrderType } = get();
      
      const order: Order = {
        id: uuidv4(),
        cartId: cartId!,
        customerId: selectedCustomer?.id,
        paymentModeId: paymentMode,
        paymentMode,
        orderType: selectedOrderType,
        status: 'paid',
        totalAmount: amount,
        paidAmount: amount,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };

      const newOrders = [...get().orders, order];
      set({ orders: newOrders });
      
      await get().clearOrder();
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  updateOrderStatus: async (orderId: string, status: Order['status']) => {
    try {
      const newOrders = get().orders.map(order => 
        order.id === orderId 
          ? { ...order, status, updatedAt: new Date().toISOString() }
          : order
      );
      set({ orders: newOrders });
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  fetchCustomerGroups: async () => {
    const cached = sessionStorage.getItem('customerGroups');
    if (cached) {
      set({ customerGroups: JSON.parse(cached) });
      return;
    }
    const groups = await getCustomerGroups();
    const names = groups.map((g: any) => g.name);
    set({ customerGroups: names });
    sessionStorage.setItem('customerGroups', JSON.stringify(names));
  },

  fetchTerritories: async () => {
    const cached = sessionStorage.getItem('territories');
    if (cached) {
      set({ territories: JSON.parse(cached) });
      return;
    }
    const terrs = await getCustomerTerritories();
    const names = terrs.map((t: any) => t.name);
    set({ territories: names });
    sessionStorage.setItem('territories', JSON.stringify(names));
  },

  getCartTotals: (): CartTotals => {
    const items = get().activeOrders;
    const itemCount = items.reduce((sum, item) => sum + item.quantity, 0);
    
    const subtotal = items.reduce((sum, item) => {
      const itemPrice = calculateItemPrice(item);
      return sum + (itemPrice * item.quantity);
    }, 0);

    const tax = items.reduce((sum, item) => {
      const itemPrice = calculateItemPrice(item);
      const taxRate = item.tax_rate || 0;
      return sum + (itemPrice * item.quantity * (taxRate / 100));
    }, 0);

    return {
      subtotal,
      tax,
      total: subtotal + tax,
      itemCount
    };
  },

  itemExistsInCart: (uniqueId: string): boolean => {
    return get().activeOrders.some(item => item.uniqueId === uniqueId);
  },

  validateQuantity: (quantity: number): boolean => {
    return !isNaN(quantity) && quantity >= MIN_QUANTITY && quantity <= MAX_QUANTITY;
  },

  getItemPrice: (item: OrderItem): number => {
    return calculateItemPrice(item);
  },

  getItemQuantityFromCart: (item: MenuItem): number => {
    return get().getItemQuantityByCode(item.item);
  },

  getItemQuantityByCode: (itemCode: string): number => {
    return get().activeOrders.reduce(
      (quantity, item) => quantity + (getItemCode(item) === itemCode ? item.quantity : 0),
      0,
    );
  },

  loadTableOrder: async (table: string) => {
    try {
      set({ orderLoading: true, error: null });
      const response = await getTableOrder(table);
      const order = response.message;
      if (order && order.name && order.items && order.items.length > 0) {
        const orderItems: OrderItem[] = order.items.map(item => {
          const orderItem = {
            id: item.item_code,
            name: item.item_name,
            price: item.rate,
            quantity: item.qty,
            amount: item.amount,
            image: item.image || null,
            item: item.item_code,
            item_name: item.item_name,
            item_image: null,
            course: '',
            description: item.description || '',
            special_dish: 0 as 0 | 1,
            tax_rate: 0,
            comment: item.comment || '',
          };
          return {
            ...orderItem,
            uniqueId: item.name || generateUniqueId(orderItem as OrderItem)
          } as OrderItem;
        });

        set({ 
          tableOrder: response,
          selectedCustomer: order.customer ? {
            id: order.customer,
            name: order.customer_name,
            phone: order.mobile_number,
          } : null,
          isUpdatingOrder: true,
          orderId: order.name,
          stockExcludeInvoice: order.docstatus === 0 ? order.name : null,
        });
        await get().hydrateOrderItems(
          orderItems,
          order.docstatus === 0 ? order.name : null,
        );
      } else {
        set((state) => ({
          tableOrder: null,
          activeOrders: [],
          selectedCustomer: getDefaultCustomer(get().posProfile),
          isUpdatingOrder: false,
          orderId: null,
          stockExcludeInvoice: null,
          cartRevision: state.cartRevision + 1,
        }));
      }
    } catch (error) {
      set((state) => ({
        error: 'Failed to load table order',
        tableOrder: null,
        activeOrders: [],
        selectedCustomer: getDefaultCustomer(get().posProfile),
        isUpdatingOrder: false,
        orderId: null,
        stockExcludeInvoice: null,
        cartRevision: state.cartRevision + 1,
      }));
    } finally {
      set({ orderLoading: false });
    }
  },

  clearTableOrder: () => {
    set((state) => ({
      tableOrder: null,
      activeOrders: [],
      selectedCustomer: getDefaultCustomer(get().posProfile),
      isUpdatingOrder: false,
      orderId: null,
      stockExcludeInvoice: null,
      cartRevision: state.cartRevision + 1,
    }));
  },

  setOrderForUpdate: (orderId: string | null, stockExcludeInvoice: string | null = null) => {
    set((state) => ({
      isUpdatingOrder: orderId !== null,
      orderId,
      stockExcludeInvoice,
      cartRevision: state.cartRevision + 1,
    }));
  },

  resetOrderState: () => {
    const { fetchMenuItems } = get();
    
    set((state) => ({
      selectedCustomer: getDefaultCustomer(get().posProfile),
      selectedTable: null,
      selectedRoom: null,
      selectedAggregator: null,
      isUpdatingOrder: false,
      orderId: null,
      stockExcludeInvoice: null,
      activeOrders: [],
      selectedItem: null,
      orderLoading: false,
      menuItems: [],
      stockByItem: {},
      error: null,
      selectedOrderType: DEFAULT_ORDER_TYPE,
      orderComment: '',
      cartRevision: state.cartRevision + 1,
    }));

    fetchMenuItems();
  },

  isMenuInteractionDisabled: () => {
    const state = get();
    return state.menuLoading || state.profileLoading;
  },

  isOrderInteractionDisabled: () => {
    const state = get();
    return state.orderLoading;
  }
}));
