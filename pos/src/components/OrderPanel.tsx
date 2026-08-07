import { useRef, useState } from 'react';
import { Trash2, Edit, FrownIcon, Plus, Loader2, MessageSquare } from 'lucide-react';
import { usePOSStore } from '../store/pos-store';
import { cn } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import { CustomerSelect } from './CustomerSelect';
import ProductDialog from './ProductDialog';
import OrderTypeSelect from './OrderTypeSelect';
import CommentDialog from './CommentDialog';
import { Button } from '@ury/ui';
import { Spinner } from '@ury/ui';
import { syncOrder } from '../lib/order-api';
import { useRootStore } from '../store/root-store';
import type { RootState } from '../store/root-store';
import { showToast } from '@ury/ui';
import { DINE_IN } from '../data/order-types';
import { t } from '../i18n';
import { showCartMutationError } from '../lib/cart-feedback';

const OrderPanel = () => {
  const { 
    activeOrders, 
    addToOrder,
    applyOrderItems,
    removeFromOrder, 
    clearOrder, 
    setSelectedItem,
    orderLoading,
    isOrderInteractionDisabled,
    isUpdatingOrder,
    posProfile,
    selectedOrderType,
    selectedTable,
    selectedRoom,
    selectedCustomer,
    selectedAggregator,
    resetOrderState,
    paymentModes,
    orderId,
    orderComment,
    setOrderComment,
    stockByItem,
    getItemQuantityByCode,
  } = usePOSStore();
  const user = useRootStore((state: RootState) => state.user);
  const [editingItem, setEditingItem] = useState<typeof activeOrders[0] | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showCommentDialog, setShowCommentDialog] = useState(false);
  const quantityMutationKeys = useRef(new Set<string>());
  const [adjustingItemKeys, setAdjustingItemKeys] = useState(new Set<string>());

  const calculateItemTotal = (item: typeof activeOrders[0]) => {
    const basePrice = item.selectedVariant?.price || item.price;
    const addonsTotal = item.selectedAddons?.reduce((sum, addon) => sum + addon.price, 0) || 0;
    return (basePrice + addonsTotal) * item.quantity;
  };

  const total = activeOrders.reduce(
    (sum, item) => sum + calculateItemTotal(item),
    0
  );

  const handleEdit = (item: typeof activeOrders[0]) => {
    const menuItem = {
      ...item,
      variants: item.variants,
      addons: item.addons,
    };
    setSelectedItem(menuItem);
    setEditingItem(item);
  };

  const getConfigurationItems = (item: typeof activeOrders[0]) =>
    item.configurationId && item.configurationRole === 'main'
      ? activeOrders.filter(orderItem => orderItem.configurationId === item.configurationId)
      : [item];

  const runItemMutation = async (
    item: typeof activeOrders[0],
    operation: () => Promise<void>,
  ) => {
    const mutationKey = item.configurationId || item.uniqueId!;
    if (quantityMutationKeys.current.has(mutationKey)) return;

    quantityMutationKeys.current.add(mutationKey);
    setAdjustingItemKeys(new Set(quantityMutationKeys.current));
    try {
      await operation();
    } finally {
      quantityMutationKeys.current.delete(mutationKey);
      setAdjustingItemKeys(new Set(quantityMutationKeys.current));
    }
  };

  const handleDecreaseQuantity = async (item: typeof activeOrders[0]) => {
    await runItemMutation(item, async () => {
      const configurationItems = getConfigurationItems(item);
      const nextQuantity = Math.max(0, Math.round((item.quantity - 1) * 1000) / 1000);
      if (nextQuantity <= 0) {
        await Promise.all(
          configurationItems.map(orderItem => removeFromOrder(orderItem.uniqueId!)),
        );
        return;
      }

      const replaceUniqueIds = configurationItems
        .map(orderItem => orderItem.uniqueId)
        .filter((uniqueId): uniqueId is string => Boolean(uniqueId));
      const result = await applyOrderItems(
        configurationItems.map(orderItem => ({ ...orderItem, quantity: nextQuantity })),
        replaceUniqueIds,
      );
      showCartMutationError(result);
    });
  };

  const handleIncreaseQuantity = async (item: typeof activeOrders[0]) => {
    await runItemMutation(item, async () => {
      const configurationItems = getConfigurationItems(item);
      const result = configurationItems.length > 1
        ? await applyOrderItems(
            configurationItems.map(orderItem => ({ ...orderItem, quantity: orderItem.quantity + 1 })),
            configurationItems
              .map(orderItem => orderItem.uniqueId)
              .filter((uniqueId): uniqueId is string => Boolean(uniqueId)),
          )
        : await addToOrder({ ...item, quantity: 1 });
      showCartMutationError(result);
    });
  };

  const handleRemoveItem = async (item: typeof activeOrders[0]) => {
    await runItemMutation(item, async () => {
      await Promise.all(
        getConfigurationItems(item).map(orderItem => removeFromOrder(orderItem.uniqueId!)),
      );
    });
  };

  const handleCommentSave = (comment: string) => {
    setOrderComment(comment);
  };

  const handleSubmit = async () => {
    try {
      if (!posProfile) {
        throw new Error(t('errors.pos_profile_not_found'));
      }

      if (!user?.name) {
        throw new Error(t('errors.user_not_logged_in'));
      }

      // Validate customer/aggregator details
      if (selectedOrderType === 'Aggregators') {
        if (!selectedAggregator?.customer) {
          showToast.error(t('errors.select_aggregator'));
          return;
        }
      } else if (!selectedCustomer?.name) {
        showToast.error(t('errors.select_customer'));
        return;
      }

      // Validate table selection for dine-in orders
      if (selectedOrderType === DINE_IN && !selectedTable) {
        showToast.error(t('errors.select_table', { order_type: DINE_IN }));
        return;
      }

      setIsSubmitting(true);
      
      const orderData = {
        items: activeOrders.map(item => ({
          item: item.id,
          item_name: item.name,
          rate: item.selectedVariant?.price || item.price,
          qty: item.quantity,
          comment: item.comment || undefined
        })),
        no_of_pax: 1,
        pos_profile: posProfile.name,
        order_type: selectedOrderType,
        table: selectedTable || undefined,
        room: selectedRoom || undefined,
        customer: selectedOrderType === 'Aggregators' ? selectedAggregator?.customer : selectedCustomer?.name,
        aggregator_id: selectedOrderType === 'Aggregators' ? selectedAggregator?.customer : undefined,
        cashier: posProfile.cashier,
        owner: posProfile.owner,
        mode_of_payment: paymentModes[0],
        last_invoice: isUpdatingOrder ? orderId : null,
        invoice: isUpdatingOrder ? orderId : null,
        waiter: user.name,
        comments: orderComment || undefined
      };

      await syncOrder(orderData);
      
      // Reset all states after successful order submission
      resetOrderState();
      showToast.success(isUpdatingOrder ? t('success.order_updated') : t('success.order_created'));
    } catch (error) {
      console.error('Failed to sync order:', error);
      // Frappe API error handling
      if (error && typeof error === 'object' && '_server_messages' in error && typeof (error as any)._server_messages === 'string') {
        try {
          const messages = JSON.parse((error as any)._server_messages);
          const messageObj = JSON.parse(messages[0]);
          showToast.error(messageObj.message || 'API error');
        } catch {
          showToast.error('API error');
        }
      } else if (error instanceof Error) {
        showToast.error(error.message);
      } else {
        showToast.error(t('errors.failed_process_order'));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const EmptyCartUI = () => (
    <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
      <div className="w-24 h-24 bg-gray-100 rounded-full flex items-center justify-center mb-6">
        <FrownIcon className="w-12 h-12 text-gray-400" />
      </div>
      
      <h3 className="text-lg font-semibold text-gray-900 mb-2">
        {t('cart.empty_title')}
      </h3>

      <p className="text-gray-500 text-sm mb-6 max-w-xs leading-relaxed">
        {t('cart.empty_subtitle')}
      </p>

      <div className="flex items-center gap-2 text-blue-600 bg-blue-50 px-4 py-2 rounded-lg">
        <Plus className="w-4 h-4" />
        <span className="text-sm font-medium">{t('cart.click_to_add')}</span>
      </div>

      <div className="mt-4 text-xs text-gray-400">
        {t('cart.double_click_hint')}
      </div>
    </div>
  );

  const LoadingOrderUI = () => (
    <div className="h-96">
      <Spinner message={t('cart.loading_order')} />
    </div>
  );

  const isInteractionDisabled = isOrderInteractionDisabled() || isSubmitting;

  return (
    <div className="w-96 bg-white border-s border-gray-200 flex flex-col h-[calc(100vh-4rem)] fixed end-0 z-10">
      <div className="p-4 border-b border-gray-200 flex-shrink-0">
        <OrderTypeSelect disabled={isInteractionDisabled} />
        <div className="mt-3"><CustomerSelect disabled={isInteractionDisabled} /></div>
      </div>
      
      {orderLoading ? (
        <LoadingOrderUI />
      ) : activeOrders.length === 0 ? (
        <EmptyCartUI />
      ) : (
        <>
          <div className="flex-1 overflow-y-auto px-6">
            {activeOrders.map((item) => {
              const stock = stockByItem[item.item];
              const isStockItem = stock?.is_stock_item ?? item.is_stock_item;
              const availableQuantity = stock?.available_qty ?? item.available_qty;
              const stockUom = stock?.stock_uom ?? item.stock_uom ?? '';
              const quantityInCart = getItemQuantityByCode(item.item);
              const remainingQuantity = typeof availableQuantity === 'number'
                ? Math.max(0, availableQuantity - quantityInCart)
                : null;
              const configurationItems = getConfigurationItems(item);
              const incrementCounts = configurationItems.reduce<Record<string, number>>((counts, orderItem) => {
                counts[orderItem.item] = (counts[orderItem.item] || 0) + 1;
                return counts;
              }, {});
              const incrementExceedsStock = Object.entries(incrementCounts).some(([itemCode, increment]) => {
                const itemStock = stockByItem[itemCode];
                return itemStock?.is_stock_item === true
                  && getItemQuantityByCode(itemCode) + increment > itemStock.available_qty + Number.EPSILON;
              });
              const isConfigurationAddon = item.configurationRole === 'addon';
              const isAdjusting = adjustingItemKeys.has(item.configurationId || item.uniqueId!);
              const displayedAddons = item.configuredAddons || item.selectedAddons || [];

              return (
              <div
                key={item.uniqueId}
                className={cn(
                  "flex flex-col py-4 border-b border-gray-100",
                  isInteractionDisabled && "opacity-50"
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <h3 className="font-medium text-gray-900 text-sm">{item.name}</h3>
                    </div>
                    {item.selectedVariant && (
                      <p className="text-sm text-gray-600">{item.selectedVariant.name}</p>
                    )}
                    {item.configurationRole === 'addon' && (
                      <p className="text-xs font-medium text-blue-600">
                        {t('cart.addon')} · × {item.quantity}
                      </p>
                    )}
                    {displayedAddons.length > 0 && (
                      <p className="text-sm text-gray-500">
                        {displayedAddons.map(addon => addon.name).join(', ')}
                      </p>
                    )}
                    <p className="text-gray-600 text-sm">{formatCurrency(calculateItemTotal(item))}</p>
                    {isStockItem === true && typeof availableQuantity === 'number' ? (
                      <p className="mt-1 text-xs text-gray-500">
                        {t('cart.stock_total', { qty: availableQuantity, uom: stockUom })}
                        {' · '}
                        {t('cart.stock_remaining', { qty: remainingQuantity ?? 0, uom: stockUom })}
                      </p>
                    ) : isStockItem === false ? (
                      <p className="mt-1 text-xs text-gray-500">{t('stock.not_tracked')}</p>
                    ) : null}
                  </div>
                  
                  <div className="flex items-center gap-2">
                    {!isConfigurationAddon && (
                      <Button
                        onClick={() => handleEdit(item)}
                        variant="ghost"
                        size="icon"
                        className="text-blue-600 hover:text-blue-700"
                        title={t('cart.edit_item')}
                        disabled={isInteractionDisabled || isAdjusting}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                    )}
                    {!isConfigurationAddon && (
                      <div className="flex items-center gap-2">
                      <Button
                        onClick={() => handleDecreaseQuantity(item)}
                        variant="outline"
                        size="icon"
                        className="w-8 h-8 rounded-full"
                        disabled={isInteractionDisabled || isAdjusting}
                      >
                        -
                      </Button>
                      <span className="w-6 text-center">{item.quantity}</span>
                      <Button
                        onClick={() => handleIncreaseQuantity(item)}
                        variant="outline"
                        size="icon"
                        className="w-8 h-8 rounded-full"
                        disabled={isInteractionDisabled || isAdjusting || item.quantity >= 99 || incrementExceedsStock}
                      >
                        +
                      </Button>
                      </div>
                    )}
                    
                    {!isConfigurationAddon && (
                      <Button
                        onClick={() => handleRemoveItem(item)}
                        variant="ghost"
                        size="icon"
                        className="text-red-500 hover:text-red-600"
                        disabled={isInteractionDisabled || isAdjusting}
                      >
                        <Trash2 className="w-5 h-5" />
                      </Button>
                    )}
                  </div>
                </div>
              </div>
              );
            })}
            {activeOrders.length > 0 && (
              <Button
                onClick={clearOrder}
                variant="ghost"
                size="sm"
                className="w-full text-gray-600 hover:text-gray-800 mt-4"
                disabled={isInteractionDisabled}
              >
                {t('cart.clear_cart')}
              </Button>
            )}
          </div>
          
          <div className="p-4 border-t border-gray-200 flex-shrink-0 bg-white">
            <div className="flex justify-between items-center mb-4">
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => setShowCommentDialog(true)}
                  variant="ghost"
                  size="sm"
                  className={cn(
                    "h-8 w-8 p-0",
                    orderComment ? "text-blue-600" : "text-gray-500 hover:text-gray-700"
                  )}
                  disabled={isInteractionDisabled}
                  title={orderComment ? t('cart.edit_comment') : t('cart.add_comment')}
                >
                  <MessageSquare className="w-4 h-4" />
                </Button>
                <span className="text-lg font-semibold">{t('cart.total')}</span>
              </div>
              <span className="text-lg font-semibold">{formatCurrency(total)}</span>
            </div>
            <Button
              onClick={handleSubmit}
              variant="default"
              size="default"
              className="w-full"
              disabled={isInteractionDisabled}
            >
              {isSubmitting ? (
                <div className="flex items-center">
                  <Loader2 className="w-4 h-4 me-2 animate-spin" />

                  {isUpdatingOrder ? t('cart.updating_order') : t('cart.processing_order')}
                </div>
              ) : isUpdatingOrder ? (
                t('cart.update_order')
              ) : (
                t('cart.add_new_order')
              )}
            </Button>
          </div>
        </>
      )}

      {editingItem && (
        <ProductDialog
          onClose={() => {
            setEditingItem(null);
            setSelectedItem(null);
          }}
          editMode
          initialVariant={editingItem.selectedVariant}
          initialAddons={editingItem.configuredAddons || editingItem.selectedAddons}
          initialQuantity={editingItem.quantity}
          itemToReplace={editingItem}
        />
      )}

      <CommentDialog
        isOpen={showCommentDialog}
        onClose={() => setShowCommentDialog(false)}
        onSave={handleCommentSave}
        initialComment={orderComment}
      />
    </div>
  );
};

export default OrderPanel;
