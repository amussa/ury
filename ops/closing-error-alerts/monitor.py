#!/usr/bin/env python3
"""Poll persisted POS closing failures and enqueue deduplicated Frappe emails.

Run with the bench Python from sites/, via the host systemd timer. No app
overlay, schema migration, or writes to closing/invoice records are required.
"""

import argparse
import fcntl
import hashlib
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote


def failure_key(row, recipient, test=False):
    payload = ["test" if test else "closing", row["name"], str(row["modified"]),
               row.get("error_message") or "", recipient]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def render_message(row, site, test=False):
    title = "[TESTE] Gelatiamo — Erro no fecho" if test else "Gelatiamo — Erro no fecho"
    subject = f"{title} {row['name']} — {row.get('pos_profile') or ''}"
    values = [
        ("Fecho", row["name"]), ("POS / loja", row.get("pos_profile")),
        ("Operador", row.get("user")), ("Data do erro", row.get("modified")),
        ("Empresa", row.get("company")), ("Valor total (MT)", row.get("grand_total")),
    ]
    body = "<p>Foi detectada uma falha no processamento do fecho de caixa.</p>"
    if test:
        body = "<p><strong>TESTE SIMULADO: não existe uma falha real de fecho.</strong></p>"
    body += "<ul>" + "".join(
        f"<li><strong>{escape(label)}:</strong> {escape(str(value if value is not None else '—'))}</li>"
        for label, value in values
    ) + "</ul>"
    error = row.get("error_message") or "Erro sem descrição; consultar o fecho e os registos do sistema."
    body += f"<p><strong>Erro:</strong></p><pre>{escape(str(error))}</pre>"
    if not test:
        url = f"https://{site}/app/pos-closing-entry/{quote(row['name'], safe='')}"
        body += f'<p><a href="{escape(url, quote=True)}">Consultar o fecho</a></p>'
    body += "<p>O alerta não reprocessa o fecho. A situação deve ser verificada no ERPNext.</p>"
    return subject, body


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def process_rows(frappe, rows, state, persist, site, recipient, *, dry_run=False, test=False):
    results = []
    for row in rows:
        if row.get("status") != "Failed" or row.get("docstatus") != 1:
            continue
        key = failure_key(row, recipient, test)
        if key in state["alerts"]:
            results.append({"closing": row["name"], "result": "already_queued",
                            "queue": state["alerts"][key]})
            continue
        if dry_run:
            results.append({"closing": row["name"], "result": "would_queue"})
            continue
        message_id = f"gelatiamo-closing-{key}@{site}"
        # Recover a crash between the DB commit and atomic state-file replacement.
        queue_name = frappe.db.get_value("Email Queue", {"message_id": message_id}, "name")
        if not queue_name:
            subject, body = render_message(row, site, test)
            reference = {} if test else {"reference_doctype": "POS Closing Entry", "reference_name": row["name"]}
            queue = frappe.sendmail(
                recipients=[recipient], subject=subject, message=body,
                message_id=message_id, delayed=True, now=False,
                is_notification=True, add_unsubscribe_link=False, **reference,
            )
            if not queue or not queue.name:
                raise RuntimeError("Frappe did not create the alert email queue")
            queue_name = queue.name
            frappe.db.commit()
        state["alerts"][key] = queue_name
        persist(state)
        results.append({"closing": row["name"], "result": "queued", "queue": queue_name})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="gelati.app.co.mz")
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-token", help="Explicitly enqueue one labelled synthetic test; reuse token to check deduplication")
    args = parser.parse_args()
    import frappe

    os.chdir("/home/frappe/frappe-bench/sites")
    frappe.init(site=args.site)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        frappe.utils.validate_email_address(args.recipient, throw=True)
        path = Path(frappe.get_site_path("private", "closing-error-alerts.json"))
        path.parent.mkdir(exist_ok=True)
        # Site-volume lock also protects manual runs alongside the host timer.
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text()) if path.exists() else {"version": 1, "alerts": {}}
            if state.get("version") != 1 or not isinstance(state.get("alerts"), dict):
                raise RuntimeError("Invalid alert state; refusing to reset deduplication history")
            rows = frappe.get_all(
                "POS Closing Entry", filters={"status": "Failed", "docstatus": 1},
                fields=["name", "status", "docstatus", "modified", "error_message",
                        "pos_profile", "user", "company", "grand_total"],
                order_by="modified asc",
            )
            if args.test_token:
                rows = [{"name": "TESTE-ALERTA-FECHO", "status": "Failed", "docstatus": 1,
                         "modified": args.test_token, "pos_profile": "Simulação de alerta",
                         "user": args.recipient, "company": "Gelatiamo", "grand_total": 0,
                         "error_message": "Falha simulada para validar o alerta automático. Nenhum fecho real foi alterado."}]
            results = process_rows(
                frappe, rows, state, lambda value: save_state(path, value),
                args.site, args.recipient, dry_run=args.dry_run, test=bool(args.test_token),
            )
            if not args.dry_run:
                save_state(path, state)
            print(json.dumps({"checked_at": frappe.utils.now(), "recipient": args.recipient,
                              "dry_run": args.dry_run, "test": bool(args.test_token),
                              "failed_count": len(rows), "results": results}, ensure_ascii=False))
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
