from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any
import requests
from datetime import datetime
from time import sleep
from decimal import Decimal

BASE_URL = "https://api.holded.com/api/invoicing/v1"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@dataclass
class ApiError(Exception):
    info: str


@dataclass
class Contact:
    id: str | None
    custom_id: str | None
    name: str
    nif: str | None
    email: str | None
    phone: str | None
    mobile: str | None
    type: str
    isperson: bool


@dataclass
class Item:
    name: str
    desc: str | None
    units: int
    subtotal: Decimal
    discount: Decimal
    tax_percentage: Decimal
    taxes: list[str]


class DocumentType(Enum):
    INVOICE = "invoice"
    CREDIT_NOTE = "creditnote"


class DocumentSort(Enum):
    CREATED_ASCENDING = "created-asc"
    CREATED_DESCENDING = "created-desc"


class DocumentStatus(Enum):
    UNPAID = 0
    PAID = 1
    PARTIALLY_PAID = 2
    CANCELED = 3


@dataclass
class Payment:
    date: datetime
    desc: str | None
    amount: Decimal


@dataclass
class Document:
    type: DocumentType
    id: str | None
    number: str
    status: DocumentStatus | None
    date: datetime
    buyer: Contact | str
    items: list[Item]
    custom_fields: dict[str, str] | None
    tags: list[str]
    notes: str | None
    numbering_series_id: str | None
    payments: list[Payment]
    total: Decimal | None
    paid: Decimal | None
    pending: Decimal | None


@dataclass(frozen=True)
class Holded:
    api_key: str

    def _call(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict | list:
        try:
            ret = requests.request(
                method,
                BASE_URL + endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Key": self.api_key,
                },
                json=payload,
                params=params,
            )
            body = ret.json()
        except Exception as e:
            logger.error("Error on request %s", e)
            sleep(10)
            return self._call(method=method, endpoint=endpoint, params=params, payload=payload)

        if type(body) is dict and "status" in body.keys() and body["status"] != 1:
            raise ApiError(body.get("info", "no info associated"))
        return body

    def list_documents(
        self,
        type: DocumentType,
        start: datetime | None = None,
        end: datetime | None = None,
        contact_id: str | None = None,
        sort: DocumentSort | None = None,
        paid: DocumentStatus | None = None,
    ) -> list[Document]:
        ret = self._call(
            "GET",
            f"/documents/{type.value}",
            params={
                "starttmp": start.timestamp() if start else None,
                "endtmp": end.timestamp() if end else None,
                "contactId": contact_id,
                "sort": sort.value if sort else None,
                "paid": paid.value if paid else None,
            },
        )

        return [
            Document(
                type=type,
                id=i["id"],
                number=i["docNumber"],
                status=DocumentStatus(i["status"]),
                date=datetime.fromtimestamp(i["date"]),
                buyer=i["contact"],
                items=[
                    Item(
                        name=p["name"],
                        desc=p["desc"],
                        units=p["units"],
                        taxes=p["taxes"],
                        subtotal=Decimal(p["price"]),
                        discount=Decimal(p["discount"]),
                        tax_percentage=Decimal(p["tax"]),
                    )
                    for p in i["products"]
                ],
                tags=i.get("tags", []),
                custom_fields=None,
                numbering_series_id=None,
                notes=i.get("notes"),
                payments=[
                    Payment(
                        date=datetime.fromtimestamp(p["date"]),
                        amount=Decimal(p["amount"]),
                        desc=None,
                    )
                    for p in i.get("paymentsDetail", [])
                ],
                total=Decimal(i["total"]),
                paid=Decimal(i["paymentsTotal"]),
                pending=Decimal(i["paymentsPending"]),
            )
            for i in ret
        ]

   # --- NUEVO: convertir un dict en Document, reutilizado por get_document ---
    def _into_document_from_dict(self, type: DocumentType, i: dict) -> Document:
        return Document(
            type=type,
            id=i["id"],
            number=i["docNumber"],
            status=DocumentStatus(i["status"]),
            date=datetime.fromtimestamp(i["date"]),
            buyer=i["contact"],
            items=list(
                map(
                    lambda p: Item(
                        name=p["name"],
                        desc=p.get("desc"),
                        units=p["units"],
                        taxes=p.get("taxes", []),
                        subtotal=Decimal(str(p["price"])),
                        discount=Decimal(str(p.get("discount", 0))),
                        tax_percentage=Decimal(str(p.get("tax", 0))),
                    ),
                    i.get("products", []),
                )
            ),
            tags=i.get("tags", []),
            custom_fields=None,
            numbering_series_id=None,
            notes=i.get("notes"),
            payments=list(
                map(
                    lambda p: Payment(
                        date=datetime.fromtimestamp(p["date"]),
                        amount=Decimal(str(p["amount"])),
                        desc=None,
                    ),
                    i.get("paymentsDetail", []),
                )
            ),
            total=Decimal(str(i.get("total", "0"))),
            paid=Decimal(str(i.get("paymentsTotal", "0"))),
            pending=Decimal(str(i.get("paymentsPending", "0"))),
        )

    # --- NUEVO: leer un documento por ID para conocer pendientes/total/paid ---
    def get_document(self, type: DocumentType, id: str) -> Document:
        raw = self._call("GET", f"/documents/{type.value}/{id}")
        # La API a veces devuelve un dict “plano” del documento
        if isinstance(raw, dict):
            return self._into_document_from_dict(type, raw)
        # Otras veces, envuelve el documento en una lista
        elif isinstance(raw, list) and raw:
            return self._into_document_from_dict(type, raw[0])
        else:
            raise ApiError(f"Documento {type.value}/{id} no encontrado")
    
    def create_document(self, document: Document, draft: bool = True) -> str:
        payload = {
            "language": "es",
            "contactId": document.buyer.id,
            "date": int(document.date.timestamp()),
            "items": [
                {
                    "name": i.name,
                    "desc": i.desc,
                    "units": i.units,
                    "subtotal": float(i.subtotal),
                    "discount": float(i.discount),
                    "tax": float(i.tax_percentage),
                    "taxes": i.taxes,
                }
                for i in document.items
            ],
            "invoiceNum": document.number,
            "currency": "eur",
            "currencyChange": 1,
            "tags": document.tags,
            "customFields": document.custom_fields,
            "notes": document.notes,
            "approveDoc": not draft,
        }
        logger.debug("Payload factura => %s", payload)
        ret = self._call("POST", f"/documents/{document.type.value}", payload=payload)
        return ret["id"]

    def _contact_payload(self, c: Contact) -> dict:
        payload = {
            "customId": c.custom_id,
            "name": c.name,
            "nif": c.nif,
            "code": c.nif,
            "email": (c.email or "").lower() if c.email else None,
            "mobile": c.mobile,
            "phone": c.phone,
            "type": c.type,
            "isperson": bool(c.isperson),
        }

        # ⚠️ Importante: payload mínimo, sin direcciones de momento
        # Así garantizamos que no falle la creación
        return {k: v for k, v in payload.items() if v not in (None, "")}

    def create_contact(self, contact: Contact):
        payload = self._contact_payload(contact)
        logger.debug("Payload create_contact => %s", payload)
        return self._call("POST", "/contacts", payload=payload)["id"]

    def update_contact(self, contact: Contact):
        payload = self._contact_payload(contact)
        logger.debug("Payload update_contact => %s", payload)
        return self._call("PUT", f"/contacts/{contact.id}", payload=payload)["id"]

    def _into_contact(self, response: dict[str, Any]):
        return Contact(
            id=response.get("id"),
            custom_id=response.get("customId"),
            name=response.get("name"),
            nif=response.get("code"),
            email=response.get("email"),
            mobile=response.get("mobile"),
            phone=response.get("phone"),
            type=response.get("type"),
            isperson=bool(response.get("isperson")),
        )

    def get_contact_by_id(self, id: str) -> Contact | None:
        try:
            return self._into_contact(self._call("GET", f"/contacts/{id}"))
        except Exception:
            return None

    def get_contact_by_mobile(self, mobile: str) -> Contact | None:
        try:
            return self._into_contact(self._call("GET", "/contacts", params={"mobile": mobile})[0])
        except Exception:
            return None

    def get_contact_by_custom_id(self, custom_id: str) -> Contact | None:
        try:
            return self._into_contact(
                self._call("GET", "/contacts", params={"customId": [custom_id]})[0]
            )
        except Exception:
            return None

    def list_contacts(self) -> list[Contact]:
        return [self._into_contact(c) for c in self._call("GET", "/contacts")]

