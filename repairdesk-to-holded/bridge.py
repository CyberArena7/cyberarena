import logging
from repairdesk import RepairDesk
import repairdesk
from holded import Holded
import holded
from datetime import datetime, timedelta
import json
import os

HOLDED_API_KEY = os.environ["HOLDED_API_KEY"]
REPAIRDESK_API_KEY = os.environ["REPAIRDESK_API_KEY"]
TAX_CLASSES = json.load(open("/etc/repairdesk-to-holded/tax-classes.json"))

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.FileHandler("/tmp/logs.txt"))


# Converts a RepairDesk customer into a Holded contact
def convert_customer(customer: repairdesk.Customer) -> holded.Contact:
    customer.full_name = customer.full_name.strip()

    if customer.full_name == "CLIENTE SIN ALTA":
        nif = None
    elif customer.nif == "-" or (customer.nif is not None and len(customer.nif) == 0):
        nif = None
    else:
        nif = customer.nif

    if customer.email == "":
        email = None
    else:
        email = customer.email.lower()

    if customer.mobile == "":
        mobile = None
    else:
        mobile = customer.mobile

    # TODO: differentiate between business and person
    return holded.Contact(
        id=None,
        custom_id=customer.id,
        name=customer.full_name,
        email=email,
        mobile=mobile,
        phone=None,
        nif=nif,
        type="client",
        isperson=True,
    )


# Finds (and updates if needed) a contact or creates it
def sync_contact(contact: holded.Contact) -> holded.Contact:
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
        id = hd.create_contact(contact=contact)
        found = hd.get_contact_by_id(id)
        assert found is not None

    return found


def convert_tax_class(id: str) -> str | None:
    if id == 0:
        return None

    return TAX_CLASSES[str(id)]


# We must fix prices because RepairDesk rounding is different from that of Holded
def convert_item(item: repairdesk.Item) -> holded.Item:
    subtotal = item.total / (1 + item.tax_percent / 100) / item.quantity

    return holded.Item(
        name=item.name,
        subtotal=subtotal,
        units=item.quantity,
        discount=0,
        tax_percentage=item.tax_percent,
        taxes=list(filter(lambda t: t is not None, [convert_tax_class(item.tax_class)])),
    )


def convert_payment(payment: repairdesk.Payment) -> holded.Payment:
    return holded.Payment(
        date=payment.date, desc=payment.method + "\n\n" + payment.notes, amount=payment.amount
    )


rd = RepairDesk(REPAIRDESK_API_KEY)
hd = Holded(HOLDED_API_KEY)


def sync_new_invoices():
    logger.debug("Syncing new invoices")
    last_invoice = hd.list_invoices(sort="created-desc")[0]

    # TODO: maybe manage refunds?
    for invoice in reversed(
        rd.invoices(from_date=last_invoice.date - timedelta(seconds=1), to_date=datetime.now())
    ):
        if invoice.order_id <= last_invoice.number:
            continue

        invoice = rd.invoice_by_id(invoice.id)
        contact = sync_contact(convert_customer(invoice.customer))

        items = list(map(convert_item, invoice.items))

        hd_invoice = holded.Invoice(
            id=None,
            number=invoice.order_id,
            date=invoice.date,
            buyer=contact,
            items=items,
            notes=invoice.notes,
            custom_fields={
                "RepairDesk-Invoice-Id": str(invoice.id)
            },  # Currently not working as current plan does not allow for custom fields
            paid=None,
            pending=None,
        )
        logger.info("Creating invoice {}".format(invoice.order_id))
        id = hd.create_invoice(hd_invoice)

        for payment in invoice.payments:
            logger.info(
                "Paying invoice {} with amount {} (id: {})".format(
                    hd_invoice.number, payment.amount, payment.id
                )
            )
            hd.pay_invoice(id, convert_payment(payment))


def sync_unpaid_invoices():
    logger.debug("Syncing unpaid invoices")
    # Unpaid invoices
    for invoice in hd.list_invoices(sort="created-asc", paid=0):
        # Repairdesk invoice with same ID
        rd_invoice = next(
            filter(lambda i: i.order_id == invoice.number, rd.invoices(keyword=invoice.number)),
            None,
        )

        if rd_invoice is not None:
            rd_invoice = rd.invoice_by_id(rd_invoice.id)
            for payment in rd_invoice.payments:
                logger.info(
                    "Paying invoice {} with amount {}".format(invoice.number, payment.amount)
                )
                hd.pay_invoice(invoice.id, convert_payment(payment))
        else:
            logger.warning(
                "An unpaid invoice ({}) has no counterpart in RepairDesk".format(invoice.number)
            )

    # Partially paid invoices
    for invoice in hd.list_invoices(sort="created-asc", paid=2):
        # Repairdesk invoice with same ID
        rd_invoice = next(
            filter(lambda i: i.order_id == invoice.number, rd.invoices(keyword=invoice.number)),
            None,
        )

        if rd_invoice is not None:
            rd_invoice = rd.invoice_by_id(rd_invoice.id)
            logger.debug("Checking partially unpaid invoice {}".format(rd_invoice.order_id))

            total = 0
            logger.debug("\t payments {}".format(rd_invoice.payments))
            # We assume payments can't be deleted or edited
            for payment in sorted(rd_invoice.payments, key=lambda p: p.date):
                if total > invoice.paid:
                    logger.error("Payments in invoice {} do not match!", rd_invoice.order_id)
                elif total == invoice.paid:
                    logger.info(
                        "Paying invoice {} with amount {}".format(invoice.number, payment.amount)
                    )
                    hd.pay_invoice(invoice.id, convert_payment(payment))
                    total += payment.amount
                    invoice.paid += payment.amount
                else:
                    total += payment.amount

            logger.debug("\t total: {}".format(total))
            logger.debug("\t pending: {}".format(invoice.pending))

        else:
            logger.warning(
                "A partially paid invoice ({}) has no counterpart in RepairDesk".format(
                    invoice.number
                )
            )
