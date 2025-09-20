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


def convert_customer(customer: repairdesk.Customer) -> holded.Contact:
    full_name = (customer.full_name or "").strip()

 
    def _norm_nif(v):
        if not v:
            return None
        v = str(v).strip().upper().replace(" ", "").replace("-", "")
        return v if v and v not in ("-", "NA", "N/A", "0") else None

    nif = _norm_nif(getattr(customer, "nif", None))
    if full_name == "CLIENTE SIN ALTA":
        nif = None

   
    email = (getattr(customer, "email", "") or "").strip().lower() or None
    mobile = (
        (getattr(customer, "mobile", "") or "").strip()
        or (getattr(customer, "phone", "") or "").strip()
        or None
    )

    isperson = not CONFIG["customer_group_is_business"][customer.customer_group_id]

  
    contact = holded.Contact(
        id=None,
        custom_id=customer.id,
        name=full_name,
        email=email,
        mobile=mobile,
        phone=None,
        nif=nif,
        type="client",
        isperson=isperson,
    )

    
    billing = getattr(customer, "billing_address", None) or {}
    shipping = getattr(customer, "shipping_address", None) or {}

    def _get(obj, key):
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key)
        return None

    street = (
        (getattr(customer, "address", None) or "").strip()
        or (str(_get(billing, "address") or "").strip())
        or (str(_get(shipping, "address") or "").strip())
        or None
    )
    city = (
        (getattr(customer, "city", None) or "").strip()
        or (str(_get(billing, "city") or "").strip())
        or (str(_get(shipping, "city") or "").strip())
        or None
    )
    province = (
        (getattr(customer, "state", None) or "").strip()
        or (str(_get(billing, "state") or "").strip())
        or (str(_get(shipping, "state") or "").strip())
        or None
    )
    zipcode = (
        (getattr(customer, "zip", None) or "").strip()
        or (str(_get(billing, "zip") or "").strip())
        or (str(_get(shipping, "zip") or "").strip())
        or None
    )
    country = (
        (getattr(customer, "country", None) or "").strip()
        or (str(_get(billing, "country") or "").strip())
        or (str(_get(shipping, "country") or "").strip())
        or None
    )

    
    if not zipcode and street:
        import re
        m = re.search(r"(\d{5})\b", street)
        if m:
            zipcode = m.group(1)

    if not city and street:
        parts = [p.strip() for p in street.split(",") if p.strip()]
        if len(parts) >= 2:
          
            city = parts[-2]

   
    addr = {
        "type": "billing",
        "street": street,
        "city": city,
        "province": province,
        "zip": zipcode,
        "country": country or "ES",
    }
    addr = {k: v for k, v in addr.items() if v}

    
    if addr and hasattr(contact, "addresses"):
        contact.addresses = [addr]

   
    if hasattr(contact, "address"):      contact.address = street
    if hasattr(contact, "city"):         contact.city = city
    if hasattr(contact, "province"):     contact.province = province
    if hasattr(contact, "zipcode"):      contact.zipcode = zipcode
    if hasattr(contact, "postal_code"):  contact.postal_code = zipcode
    if hasattr(contact, "country"):      contact.country = country or "ES"

    return contact


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
