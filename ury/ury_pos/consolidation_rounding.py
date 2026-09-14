"""Preserve source POS totals through native Sales Invoice rounding entries."""
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals


def source_rounding_plan(doc):
    """Resolve complete, unmodified source rows; payment amounts are not totals."""
    names = {row.get("pos_invoice") for row in doc.items}
    if not names or None in names or "" in names:
        frappe.throw(_("Consolidation rounding requires a source POS Invoice for every item."))
    sources = {name: frappe.get_doc("POS Invoice", name) for name in names}
    expected_rows = {}
    total = Decimal(0)
    original_rounding = Decimal(0)
    for source in sources.values():
        if (source.docstatus != 1 or source.company != doc.company
                or source.currency != doc.currency
                or flt(source.conversion_rate) != flt(doc.conversion_rate)
                or bool(source.is_return) != bool(doc.is_return)):
            frappe.throw(_("Source POS Invoice does not match the consolidated invoice."))
        total += Decimal(str(source.rounded_total or source.grand_total or 0))
        original_rounding += abs(Decimal(str(source.rounding_adjustment or 0)))
        for row in source.items:
            expected_rows[row.name] = (source.name, row)
    seen = set()
    for row in doc.items:
        key = row.get("pos_invoice_item")
        if key in seen or key not in expected_rows:
            frappe.throw(_("Missing or duplicate source item in consolidated invoice."))
        seen.add(key)
        parent, source = expected_rows[key]
        if (row.pos_invoice != parent or row.item_code != source.item_code
                or flt(row.qty) != flt(source.qty)
                or abs(flt(row.amount) - flt(source.net_amount)) > 0.000001):
            frappe.throw(_("Consolidated item differs from its source POS Invoice."))
    if seen != set(expected_rows):
        frappe.throw(_("Consolidated invoice must contain every source POS item exactly once."))
    return float(total), float(original_rounding)


def apply_rounding(doc, target, original_rounding=0):
    """Bound the residual by source rounding and per-line currency precision."""
    unit = 10 ** -doc.precision("grand_total")
    multiplier = 1 + sum(abs(flt(t.rate)) / 100 for t in doc.get("taxes") or [])
    line_error = sum(0.5 * 10 ** -r.precision("net_amount") for r in doc.items)
    tax_error = sum(0.5 * 10 ** -t.precision("tax_amount") for t in doc.get("taxes") or [])
    bound = original_rounding + line_error * multiplier + tax_error + unit
    delta = flt(target - doc.grand_total, doc.precision("rounding_adjustment"))
    if abs(delta) > bound + 0.000001:
        frappe.throw(_("Consolidation difference {0} exceeds the rounding tolerance {1}.").format(delta, flt(bound, 4)))
    # Native ERPNext falls back to grand_total when rounded_total is zero.
    if not target and delta:
        frappe.throw(_("A zero-value consolidation cannot absorb a nonzero total."))
    doc.disable_rounded_total = 0
    doc.rounded_total = flt(target, doc.precision("rounded_total"))
    doc.base_rounded_total = flt(target * doc.conversion_rate, doc.precision("base_rounded_total"))
    doc.rounding_adjustment = delta
    doc.base_rounding_adjustment = flt(
        doc.base_rounded_total - doc.base_grand_total, doc.precision("base_rounding_adjustment")
    )


class ConsolidationTaxesAndTotals(calculate_taxes_and_totals):
    def __init__(self, doc):
        self.source_total, self.source_rounding = source_rounding_plan(doc)
        super().__init__(doc)

    def set_rounded_total(self):
        # Before outstanding/change/write-off calculation in the native pipeline.
        apply_rounding(self.doc, self.source_total, self.source_rounding)


class URYSalesInvoice(SalesInvoice):
    def calculate_taxes_and_totals(self):
        if not (self.get("is_consolidated") and self.get("is_pos")):
            return super().calculate_taxes_and_totals()
        ConsolidationTaxesAndTotals(self)
        self.calculate_commission()
        self.calculate_contribution()
