# Contains functions to convert from RepairDesk types into Holded ones

from dataclasses import dataclass
import dataclasses
from datetime import timedelta
import holded
import repairdesk
import json
from decimal import Decimal
from server import warnings_lock
import logging
from uuid import uuid4
from server import Warning
import os

logger = logging.getLogger(__name__)

# Importing twice is pretty bad...
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))


# Paginates using timestamps to get all invoices
def find_holded_invoice_by_number(
    hd: holded.Holded, contact: holded.Contact, number: str
) -> holded.Document | None:
    # We assume sorting by created order sorts by the `date` field, which might not be true
    initial_search = list(
        filter(
            lambda i: i.status != holded.DocumentStatus.CANCELED,
            hd.list_documents(
                type=holded.DocumentType.INVOICE,
                contact_id=contact.id,
                sort=holded.DocumentSort.CREATED_DESCENDING,
            ),
        )
    )

    if len(initial_search) == 0:
        return None

    found = next(filter(lambda i: i.number == number, initial_search), None)
    if found is not None:
        return found
    else:
        oldest_invoice = sorted(initial_search, key=lambda i: i.date)[0]
        while (
            len(
                page := list(
                    filter(
                        lambda i: i.status != holded.DocumentStatus.CANCELED,
                        hd.list_documents(
                            type=holded.DocumentType.INVOICE,
                            contact_id=contact.id,
                            sort=holded.DocumentSort.CREATED_DESCENDING,
                            # TODO: Kind of an arbitrary amount of time to paginate, should be checked
                            start=oldest_invoice.date - timedelta(days=90),
                            end=oldest_invoice.date,
                        ),
                    )
                )
            )
            > 0
        ):
            oldest_invoice = sorted(page, key=lambda i: i.date)[0]
            found = next(filter(lambda i: i.number == number, page), None)
            if found is not None:
                return found


# Adds a warning to the web UI
def append_warning(
    message: str, order_id: str, hd_invoice_id: str | None, rd_invoice_id: str | None
):
    # TODO: if a invoice is already affected, stack messages
    with warnings_lock:
        # TODO: wrong paths are not handled...
        try:
            with open(CONFIG["data_dir"].rstrip("/") + "/warnings.json") as warn_file:
                warns = list(
                    map(
                        lambda w: Warning(
                            id=w["id"],
                            messages=w["messages"],
                            hd_invoice_id=w["hd_invoice_id"],
                            rd_invoice_id=w["rd_invoice_id"],
                            order_id=w["order_id"],
                        ),
                        json.load(warn_file),
                    )
                )
        except FileNotFoundError:
            warns = []

        # Find index of existing warning or None
        if hd_invoice_id is not None and rd_invoice_id is not None:
            idx = next(
                (
                    i
                    for i, w in enumerate(warns)
                    if (w.rd_invoice_id == rd_invoice_id) and (w.hd_invoice_id == hd_invoice_id)
                ),
                None,
            )
        elif hd_invoice_id is not None:
            idx = next(
                (i for i, w in enumerate(warns) if (w.hd_invoice_id == hd_invoice_id)),
                None,
            )
        elif rd_invoice_id is not None:
            idx = next(
                (i for i, w in enumerate(warns) if (w.rd_invoice_id == rd_invoice_id)),
                None,
            )
        else:
            idx = None

        if idx is not None:
            if message not in warns[idx].messages:
                warns[idx].messages.append(message)
        else:
            warns.append(
                Warning(
                    id=str(uuid4()),
                    messages=[message],
                    hd_invoice_id=hd_invoice_id,
                    rd_invoice_id=rd_invoice_id,
                    order_id=order_id,
                )
            )

        with open(CONFIG["data_dir"].rstrip("/") + "/warnings.json", "w") as warn_file:
            json.dump(list(map(dataclasses.asdict, warns)), warn_file)


# Converts a RepairDesk customer into a Holded contact
def convert_customer(customer: repairdesk.Customer) -> holded.Contact:
    customer.full_name = customer.full_name.strip()

    if customer.full_name == "CLIENTE SIN ALTA":
        nif = None
    elif customer.nif is not None and (customer.nif == "-" or len(customer.nif) == 0):
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

    isperson = not CONFIG["customer_group_is_business"][customer.customer_group_id]

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
        isperson=isperson,
    )


def convert_document(
    type: holded.DocumentType, rd_invoice: repairdesk.Invoice, hd_contact: holded.Contact
) -> holded.Document:
    return holded.Document(
        type=type,
        id=None,
        status=None,
        number=into_numbering_series(int(rd_invoice.order_id)),
        date=rd_invoice.date,
        buyer=hd_contact,
        items=list(map(convert_item, rd_invoice.items)),
        numbering_series_id=CONFIG["num_series_id"][type.value],
        notes=rd_invoice.notes,
        custom_fields=None,
        tags=[],
        payments=list(map(convert_payment, rd_invoice.payments)),
        paid=None,
        pending=None,
        # TODO: this can be calculated
        total=None,
    )


def into_numbering_series(id: int) -> str:
    return "{0:05}".format(id)


def from_numbering_series(id: str) -> int:
    return int(id)


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
        desc=item.notes,
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
