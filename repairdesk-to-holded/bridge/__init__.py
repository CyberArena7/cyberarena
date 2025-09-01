import logging
from decimal import Decimal
import itertools
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
    from_numbering_series,
    convert_payment,
    Warning,
)


HOLDED_API_KEY = os.environ["HOLDED_API_KEY"]
REPAIRDESK_API_KEY = os.environ["REPAIRDESK_API_KEY"]
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.FileHandler("/tmp/logs.txt"))


rd = RepairDesk(REPAIRDESK_API_KEY)
hd = Holded(HOLDED_API_KEY)

# Contains the name of all ticket statuses in the "Closed" category
CLOSED_STATUS_LIST = list(
    map(lambda s: s.name, filter(lambda s: s.type == "Closed", rd.ticket_statuses()))
)


# Finds (and updates if needed) a contact or creates it
def _sync_contact(contact: holded.Contact) -> holded.Contact:
    found = None

    if contact.custom_id is not None:
        found = hd.get_contact_by_custom_id(contact.custom_id)
    elif found is None and contact.mobile is not None:
        found = hd.get_contact_by_mobile(contact.mobile)

    if found is not None:
        if (
            contact.name != found.name
            or contact.nif != found.nif
            or contact.email != found.email
            or contact.mobile != found.mobile
            or contact.isperson != found.isperson
        ):
            logger.info(
                "Customer {} (id: {}) has been changed on RepairDesk, syncing changes".format(
                    contact.name, contact.custom_id
                )
            )
            contact.id = found.id
            hd.update_contact(contact)
            found = contact
    else:
        logging.info("Creating new customer {} (id: {})".format(contact.name, contact.id))
        id = hd.create_contact(contact=contact)
        found = hd.get_contact_by_id(id)
        assert found is not None

    return found


# Creates or updates an invoice as needed
def _sync_invoice(rd_invoice: repairdesk.Invoice):
    hd_contact = _sync_contact(convert_customer(rd_invoice.customer))

    contact_invoices = sorted(
        hd.list_documents(
            type=holded.DocumentType.INVOICE,
            contact_id=hd_contact.id,
            sort=holded.DocumentSort.CREATED_DESCENDING,
        ),
        key=lambda i: int(i.number if i.number is not None else "0"),
        reverse=True,
    )

    # This is an awful method for finding invoices but there's no better way
    # timestamp cannot be used as holded returns created timestamp on their end
    found = next(
        filter(
            lambda hd_invoice: rd_invoice.order_id == hd_invoice.number,
            contact_invoices,
        ),
        None,
    )
    # TODO: Check if invoice was not found because of paging or limits (order id is behind current one)
    # TODO: whether document should be instantly approved or not (no ticket associated)

    if rd_invoice.ticket is not None:
        logger.debug("Invoice: {}; {}".format(rd_invoice.id, rd_invoice.ticket))
        draft = False
        for device in rd_invoice.ticket.devices:
            if device.status not in CLOSED_STATUS_LIST:
                draft = True
                break
    else:
        draft = False

    converted_hd_invoice = convert_document(holded.DocumentType.INVOICE, rd_invoice, hd_contact)

    # Invoice already exists (check changes and sync if needed)
    if found is not None:
        mismatch = False
        reason = ""
        if (rd_invoice.total - found.total) > Decimal("0.001"):
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
                    logger.debug("missing item {}", found.number)
                    break

                # TODO: change types of repairdesk to only have Decimal
                assert rd_item.price is not None
                assert rd_item.tax is not None

                # Not checking exactly because imprecisions are very likely to occur
                if (rd_item.price + rd_item.tax) - (
                    hd_item.subtotal * (1 + hd_item.tax_percentage / 100)
                ) > Decimal("0.001"):
                    reason = (
                        "price mismatch on individual item {}; RepairDesk: {}, Holded: {}".format(
                            rd_item.name,
                            rd_item.price + rd_item.tax,
                            hd_item.subtotal * (1 + hd_item.tax_percentage / 100),
                        )
                    )
                    mismatch = True
                    logger.debug("price mismatch {}", found.number)
                    break

        if mismatch:
            logger.info("Invoice {} is unsynced, reason: {}".format(rd_invoice.order_id, reason))
            try:
                hd.delete_document(found)
                new_id = hd.create_document(converted_hd_invoice)
                for payment in converted_hd_invoice.payments:
                    hd.pay_document(converted_hd_invoice.type, new_id, payment)
            # TODO: Holded exception
            except Exception as e:
                # TODO: Create rectificative, this requires following the chain of related documents
                # which is not well defined through API so this is out of scope

                append_warning(
                    Warning(
                        hd_invoice_id=found.id,
                        rd_invoice_id=str(rd_invoice.id),
                        messages=["approved document is mismatched"],
                    )
                )
                raise e

        # First we sync payments as approved documents still allow adding payments
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
                    Warning(
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        messages=["missing payments in RepairDesk (payments deleted?)"],
                    )
                )
            # Payments do not match, ask for manual sync
            elif rd_payment.amount != hd_payment.amount:
                append_warning(
                    Warning(
                        rd_invoice_id=str(rd_invoice.id),
                        hd_invoice_id=found.id,
                        messages=["mismatched payment amount between Holded and RepairDesk"],
                    )
                )
                pass

    # Invoice doesn't exist (create)
    else:
        id = hd.create_document(converted_hd_invoice)
        logger.info("Created invoice {}, draft: {}".format(rd_invoice.order_id, draft))

        for payment in converted_hd_invoice.payments:
            hd.pay_document(converted_hd_invoice.type, id, payment)
            logger.info(
                "Payed invoice {} with amount {}".format(rd_invoice.order_id, payment.amount)
            )


def sync_new_invoices():
    logger.debug("Syncing new invoices")
    invoices = hd.list_documents(
        type=holded.DocumentType.INVOICE, sort=holded.DocumentSort.CREATED_DESCENDING
    )

    last_invoice = sorted(
        invoices,
        # Kind of a hacky solution, but eh
        key=lambda d: from_numbering_series(d.number if d.number is not None else "0"),
        reverse=True,
    )[0]

    # NOTE / Unfun fact: Holded floors dates to start of the day,
    # so we must set the page size to a sufficiently large number
    for invoice in reversed(
        rd.invoices(from_date=last_invoice.date, to_date=datetime.now(), page_size=10000)
    ):
        if int(invoice.order_id) <= from_numbering_series(last_invoice.number):
            continue

        invoice = rd.invoice_by_id(invoice.id)

        _sync_invoice(invoice)


# Syncs n invoices indiscriminately (check for updates)
def sync_last_invoices(page: int = 50):
    for invoice in reversed(rd.invoices(page_size=page)):
        _sync_invoice(rd.invoice_by_id(invoice.id))
