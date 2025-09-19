import logging
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


# Finds (and updates if needed) a contact or creates it
def _sync_contact(contact: holded.Contact) -> holded.Contact:
    def _set_if_value(dst, src, attr) -> bool:
        val = getattr(src, attr, None)
        if val is not None and getattr(dst, attr, None) != val:
            setattr(dst, attr, val)
            return True
        return False

    def _maybe_set(obj, attr, val) -> bool:
        if val is not None and getattr(obj, attr, None) != val:
            setattr(obj, attr, val)
            return True
        return False

    def _merge_address(dst, src) -> bool:
        changed = False

       
        src_addresses = getattr(src, "addresses", None)
        if isinstance(src_addresses, list) and len(src_addresses) > 0:
            if getattr(dst, "addresses", None) != src_addresses:
                setattr(dst, "addresses", src_addresses)
                changed = True

       
        for fld in ("address", "city", "province", "zipcode", "postal_code", "country"):
            if hasattr(dst, fld) or hasattr(src, fld):
                if _set_if_value(dst, src, fld):
                    changed = True
        return changed

 
    try:
        need_email = getattr(contact, "email", None) in (None, "")
        need_addr = True
       
        flat_vals = [getattr(contact, f, None) for f in ("address", "city", "province", "zipcode", "postal_code", "country")]
        has_flat_addr = any(v not in (None, "") for v in flat_vals)
        has_list_addr = isinstance(getattr(contact, "addresses", None), list) and len(getattr(contact, "addresses", [])) > 0
        need_addr = not (has_flat_addr or has_list_addr)

        if (need_email or need_addr) and getattr(contact, "custom_id", None):
            
            rd_customer = None
            
            for fn in ("customer_by_id", "get_customer_by_id", "customer"):
                if hasattr(rd, fn):
                    try:
                        rd_customer = getattr(rd, fn)(contact.custom_id)
                        break
                    except Exception:
                        rd_customer = None
            if rd_customer is not None:
                # Email
                if need_email:
                    _maybe_set(contact, "email", getattr(rd_customer, "email", None))

                # Dirección: intentamos varias ubicaciones comunes en RD
                def pick(*paths):
                    for p in paths:
                        val = None
                        try:
                            val = getattr(rd_customer, p, None)
                        except Exception:
                            val = None
                        if val:
                            return val
                    return None

                
                billing = getattr(rd_customer, "billing_address", None) or {}
                shipping = getattr(rd_customer, "shipping_address", None) or {}

                street = pick("address") or getattr(billing, "address", None) or getattr(shipping, "address", None) or (billing.get("address") if isinstance(billing, dict) else None) or (shipping.get("address") if isinstance(shipping, dict) else None)
                city = pick("city") or getattr(billing, "city", None) or getattr(shipping, "city", None) or (billing.get("city") if isinstance(billing, dict) else None) or (shipping.get("city") if isinstance(shipping, dict) else None)
                province = pick("state") or getattr(billing, "state", None) or getattr(shipping, "state", None) or (billing.get("state") if isinstance(billing, dict) else None) or (shipping.get("state") if isinstance(shipping, dict) else None)
                zipcode = pick("zip") or getattr(billing, "zip", None) or getattr(shipping, "zip", None) or (billing.get("zip") if isinstance(billing, dict) else None) or (shipping.get("zip") if isinstance(shipping, dict) else None)
                country = pick("country") or getattr(billing, "country", None) or getattr(shipping, "country", None) or (billing.get("country") if isinstance(billing, dict) else None) or (shipping.get("country") if isinstance(shipping, dict) else None)

                
                if need_addr and hasattr(contact, "addresses"):
                    addr = {
                        "type": "billing",
                        "street": street,
                        "city": city,
                        "province": province,
                        "zip": zipcode,
                        "country": country,
                    }
                   
                    addr = {k: v for k, v in addr.items() if v not in (None, "")}
                    if addr:
                        contact.addresses = [addr]
                else:
                   
                    _maybe_set(contact, "address", street)
                    _maybe_set(contact, "city", city)
                    
                    _maybe_set(contact, "province", province)
                    _maybe_set(contact, "zipcode", zipcode)
                    _maybe_set(contact, "postal_code", zipcode)
                    _maybe_set(contact, "country", country)
    except Exception as _e:
       
        logger.debug(f"Could not enrich contact from RepairDesk: {type(_e).__name__}: {_e}")

 
    found = None
    if getattr(contact, "custom_id", None) is not None:
        found = hd.get_contact_by_custom_id(contact.custom_id)
    elif getattr(contact, "mobile", None) is not None:
        found = hd.get_contact_by_mobile(contact.mobile)

    if found is not None:
        changed = False
        for fld in ("name", "nif", "email", "mobile", "isperson"):
            if _set_if_value(found, contact, fld):
                changed = True
        if _merge_address(found, contact):
            changed = True

        if changed:
            logger.info(
                "Customer {} (id: {}) has been changed on RepairDesk, syncing changes".format(
                    contact.name, getattr(contact, "custom_id", None)
                )
            )
            contact.id = found.id
            hd.update_contact(found)
        return found

    logging.info("Creating new customer {} (id: {})".format(contact.name, contact.id))
    new_id = hd.create_contact(contact=contact)
    found = hd.get_contact_by_id(new_id)
    assert found is not None
    return found


# Creates or updates an invoice as needed
def _sync_invoice(rd_invoice: repairdesk.Invoice):
    logger.debug("Syncing invoice {}".format(rd_invoice.order_id))
    rebu = False

    # Sanity checks
    # Sum of item prices is not equal to invoice total (usually payments will later mismatch)
    if abs(sum(map(lambda i: i.total, rd_invoice.items)) - rd_invoice.total) > Decimal("0.001"):
        append_warning(
            message="failed sanity check: sum of item prices is not equal to invoice total price",
            rd_invoice_id=str(rd_invoice.id),
            order_id=rd_invoice.order_id,
            hd_invoice_id=None,
        )
        return
    # Invoice with Bienes Usados tax must not contain general IVA items
    if CONFIG["used_goods_tax_class"] in map(lambda i: i.tax_class, rd_invoice.items):
        for item in rd_invoice.items:
            if item.total != Decimal(0) and item.tax_class != CONFIG["used_goods_tax_class"]:
                append_warning(
                    message="failed sanity check: REBU invoice contains other items with non-zero price",
                    rd_invoice_id=str(rd_invoice.id),
                    order_id=rd_invoice.order_id,
                    hd_invoice_id=None,
                )
                return
        rebu = True
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
