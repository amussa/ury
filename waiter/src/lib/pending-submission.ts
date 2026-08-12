import { storage } from '@ury/core';
import type { DraftOrderItem, RegisterOrderItem, RegisterOrderRequest } from '@/types';

export const PENDING_SUBMISSION_KEY_PREFIX = 'ury_waiter_pending_submission_v1:';
const PENDING_SUBMISSION_VERSION = 1 as const;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;

export interface PendingSubmissionRecord {
  version: typeof PENDING_SUBMISSION_VERSION;
  user: string;
  saved_at: string;
  request: RegisterOrderRequest;
  draft_items: DraftOrderItem[];
}

type UnknownRecord = Record<string, unknown>;

const record = (value: unknown): UnknownRecord | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null;

const validString = (value: unknown, maximum: number, allowEmpty = false): value is string =>
  typeof value === 'string' && value.length <= maximum && (allowEmpty || value.trim().length > 0);

const validPositiveInteger = (value: unknown, maximum: number): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= maximum;

function parseRequestItem(value: unknown): RegisterOrderItem | null {
  const row = record(value);
  if (!row || !validString(row.item_code, 140) || !validPositiveInteger(row.qty, 999)) return null;
  if (typeof row.expected_rate !== 'number' || !Number.isFinite(row.expected_rate) || row.expected_rate < 0) return null;
  if (row.price_option !== undefined && !validString(row.price_option, 140)) return null;
  if (row.comment !== undefined && !validString(row.comment, 500, true)) return null;
  return {
    item_code: row.item_code,
    qty: row.qty,
    expected_rate: row.expected_rate,
    ...(typeof row.price_option === 'string' ? { price_option: row.price_option } : {}),
    ...(typeof row.comment === 'string' && row.comment ? { comment: row.comment } : {}),
  };
}

function parseRequest(value: unknown): RegisterOrderRequest | null {
  const row = record(value);
  if (!row) return null;
  if (!validString(row.table, 140) || !validString(row.room, 140)) return null;
  if (!validString(row.request_id, 128) || !REQUEST_ID_PATTERN.test(row.request_id)) return null;
  if (!validPositiveInteger(row.no_of_pax, 99)) return null;
  if (!Array.isArray(row.items) || row.items.length < 1 || row.items.length > 100) return null;
  if (row.comments !== undefined && row.comments !== null && !validString(row.comments, 1000, true)) return null;
  if (row.expected_modified !== undefined && row.expected_modified !== null && !validString(row.expected_modified, 100)) return null;

  const items = row.items.map(parseRequestItem);
  if (items.some((item) => item === null)) return null;

  return {
    table: row.table,
    room: row.room,
    items: items as RegisterOrderItem[],
    no_of_pax: row.no_of_pax,
    comments: typeof row.comments === 'string' ? row.comments : null,
    expected_modified: typeof row.expected_modified === 'string' ? row.expected_modified : null,
    request_id: row.request_id,
  };
}

function parseDraftItem(value: unknown): DraftOrderItem | null {
  const row = record(value);
  if (!row) return null;
  if (!validString(row.id, 160) || !validString(row.item_code, 140) || !validString(row.item_name, 300)) return null;
  if (!validPositiveInteger(row.qty, 999)) return null;
  if (typeof row.rate !== 'number' || !Number.isFinite(row.rate) || row.rate < 0) return null;
  if (row.price_option !== undefined && row.price_option !== null && !validString(row.price_option, 140)) return null;
  if (row.price_option_label !== undefined && row.price_option_label !== null && !validString(row.price_option_label, 140)) return null;
  if (!validString(row.comment, 500, true)) return null;
  if (row.available_qty !== null && (typeof row.available_qty !== 'number' || !Number.isFinite(row.available_qty))) return null;
  if (typeof row.is_stock_item !== 'boolean' || typeof row.negative_stock_allowed !== 'boolean') return null;
  if (row.stock_uom !== null && !validString(row.stock_uom, 140, true)) return null;

  return {
    id: row.id,
    item_code: row.item_code,
    item_name: row.item_name,
    qty: row.qty,
    rate: row.rate,
    price_option: typeof row.price_option === 'string' ? row.price_option : null,
    price_option_label: typeof row.price_option_label === 'string' ? row.price_option_label : null,
    comment: row.comment,
    available_qty: row.available_qty as number | null,
    is_stock_item: row.is_stock_item,
    negative_stock_allowed: row.negative_stock_allowed,
    stock_uom: row.stock_uom as string | null,
  };
}

function requestMatchesDraft(request: RegisterOrderRequest, draftItems: DraftOrderItem[]): boolean {
  if (request.items.length !== draftItems.length) return false;
  return request.items.every((item, index) => {
    const draft = draftItems[index];
    return Boolean(
      draft
      && item.item_code === draft.item_code
      && item.qty === draft.qty
      && item.expected_rate === draft.rate
      && (item.price_option ?? null) === draft.price_option
      && (item.comment ?? '') === draft.comment,
    );
  });
}

function parseRecord(value: unknown): PendingSubmissionRecord | null {
  const row = record(value);
  if (!row || row.version !== PENDING_SUBMISSION_VERSION) return null;
  if (!validString(row.user, 140) || !validString(row.saved_at, 64)) return null;
  const request = parseRequest(row.request);
  if (!request || !Array.isArray(row.draft_items) || row.draft_items.length < 1 || row.draft_items.length > 100) return null;
  const draftItems = row.draft_items.map(parseDraftItem);
  if (draftItems.some((item) => item === null)) return null;
  const validDraftItems = draftItems as DraftOrderItem[];
  if (!requestMatchesDraft(request, validDraftItems)) return null;

  return {
    version: PENDING_SUBMISSION_VERSION,
    user: row.user,
    saved_at: row.saved_at,
    request,
    draft_items: validDraftItems,
  };
}

const storageKeyForUser = (user: string): string =>
  `${PENDING_SUBMISSION_KEY_PREFIX}${encodeURIComponent(user)}`;

function readStoredRecord(user: string): PendingSubmissionRecord | null {
  const serialized = storage.getItem(storageKeyForUser(user));
  if (!serialized) return null;
  try {
    return parseRecord(JSON.parse(serialized));
  } catch {
    return null;
  }
}

function removeStoredRecord(user: string): void {
  try {
    storage.removeItem(storageKeyForUser(user));
  } catch {
    // A failed removal leaves an idempotent retry record, which is safer than
    // silently replacing it with a new request.
  }
}

export function loadPendingSubmission(currentUser: string): PendingSubmissionRecord | null {
  let stored: PendingSubmissionRecord | null;
  try {
    stored = readStoredRecord(currentUser);
  } catch {
    return null;
  }

  if (!stored) {
    removeStoredRecord(currentUser);
    return null;
  }
  if (stored.user !== currentUser) {
    removeStoredRecord(currentUser);
    return null;
  }
  return stored;
}

export function persistPendingSubmission(
  user: string,
  request: RegisterOrderRequest,
  draftItems: DraftOrderItem[],
): PendingSubmissionRecord {
  const candidate = parseRecord({
    version: PENDING_SUBMISSION_VERSION,
    user,
    saved_at: new Date().toISOString(),
    request,
    draft_items: draftItems,
  });
  if (!candidate) throw new Error('Não foi possível validar a cópia local do pedido.');

  let existing: PendingSubmissionRecord | null = null;
  try {
    existing = readStoredRecord(user);
  } catch {
    // The write below remains authoritative if the previous value cannot be read.
  }
  if (existing && existing.user === user) {
    const sameRequest = existing.request.request_id === candidate.request.request_id
      && JSON.stringify(existing.request) === JSON.stringify(candidate.request)
      && JSON.stringify(existing.draft_items) === JSON.stringify(candidate.draft_items);
    if (!sameRequest) {
      throw new Error('Já existe uma tentativa de pedido por confirmar neste dispositivo. Actualize a página para a recuperar.');
    }
  }

  const serialized = JSON.stringify(candidate);
  const storageKey = storageKeyForUser(user);
  storage.setItem(storageKey, serialized);
  if (storage.getItem(storageKey) !== serialized) {
    throw new Error('O navegador não confirmou a gravação segura do pedido.');
  }
  return candidate;
}

export function clearPendingSubmission(user: string, requestId: string): void {
  try {
    const stored = readStoredRecord(user);
    if (!stored || (stored.user === user && stored.request.request_id === requestId)) {
      removeStoredRecord(user);
    }
  } catch {
    // Keep the record when its identity cannot be checked. A later replay with
    // the same request_id is safe and cannot append duplicate lines.
  }
}
