# Contains functions to convert from RepairDesk types into Holded ones

import holded
import repairdesk
import json
from decimal import Decimal


# TODO: reading config twice is kind of bad...
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))


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


def convert_document(
    type: holded.DocumentType, rd_invoice: repairdesk.Invoice, hd_contact: holded.Contact
) -> holded.Document:
    return holded.Document(
        type=type,
        id=None,
        number=into_numbering_series(int(rd_invoice.order_id)),
        date=rd_invoice.date,
        buyer=hd_contact,
        items=list(map(convert_item, rd_invoice.items)),
        numbering_series_id=CONFIG["num_series_id"][type.value],
        notes=rd_invoice.notes,
        custom_fields={
            "RepairDesk-Invoice-Id": str(rd_invoice.id)
        },  # Currently not working as current plan does not allow for custom fields
        paid=None,
        pending=None,
    )


def into_numbering_series(id: int) -> str:
    return "RD{0:05}".format(id)


def from_numbering_series(id: str) -> int:
    return int(id.lstrip("RD"))


def convert_tax_class(id: str | None) -> str | None:
    if id == 0 or id is None:
        return None

    return CONFIG["tax_classes"][str(id)]


def convert_item(item: repairdesk.Item) -> holded.Item:
    # We must fix prices because RepairDesk rounding results in wrong tax amounts
    subtotal = item.total / (1 + item.tax_percent / 100) / item.quantity

    tax_class = convert_tax_class(item.tax_class)

    return holded.Item(
        name=item.name,
        subtotal=subtotal,
        units=item.quantity,
        discount=Decimal(0),
        tax_percentage=item.tax_percent,
        taxes=[tax_class] if tax_class is not None else [],
    )


def convert_payment(payment: repairdesk.Payment) -> holded.Payment:
    return holded.Payment(
        date=payment.date, desc=payment.method + "\n\n" + payment.notes, amount=payment.amount
    )
