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

def _sync_contact(contact: holded.Contact) -> holded.Contact:
    found = None

    if contact.custom_id is not None:
        found = hd.get_contact_by_custom_id(contact.custom_id)
    if found is None and getattr(contact, "mobile", None):
        found = hd.get_contact_by_mobile(contact.mobile)

    if found:
        need_update = (
            (contact.name or "") != (found.name or "")
            or (contact.nif or "") != (found.nif or "")
            or (contact.email or "") != (found.email or "")
            or (contact.mobile or "") != (found.mobile or "")
            or bool(getattr(contact, "isperson", False)) != bool(getattr(found, "isperson", False))
            or _addr_tuple(getattr(contact, "billing_address", None)) != _addr_tuple(getattr(found, "billing_address", None))
            or _addr_tuple(getattr(contact, "shipping_address", None)) != _addr_tuple(getattr(found, "shipping_address", None))
        )
        if not need_update:
            return found

        logger.info("Customer %s (%s) changed; syncing to Holded", contact.name, contact.custom_id)
        contact.id = found.id
        try:
            hd.update_contact(contact)
            return contact
        except Exception as e:
            logger.warning("Holded rechazó update_contact con dirección (%s). Reintentando sin dirección...", e)
            safe = dataclasses.replace(contact) if hasattr(dataclasses, "replace") else contact
            _strip_addr_fields(safe)
            hd.update_contact(safe)
            return safe

    # Crear nuevo
    logging.info("Creating new customer %s (id: %s)", contact.name, getattr(contact, "id", None))
    try:
        new_id = hd.create_contact(contact=contact)
    except Exception as e:
        logger.warning("Holded rechazó create_contact con dirección (%s). Reintentando sin dirección...", e)
        safe = dataclasses.replace(contact) if hasattr(dataclasses, "replace") else contact
        _strip_addr_fields(safe)
        new_id = hd.create_contact(contact=safe)

    created = hd.get_contact_by_id(new_id)
    assert created is not None
    return created

# Creates or updates an invoice as needed
def _sync_invoice(rd_invoice: repairdesk.Invoice):
    TOL = Decimal("0.01")  # tolerancia de 1 céntimo
    logger.debug("Syncing invoice %s", rd_invoice.order_id)
    rebu = False

    # --- Sanity checks ---
    # 1) Suma de líneas vs total
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

    # 2) REBU: si hay clase usados, no puede haber otras con precio != 0
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

    # 3) Walk-in no permitidos
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

    # --- Decidir si crear como borrador ---
    if rd_invoice.ticket is not None:
        draft = False
        for device in rd_invoice.ticket.devices:
            if device.status not in CLOSED_STATUS_LIST:
                draft = True
                break
    else:
        draft = not rebu  # sin ticket: si no es REBU, aprobada; si es REBU, borrador

    converted_hd_invoice = convert_document(holded.DocumentType.INVOICE, rd_invoice, hd_contact)

    # --- Ya existe: comprobar cambios ---
    if found is not None:
        logger.debug("\tHolded invoice encontrada, id: %s", found.id)
        mismatch = False
        reason = ""

        # Precio total
        if abs(rd_invoice.total - found.total) > TOL:
            reason = f"total mismatch RD:{rd_invoice.total} HD:{found.total}"
            mismatch = True
            logger.debug("Invoice %s %s", rd_invoice.order_id, reason)
        else:
            # Comparación por líneas con tolerancia
            for rd_item, hd_item in itertools.zip_longest(rd_invoice.items, found.items):
                if rd_item is None or hd_item is None:
                    missing = rd_item.name if rd_item is not None else hd_item.name
                    reason = f"missing item {missing}"
                    mismatch = True
                    logger.debug("\tMissing item: %s", missing)
                    break

                assert rd_item.price is not None
                assert rd_item.tax is not None

                rd_unit = (rd_item.total / rd_item.quantity)
                hd_unit = (hd_item.subtotal * (1 + hd_item.tax_percentage / 100))
                if abs(rd_unit - hd_unit) > TOL:
                    reason = f"item price mismatch {rd_item.name}; RD:{rd_unit} HD:{hd_unit}"
                    mismatch = True
                    logger.debug("\tPrice mismatch item %s", rd_item.name)
                    break

        if mismatch:
            logger.info("Invoice %s desincronizada: %s (se recrea)", rd_invoice.order_id, reason)
            try:
                hd.delete_document(found)
                new_id = hd.create_document(converted_hd_invoice, draft=draft)
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, new_id, payment)
                if draft is False and CONFIG.get("send_email", False):
                    assert type(converted_hd_invoice.buyer) is holded.Contact
                    send_to = converted_hd_invoice.buyer.email
                    if send_to:
                        hd.send_document(converted_hd_invoice.type, new_id, send_to)
            except holded.ApiError as e:
                append_warning(
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=found.id,
                    rd_invoice_id=str(rd_invoice.id),
                    message="approved document is mismatched",
                )
        else:
            # Sin cambios: sincronizar pagos con tolerancia
            for rd_payment, hd_payment in itertools.zip_longest(
                sorted(rd_invoice.payments, key=lambda p: p.date),
                sorted(found.payments, key=lambda p: p.date),
            ):
                if hd_payment is None:
                    hd.pay_document(found.type, found.id, convert_payment(rd_payment))
                    logger.info("Payed %s for invoice %s", rd_payment.amount, found.number)
                elif rd_payment is None:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="missing payments in RepairDesk (payments deleted?)",
                    )
                elif abs(rd_payment.amount - hd_payment.amount) > TOL:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="mismatched payment amount between Holded and RepairDesk",
                    )

    # --- No existe: crear (aprobada o borrador) ---
    else:
        try:
            if draft is False:
                id = hd.create_document(converted_hd_invoice, draft=False)
                logger.info("Created invoice %s (approved)", rd_invoice.order_id)
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, id, payment)
                    logger.info("Payed invoice %s amount %s", rd_invoice.order_id, payment.amount)
                if CONFIG.get("send_email", False):
                    assert type(converted_hd_invoice.buyer) is holded.Contact
                    send_to = converted_hd_invoice.buyer.email
                    if send_to:
                        hd.send_document(converted_hd_invoice.type, id, send_to)
            else:
                # ⚠️ Antes aquí NO se creaba nada si no estaba cerrado → ahora sí creamos BORRADOR
                id = hd.create_document(converted_hd_invoice, draft=True)
                logger.info("Created DRAFT invoice %s", rd_invoice.order_id)
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, id, payment)
                    logger.info("Payed draft invoice %s amount %s", rd_invoice.order_id, payment.amount)
                if rebu:
                    append_warning(
                        message="REBU invoice (created as draft)",
                        rd_invoice_id=str(rd_invoice.id),
                        order_id=rd_invoice.order_id,
                        hd_invoice_id=id,
                    )
                else:
                    if rd_invoice.ticket is not None and \
                       (datetime.now() - rd_invoice.ticket.created_date) > timedelta(days=30):
                        append_warning(
                            message="associated ticket > 30 days (draft created)",
                            hd_invoice_id=id,
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
    # Walkin customer invoices are not allowed
    if int(rd_invoice.customer.id) == 0:
        append_warning(
            message="failed sanity check: walkin customer invoices are not allowed",
            rd_invoice_id=str(rd_invoice.id),
            order_id=rd_invoice.order_id,
            hd_invoice_id=None,
        )
        return

    hd_contact = _sync_contact(convert_customer(rd_invoice.customer))

    # This is an awful method for finding invoices but there's no better way
    # timestamp cannot be used as holded returns created timestamp on their end
    found = find_holded_invoice_by_number(hd, hd_contact, rd_invoice.order_id)
    # TODO: Check if invoice was not found because of paging or limits (order id is behind current one)
    # TODO: whether document should be instantly approved or not (no ticket associated)

    if rd_invoice.ticket is not None:
        draft = False
        for device in rd_invoice.ticket.devices:
            if device.status not in CLOSED_STATUS_LIST:
                draft = True
                break
    else:
        if not rebu:
            draft = False
        # REBU invoices must have a seat created manually
        else:
            draft = True

    converted_hd_invoice = convert_document(holded.DocumentType.INVOICE, rd_invoice, hd_contact)

    # Invoice already exists (check changes and sync if needed)
    if found is not None:
        logger.debug("\tholded invoice found, id: {}".format(found.id))
        mismatch = False
        reason = ""
        if abs(rd_invoice.total - found.total) > Decimal("0.001"):
            reason = "total prices do not match, RepairDesk: {}, Holded: {}".format(
                rd_invoice.total, found.total
            )
            logger.debug("Invoice {} total price does not match".format(rd_invoice.order_id))
            mismatch = True
        else:
            for rd_item, hd_item in itertools.zip_longest(rd_invoice.items, found.items):
                if rd_item is None or hd_item is None:
                    if rd_item is not None:
                        missing = rd_item.name
                    else:
                        missing = hd_item.name

                    reason = "missing item {}".format(missing)
                    mismatch = True
                    logger.debug("\tmissing item {}".format(found.number))
                    break

                # TODO: change types of repairdesk to only have Decimal
                assert rd_item.price is not None
                assert rd_item.tax is not None

                # Not checking exactly because imprecisions are very likely to occur
                if abs(
                    (rd_item.total / rd_item.quantity)
                    - (hd_item.subtotal * (1 + hd_item.tax_percentage / 100))
                ) > Decimal("0.001"):
                    reason = (
                        "price mismatch on individual item {}; RepairDesk: {}, Holded: {}".format(
                            rd_item.name,
                            rd_item.total / rd_item.quantity,
                            hd_item.subtotal * (1 + hd_item.tax_percentage / 100),
                        )
                    )
                    mismatch = True
                    logger.debug("\tprice mismatch {}".format(found.number))
                    break

        if mismatch:
            logger.info("Invoice {} is unsynced, reason: {}".format(rd_invoice.order_id, reason))
            try:
                hd.delete_document(found)
                new_id = hd.create_document(converted_hd_invoice, draft=draft)
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, new_id, payment)
                if draft is False and CONFIG["send_email"]:
                    assert type(converted_hd_invoice.buyer) is holded.Contact
                    send_to = converted_hd_invoice.buyer.email
                    if send_to is not None:
                        hd.send_document(converted_hd_invoice.type, new_id, send_to)
            except holded.ApiError as e:
                # TODO: Create rectificative, this requires following the chain of related documents
                # which is not well defined through API so this is out of scope

                append_warning(
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=found.id,
                    rd_invoice_id=str(rd_invoice.id),
                    message="approved document is mismatched",
                )
        else:
            # Sync payments
            for rd_payment, hd_payment in itertools.zip_longest(
                sorted(rd_invoice.payments, key=lambda p: p.date),
                sorted(found.payments, key=lambda p: p.date),
            ):
                # Missing payments, just need to pay
                if hd_payment is None:
                    hd.pay_document(found.type, found.id, convert_payment(rd_payment))
                    logger.info("Payed {} for invoice {}".format(rd_payment.amount, found.number))
                # Payment has been deleted from RepairDesk, ask for manual sync
                elif rd_payment is None:
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="missing payments in RepairDesk (payments deleted?)",
                    )
                # Payments do not match, ask for manual sync
                elif abs(rd_payment.amount - hd_payment.amount) > Decimal("0.001"):
                    append_warning(
                        order_id=rd_invoice.order_id,
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        message="mismatched payment amount between Holded and RepairDesk",
                    )

    # Invoice doesn't exist (create)
    else:
        if draft is False:
            id = hd.create_document(converted_hd_invoice, draft=draft)
            logger.info("Created invoice {}".format(rd_invoice.order_id))

            for payment in converted_hd_invoice.payments:
                hd.pay_document(converted_hd_invoice.type, id, payment)
                logger.info(
                    "Payed invoice {} with amount {}".format(rd_invoice.order_id, payment.amount)
                )
            if CONFIG["send_email"]:
                assert type(converted_hd_invoice.buyer) is holded.Contact
                send_to = converted_hd_invoice.buyer.email
                if send_to is not None:
                    hd.send_document(converted_hd_invoice.type, id, send_to)
        else:
            if rebu:
                id = hd.create_document(converted_hd_invoice, draft=True)
                logger.info("Created draft {}".format(rd_invoice.order_id))
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, id, payment)
                    logger.info(
                        "Payed invoice {} with amount {}".format(
                            rd_invoice.order_id, payment.amount
                        )
                    )
                append_warning(
                    message="REBU invoice",
                    rd_invoice_id=str(rd_invoice.id),
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=id,
                )

            else:
                assert rd_invoice.ticket is not None
                if (datetime.now() - rd_invoice.ticket.created_date) > timedelta(days=30):
                    append_warning(
                        message="associated ticket is over 1 month old",
                        hd_invoice_id=None,
                        rd_invoice_id=str(rd_invoice.id),
                        order_id=rd_invoice.order_id,
                    )
                logger.debug("\thas associated ticket and is not finished, not syncing")


def sync_new_invoices(exit_event: threading.Event):
    logger.debug("Syncing new invoices")
    invoices = hd.list_documents(
        type=holded.DocumentType.INVOICE, sort=holded.DocumentSort.CREATED_DESCENDING
    )

    last_invoice = sorted(
        filter(lambda i: i.status != holded.DocumentStatus.CANCELED, invoices),
        # Kind of a hacky solution, but eh
        key=lambda d: from_numbering_series(d.number if d.number is not None else "0"),
        reverse=True,
    )[0]

    # NOTE / Unfun fact: Holded floors dates to start of the day,
    # so we must set the page size to a sufficiently large number
    for invoice in reversed(
        rd.invoices(from_date=last_invoice.date, to_date=datetime.now(), page_size=10000)
    ):
        if exit_event.is_set():
            break
        if int(invoice.order_id) <= from_numbering_series(last_invoice.number):
            continue

        invoice = rd.invoice_by_id(invoice.id)

        _sync_invoice(invoice)


# Syncs all invoice in the last `time_before` time
def sync_last_invoices(exit_event: threading.Event, time_before: timedelta):
    from_date = max(
        datetime.fromtimestamp(CONFIG["only_sync_later_than"])
        if "only_sync_later_than" in CONFIG.keys()
        else datetime.fromtimestamp(0),
        datetime.now() - time_before,
    )
    logger.debug("Checking invoices up to {}".format(from_date))

    # TODO: do proper paging please...
    invoices = rd.invoices(from_date=from_date, page_size=10000)

    for idx, invoice in enumerate(reversed(invoices)):
        if exit_event.is_set():
            logger.warning(
                "Shutting down in the middle of an invoice check, {}/{}".format(idx, len(invoices))
            )
            break
        _sync_invoice(rd.invoice_by_id(invoice.id))
