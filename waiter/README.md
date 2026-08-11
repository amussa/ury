# URY Waiter

Mobile-first order-entry page for restaurant waiters. It is served at `/waiter`, uses the existing Frappe session and writes new order lines into the same draft POS Invoice used by the standard URY POS.

## Scope

- Designed for touch screens from 320 px wide, primarily portrait phones.
- Select room and a server-authoritative `free` or `mine` table.
- Search/filter the menu, inspect availability, add quantities and observations.
- See existing submitted lines as read-only and add only new line deltas.
- Register through the URY backend, which validates price/stock/concurrency and decides KOT routing.
- Persist and reuse the exact payload and `request_id` after a timeout, reload, crash or unknown response.
- Block order entry when there is no active opening or the backend returns `can_register: false`.
- Let an unauthenticated waiter sign in directly at `/waiter`, without visiting ERPNext Desk first.
- Install from Chrome as a standalone PWA while keeping all authenticated/API traffic network-only.

This page intentionally has no payment, till opening/closing, discount, cancellation, transfer or print actions.

## API contract

All methods are whitelisted Frappe RPCs under `ury.ury_pos.waiter_api`. Frappe wraps each payload in `{ "message": ... }`; `src/lib/waiter-api.ts` unwraps and normalizes it.

### `get_context()`

```json
{
  "user": { "name": "user@example.com", "full_name": "Atendente" },
  "branch": "Polana",
  "pos_profile": "POS Polana",
  "rooms": [{ "name": "Sala", "is_open": true }],
  "opening": { "is_open": true, "reason_code": null, "reason": null },
  "can_register": true
}
```

The frontend never requests, displays or controls the opening entry or cashier identity. The normalizer supplies the application display defaults `MZN`/`MT`; these are not backend authority. Each pending line submits the last displayed price as `expected_rate`, used only as a concurrency token; the backend independently resolves the authoritative rate and rejects the request if it changed. Currency is never submitted with an order.

### `get_tables(room?)`

```json
{
  "rooms": ["Sala"],
  "opening": { "is_open": true, "reason_code": null, "reason": null },
  "tables": [
    {
      "name": "Mesa 1",
      "room": "Sala",
      "occupied": true,
      "state": "mine",
      "ownership": "mine",
      "invoice": "ACC-PSINV-2026-00001",
      "editable": true,
      "modified": "2026-08-10 12:00:00.000000"
    }
  ]
}
```

Raw `state` can be `free`, `mine`, `occupied`, `locked`, or `unavailable`. The wrapper normalizes every non-actionable state to `locked`. The UI acts only on `editable: true` rows whose normalized state is `free` or `mine`; it does not infer ownership from a cached table flag.

### `get_menu(room)`

```json
{
  "room": "Sala",
  "menu": { "name": "Menu Polana", "modified": "2026-08-10 12:00:00.000000" },
  "items": [
    {
      "item": "BEB-001",
      "item_name": "Água",
      "item_image": "/files/agua.png",
      "rate": 50,
      "course": "Bebidas",
      "course_label": "Bebidas",
      "available": true,
      "available_qty": 12,
      "is_stock_item": true,
      "negative_stock_allowed": false,
      "stock_uom": "Unidade"
    }
  ]
}
```

Rates and availability are display hints from the server. The frontend echoes the displayed rate as `expected_rate`; it is never trusted as the price to charge. The backend re-resolves the price, compares it with that token, and revalidates stock transactionally.

### `get_table_order(table)`

```json
{
  "table": "Mesa 1",
  "room": "Sala",
  "editable": true,
  "opening": { "is_open": true, "reason_code": null, "reason": null },
  "order": {
    "invoice": "ACC-PSINV-2026-00001",
    "modified": "2026-08-10 12:00:00.000000",
    "table": "Mesa 1",
    "room": "Sala",
    "waiter_is_current_user": true,
    "no_of_pax": 2,
    "comments": "",
    "items": [
      {
        "line_id": "line-id",
        "item": "BEB-001",
        "item_name": "Água",
        "qty": 2,
        "rate": 50,
        "amount": 100,
        "comment": ""
      }
    ]
  }
}
```

`order` is `null` for a free table. Returned items are already registered and always read-only in the mobile UI.

### `register_order(table, room, items, no_of_pax=1, comments=None, expected_modified=None, request_id)`

Request example:

```json
{
  "table": "Mesa 1",
  "room": "Sala",
  "items": [{ "item_code": "BEB-001", "qty": 2, "expected_rate": 50, "comment": "Sem gelo" }],
  "no_of_pax": 2,
  "comments": null,
  "expected_modified": "2026-08-10 12:00:00.000000",
  "request_id": "9f3281e3-2b5e-4aee-adff-46787a9c7a87"
}
```

`items` contains **new/pending deltas only**. It is not a replacement for existing lines. `expected_rate` is mandatory and causes a conflict response if the current authoritative price no longer matches what the waiter saw.
`request_id` is mandatory and must remain identical when retrying the same payload.

Response example:

```json
{
  "status": "success",
  "replayed": false,
  "request_id": "9f3281e3-2b5e-4aee-adff-46787a9c7a87",
  "invoice": "ACC-PSINV-2026-00001",
  "modified": "2026-08-10 12:01:00.000000",
  "kots": [{ "name": "KOT-2026-00001", "production": "Balcão" }],
  "kot_warning": null,
  "order": { "invoice": "ACC-PSINV-2026-00001", "items": [] }
}
```

On a timeout, missing HTTP response, or 5xx outcome, the UI keeps and locks the pending lines until the same payload is retried with the same `request_id`. A deterministic 4xx validation response permits corrections and starts a new request ID after the cart changes. A successful response must have `status: "success"`, `invoice`, and at least one validated KOT; only then does the UI clear pending lines. A non-empty `kot_warning` is shown explicitly instead of being presented as full KOT success.

## Recovery after reload or crash

Immediately before each POST, the UI writes a versioned record to `localStorage` under `ury_waiter_pending_submission_v1:<encoded-user>`:

```json
{
  "version": 1,
  "user": "user@example.com",
  "saved_at": "2026-08-10T12:00:00.000Z",
  "request": {
    "table": "Mesa 1",
    "room": "Sala",
    "items": [{ "item_code": "BEB-001", "qty": 2, "expected_rate": 50, "comment": "Sem gelo" }],
    "no_of_pax": 2,
    "comments": null,
    "expected_modified": "2026-08-10 12:00:00.000000",
    "request_id": "9f3281e3-2b5e-4aee-adff-46787a9c7a87"
  },
  "draft_items": [
    {
      "id": "local-line-id",
      "item_code": "BEB-001",
      "item_name": "Água",
      "qty": 2,
      "rate": 50,
      "comment": "Sem gelo",
      "available_qty": 12,
      "is_stock_item": true,
      "negative_stock_allowed": false,
      "stock_uom": "Unidade"
    }
  ]
}
```

`draft_items` is a validated display snapshot; it never becomes backend authority. On bootstrap, a valid record for the authenticated user restores the table, room and pending lines, refreshes menu/order for display, opens the summary and freezes all mutations. “Repetir envio seguro” POSTs the stored `request` object unchanged—including its original `expected_modified` and `request_id`.

The record is removed only after confirmed success, a deterministic non-authentication 4xx response, or when the current user's record is structurally invalid. A network/5xx outcome, authentication/session error and explicit logout preserve it. Keys are isolated per user, so a login by another waiter neither exposes nor removes the first waiter's pending attempt. A second submission cannot overwrite a different pending request for the same user in another tab.

## Authentication, installation and build

`index.html` receives only the session-specific `csrf_token` and an HTML-escaped `data-session-user` from the `/waiter` Jinja page; it deliberately does not embed the full ERPNext Desk boot payload. The rendered identity avoids calling Frappe 15's protected `frappe.auth.get_logged_user` method as Guest. It is only a bootstrap hint: every waiter API still enforces authentication and authorization on the server. An unauthenticated session remains at `/waiter` and sees the embedded username/password form. Login is a same-origin `POST /api/method/login`; after success, `/waiter` reloads so the authenticated session obtains a fresh CSRF token and rendered identity before the application bootstrap. The password is held only in transient React state, is never logged or written to browser storage, and is cleared after every attempt.

The page links to `/waiter-manifest.webmanifest` and registers `/waiter-service-worker.js` with scope `/waiter`. The service worker deliberately has no fetch handler or cache: it exists to provide the installable Chrome application shell without ever caching login responses, API calls, order data or authenticated HTML. Chrome shows the in-app **Instalar aplicação** action when its `beforeinstallprompt` event is available; the normal browser installation icon remains supported as well. Source icons live in `public/icons/` and are derived from the existing Gelatiamo favicon.

```bash
# from the URY repository root
corepack yarn workspace waiter typecheck
corepack yarn workspace waiter build
```

The production build writes assets to `ury/public/waiter/` and copies its entry to `ury/www/waiter.html`.
