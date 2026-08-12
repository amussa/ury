import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, LoaderCircle, LockKeyhole, LogOut, X } from 'lucide-react';
import { cn } from '@ury/ui';
import { AppHeader } from '@/components/AppHeader';
import { ItemEditorSheet, type ItemEditorValue } from '@/components/ItemEditorSheet';
import { LoginScreen } from '@/components/LoginScreen';
import { MenuView } from '@/components/MenuView';
import { OrderDock } from '@/components/OrderDock';
import { OrderSummarySheet } from '@/components/OrderSummarySheet';
import { FullPageLoading, OpeningBlocked, StatePanel } from '@/components/ScreenState';
import { TableSelection } from '@/components/TableSelection';
import { getErrorMessage, isAuthenticationError, isAuthorizationError, isUnknownSubmissionOutcome } from '@/lib/errors';
import { createRequestId } from '@/lib/format';
import {
  clearPendingSubmission,
  loadPendingSubmission,
  persistPendingSubmission,
  type PendingSubmissionRecord,
} from '@/lib/pending-submission';
import {
  getRenderedSessionUser,
  getWaiterContext,
  getWaiterMenu,
  getWaiterTableOrder,
  getWaiterTables,
  logoutWaiter,
  registerWaiterOrder,
} from '@/lib/waiter-api';
import type {
  DraftOrderItem,
  RegisterOrderRequest,
  TableOrder,
  WaiterContext,
  WaiterMenu,
  WaiterMenuItem,
  WaiterPriceOption,
  WaiterTable,
} from '@/types';

interface ItemEditorState {
  item: WaiterMenuItem;
  line: DraftOrderItem | null;
}

interface Notice {
  kind: 'success' | 'warning' | 'error';
  text: string;
}

function App() {
  const [context, setContext] = useState<WaiterContext | null>(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [accountNotAuthorized, setAccountNotAuthorized] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  const [selectedRoom, setSelectedRoom] = useState('');
  const [tables, setTables] = useState<WaiterTable[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesError, setTablesError] = useState<string | null>(null);

  const [selectedTable, setSelectedTable] = useState<WaiterTable | null>(null);
  const [menu, setMenu] = useState<WaiterMenu>({ items: [], categories: [] });
  const [menuLoading, setMenuLoading] = useState(false);
  const [menuError, setMenuError] = useState<string | null>(null);
  const [existingOrder, setExistingOrder] = useState<TableOrder | null>(null);
  const [draftItems, setDraftItems] = useState<DraftOrderItem[]>([]);
  const [noOfPax, setNoOfPax] = useState(1);
  const [comments, setComments] = useState('');
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('');

  const [summaryOpen, setSummaryOpen] = useState(false);
  const [itemEditor, setItemEditor] = useState<ItemEditorState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pendingSubmission, setPendingSubmission] = useState<PendingSubmissionRecord | null>(null);

  const tablesRequestRef = useRef(0);
  const selectionRequestRef = useRef(0);

  const requireLogin = useCallback(() => {
    tablesRequestRef.current += 1;
    selectionRequestRef.current += 1;
    setContext(null);
    setBootError(null);
    setBooting(false);
    setRefreshing(false);
    setAccountNotAuthorized(false);
    setLogoutError(null);
  }, []);

  const restorePendingUi = useCallback(async (
    restored: PendingSubmissionRecord,
    activeContext: WaiterContext,
  ) => {
    const restoredTable: WaiterTable = {
      name: restored.request.table,
      room: restored.request.room,
      status: restored.request.expected_modified ? 'mine' : 'free',
      editable: true,
      occupied: Boolean(restored.request.expected_modified),
      ownership: 'mine',
      reason: 'pending_submission_recovery',
      waiter: activeContext.user.name,
      waiter_name: activeContext.user.full_name,
      invoice: null,
      no_of_pax: restored.request.no_of_pax,
      modified: restored.request.expected_modified ?? null,
    };
    setPendingSubmission(restored);
    setSelectedRoom(restored.request.room);
    setSelectedTable(restoredTable);
    setDraftItems(restored.draft_items);
    setNoOfPax(restored.request.no_of_pax);
    setComments(restored.request.comments ?? '');
    setSearch('');
    setSelectedCategory('');
    setSummaryOpen(true);
    setItemEditor(null);
    setSubmitError('Foi recuperada uma tentativa cujo resultado não foi confirmado. Repita exactamente este envio para confirmar o pedido sem duplicar produtos.');
    setMenuLoading(true);
    setMenuError(null);
    const [restoredMenu, restoredOrder] = await Promise.allSettled([
      getWaiterMenu(restored.request.room),
      getWaiterTableOrder(restored.request.table, true),
    ]);
    const authFailure = [restoredMenu, restoredOrder].find(
      (result) => result.status === 'rejected' && isAuthenticationError(result.reason),
    );
    if (authFailure) {
      requireLogin();
      return;
    }
    if (restoredMenu.status === 'fulfilled') {
      setMenu(restoredMenu.value);
    } else {
      setMenu({ items: [], categories: [] });
      setMenuError(getErrorMessage(restoredMenu.reason, 'Não foi possível actualizar o menu. Pode repetir o pedido pendente com segurança.'));
    }
    setExistingOrder(restoredOrder.status === 'fulfilled' ? restoredOrder.value : null);
    setMenuLoading(false);
  }, [requireLogin]);

  const loadContext = useCallback(async () => {
    const loggedUser = getRenderedSessionUser();
    if (!loggedUser) return null;
    return getWaiterContext();
  }, []);

  const bootstrap = useCallback(async () => {
    setBooting(true);
    setBootError(null);
    setAccountNotAuthorized(false);
    setLogoutError(null);
    try {
      const nextContext = await loadContext();
      if (!nextContext) {
        setContext(null);
        return;
      }
      setContext(nextContext);
      const restored = loadPendingSubmission(nextContext.user.name);
      if (restored) {
        await restorePendingUi(restored, nextContext);
        return;
      }
      setSelectedRoom((current) => {
        const stillAvailable = nextContext.rooms.some((room) => room.name === current && room.is_open);
        return stillAvailable ? current : nextContext.rooms.find((room) => room.is_open)?.name ?? nextContext.rooms[0]?.name ?? '';
      });
    } catch (error) {
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      if (isAuthorizationError(error)) {
        setContext(null);
        setAccountNotAuthorized(true);
        setBootError('Esta conta não está autorizada a usar o atendimento móvel. Termine a sessão e entre com uma conta de atendente.');
        return;
      }
      setBootError(getErrorMessage(error, 'Não foi possível iniciar a página de atendimento.'));
    } finally {
      setBooting(false);
    }
  }, [loadContext, requireLogin, restorePendingUi]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), notice.kind === 'success' ? 4500 : 8000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const loadTables = useCallback(async (room: string) => {
    if (!room) return;
    const requestNumber = ++tablesRequestRef.current;
    setTablesLoading(true);
    setTablesError(null);
    try {
      const nextTables = await getWaiterTables(room);
      if (tablesRequestRef.current !== requestNumber) return;
      setTables(nextTables.filter((table) => table.room === room || !table.room));
    } catch (error) {
      if (tablesRequestRef.current !== requestNumber) return;
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      setTables([]);
      setTablesError(getErrorMessage(error, 'Não foi possível obter as mesas.'));
    } finally {
      if (tablesRequestRef.current === requestNumber) setTablesLoading(false);
    }
  }, [requireLogin]);

  useEffect(() => {
    if (!context?.opening.is_open || !context.can_register || !selectedRoom || selectedTable) return;
    void loadTables(selectedRoom);
  }, [context?.can_register, context?.opening.is_open, loadTables, selectedRoom, selectedTable]);

  const loadSelectedTable = useCallback(async (table: WaiterTable, room: string, preservePendingDetails = false) => {
    const requestNumber = ++selectionRequestRef.current;
    setMenuLoading(true);
    setMenuError(null);
    try {
      const [nextMenu, nextOrder] = await Promise.all([
        getWaiterMenu(room),
        getWaiterTableOrder(table.name, preservePendingDetails),
      ]);
      if (selectionRequestRef.current !== requestNumber) return;
      setMenu(nextMenu);
      setExistingOrder(nextOrder);
      if (!preservePendingDetails) {
        setNoOfPax(nextOrder?.no_of_pax ?? table.no_of_pax ?? 1);
        setComments(nextOrder?.comments ?? '');
      }
    } catch (error) {
      if (selectionRequestRef.current !== requestNumber) return;
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      setMenu({ items: [], categories: [] });
      setExistingOrder(null);
      setMenuError(getErrorMessage(error, 'Não foi possível carregar o menu e o pedido desta mesa.'));
    } finally {
      if (selectionRequestRef.current === requestNumber) setMenuLoading(false);
    }
  }, [requireLogin]);

  const selectTable = useCallback((table: WaiterTable) => {
    if (!table.editable || table.status === 'locked') return;
    setSelectedTable(table);
    setDraftItems([]);
    setSearch('');
    setSelectedCategory('');
    setSubmitError(null);
    setSummaryOpen(false);
    void loadSelectedTable(table, table.room || selectedRoom);
  }, [loadSelectedTable, selectedRoom]);

  const refreshContextOnly = useCallback(async () => {
    setRefreshing(true);
    setBootError(null);
    try {
      const nextContext = await loadContext();
      if (!nextContext) {
        requireLogin();
        return;
      }
      setContext(nextContext);
      setSelectedRoom((current) => {
        const stillAvailable = nextContext.rooms.some((room) => room.name === current && room.is_open);
        return stillAvailable ? current : nextContext.rooms.find((room) => room.is_open)?.name ?? nextContext.rooms[0]?.name ?? '';
      });
    } catch (error) {
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      setNotice({ kind: 'error', text: getErrorMessage(error, 'Não foi possível actualizar o estado do atendimento.') });
    } finally {
      setRefreshing(false);
    }
  }, [loadContext, requireLogin]);

  const refreshCurrent = useCallback(async () => {
    setRefreshing(true);
    try {
      const nextContext = await loadContext();
      if (!nextContext) {
        requireLogin();
        return;
      }
      setContext(nextContext);
      if (!nextContext.opening.is_open || !nextContext.can_register) return;

      if (selectedTable) {
        await loadSelectedTable(selectedTable, selectedTable.room || selectedRoom, Boolean(pendingSubmission));
      } else {
        await loadTables(selectedRoom);
      }
    } catch (error) {
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      setNotice({ kind: 'error', text: getErrorMessage(error, 'Não foi possível actualizar os dados.') });
    } finally {
      setRefreshing(false);
    }
  }, [loadContext, loadSelectedTable, loadTables, pendingSubmission, requireLogin, selectedRoom, selectedTable]);

  const draftQuantityByCode = useMemo(() => draftItems.reduce<Record<string, number>>((totals, line) => {
    totals[line.item_code] = (totals[line.item_code] ?? 0) + line.qty;
    return totals;
  }, {}), [draftItems]);

  const draftCount = useMemo(() => draftItems.reduce((sum, line) => sum + line.qty, 0), [draftItems]);
  const draftTotal = useMemo(() => draftItems.reduce((sum, line) => sum + line.qty * line.rate, 0), [draftItems]);
  const draftLocked = Boolean(pendingSubmission);

  const menuItemForLine = useCallback((line: DraftOrderItem): WaiterMenuItem => {
    return menu.items.find((item) => item.item_code === line.item_code) ?? {
      item_code: line.item_code,
      item_name: line.item_name,
      description: '',
      image: null,
      rate: line.rate,
      price_options: line.price_option ? [{
        id: line.price_option,
        label: line.price_option_label || 'Preço anterior',
        rate: line.rate,
        available_qty: line.available_qty ?? line.qty,
        is_default: false,
      }] : [],
      category: '',
      category_label: '',
      available_qty: line.available_qty,
      is_stock_item: line.is_stock_item,
      negative_stock_allowed: line.negative_stock_allowed,
      stock_uom: line.stock_uom,
    };
  }, [menu.items]);

  const maximumQuantity = useCallback((
    item: WaiterMenuItem,
    priceOption: string | null = null,
    excludeLineId?: string,
  ): number => {
    const otherPhysicalQuantity = draftItems.reduce(
      (sum, line) => line.item_code === item.item_code && line.id !== excludeLineId ? sum + line.qty : sum,
      0,
    );
    const physicalMaximum = !item.is_stock_item || item.negative_stock_allowed || item.available_qty === null
      ? 99
      : Math.max(0, Math.floor(item.available_qty - otherPhysicalQuantity));
    if (item.price_options.length === 0) {
      return priceOption && priceOption !== 'standard' ? 0 : Math.min(99, physicalMaximum);
    }

    const defaultOption = item.price_options.find((option) => option.is_default) ?? null;
    const effectiveOptionId = priceOption ?? defaultOption?.id ?? null;
    const option = item.price_options.find((entry) => entry.id === effectiveOptionId);
    if (!option) return 0;
    const otherOptionQuantity = draftItems.reduce((sum, line) => {
      if (line.item_code !== item.item_code || line.id === excludeLineId) return sum;
      const lineOptionId = line.price_option ?? defaultOption?.id ?? null;
      return lineOptionId === option.id ? sum + line.qty : sum;
    }, 0);
    const optionMaximum = Math.max(0, Math.floor(option.available_qty - otherOptionQuantity));
    return Math.max(0, Math.min(99, physicalMaximum, optionMaximum));
  }, [draftItems]);

  const availablePriceOptionsByCode = useMemo(() => menu.items.reduce<Record<string, WaiterPriceOption[]>>(
    (result, item) => {
      result[item.item_code] = item.price_options
        .map((option) => ({
          ...option,
          available_qty: maximumQuantity(item, option.id),
        }))
        .filter((option) => option.available_qty > 0);
      return result;
    },
    {},
  ), [maximumQuantity, menu.items]);

  const selectedOptionFor = useCallback((item: WaiterMenuItem, optionId: string | null) => {
    if (item.price_options.length === 0) return null;
    return item.price_options.find((option) => option.id === optionId)
      ?? (optionId === null ? item.price_options.find((option) => option.is_default) ?? null : null);
  }, []);

  const createDraftLine = useCallback((
    item: WaiterMenuItem,
    option: WaiterPriceOption | null,
    qty: number,
    comment: string,
  ): DraftOrderItem => ({
    id: createRequestId(),
    item_code: item.item_code,
    item_name: item.item_name,
    qty,
    rate: option?.rate ?? item.rate,
    price_option: option?.id ?? null,
    price_option_label: option?.label ?? null,
    comment,
    available_qty: item.available_qty,
    is_stock_item: item.is_stock_item,
    negative_stock_allowed: item.negative_stock_allowed,
    stock_uom: item.stock_uom,
  }), []);

  const markDraftChanged = useCallback(() => {
    setSubmitError(null);
  }, []);

  const showStockError = useCallback((item: WaiterMenuItem) => {
    setNotice({
      kind: 'warning',
      text: `${item.item_name} já não tem quantidade suficiente disponível.`,
    });
  }, []);

  const showPendingAttemptWarning = useCallback(() => {
    setNotice({
      kind: 'warning',
      text: 'Primeiro tente confirmar novamente o envio anterior. O pedido foi bloqueado para evitar produtos duplicados.',
    });
  }, []);

  const quickAdd = useCallback((item: WaiterMenuItem) => {
    if (draftLocked) {
      showPendingAttemptWarning();
      return;
    }
    const availableOptions = item.price_options.filter(
      (option) => maximumQuantity(item, option.id) > 0,
    );
    if (item.price_options.length > 0 && availableOptions.length !== 1) {
      if (availableOptions.length === 0) showStockError(item);
      else setItemEditor({ item, line: null });
      return;
    }
    const option = availableOptions[0] ?? null;
    if (maximumQuantity(item, option?.id ?? null) <= 0) {
      showStockError(item);
      return;
    }
    markDraftChanged();
    setDraftItems((current) => {
      const lineIndex = current.findIndex((line) => (
        line.item_code === item.item_code
        && line.price_option === (option?.id ?? null)
        && !line.comment
      ));
      if (lineIndex < 0) {
        return [...current, createDraftLine(item, option, 1, '')];
      }
      return current.map((line, index) => index === lineIndex ? { ...line, qty: line.qty + 1 } : line);
    });
  }, [createDraftLine, draftLocked, markDraftChanged, maximumQuantity, showPendingAttemptWarning, showStockError]);

  const saveItemEditor = useCallback((value: ItemEditorValue) => {
    if (!itemEditor) return;
    if (draftLocked) {
      showPendingAttemptWarning();
      return;
    }
    const { item, line } = itemEditor;
    const option = selectedOptionFor(item, value.priceOption);
    if (item.price_options.length > 0 && !option) {
      setNotice({ kind: 'warning', text: `Escolha o preço de ${item.item_name}.` });
      return;
    }
    if (value.qty > maximumQuantity(item, option?.id ?? null, line?.id)) {
      showStockError(item);
      return;
    }
    markDraftChanged();
    setDraftItems((current) => {
      if (line) {
        const matchingLine = current.find((entry) => (
          entry.id !== line.id
          && entry.item_code === line.item_code
          && entry.price_option === line.price_option
          && entry.comment === value.comment
        ));
        if (matchingLine) {
          return current
            .filter((entry) => entry.id !== line.id)
            .map((entry) => entry.id === matchingLine.id
              ? { ...entry, qty: entry.qty + value.qty }
              : entry);
        }
        return current.map((entry) => entry.id === line.id
          ? { ...entry, qty: value.qty, comment: value.comment }
          : entry);
      }
      const matchingLine = current.find((entry) => (
        entry.item_code === item.item_code
        && entry.price_option === (option?.id ?? null)
        && entry.comment === value.comment
      ));
      if (matchingLine) {
        return current.map((entry) => entry.id === matchingLine.id
          ? { ...entry, qty: entry.qty + value.qty }
          : entry);
      }
      return [...current, createDraftLine(item, option, value.qty, value.comment)];
    });
    setItemEditor(null);
  }, [createDraftLine, draftLocked, itemEditor, markDraftChanged, maximumQuantity, selectedOptionFor, showPendingAttemptWarning, showStockError]);

  const increaseLine = useCallback((line: DraftOrderItem) => {
    if (draftLocked) {
      showPendingAttemptWarning();
      return;
    }
    const item = menuItemForLine(line);
    if (line.qty >= maximumQuantity(item, line.price_option, line.id)) {
      showStockError(item);
      return;
    }
    markDraftChanged();
    setDraftItems((current) => current.map((entry) => entry.id === line.id ? { ...entry, qty: entry.qty + 1 } : entry));
  }, [draftLocked, markDraftChanged, maximumQuantity, menuItemForLine, showPendingAttemptWarning, showStockError]);

  const decreaseLine = useCallback((line: DraftOrderItem) => {
    if (draftLocked) {
      showPendingAttemptWarning();
      return;
    }
    if (line.qty <= 1) return;
    markDraftChanged();
    setDraftItems((current) => current.map((entry) => entry.id === line.id ? { ...entry, qty: entry.qty - 1 } : entry));
  }, [draftLocked, markDraftChanged, showPendingAttemptWarning]);

  const removeLine = useCallback((line: DraftOrderItem) => {
    if (draftLocked) {
      showPendingAttemptWarning();
      return;
    }
    markDraftChanged();
    setDraftItems((current) => current.filter((entry) => entry.id !== line.id));
  }, [draftLocked, markDraftChanged, showPendingAttemptWarning]);

  const editorMaximumQuantity = useCallback((priceOption: string | null): number => {
    if (!itemEditor) return 1;
    return maximumQuantity(itemEditor.item, priceOption, itemEditor.line?.id);
  }, [itemEditor, maximumQuantity]);

  const backToTables = useCallback(() => {
    if (draftLocked) {
      showPendingAttemptWarning();
      setSummaryOpen(true);
      return;
    }
    if (draftItems.length > 0 && !window.confirm('Existem produtos ainda não registados. Pretende descartá-los e voltar às mesas?')) {
      return;
    }
    selectionRequestRef.current += 1;
    setSelectedTable(null);
    setMenu({ items: [], categories: [] });
    setExistingOrder(null);
    setDraftItems([]);
    setSummaryOpen(false);
    setItemEditor(null);
    setSubmitError(null);
  }, [draftItems.length, draftLocked, showPendingAttemptWarning]);

  const registerOrder = useCallback(async () => {
    if (!selectedTable || draftItems.length === 0 || submitting) return;
    setSubmitError(null);
    let activeSubmission = pendingSubmission;
    let postStarted = false;

    try {
      if (!activeSubmission) {
        const request: RegisterOrderRequest = {
          table: selectedTable.name,
          room: selectedTable.room || selectedRoom,
          items: draftItems.map((line) => ({
            item_code: line.item_code,
            qty: line.qty,
            expected_rate: line.rate,
            ...(line.price_option ? { price_option: line.price_option } : {}),
            ...(line.comment ? { comment: line.comment } : {}),
          })),
          no_of_pax: noOfPax,
          comments: comments.trim() || null,
          expected_modified: existingOrder?.modified ?? selectedTable.modified,
          request_id: createRequestId(),
        };
        activeSubmission = persistPendingSubmission(context?.user.name ?? '', request, draftItems);
      } else {
        activeSubmission = persistPendingSubmission(
          activeSubmission.user,
          activeSubmission.request,
          activeSubmission.draft_items,
        );
      }

      setPendingSubmission(activeSubmission);
      setSubmitting(true);
      postStarted = true;
      const result = await registerWaiterOrder(activeSubmission.request);

      if (result.status !== 'success' || !result.invoice || result.kots.length === 0) {
        throw new Error('O servidor não confirmou o documento e o encaminhamento KOT do pedido. Os produtos foram mantidos para uma nova tentativa segura.');
      }

      clearPendingSubmission(activeSubmission.user, activeSubmission.request.request_id);
      setPendingSubmission(null);
      setDraftItems([]);
      setSummaryOpen(false);
      setExistingOrder(result.order ?? {
        invoice: result.invoice,
        modified: result.modified,
        waiter: context?.user.name ?? null,
        waiter_name: context?.user.full_name ?? null,
        no_of_pax: noOfPax,
        comments,
        sent_items: [],
      });

      if (result.kot_warning) {
        setNotice({ kind: 'warning', text: `Pedido ${result.invoice} registado no POS. Aviso do encaminhamento KOT: ${result.kot_warning}` });
      } else {
        setNotice({ kind: 'success', text: `Pedido ${result.invoice} registado com sucesso.` });
      }

      const [freshOrder, freshMenu] = await Promise.allSettled([
        getWaiterTableOrder(selectedTable.name),
        getWaiterMenu(selectedTable.room || selectedRoom),
      ]);
      if (freshOrder.status === 'fulfilled') setExistingOrder(freshOrder.value);
      if (freshMenu.status === 'fulfilled') setMenu(freshMenu.value);
    } catch (error) {
      if (!postStarted) {
        const recovered = context?.user.name ? loadPendingSubmission(context.user.name) : null;
        if (recovered && context) {
          await restorePendingUi(recovered, context);
          setSubmitError('Já existia uma tentativa por confirmar neste dispositivo. Foi recuperado o pedido original; repita esse envio antes de iniciar outro.');
        } else {
          if (!pendingSubmission) setPendingSubmission(null);
          setSubmitError(getErrorMessage(error, 'O navegador não conseguiu guardar uma cópia segura do pedido. O pedido não foi enviado.'));
        }
        return;
      }
      if (isAuthenticationError(error)) {
        requireLogin();
        return;
      }
      if (!isUnknownSubmissionOutcome(error)) {
        if (activeSubmission) clearPendingSubmission(activeSubmission.user, activeSubmission.request.request_id);
        const [freshOrder, freshMenu] = await Promise.allSettled([
          getWaiterTableOrder(selectedTable.name),
          getWaiterMenu(selectedTable.room || selectedRoom),
        ]);
        if (freshOrder.status === 'fulfilled') setExistingOrder(freshOrder.value);
        if (freshMenu.status === 'fulfilled') {
          setMenu(freshMenu.value);
          const currentItems = new Map(
            freshMenu.value.items.map((item) => [item.item_code, item]),
          );
          // A deterministic rejection (notably a stale-price conflict)
          // unlocks the draft. Refresh the displayed snapshot so the next
          // explicit submit uses exactly the rate the waiter can now review.
          setDraftItems((current) => current.map((line) => {
            const item = currentItems.get(line.item_code);
            if (!item) return line;
            const option = item.price_options.find((entry) => entry.id === line.price_option)
              ?? (line.price_option === null
                ? item.price_options.find((entry) => entry.is_default) ?? null
                : null);
            const isStandardLine = line.price_option === null || line.price_option === 'standard';
            return {
              ...line,
              rate: option?.rate ?? (item.price_options.length === 0 && isStandardLine ? item.rate : line.rate),
              price_option: option?.id ?? line.price_option,
              price_option_label: option?.label ?? line.price_option_label,
              available_qty: item.available_qty,
              is_stock_item: item.is_stock_item,
              negative_stock_allowed: item.negative_stock_allowed,
              stock_uom: item.stock_uom,
            };
          }));
        }
        setPendingSubmission(null);
      }
      setSubmitError(getErrorMessage(error, 'Não foi possível confirmar o pedido.'));
    } finally {
      setSubmitting(false);
    }
  }, [comments, context, draftItems, existingOrder?.modified, noOfPax, pendingSubmission, requireLogin, restorePendingUi, selectedRoom, selectedTable, submitting]);

  const handleLogout = useCallback(async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logoutWaiter();
      // Reload only this page so the guest session receives a fresh CSRF token
      // and immediately renders the embedded login form.
      window.location.replace('/waiter');
    } catch {
      const message = 'Não foi possível terminar a sessão. A sessão atual continua ativa; verifique a ligação e tente novamente.';
      setLogoutError(message);
      if (context) setNotice({ kind: 'error', text: message });
    } finally {
      setLoggingOut(false);
    }
  }, [context, loggingOut]);

  if (booting) return <FullPageLoading />;

  if (!context && !bootError) {
    return <LoginScreen onAuthenticated={() => window.location.replace('/waiter')} />;
  }

  if (accountNotAuthorized) {
    return (
      <main className="min-h-dvh min-w-[320px] bg-slate-50">
        <StatePanel
          icon={<LockKeyhole className="h-7 w-7" aria-hidden="true" />}
          title="Conta não autorizada"
          message={[bootError, logoutError].filter(Boolean).join(' ')}
          actionLabel={loggingOut ? 'A terminar sessão…' : 'Terminar sessão e mudar de utilizador'}
          actionIcon={loggingOut
            ? <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden="true" />
            : <LogOut className="h-5 w-5" aria-hidden="true" />}
          actionLoading={loggingOut}
          onAction={() => void handleLogout()}
        />
      </main>
    );
  }

  if (bootError || !context) {
    return (
      <main className="min-h-dvh min-w-[320px] bg-slate-50">
        <StatePanel
          title="Não foi possível abrir o atendimento"
          message={[
            bootError || 'A configuração do atendente não está disponível.',
            logoutError,
          ].filter(Boolean).join(' ')}
          actionLabel="Tentar novamente"
          onAction={() => void bootstrap()}
          actionLoading={loggingOut}
          secondaryActionLabel="Mudar de utilizador"
          onSecondaryAction={() => void handleLogout()}
        />
      </main>
    );
  }

  const roomLabel = context.rooms.find((room) => room.name === (selectedTable?.room || selectedRoom))?.label
    ?? selectedTable?.room
    ?? selectedRoom;
  const recoveringSubmission = Boolean(pendingSubmission && selectedTable);

  return (
    <div className="min-h-dvh min-w-[320px] bg-slate-50 text-slate-950">
      <AppHeader
        userName={context.user.full_name}
        branch={context.branch}
        refreshing={refreshing}
        loggingOut={loggingOut}
        onRefresh={() => void refreshCurrent()}
        onLogout={() => void handleLogout()}
      />

      {notice ? (
        <div className="pointer-events-none fixed inset-x-0 top-20 z-50 px-3" aria-live="polite">
          <div className={cn(
            'pointer-events-auto mx-auto flex max-w-xl items-start gap-3 rounded-2xl border p-3 shadow-lg',
            notice.kind === 'success' && 'border-emerald-200 bg-emerald-50 text-emerald-900',
            notice.kind === 'warning' && 'border-amber-200 bg-amber-50 text-amber-900',
            notice.kind === 'error' && 'border-red-200 bg-red-50 text-red-900',
          )}>
            {notice.kind === 'success'
              ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
              : <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />}
            <p className="min-w-0 flex-1 text-sm font-medium leading-5">{notice.text}</p>
            <button
              type="button"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
              aria-label="Fechar aviso"
              onClick={() => setNotice(null)}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : null}

      {!recoveringSubmission && !context.opening.is_open ? (
        <OpeningBlocked message={context.opening.message} onRetry={() => void refreshContextOnly()} loading={refreshing} />
      ) : !recoveringSubmission && !context.can_register ? (
        <StatePanel
          icon={<LockKeyhole className="h-7 w-7" aria-hidden="true" />}
          title="Registo indisponível"
          message={context.opening.message || 'A sua sessão não está autorizada a registar pedidos neste caixa.'}
          actionLabel="Verificar novamente"
          onAction={() => void refreshContextOnly()}
          actionLoading={refreshing}
        />
      ) : !recoveringSubmission && (context.rooms.length === 0 || !context.rooms.some((room) => room.is_open)) ? (
        <StatePanel
          title="Nenhuma sala disponível"
          message="Não existem salas atribuídas a este atendente no caixa aberto. Contacte o responsável."
          actionLabel="Actualizar"
          onAction={() => void refreshContextOnly()}
          actionLoading={refreshing}
        />
      ) : selectedTable ? (
        <>
          <MenuView
            tableName={selectedTable.name}
            roomName={roomLabel}
            items={menu.items}
            categories={menu.categories}
            selectedCategory={selectedCategory}
            search={search}
            draftQuantityByCode={draftQuantityByCode}
            availablePriceOptionsByCode={availablePriceOptionsByCode}
            currency={context.currency}
            currencySymbol={context.currency_symbol}
            loading={menuLoading}
            error={menuError}
            interactionDisabled={draftLocked}
            onBack={backToTables}
            onSearchChange={setSearch}
            onCategoryChange={setSelectedCategory}
            onQuickAdd={quickAdd}
            onConfigure={(item) => setItemEditor({ item, line: null })}
            onRetry={() => void loadSelectedTable(selectedTable, selectedTable.room || selectedRoom, draftLocked)}
          />
          {!menuLoading && !menuError ? (
            <OrderDock
              itemCount={draftCount}
              total={draftTotal}
              hasExistingOrder={Boolean(existingOrder?.invoice)}
              currency={context.currency}
              currencySymbol={context.currency_symbol}
              onOpen={() => setSummaryOpen(true)}
            />
          ) : null}
          <OrderSummarySheet
            open={summaryOpen}
            tableName={selectedTable.name}
            existingOrder={existingOrder}
            draftItems={draftItems}
            noOfPax={noOfPax}
            comments={comments}
            currency={context.currency}
            currencySymbol={context.currency_symbol}
            submitting={submitting}
            submitError={submitError}
            mutationDisabled={draftLocked}
            onClose={() => setSummaryOpen(false)}
            onNoOfPaxChange={(value) => {
              markDraftChanged();
              setNoOfPax(value);
            }}
            onCommentsChange={(value) => {
              markDraftChanged();
              setComments(value);
            }}
            onIncrease={increaseLine}
            onDecrease={decreaseLine}
            onEdit={(line) => {
              setSummaryOpen(false);
              setItemEditor({ item: menuItemForLine(line), line });
            }}
            onRemove={removeLine}
            onSubmit={() => void registerOrder()}
          />
          <ItemEditorSheet
            open={Boolean(itemEditor)}
            item={itemEditor?.item ?? null}
            line={itemEditor?.line ?? null}
            maximumQuantity={editorMaximumQuantity}
            currency={context.currency}
            currencySymbol={context.currency_symbol}
            onClose={() => {
              const wasEditing = Boolean(itemEditor?.line);
              setItemEditor(null);
              if (wasEditing) setSummaryOpen(true);
            }}
            onSave={(value) => {
              const wasEditing = Boolean(itemEditor?.line);
              saveItemEditor(value);
              if (wasEditing) setSummaryOpen(true);
            }}
          />
        </>
      ) : (
        <TableSelection
          rooms={context.rooms}
          selectedRoom={selectedRoom}
          tables={tables}
          loading={tablesLoading}
          error={tablesError}
          onRoomChange={setSelectedRoom}
          onTableSelect={selectTable}
          onRetry={() => void loadTables(selectedRoom)}
        />
      )}
    </div>
  );
}

export default App;
