import logging
import itertools
from repairdesk import RepairDesk
import repairdesk
from holded import Holded
import holded
from datetime import datetime, timedelta
import json
import os
from .utils import (
    convert_customer,
    convert_document,
    from_numbering_series,
    convert_payment,
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
def _sync_invoice(rd_invoice: repairdesk.Invoice, hd_contact: holded.Contact):
    # This is an awful method for finding invoices but there's no better way
    # timestamp cannot be used as holded returns created timestamp on their end
    found = next(
        filter(
            lambda hd_invoice: rd_invoice.order_id == hd_invoice.number,
            hd.list_documents(
                type=holded.DocumentType.INVOICE,
                contact_id=hd_contact.id,
                sort=holded.DocumentSort.CREATED_DESCENDING,
            ),
        ),
        None,
    )
    # TODO: Check for any rectified
    # TODO: Check if invoice was not found because of paging or limits (order id is behind current one)

    converted_hd_invoice = convert_document(holded.DocumentType.INVOICE, rd_invoice, hd_contact)

    # Invoice already exists (check changes and sync if needed)
    if found is not None:
        # TODO: copy / merge code from (partially) unpaid

        # First we sync payments as approved documents still allow adding payments
        for rd_payment, hd_payment in itertools.zip_longest(
            sorted(rd_invoice.payments, key=lambda p: p.date),
            sorted(converted_hd_invoice.payments, key=lambda p: p.date),
        ):
            # Missing payments, just need to pay
            if hd_payment is None:
                hd.pay_document(found.type, found.id, convert_payment(rd_payment))
            # Payment has been deleted from RepairDesk, ask for manual sync
            elif rd_payment is None:
                # TODO: raise error
                pass
            # Payments do not match, ask for manual sync
            elif rd_payment.amount != hd_payment.amount:
                # TODO: raise error
                pass

        # Document updates can fail if already approved
        try:
            mismatch = False
            if rd_invoice.total != found.total
                pass
        # TODO: Approved document exception
        # Document is already approved, so we must rectify and create a new invoice
        except:
            pass

    # Invoice doesn't exist (create)
    else:
        id = hd.create_document(converted_hd_invoice)
        logger.info("Created invoice {}".format(rd_invoice.order_id))

        for payment in converted_hd_invoice.payments:
            hd.pay_document(converted_hd_invoice.type, id, payment)
            logger.info(
                "Payed invoice {} with amount {}".format(rd_invoice.order_id, payment.amount)
            )


def sync_new_invoices():
    logger.debug("Syncing new invoices")
    # TODO: This assumes last created invoice is the newest (latest order id)
    # TODO: credit notes should also be fetched and biggest order id one should be taken

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
        contact = _sync_contact(convert_customer(invoice.customer))

        _sync_invoice(invoice, contact)

    # TODO: maybe manage refunds?
    # for invoice in reversed(
    #     rd.invoices(from_date=last_invoice.date - timedelta(seconds=1), to_date=datetime.now())
    # ):
    #     if invoice.order_id <= last_invoice.number:
    #         continue

    #     invoice = rd.invoice_by_id(invoice.id)
    #     contact = sync_contact(convert_customer(invoice.customer))

    #     items = list(map(convert_item, invoice.items))

    #     hd_invoice = holded.Invoice(
    #         id=None,
    #         number=invoice.order_id,
    #         date=invoice.date,
    #         buyer=contact,
    #         items=items,
    #         notes=invoice.notes,
    #         custom_fields={
    #             "RepairDesk-Invoice-Id": str(invoice.id)
    #         },  # Currently not working as current plan does not allow for custom fields
    #         paid=None,
    #         pending=None,
    #     )
    #     logger.info("Creating invoice {}".format(invoice.order_id))
    #     id = hd.create_document(hd_invoice)

    #     for payment in invoice.payments:
    #         logger.info(
    #             "Paying invoice {} with amount {} (id: {})".format(
    #                 hd_invoice.number, payment.amount, payment.id
    #             )
    #         )
    #         hd.pay_invoice(id, convert_payment(payment))


# def sync_unpaid_invoices():
#     logger.debug("Syncing unpaid invoices")
#     # Unpaid invoices
#     for invoice in hd.list_invoices(sort="created-asc", paid=0):
#         # Repairdesk invoice with same ID
#         rd_invoice = next(
#             filter(lambda i: i.order_id == invoice.number, rd.invoices(keyword=invoice.number)),
#             None,
#         )

#         if rd_invoice is not None:
#             rd_invoice = rd.invoice_by_id(rd_invoice.id)
#             for payment in rd_invoice.payments:
#                 logger.info(
#                     "Paying invoice {} with amount {}".format(invoice.number, payment.amount)
#                 )
#                 hd.pay_invoice(invoice.id, convert_payment(payment))
#         else:
#             logger.warning(
#                 "An unpaid invoice ({}) has no counterpart in RepairDesk".format(invoice.number)
#             )

#     # Partially paid invoices
#     for invoice in hd.list_invoices(sort="created-asc", paid=2):
#         # Repairdesk invoice with same ID
#         rd_invoice = next(
#             filter(lambda i: i.order_id == invoice.number, rd.invoices(keyword=invoice.number)),
#             None,
#         )

#         if rd_invoice is not None:
#             rd_invoice = rd.invoice_by_id(rd_invoice.id)
#             logger.debug("Checking partially unpaid invoice {}".format(rd_invoice.order_id))

#             total = 0
#             logger.debug("\t payments {}".format(rd_invoice.payments))
#             # We assume payments can't be deleted or edited
#             for payment in sorted(rd_invoice.payments, key=lambda p: p.date):
#                 if total > invoice.paid:
#                     logger.error("Payments in invoice {} do not match!", rd_invoice.order_id)
#                 elif total == invoice.paid:
#                     logger.info(
#                         "Paying invoice {} with amount {}".format(invoice.number, payment.amount)
#                     )
#                     hd.pay_invoice(invoice.id, convert_payment(payment))
#                     total += payment.amount
#                     invoice.paid += payment.amount
#                 else:
#                     total += payment.amount

#             logger.debug("\t total: {}".format(total))
#             logger.debug("\t pending: {}".format(invoice.pending))

#         else:
#             logger.warning(
#                 "A partially paid invoice ({}) has no counterpart in RepairDesk".format(
#                     invoice.number
#                 )
#             )
