import React, { useState, useEffect, useRef, useCallback, ChangeEvent } from 'react';
import { X, Plus, Minus, Loader2, Percent, Tag } from 'lucide-react';
import { OrderItem, usePOSStore } from '../store/pos-store';
import { cn } from '@ury/ui';
import { formatCurrency } from '@ury/core';
import { Button, Dialog, DialogContent, Input } from '@ury/ui';
import { db } from '@ury/core';
import { t } from '../i18n';
import { showCartMutationError } from '../lib/cart-feedback';
import type { PriceOption } from '../lib/menu-api';
import {
  calculateItemDiscountAmount,
  type ItemDiscountType,
} from '../lib/item-discount';

interface Variant {
  id: string;
  name: string;
  price: number;
}

interface Addon {
  id: string;
  name: string;
  price: number;
  category: 'sides' | 'drinks' | 'desserts';
}

interface ItemReference {
  item: string;
}

interface ItemDocument {
  name: string;
  image?: string | null;
  item?: string;
  custom_pos_add_on_items?: ItemReference[];
  custom_pos_item_variants?: ItemReference[];
}

interface ProductDialogProps {
  onClose: () => void;
  editMode?: boolean;
  initialVariant?: Variant;
  initialAddons?: Array<Omit<Addon, 'category'>>;
  initialQuantity?: number;
  itemToReplace?: OrderItem;
  initialPriceOption?: PriceOption;
}

const getDefaultPriceOption = (
  item?: { price_options?: PriceOption[] } | null,
): PriceOption | undefined => {
  const options = item?.price_options || [];
  return options.find(option => option.is_default && option.available_qty > 0)
    || options.find(option => option.available_qty > 0)
    || options.find(option => option.is_default)
    || options[0];
};

const getAvailablePriceOption = (
  item?: { price_options?: PriceOption[] } | null,
): PriceOption | undefined => {
  const options = item?.price_options || [];
  return options.find(option => option.is_default && option.available_qty > 0)
    || options.find(option => option.available_qty > 0);
};

const ProductDialog: React.FC<ProductDialogProps> = ({
  onClose,
  editMode = false,
  initialVariant,
  initialAddons = [],
  initialQuantity,
  itemToReplace,
  initialPriceOption,
}) => {
  const { 
    selectedItem, 
    applyOrderItems,
    setSelectedItem, 
    getItemQuantityByCode,
    activeOrders,
    menuItems,
    stockByItem,
    posProfile,
  } = usePOSStore();

  const handleClose = useCallback(() => {
    setSelectedItem(null);
    onClose();
  }, [onClose, setSelectedItem]);
  
  // Find existing item in cart
  const existingCartItem = selectedItem ? activeOrders.find(
    order => order.configurationRole !== 'addon' &&
    order.id === selectedItem.id &&
    (
      (selectedItem.price_options?.length ?? 0) <= 1
      || (initialPriceOption && order.selectedPriceOption?.id === initialPriceOption.id)
    ) &&
    (!order.selectedVariant || order.selectedVariant.id === initialVariant?.id) &&
    (!order.selectedAddons || order.selectedAddons.length === initialAddons.length && 
      order.selectedAddons.every(addon => 
        initialAddons.some(initAddon => initAddon.id === addon.id)
      ))
  ) : null;

  // State for the full item doc (used for all dialog content)
  const [itemDoc, setItemDoc] = useState<ItemDocument | null>(null);
  const [isItemLoading, setIsItemLoading] = useState(false);
  const [itemError, setItemError] = useState<string | null>(null);

  // Fetch Item doc when dialog opens or selectedItem changes
  useEffect(() => {
    let cancelled = false;
    if (!selectedItem) {
      setItemDoc(null);
      setItemError(null);
      setIsItemLoading(false);
      return;
    }
    setIsItemLoading(true);
    setItemError(null);
    db.getDoc('Item', selectedItem.item)
      .then((doc) => {
        if (!cancelled) setItemDoc(doc as unknown as ItemDocument);
      })
      .catch(() => {
        if (!cancelled) {
          setItemError('Failed to fetch item details');
          setItemDoc(null);
        }
      })
      .finally(() => {
        if (!cancelled) setIsItemLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedItem]);

  
  const addonDetails = Array.isArray(itemDoc?.custom_pos_add_on_items)
    ? itemDoc.custom_pos_add_on_items
        .map((entry: ItemReference) => {
          const menuAddon = menuItems.find(menuItem => menuItem.item === entry.item);
          return menuAddon
            ? {
                id: menuAddon.item,
                name: menuAddon.item_name,
                price: Number(menuAddon.price)
              }
            : {
                id: entry.item,
                name: entry.item,
                price: 0
              };
        })
        .filter(Boolean)
    : [];

  const variantDetails = Array.isArray(itemDoc?.custom_pos_item_variants)
    ? itemDoc.custom_pos_item_variants
        .map((entry: ItemReference) => {
          const menuVariant = menuItems.find(menuItem => menuItem.item === entry.item);
          return menuVariant
            ? {
                id: menuVariant.item,
                name: menuVariant.item_name,
                price: Number(menuVariant.price)
              }
            : {
                id: entry.item,
                name: entry.item,
                price: 0
              };
        })
        .filter(Boolean)
    : [];

  const initialReplacementItemRef = useRef(editMode ? itemToReplace : existingCartItem);
  const [selectedPriceOptionId, setSelectedPriceOptionId] = useState<string | undefined>(
    initialPriceOption?.id
      || initialReplacementItemRef.current?.selectedPriceOption?.id
      || getAvailablePriceOption(selectedItem)?.id,
  );
  const [selectedAddons, setSelectedAddons] = useState<Array<{ id: string; name: string; price: number }>>(
    (initialAddons.length > 0 ? initialAddons : initialReplacementItemRef.current?.configuredAddons || [])
      .map(addon => ({ ...addon })),
  );
  const [quantity, setQuantity] = useState<string>(editMode ? initialQuantity?.toString() || '0' : '0');
  const [comments, setComments] = useState<string>(itemToReplace?.comment || existingCartItem?.comment || '');
  const initialManualDiscount = initialReplacementItemRef.current?.manualDiscount;
  const [manualDiscountEnabled, setManualDiscountEnabled] = useState(Boolean(initialManualDiscount));
  const [manualDiscountType, setManualDiscountType] = useState<ItemDiscountType>(
    initialManualDiscount?.type || 'Percent',
  );
  const [manualDiscountValue, setManualDiscountValue] = useState(
    initialManualDiscount?.value ? String(initialManualDiscount.value) : '',
  );
  const [manualDiscountReason, setManualDiscountReason] = useState(
    initialManualDiscount?.reason || '',
  );
  const [isApplying, setIsApplying] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const configurationIdRef = useRef(
    initialReplacementItemRef.current?.configurationId
      || globalThis.crypto?.randomUUID?.()
      || `configuration-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );

  // Initialize quantity and comments from cart if not in edit mode
  useEffect(() => {
    if (!editMode && selectedItem) {
      const initialReplacementItem = initialReplacementItemRef.current;
      if (initialReplacementItem) {
        setQuantity(initialReplacementItem.quantity.toString());
        setComments(initialReplacementItem.comment || '');
      } else {
        setQuantity('0');
      }
    }
  }, [selectedItem, editMode]);

  useEffect(() => {
    const options = selectedItem?.price_options || [];
    if (options.length === 0) {
      setSelectedPriceOptionId(undefined);
      return;
    }
    if (!options.some(option => option.id === selectedPriceOptionId)) {
      setSelectedPriceOptionId(getAvailablePriceOption(selectedItem)?.id);
    }
  }, [selectedItem, selectedPriceOptionId]);

  // Handle click outside to close dialog
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(event.target as Node)) {
        handleClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [handleClose]);

  // Handle escape key to close dialog
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('keydown', handleEscape);
    };
  }, [handleClose]);

  if (!selectedItem) return null;

  // Always get price from menuItems for the main item
  const priceOptions = selectedItem.price_options || [];
  const historicalPriceOption = initialReplacementItemRef.current?.item === selectedItem.item
    ? initialReplacementItemRef.current.selectedPriceOption
    : undefined;
  const selectedPriceOption = priceOptions.find(option => option.id === selectedPriceOptionId)
    || historicalPriceOption;
  const basePrice = selectedPriceOption?.rate ?? Number(selectedItem.price || 0);
  const numericQuantity = quantity === '' ? 0 : parseFloat(quantity);
  const replacementItem = initialReplacementItemRef.current;
  const replacementItems = replacementItem?.configurationId
    ? activeOrders.filter(item => item.configurationId === replacementItem.configurationId)
    : replacementItem
      ? [replacementItem]
      : [];
  const getAddonPriceOption = (addonId: string) => {
    const addonItem = stockByItem[addonId] || menuItems.find(item => item.item === addonId);
    const previousOption = replacementItems.find(
      item => item.configurationRole === 'addon' && item.item === addonId,
    )?.selectedPriceOption;
    if (previousOption) {
      return addonItem?.price_options?.find(option => option.id === previousOption.id)
        || previousOption;
    }
    return getDefaultPriceOption(addonItem);
  };
  const getAddonRate = (addon: { id: string; price: number }) => (
    getAddonPriceOption(addon.id)?.rate ?? addon.price
  );
  const addonsTotal = selectedAddons.reduce((sum, addon) => sum + getAddonRate(addon), 0);
  const parsedManualDiscountValue = Number(manualDiscountValue);
  const manualDiscount = manualDiscountEnabled
    && Number.isFinite(parsedManualDiscountValue)
    && parsedManualDiscountValue > 0
    ? {
        type: manualDiscountType,
        value: parsedManualDiscountValue,
        reason: manualDiscountReason.trim(),
      }
    : undefined;
  const manualDiscountAmount = calculateItemDiscountAmount(
    basePrice,
    numericQuantity,
    manualDiscount,
  );
  const itemTotalBeforeDiscount = basePrice * numericQuantity;
  const total = Math.max(0, itemTotalBeforeDiscount - manualDiscountAmount)
    + addonsTotal * numericQuantity;
  const discountEnabledForProfile = Number(posProfile?.enable_discount ?? 0) === 1;
  const maxDiscountPercentage = Number(posProfile?.custom_ury_max_discount_percentage ?? 100);
  const manualDiscountError = manualDiscountEnabled
    ? !discountEnabledForProfile
      ? t('product_dialog.discount_disabled')
      : !Number.isFinite(parsedManualDiscountValue) || parsedManualDiscountValue <= 0
        ? t('product_dialog.discount_value_required')
        : manualDiscountType === 'Percent' && parsedManualDiscountValue > maxDiscountPercentage
          ? t('product_dialog.discount_exceeds_limit', { limit: String(maxDiscountPercentage) })
          : manualDiscountType === 'Amount' && parsedManualDiscountValue > itemTotalBeforeDiscount
            ? t('product_dialog.discount_exceeds_line')
            : !manualDiscountReason.trim()
              ? t('product_dialog.discount_reason_required')
              : null
    : null;
  const getReplacementQuantity = (itemCode: string) => replacementItems.reduce(
    (sum, item) => sum + (item.item === itemCode ? item.quantity : 0),
    0,
  );
  const selectedStock = stockByItem[selectedItem.item];
  const isStockItem = selectedStock?.is_stock_item ?? selectedItem.is_stock_item;
  const availableQuantity = selectedStock?.total_available_qty
    ?? selectedItem.total_available_qty
    ?? selectedStock?.available_qty
    ?? selectedItem.available_qty;
  const stockUom = selectedStock?.stock_uom ?? selectedItem.stock_uom ?? '';
  const quantityAlreadyInCart = getItemQuantityByCode(selectedItem.item);
  const replacedQuantity = getReplacementQuantity(selectedItem.item);
  const quantityOutsideReplacement = Math.max(0, quantityAlreadyInCart - replacedQuantity);
  const quantityForSelectedOption = activeOrders.reduce(
    (sum, item) => item.item === selectedItem.item
      && item.selectedPriceOption?.id === selectedPriceOption?.id
      ? sum + item.quantity
      : sum,
    0,
  );
  const replacedOptionQuantity = replacementItems.reduce(
    (sum, item) => item.item === selectedItem.item
      && item.selectedPriceOption?.id === selectedPriceOption?.id
      ? sum + item.quantity
      : sum,
    0,
  );
  const quantityOutsideSelectedOption = Math.max(0, quantityForSelectedOption - replacedOptionQuantity);
  const selectedOptionAvailable = selectedPriceOption?.available_qty;
  const remainingAfterSelection = typeof availableQuantity === 'number'
    ? Math.max(0, availableQuantity - quantityOutsideReplacement - (Number.isFinite(numericQuantity) ? numericQuantity : 0))
    : null;
  const mainItemExceedsStock = isStockItem === true
    && typeof availableQuantity === 'number'
    && quantityOutsideReplacement + numericQuantity > availableQuantity + Number.EPSILON;
  const selectedOptionExceedsAvailability = typeof selectedOptionAvailable === 'number'
    && quantityOutsideSelectedOption + numericQuantity > selectedOptionAvailable + Number.EPSILON;
  const selectedAddonsExceedStock = selectedAddons.some((addon) => {
    const stock = stockByItem[addon.id] || menuItems.find(item => item.item === addon.id);
    const physicalAvailable = stock?.total_available_qty ?? stock?.available_qty;
    if (stock?.is_stock_item !== true || typeof physicalAvailable !== 'number') return false;
    const quantityOutsideReplacement = Math.max(
      0,
      getItemQuantityByCode(addon.id) - getReplacementQuantity(addon.id),
    );
    const addonPriceOption = getAddonPriceOption(addon.id);
    const quantityInAddonOption = addonPriceOption
      ? activeOrders.reduce(
          (sum, item) => item.item === addon.id
            && item.selectedPriceOption?.id === addonPriceOption.id
            ? sum + item.quantity
            : sum,
          0,
        ) - replacementItems.reduce(
          (sum, item) => item.item === addon.id
            && item.selectedPriceOption?.id === addonPriceOption.id
            ? sum + item.quantity
            : sum,
          0,
        )
      : 0;
    return quantityOutsideReplacement + numericQuantity > physicalAvailable + Number.EPSILON
      || Boolean(
        addonPriceOption
        && quantityInAddonOption + numericQuantity > addonPriceOption.available_qty + Number.EPSILON,
      );
  });

  const handleQuantityChange = (value: string) => {
    // Only allow valid numbers and one decimal point
    const isValid = /^\d*\.?\d*$/.test(value);
    if (!isValid) return;

    if (value === '') {
      setQuantity('');
      return;
    }

    setQuantity(value);
  };

  const handleIncrement = () => {
    const currentNum = quantity === '' ? 0 : parseFloat(quantity);
    const nextQuantity = Math.round((currentNum + 1) * 1000) / 1000;
    const exceedsStock = isStockItem === true
      && typeof availableQuantity === 'number'
      && quantityOutsideReplacement + nextQuantity > availableQuantity + Number.EPSILON;
    const exceedsPriceOption = typeof selectedOptionAvailable === 'number'
      && quantityOutsideSelectedOption + nextQuantity > selectedOptionAvailable + Number.EPSILON;
    if (currentNum < 99 && !exceedsStock && !exceedsPriceOption) {
      setQuantity(nextQuantity + '');
    }
  };

  const handleDecrement = () => {
    const currentNum = quantity === '' ? 0 : parseFloat(quantity);
    if (currentNum > 0) {
      setQuantity(Math.round((currentNum - 1) * 1000) / 1000 + '');
    }
  };

  const handleAddToOrder = async () => {
    const numericQuantity = typeof quantity === 'string' ? parseFloat(quantity) : quantity;
    if (isNaN(numericQuantity) || numericQuantity <= 0) {
      return; // Don't add to order if quantity is 0 or invalid
    }

    const configurationId = configurationIdRef.current;
    // Add main item as a cart line. Add-ons stay as distinct invoice lines,
    // while configuration metadata lets an edit replace the whole selection.
    const orderItem: OrderItem = {
      ...selectedItem,
      quantity: numericQuantity,
      price: basePrice,
      selectedPriceOption,
      comment: comments || undefined,
      uniqueId: `${configurationId}-main`,
      configurationId,
      configurationRole: 'main',
      configuredAddons: selectedAddons.map(addon => ({
        ...addon,
        price: getAddonRate(addon),
      })),
      manualDiscount,
    };
    // Add each selected add-on as a separate cart line. The store validates
    // every item in one batch and commits the complete snapshot only on success.
    const addonOrderItems = selectedAddons.map(addon => {
      // Find the full menu item details for the add-on
      const menuAddon = menuItems.find(item => item.item === addon.id);
      const addonPriceOption = getAddonPriceOption(addon.id);
      const addonRate = addonPriceOption?.rate ?? addon.price;
      const addonOrderItem: OrderItem = menuAddon
        ? {
            ...menuAddon,
            quantity: numericQuantity,
            price: addonRate,
            selectedPriceOption: addonPriceOption,
            uniqueId: `${configurationId}-addon-${addon.id}`,
            configurationId,
            configurationRole: 'addon',
          }
        : {
            id: addon.id,
            name: addon.name,
            price: addon.price,
            quantity: numericQuantity,
            image: null,
            item: addon.id,
            item_name: addon.name,
            course: '',
            description: '',
            special_dish: 0 as 0 | 1,
            tax_rate: 0,
            uniqueId: `${configurationId}-addon-${addon.id}`,
            configurationId,
            configurationRole: 'addon',
          } as OrderItem;
      return addonOrderItem;
    });

    setIsApplying(true);
    try {
      const replaceUniqueIds = replacementItems
        .map(item => item.uniqueId)
        .filter((uniqueId): uniqueId is string => Boolean(uniqueId));
      const result = await applyOrderItems([orderItem, ...addonOrderItems], replaceUniqueIds);
      if (showCartMutationError(result)) return;
      handleClose();
    } finally {
      setIsApplying(false);
    }
  };

  const handleAddonToggle = (addon: Omit<Addon, 'category'>) => {
    setSelectedAddons(current => 
      current.some(item => item.id === addon.id)
        ? current.filter(item => item.id !== addon.id)
        : [...current, addon]
    );
  };

  // Handler to switch to a variant item
  const handleVariantClick = (variantId: string) => {
    const menuVariant = menuItems.find(menuItem => menuItem.item === variantId);
    if (menuVariant) {
      setSelectedItem(menuVariant);
      setSelectedPriceOptionId(getAvailablePriceOption(menuVariant)?.id);
    }
  };

  return (
    <Dialog open={true} onOpenChange={handleClose}>
      <DialogContent 
        ref={dialogRef}
        variant="xlarge"
        className="bg-white w-full max-w-[90rem] max-h-[90vh] overflow-y-auto flex flex-col md:flex-row p-0"
        showCloseButton={false}
      >
        {/* Left Column - Image  */}
        <div className="md:w-1/3 relative">
          {itemDoc?.image ? (
            <img
              src={itemDoc.image}
              alt={itemDoc.name}
              className="w-full min-h-96 h-full object-cover rounded-t-lg md:rounded-l-lg md:rounded-tr-none filter saturate-75 brightness-95"
              style={{ filter: 'saturate(0.7) brightness(0.95)' }}
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.style.display = 'none';
                const parent = target.parentElement;
                if (parent) {
                  const placeholder = document.createElement('div');
                  placeholder.className = 'w-full h-96 bg-gray-200 flex items-center justify-center text-[8rem] text-gray-400 font-medium rounded-t-lg md:rounded-l-lg md:rounded-tr-none';
                  placeholder.textContent = itemDoc.name.slice(0, 2).toUpperCase();
                  parent.insertBefore(placeholder, target);
                }
              }}
            />
          ) : (
            <div className="w-full min-h-96 h-full bg-gray-200 flex items-center justify-center text-[8rem] text-gray-400 font-medium rounded-t-lg md:rounded-l-lg md:rounded-tr-none">
              {itemDoc?.name.slice(0, 2).toUpperCase()}
            </div>
          )}
          <Button
            onClick={handleClose}
            variant="outline"
            size="icon"
            className="absolute top-4 right-4 bg-white shadow-lg"
          >
            <X className="w-5 h-5" />
          </Button>
        </div>

        {/* Middle Column - Variants and Quantity */}
        <div className="md:w-1/3 p-6 overflow-y-auto">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{selectedItem?.item_name}</h2>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-sm text-gray-500">{selectedItem?.item}</span>
              {(selectedItem?.course_label || selectedItem?.course) && (
                <>
                  <span className="text-gray-300">•</span>
                  <span className="text-sm font-medium text-blue-600">{selectedItem?.course_label || selectedItem?.course}</span>
                </>
              )}
            </div>
          </div>

          <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-600">
            {isStockItem === true && typeof availableQuantity === 'number' ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>{t('cart.stock_total', { qty: availableQuantity, uom: stockUom })}</span>
                <span className={cn(mainItemExceedsStock && 'font-semibold text-red-600')}>
                  {t('cart.stock_remaining', { qty: remainingAfterSelection ?? 0, uom: stockUom })}
                </span>
              </div>
            ) : (
              <span>{t('stock.not_tracked')}</span>
            )}
          </div>

          {priceOptions.length > 1 && (
            <div className="mt-6">
              <h3 className="mb-3 text-lg font-semibold">{t('product_dialog.price_option')}</h3>
              <div className="space-y-2">
                {priceOptions.map(option => {
                  const selected = option.id === selectedPriceOption?.id;
                  const quantityReservedElsewhere = activeOrders.reduce(
                    (sum, item) => item.item === selectedItem.item
                      && item.selectedPriceOption?.id === option.id
                      ? sum + item.quantity
                      : sum,
                    0,
                  ) - replacementItems.reduce(
                    (sum, item) => item.item === selectedItem.item
                      && item.selectedPriceOption?.id === option.id
                      ? sum + item.quantity
                      : sum,
                    0,
                  );
                  const remaining = Math.max(0, option.available_qty - quantityReservedElsewhere);
                  const unavailable = remaining <= 0;

                  return (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => setSelectedPriceOptionId(option.id)}
                      disabled={unavailable || isApplying}
                      className={cn(
                        'flex w-full items-center justify-between gap-3 rounded-lg border p-3 text-left transition-colors',
                        selected
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-gray-200 hover:border-blue-200',
                        unavailable && 'cursor-not-allowed opacity-50',
                      )}
                      aria-pressed={selected}
                    >
                      <span>
                        <span className="block font-medium text-gray-900">{option.label}</span>
                        <span className="block text-xs text-gray-500">
                          {t('stock.available', { qty: remaining, uom: stockUom })}
                        </span>
                      </span>
                      <span className="font-semibold tabular-nums text-gray-900">
                        {formatCurrency(option.rate)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div className="mt-6">
            <h3 className="text-lg font-semibold mb-3">{t('product_dialog.special_instructions')}</h3>
            <Input
              placeholder={t('product_dialog.special_instructions_placeholder')}
              value={comments}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setComments(e.target.value)}
              className="resize-none"
            />
          </div>

          <div className="mt-6">
            <h3 className="text-lg font-semibold mb-3">{t('product_dialog.quantity')}</h3>
            <div className="flex items-center space-x-2">
              <Button
                onClick={handleDecrement}
                variant="outline"
                size="icon"
                className="h-8 w-8 rounded-full"
                disabled={isApplying}
              >
                <Minus className="h-4 w-4" />
              </Button>
              <Input
                type="number"
                min="0"
                max="99"
                value={quantity}
                onChange={(e) => handleQuantityChange(e.target.value)}
                onBlur={() => {
                  // If empty on blur, set to 0
                  if (quantity === '') {
                    setQuantity('0');
                  }
                }}
                className="w-16 text-center [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                disabled={isApplying}
              />
              <Button
                onClick={handleIncrement}
                variant="outline"
                size="icon"
                className="h-8 w-8 rounded-full"
                disabled={isApplying || numericQuantity >= 99 || (
                  isStockItem === true
                  && typeof availableQuantity === 'number'
                  && quantityOutsideReplacement + numericQuantity + 1 > availableQuantity + Number.EPSILON
                ) || (
                  typeof selectedOptionAvailable === 'number'
                  && quantityOutsideSelectedOption + numericQuantity + 1 > selectedOptionAvailable + Number.EPSILON
                )}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
          {/* Variants Section  */}
          {variantDetails.length > 0 && (
            <div className="mt-6">
              <h3 className="text-lg font-semibold mb-3">{t('product_dialog.variants')}</h3>
              <div className="flex gap-2 flex-wrap">
                {variantDetails.map(variant => {
                  const menuVariant = menuItems.find(menuItem => menuItem.item === variant.id);
                  const variantStock = stockByItem[variant.id] || menuVariant;
                  const variantPhysicalAvailable = variantStock?.total_available_qty
                    ?? variantStock?.available_qty;
                  const variantHasAvailableOption = !variantStock?.price_options?.length
                    || variantStock.price_options.some(option => option.available_qty > 0);
                  const variantPriceOption = getDefaultPriceOption(variantStock);
                  const variantRate = variantPriceOption?.rate
                    ?? (menuVariant ? Number(menuVariant.price) : 0);
                  const variantUnavailable = variantStock?.is_stock_item === true
                    && ((variantPhysicalAvailable ?? 0) <= 0 || !variantHasAvailableOption);
                  return (
                    <button
                      key={variant.id}
                      onClick={() => handleVariantClick(variant.id)}
                      disabled={variantUnavailable || isApplying}
                      className={cn(
                        'p-2 rounded-lg border text-left w-full flex justify-between items-center',
                        variant.id === itemDoc?.item
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-gray-200 hover:border-blue-200',
                        variantUnavailable && 'cursor-not-allowed opacity-50'
                      )}
                    >
                      <div className="font-medium">{variant.name}</div>
                      <div className="text-end text-sm text-gray-500">
                        <div>{formatCurrency(variantRate)}</div>
                        {variantStock?.is_stock_item === true && (
                          <div>{t('stock.available', {
                            qty: variantPhysicalAvailable ?? 0,
                            uom: variantStock.stock_uom || '',
                          })}</div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>


        {/* Right Column - Add-ons and Order Button */}
        <div className="h-auto md:w-1/3 p-6 border-t md:border-t-0 md:border-l border-gray-200 overflow-y-auto flex flex-col">
          <div className="overflow-y-auto mb-6">
            {isItemLoading ? (
              <div className="mb-6 flex items-center justify-center text-gray-500">{t('product_dialog.loading_addons')}</div>
            ) : itemError ? (
              <div className="flex items-center justify-center text-red-500">{itemError}</div>
            ) : addonDetails.length > 0 ? (
              <div className="mb-6">
                <h3 className="text-lg font-semibold mb-3">{t('product_dialog.addons')}</h3>
                <div className="space-y-2">
                  {addonDetails.map(addon => {
                    const stock = stockByItem[addon.id] || menuItems.find(item => item.item === addon.id);
                    const selected = selectedAddons.some(item => item.id === addon.id);
                    const physicalAvailable = stock?.total_available_qty ?? stock?.available_qty;
                    const addonPriceOption = getAddonPriceOption(addon.id);
                    const addonRate = addonPriceOption?.rate ?? Number(addon.price);
                    const optionAlreadyInCart = addonPriceOption
                      ? activeOrders.reduce(
                          (sum, item) => item.item === addon.id
                            && item.selectedPriceOption?.id === addonPriceOption.id
                            ? sum + item.quantity
                            : sum,
                          0,
                        ) - replacementItems.reduce(
                          (sum, item) => item.item === addon.id
                            && item.selectedPriceOption?.id === addonPriceOption.id
                            ? sum + item.quantity
                            : sum,
                          0,
                        )
                      : 0;
                    const remaining = typeof physicalAvailable === 'number'
                      ? Math.max(
                          0,
                          physicalAvailable
                            - getItemQuantityByCode(addon.id)
                            + getReplacementQuantity(addon.id),
                        )
                      : null;
                    const optionRemaining = addonPriceOption
                      ? Math.max(0, addonPriceOption.available_qty - optionAlreadyInCart)
                      : null;
                    const unavailable = !selected
                      && stock?.is_stock_item === true
                      && (
                        (remaining ?? 0) + Number.EPSILON < Math.max(numericQuantity, 1)
                        || (stock.price_options?.length && !addonPriceOption)
                        || (optionRemaining !== null
                          && optionRemaining + Number.EPSILON < Math.max(numericQuantity, 1))
                      );

                    return (
                      <button
                        key={addon.id}
                        onClick={() => handleAddonToggle({ id: addon.id, name: addon.name, price: addonRate })}
                        disabled={unavailable || isApplying}
                        className={cn(
                          'w-full p-3 rounded-lg border text-left',
                          selected
                            ? 'border-blue-500 bg-blue-50'
                            : 'border-gray-200 hover:border-blue-200',
                          unavailable && 'cursor-not-allowed opacity-50'
                        )}
                      >
                        <div className="flex justify-between gap-3">
                          <span>{addon.name}</span>
                          <span className="text-end text-sm text-gray-500">
                            <span className="block">+{formatCurrency(addonRate)}</span>
                            {addonPriceOption && (
                              <span className="block font-medium text-blue-700">
                                {addonPriceOption.label}
                              </span>
                            )}
                            {stock?.is_stock_item === true && (
                              <span className="block">
                                {t('stock.available', {
                                  qty: physicalAvailable ?? 0,
                                  uom: stock.stock_uom || '',
                                })}
                              </span>
                            )}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center text-gray-400 text-sm">{t('product_dialog.no_addons')}</div>
            )}
          </div>

          {discountEnabledForProfile && (
            <div className="mb-6 rounded-lg border border-gray-200 p-4">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={manualDiscountEnabled}
                  onChange={(event) => setManualDiscountEnabled(event.target.checked)}
                  disabled={isApplying}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                />
                <Tag className="mt-0.5 h-5 w-5 text-green-700" />
                <span>
                  <span className="block font-semibold text-gray-900">
                    {t('product_dialog.item_discount')}
                  </span>
                  <span className="mt-1 block text-xs text-gray-500">
                    {t('product_dialog.item_discount_help')}
                  </span>
                </span>
              </label>

              {manualDiscountEnabled && (
                <div className="mt-4 space-y-3 border-t border-gray-200 pt-4">
                  <div className="flex gap-2">
                    <div className="inline-flex overflow-hidden rounded-md border border-gray-200" role="group">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className={cn(
                          'rounded-none px-3',
                          manualDiscountType === 'Percent' && 'bg-primary-50 text-primary-700',
                        )}
                        onClick={() => {
                          setManualDiscountType('Percent');
                          setManualDiscountValue('');
                        }}
                        disabled={isApplying}
                        aria-pressed={manualDiscountType === 'Percent'}
                      >
                        <Percent className="h-4 w-4" />
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className={cn(
                          'rounded-none border-s px-3',
                          manualDiscountType === 'Amount' && 'bg-primary-50 text-primary-700',
                        )}
                        onClick={() => {
                          setManualDiscountType('Amount');
                          setManualDiscountValue('');
                        }}
                        disabled={isApplying}
                        aria-pressed={manualDiscountType === 'Amount'}
                      >
                        MT
                      </Button>
                    </div>
                    <Input
                      type="number"
                      min="0"
                      max={manualDiscountType === 'Percent'
                        ? maxDiscountPercentage
                        : itemTotalBeforeDiscount}
                      step="0.01"
                      value={manualDiscountValue}
                      onChange={(event) => setManualDiscountValue(event.target.value)}
                      placeholder={manualDiscountType === 'Percent' ? '%' : 'MT'}
                      disabled={isApplying}
                      aria-label={t('product_dialog.discount_value')}
                    />
                  </div>
                  <Input
                    value={manualDiscountReason}
                    onChange={(event) => setManualDiscountReason(event.target.value)}
                    placeholder={t('product_dialog.discount_reason_placeholder')}
                    disabled={isApplying}
                    maxLength={500}
                    aria-label={t('product_dialog.discount_reason')}
                  />
                  {manualDiscountAmount > 0 && !manualDiscountError && (
                    <div className="flex items-center justify-between text-sm text-green-700">
                      <span>{t('product_dialog.discount_applied')}</span>
                      <strong>-{formatCurrency(manualDiscountAmount)}</strong>
                    </div>
                  )}
                  {manualDiscountError && (
                    <p className="text-sm text-red-600">{manualDiscountError}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Always show total section at the end */}
          <div className="mt-auto pt-2 border-t border-gray-200">
            {manualDiscountAmount > 0 && !manualDiscountError && (
              <div className="mb-2 flex justify-between text-sm text-gray-500">
                <span>{t('product_dialog.before_discount')}</span>
                <span className="line-through">{formatCurrency(itemTotalBeforeDiscount + addonsTotal * numericQuantity)}</span>
              </div>
            )}
            <div className="flex justify-between items-center text-lg font-semibold">
              <span>{t('product_dialog.total')}&nbsp;</span>
              <span>{formatCurrency(total)}</span>
            </div>
            <Button
              onClick={handleAddToOrder}
              className="w-full mt-4"
              size="lg"
              disabled={
                isApplying
                || !Number.isFinite(numericQuantity)
                || numericQuantity <= 0
                || mainItemExceedsStock
                || selectedOptionExceedsAvailability
                || (priceOptions.length > 0 && !selectedPriceOption)
                || selectedAddonsExceedStock
                || !!manualDiscountError
              }
            >
              {isApplying ? (
                <span className="flex items-center justify-center">
                  <Loader2 className="me-2 h-4 w-4 animate-spin" />
                  {t('product_dialog.checking_stock')}
                </span>
              ) : editMode || replacementItem ? t('product_dialog.update_order') : t('product_dialog.add_to_order')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ProductDialog;
