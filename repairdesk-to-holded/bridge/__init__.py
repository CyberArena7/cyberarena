import logging
import dataclasses
from decimal import Decimal
import itertools
import threading
from repairdesk import RepairDesk
import repairdesk
from holded import Holded
import holded
from datetime import datetime, timedelta
import json
import os
from .utils import (
    append_warning,
    convert_customer,
    convert_document,
    find_holded_invoice_by_number,
    from_numbering_series,
    convert_payment,
)

HOLDED_API_KEY = os.environ["HOLDED_API_KEY"]
REPAIRDESK_API_KEY = os.environ["REPAIRDESK_API_KEY"]
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.FileHandler("/tmp/logs.txt"))

rd = RepairDesk(REPAIRDESK_API_KEY)
hd = Holded(HOLDED_API_KEY)

# Contains the name of all ticket statuses in the "Closed" category
CLOSED_STATUS_LIST = list(
    map(lambda s: s.name, filter(lambda s: s.type == "Closed", rd.ticket_statuses()))
)


# ---------- helpers de dirección para comparar/limpiar ----------
def _addr_tuple(addr) -> tuple:
    if not addr:
        return (None, None, None, None, None)
    return (
        getattr(addr, "street", None),
        getattr(addr, "city", None),
        getattr(addr, "region", None),
        getattr(addr, "zip", None),
        getattr(addr, "country", None),
    )


def _strip_addr_fields(c: holded.Contact):
    # si Holded rechazara la dirección, reintentamos sin ella
    for fld in ("billing_address", "shipping_address"):
        if hasattr(c, fld):
            try:
                setattr(c, fld, None)
            except Exception:
                pass


# ---------- sincronía de contacto ----------
def _sync_contact(contact: holded.Contact) -> holded.Contact:
    found = None
    if contact.custom_id is not None:
        found = hd.get_contact_by_custom_id(contact.custom_id)
    if found is None and contact.mobile is not None:
        found = hd.get_contact_by_mobile(contact.mobile)

    if found:
        need_update = (
            contact.name != found.name
            or contact.nif != found.nif
            or contact.email != found.email
            or contact.mobile != found.mobile
            or contact.isperson != found.isperson
            or contact.billAddress != found.billAddress
        )
        if not need_update:
            return found

        logger.info("Customer %s (%s) changed; syncing to Holded", contact.name, contact.custom_id)
        contact.id = found.id
        try:
            hd.update_contact(contact)
            return contact
        except Exception as e:
            logger.warning(
                "Holded rechazó update_contact con dirección (%s). Reintentando sin dirección...", e
            )
            safe = dataclasses.replace(contact) if hasattr(dataclasses, "replace") else contact
            _strip_addr_fields(safe)
            hd.update_contact(safe)
            return safe

    # Crear nuevo
    logging.info("Creating new customer %s (id: %s)", contact.name, getattr(contact, "id", None))
    try:
        new_id = hd.create_contact(contact=contact)
    except Exception as e:
        logger.warning(
            "Holded rechazó create_contact con dirección (%s). Reintentando sin dirección...", e
        )
        safe = dataclasses.replace(contact) if hasattr(dataclasses, "replace") else contact
        _strip_addr_fields(safe)
        new_id = hd.create_contact(contact=safe)

    created = hd.get_contact_by_id(new_id)
    assert created is not None
    return created


# ---------- sincronía de facturas ----------
def _sync_invoice(rd_invoice: repairdesk.Invoice):
    """
    Crea/actualiza la factura y registra pagos.
    - Tolerancia de 0,01 en comparaciones.
    - Si RD marca 'PAID' pero los pagos de RD no alcanzan el total por céntimos,
      añadimos un pago de ajuste por la diferencia exacta (hasta 0,05) para
      dejarla en Pagado en Holded.
    """
    TOL = Decimal("0.01")
    logger.debug("Syncing invoice %s", rd_invoice.order_id)
    rebu = False

    # --- Sanity checks ---
    suma_lineas = sum(map(lambda i: i.total, rd_invoice.items))
    if abs(suma_lineas - rd_invoice.total) > TOL:
        append_warning(
            message=(
                "failed sanity check: sum(items) != total "
                f"(items={suma_lineas}, total={rd_invoice.total})"
            ),
            rd_invoice_id=str(rd_invoice.id),
            order_id=rd_invoice.order_id,
            hd_invoice_id=None,
        )
        logger.warning("Invoice %s descartada por sum(items) != total", rd_invoice.order_id)
        return

    if CONFIG["used_goods_tax_class"] in map(lambda i: i.tax_class, rd_invoice.items):
        for item in rd_invoice.items:
            if item.total != Decimal(0) and item.tax_class != CONFIG["used_goods_tax_class"]:
                append_warning(
                    message="failed sanity check: REBU invoice contains other items with non-zero price",
                    rd_invoice_id=str(rd_invoice.id),
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=None,
                )
                logger.warning("Invoice %s descartada por mixto REBU", rd_invoice.order_id)
                return
        rebu = True

    if int(rd_invoice.customer.id) == 0:
        append_warning(
            message="failed sanity check: walkin customer invoices are not allowed",
            rd_invoice_id=str(rd_invoice.id),
            order_id=rd_invoice.order_id,
            hd_invoice_id=None,
        )
        logger.warning("Invoice %s descartada por walk-in", rd_invoice.order_id)
        return

    # --- Contacto ---
    hd_contact = _sync_contact(convert_customer(rd_invoice.customer))
    if hd_contact is None or getattr(hd_contact, "id", None) is None:
        append_warning(
            message="failed: could not sync or find Holded contact",
            rd_invoice_id=str(rd_invoice.id),
            order_id=rd_invoice.order_id,
            hd_invoice_id=None,
        )
        logger.error("Invoice %s: contacto no disponible en Holded", rd_invoice.order_id)
        return

    # --- Buscar documento existente por número/cliente ---
    found = find_holded_invoice_by_number(hd, hd_contact, rd_invoice.order_id)

    # --- ¿Borrador o aprobado? ---
    if rd_invoice.ticket is not None:
        draft = any(d.status not in CLOSED_STATUS_LIST for d in rd_invoice.ticket.devices)
    else:
        draft = bool(rebu)  # REBU en borrador, resto aprobado

    converted_hd_invoice = convert_document(holded.DocumentType.INVOICE, rd_invoice, hd_contact)

    # Helper: pagar según RD y, si RD=PAID, cerrar por diferencia exacta RD
    def _apply_payments_and_fix_with_rd(invoice_id: str):
        # 1) Pagos de RD (exactos, sin tolerancia)
        for payment in converted_hd_invoice.payments:
            hd.pay_document(converted_hd_invoice.type, invoice_id, payment)
            logger.info("Payed %s for invoice %s", payment.amount, rd_invoice.order_id)

        # 2) Si RD está pagada pero la suma de pagos RD no alcanza el total RD por céntimos,
        #    añadimos un pago de ajuste de la diferencia exacta (máx 0,05 €)
        if rd_invoice.status == repairdesk.InvoiceStatus.PAID:
            total_rd = rd_invoice.total
            paid_rd = sum((p.amount for p in converted_hd_invoice.payments), Decimal("0"))
            diff = (total_rd - paid_rd).quantize(Decimal("0.01"))
            if diff > Decimal("0.00"):
                if diff <= Decimal("0.05"):
                    fix = holded.Payment(
                        date=datetime.now(),
                        desc="Ajuste redondeo (auto)",
                        amount=diff,
                    )
                    hd.pay_document(converted_hd_invoice.type, invoice_id, fix)
                    logger.info(
                        "Applied RD rounding fix %s to invoice %s", diff, rd_invoice.order_id
                    )
                else:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=invoice_id,
                        message=f"RD says PAID but RD payments miss {diff} (>0.05)",
                    )

    # --- Ya existe: comprobar y sincronizar ---
    if found is not None:
        logger.debug("\tHolded invoice found, id: %s", found.id)
        mismatch = False
        reason = ""

        # Total
        if abs(rd_invoice.total - found.total) > TOL:
            reason = f"total mismatch RD:{rd_invoice.total} HD:{found.total}"
            mismatch = True
        else:
            # Línea a línea con tolerancia de 0,01
            for rd_item, hd_item in itertools.zip_longest(rd_invoice.items, found.items):
                if rd_item is None or hd_item is None:
                    missing = rd_item.name if rd_item is not None else hd_item.name
                    reason = f"missing item {missing}"
                    mismatch = True
                    break

                assert rd_item.price is not None
                assert rd_item.tax is not None

                rd_unit = rd_item.total / rd_item.quantity
                hd_unit = hd_item.subtotal * (1 + hd_item.tax_percentage / 100)
                if abs(rd_unit - hd_unit) > TOL:
                    reason = f"item price mismatch {rd_item.name}; RD:{rd_unit} HD:{hd_unit}"
                    mismatch = True
                    break

        if mismatch:
            logger.info("Invoice %s is unsynced, reason: %s", rd_invoice.order_id, reason)
            try:
                hd.delete_document(found)
                new_id = hd.create_document(converted_hd_invoice, draft=draft)
                _apply_payments_and_fix_with_rd(new_id)
                if draft is False and CONFIG.get("send_email", False):
                    assert isinstance(converted_hd_invoice.buyer, holded.Contact)
                    send_to = converted_hd_invoice.buyer.email
                    if send_to:
                        hd.send_document(converted_hd_invoice.type, new_id, send_to)
            except holded.ApiError:
                append_warning(
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=found.id,
                    rd_invoice_id=str(rd_invoice.id),
                    message="approved document is mismatched",
                )
        else:
            # Sin cambios de líneas: sincronizamos pagos que falten y aplicamos ajuste si procede
            for rd_payment, hd_payment in itertools.zip_longest(
                sorted(rd_invoice.payments, key=lambda p: p.date),
                sorted(found.payments, key=lambda p: p.date),
            ):
                if hd_payment is None and rd_payment is not None:
                    hd.pay_document(found.type, found.id, convert_payment(rd_payment))
                    logger.info("Payed %s for invoice %s", rd_payment.amount, found.number)
                elif rd_payment is None and hd_payment is not None:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="missing payments in RepairDesk (payments deleted?)",
                    )
                elif rd_payment and hd_payment and abs(rd_payment.amount - hd_payment.amount) > TOL:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="mismatched payment amount between Holded and RepairDesk",
                    )

            # Ajuste final con datos RD
            _apply_payments_and_fix_with_rd(found.id)

    # --- No existe: crear (aprobada/borrador) ---
    else:
        try:
            new_id = hd.create_document(converted_hd_invoice, draft=draft)
            logger.info("Created %s %s", "DRAFT" if draft else "invoice", rd_invoice.order_id)
            _apply_payments_and_fix_with_rd(new_id)
            if draft is False and CONFIG.get("send_email", False):
                assert isinstance(converted_hd_invoice.buyer, holded.Contact)
                send_to = converted_hd_invoice.buyer.email
                if send_to:
                    hd.send_document(converted_hd_invoice.type, new_id, send_to)

            if draft and rebu:
                append_warning(
                    message="REBU invoice (created as draft)",
                    rd_invoice_id=str(rd_invoice.id),
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=new_id,
                )
            elif (
                draft
                and rd_invoice.ticket is not None
                and (datetime.now() - rd_invoice.ticket.created_date) > timedelta(days=30)
            ):
                append_warning(
                    message="associated ticket > 30 days (draft created)",
                    hd_invoice_id=new_id,
                    rd_invoice_id=str(rd_invoice.id),
                    order_id=rd_invoice.order_id,
                )
        except holded.ApiError as e:
            logger.error("Error creando documento en Holded: %s", e)
            append_warning(
                message=f"Holded API error while creating document: {e}",
                rd_invoice_id=str(rd_invoice.id),
                order_id=rd_invoice.order_id,
                hd_invoice_id=None,
            )


# ---------- lotes de sincronización ----------
def sync_new_invoices(exit_event: threading.Event):
    logger.debug("Syncing new invoices")
    try:
        invoices_hd = hd.list_documents(
            type=holded.DocumentType.INVOICE,
            sort=holded.DocumentSort.CREATED_DESCENDING,
        )
    except Exception as e:
        logger.error("Error listando facturas en Holded: %s", e)
        invoices_hd = []

    # Si no hay facturas en Holded, nos vamos 90 días atrás
    if not invoices_hd:
        from_dt = datetime.now() - timedelta(days=90)
        logger.info("No hay facturas en Holded; se sincroniza desde %s", from_dt)
    else:
        last_invoice = sorted(
            filter(lambda i: i.status != holded.DocumentStatus.CANCELED, invoices_hd),
            key=lambda d: from_numbering_series(d.number if d.number is not None else "0"),
            reverse=True,
        )[0]
        from_dt = last_invoice.date
        logger.info(
            "Última factura en Holded fecha=%s número=%s", last_invoice.date, last_invoice.number
        )

    # Pedimos RD desde from_dt hasta ahora
    for invoice in reversed(
        rd.invoices(from_date=from_dt, to_date=datetime.now(), page_size=10000)
    ):
        if exit_event.is_set():
            break
        inv_full = rd.invoice_by_id(invoice.id)
        _sync_invoice(inv_full)


def sync_last_invoices(exit_event: threading.Event, time_before: timedelta):
    from_date = max(
        datetime.fromtimestamp(CONFIG.get("only_sync_later_than", 0)),
        datetime.now() - time_before,
    )
    logger.debug("Checking invoices up to %s", from_date)

    invoices = rd.invoices(from_date=from_date, page_size=10000)
    for idx, invoice in enumerate(reversed(invoices)):
        if exit_event.is_set():
            logger.warning(
                "Shutting down in the middle of an invoice check, %s/%s", idx, len(invoices)
            )
            break
        _sync_invoice(rd.invoice_by_id(invoice.id))
