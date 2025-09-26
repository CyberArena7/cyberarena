# Contains functions to convert from RepairDesk types into Holded ones


from dataclasses import dataclass
import dataclasses
from datetime import datetime, timedelta
import holded
import repairdesk
import json
from decimal import Decimal
from server import warnings_lock
import logging
from uuid import uuid4
from server import Warning
import os
import re

logger = logging.getLogger(__name__)

# Importing twice is pretty bad...
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))

# ---------------------------------------------------------------------
# BÚSQUEDAS Y AVISOS
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# CONVERSORES RD -> HOLDED
# ---------------------------------------------------------------------


def convert_customer(customer: repairdesk.Customer) -> holded.Contact:
    full_name = (customer.full_name or "").strip()

    # NIF robusto
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

    billingAddress = holded.Address(
        address=customer.address,
        city=customer.city,
        postalCode=customer.postcode,
        province=customer.state,
        country=customer.country,
    )

    return holded.Contact(
        id=None,
        custom_id=customer.id,
        name=full_name,
        email=email,
        mobile=mobile,
        phone=None,
        nif=nif,
        type="client",
        isperson=isperson,
        billAddress=billingAddress,
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


# ---------------------------------------------------------------------
# HELPERS: CREAR DOCUMENTO Y CERRARLO CON PAGOS
# ---------------------------------------------------------------------


def _safe_decimal(x) -> Decimal:
    try:
        return Decimal(x)
    except Exception:
        return Decimal("0")


def _apply_payments_then_close(
    hd, doc_type, doc_id: str, rd_payments: list, rd_total: Decimal, tol: Decimal = Decimal("0.02")
):
    """
    Aplica todos los pagos de RD y, si la suma no llega al total de RD,
    registra un pago 'de ajuste' por la diferencia para que Holded quede Pagada.
    """
    total_rd = _safe_decimal(rd_total)
    suma = Decimal("0")

    # 1) aplicar pagos que vengan de RD
    for p in rd_payments or []:
        try:
            amount = _safe_decimal(getattr(p, "amount", 0))
            if amount.copy_abs() <= tol:
                logger.debug("Pago ~0 ignorado: %s", amount)
                continue
            fecha = getattr(p, "date", None)
            logger.info("Aplicando pago RD %s al doc %s", amount, doc_id)
            hd.pay_document(doc_type, doc_id, p)
            suma += amount
        except Exception as e:
            logger.warning("Pago RD no aplicado (%s): %s", getattr(p, "amount", None), e)

    # 2) si no llega al total RD, pagar la diferencia
    diferencia = (total_rd - suma).quantize(Decimal("0.01"))
    if diferencia > tol:
        try:
            logger.info(
                "Registrando pago de ajuste %s para cerrar doc %s (RD total=%s, sum pagos=%s)",
                diferencia,
                doc_id,
                total_rd,
                suma,
            )
            ajuste = holded.Payment(
                date=datetime.now(), desc="Ajuste sincronización", amount=diferencia
            )
            hd.pay_document(doc_type, doc_id, ajuste)
        except Exception as e:
            logger.warning(
                "No se pudo registrar el pago de ajuste %s en el doc %s: %s", diferencia, doc_id, e
            )


def create_document_and_close_with_rd_payments(
    hd, document, rd_total: Decimal, draft: bool = False, send_email: bool = False
):
    """
    Crea el documento en Holded y lo cierra con los pagos de RD.
    Si la suma de pagos no alcanza rd_total, añade un pago de ajuste.
    Devuelve el id del documento creado.
    """
    assert getattr(document, "buyer", None) is not None and getattr(document.buyer, "id", None), (
        "document.buyer.id requerido"
    )

    logger.debug(
        "Creando documento en Holded (draft=%s) para order %s",
        draft,
        getattr(document, "number", None),
    )
    doc_id = hd.create_document(document, draft=draft)

    # Aplica pagos y cierra
    _apply_payments_then_close(
        hd,
        document.type,
        doc_id,
        getattr(document, "payments", []),
        rd_total=rd_total,
    )

    # Enviar por email si procede y NO es borrador
    if (not draft) and send_email:
        try:
            buyer_email = getattr(document.buyer, "email", None)
            if buyer_email:
                logger.info("Enviando documento %s por email a %s", doc_id, buyer_email)
                hd.send_document(document.type, doc_id, buyer_email)
        except Exception as e:
            logger.warning("Fallo enviando documento %s por email: %s", doc_id, e)

    return doc_id
