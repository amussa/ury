const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const handlers = {}, timers = [];
    const row = { remaining_qty: 5, allocated_qty: 17 };
    const frm = {
        doctype: 'URY Menu',
        doc: { name: 'Menu Polana', __onload: { promotion_balances: {
            PROMO: { allocated_qty: 17, used_qty: 12, available_qty: 5 },
        } } },
        is_new: () => false,
        is_dirty: () => !!frm.dirtied,
        dirty: () => { frm.dirtied = true; },
        refresh_field: () => {},
        reload_doc: () => { frm.reloaded = true; },
    };
    const state = {};
    const context = {
        cur_frm: frm, locals: { 'URY Menu Price Option': { PROMO: row } },
        __: value => value,
        setTimeout: callback => { timers.push(callback); return timers.length; },
        clearTimeout: () => {},
        frappe: {
            get_route: () => ['Form', 'URY Menu', 'Menu Polana'],
            ui: { form: { on: (name, events) => { handlers[name] = events; } } },
            prompt: (field, callback) => {
                state.field = field; state.apply = callback;
                return { $wrapper: { on: (_, close) => { state.close = close; } } };
            },
        },
    };
    vm.runInNewContext(fs.readFileSync(`${__dirname}/ury_menu.js`, 'utf8'), context);
    return { handlers, timers, row, frm, state };
}

test('operator enters 5, not 17; internal allocation is not changed by browser', () => {
    const { handlers, frm, state, row } = setup();
    handlers['URY Menu Price Option'].set_remaining_qty(frm, 'URY Menu Price Option', 'PROMO');
    assert.equal(state.field.default, 5);
    state.apply({ quantity: 5 });
    assert.equal(row._remaining_request.quantity, 5);
    assert.equal(row.allocated_qty, 17);
    assert.equal(frm.dirtied, true);
});

test('zero explicitly clears the available promotion', () => {
    const { handlers, frm, state, row } = setup();
    handlers['URY Menu Price Option'].set_remaining_qty(frm, 'URY Menu Price Option', 'PROMO');
    state.apply({ quantity: 0 });
    assert.equal(row._remaining_request.quantity, 0);
    assert.equal(row.remaining_qty, 0);
});

test('baseline is captured when the dialog opens', () => {
    const { handlers, frm, state, row } = setup();
    handlers['URY Menu Price Option'].set_remaining_qty(frm, 'URY Menu Price Option', 'PROMO');
    frm.doc.__onload.promotion_balances.PROMO.used_qty = 13;
    state.apply({ quantity: 4 });
    assert.equal(row._remaining_request.expected.used_qty, 12);
});

test('clean form refreshes to show orders from other tills', () => {
    const { handlers, frm, timers } = setup();
    handlers['URY Menu'].refresh(frm);
    timers.shift()();
    assert.equal(frm.reloaded, true);
});

test('unsaved edits are never overwritten by polling', () => {
    const { handlers, frm, timers } = setup();
    frm.dirtied = true;
    handlers['URY Menu'].refresh(frm);
    timers.shift()();
    assert.equal(frm.reloaded, undefined);
    assert.equal(timers.length, 1);
});

test('open dialog suspends refresh until it is closed', () => {
    const { handlers, frm, timers, state } = setup();
    handlers['URY Menu Price Option'].set_remaining_qty(frm, 'URY Menu Price Option', 'PROMO');
    handlers['URY Menu'].refresh(frm);
    timers.shift()();
    assert.equal(frm.reloaded, undefined);
    state.close();
    timers.shift()();
    assert.equal(frm.reloaded, true);
});
