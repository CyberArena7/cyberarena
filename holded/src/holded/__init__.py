from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Optional, Dict
import requests
from datetime import datetime
from time import sleep
from decimal import Decimal

# Docs: https://developers.holded.com/reference
BASE_URL = "https://api.holded.com/api/invoicing/v1"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@dataclass
class ApiError(Exception):
    info: str


@dataclass
class BillingAddress:
    street: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None     
    zip: Optional[str] = None
    country: Optional[str] = None     


@dataclass
class Contact:
    id: Optional[str]
    custom_id: Optional[str]
    name: str
    nif: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    mobile: Optional[str]
    type: str
    isperson: bool
    # NUEVO: direcciones nativas de Holded
    billing_address: Optional[BillingAddress] = None
    shipping_address: Optional[BillingAddress] = None


@dataclass
class Item:
    name: str
    desc: Optional[str]
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
    desc: Optional[str]
    amount: Decimal


@dataclass
class Document:
    type: DocumentType
    id: Optional[str]
    number: Optional[str]
    status: Optional[DocumentStatus]
    date: datetime
    buyer: Contact | str
    items: list[Item]
    custom_fields: Optional[dict[str, str]]
    tags: list[str]
    notes: Optional[str]
    numbering_series_id: Optional[str]
    payments: list[Payment]
    total: Optional[Decimal]
    paid: Optional[Decimal]
    pending: Optional[Decimal]


@dataclass(frozen=True)
class Holded:
    api_key: str

    def _call(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict | list:
        try:
            logger.debug("%s %s", method, endpoint)
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
                timeout=30,
            )
            body = ret.json()
        except Exception as e:
            logger.error("Error on request %s %s: %s", method, endpoint, e)
            sleep(5)
            return self._call(method=method, endpoint=endpoint, params=params, payload=payload)

        # API moderna: cuando hay "status" y != 1, es error
        if isinstance(body, dict) and body.get("status") not in (None, 1):
            raise ApiError(body.get("info", "Holded API error"))
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
                "starttmp": int(start.timestamp()) if start is not None else None,
                "endtmp": int(end.timestamp()) if end is not None else None,
                "contactId": contact_id,
                "sort": sort.value if sort is not None else None,
                "paid": paid.value if paid is not None else None,
            },
        )

        return [
            Document(
                type=type,
                id=i.get("id"),
                number=i.get("docNumber"),
                status=DocumentStatus(i["status"]) if "status" in i else None,
                date=datetime.fromtimestamp(i["date"]),
                buyer=i["contact"],
                items=[
                    Item(
                        name=p["name"],
                        desc=p.get("desc"),
                        units=p["units"],
                        taxes=p.get("taxes", []),
                        subtotal=Decimal(str(p["price"])),
                        discount=Decimal(str(p.get("discount", 0))),
                        tax_percentage=Decimal(str(p.get("tax", 0))),
                    )
                    for p in i.get("products", [])
                ],
                tags=i.get("tags", []),
                custom_fields=None,
                numbering_series_id=None,
                notes=i.get("notes"),
                payments=[
                    Payment(
                        date=datetime.fromtimestamp(p["date"]),
                        amount=Decimal(str(p["amount"])),
                        desc=None,
                    )
                    for p in i.get("paymentsDetail", [])
                ],
                total=Decimal(str(i["total"])) if "total" in i else None,
                paid=Decimal(str(i.get("paymentsTotal", 0))),
                pending=Decimal(str(i.get("paymentsPending", 0))),
            )
            for i in ret
        ]

    def create_document(self, document: Document, draft: bool = True) -> str:
        assert isinstance(document.buyer, Contact) and document.buyer.id, "buyer.id requerido"
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
        ret = self._call("POST", f"/documents/{document.type.value}", payload=payload)
        return ret["id"]

    def delete_document(self, document: Document):
        assert document.id is not None
        self._call("DELETE", f"/documents/{document.type.value}/{document.id}")

 

    def _into_contact(self, response: dict[str, Any]) -> Contact:
       
        nif = response.get("nif", response.get("code"))
      
        def into_addr(obj: Optional[Dict[str, Any]]) -> Optional[BillingAddress]:
            if not obj:
                return None
            return BillingAddress(
                street=obj.get("street"),
                city=obj.get("city"),
                region=obj.get("region"),
                zip=obj.get("zip"),
                country=obj.get("country"),
            )

        return Contact(
            id=response.get("id"),
            custom_id=response.get("customId"),
            name=response.get("name", ""),
            nif=nif,
            email=response.get("email"),
            mobile=response.get("mobile"),
            phone=response.get("phone"),
            type=response.get("type", "client"),
            isperson=bool(response.get("isperson", True)),
            billing_address=into_addr(response.get("billingAddress")),
            shipping_address=into_addr(response.get("shippingAddress")),
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
        
def _contact_payload(self, c: Contact) -> dict:
    def compact(d: dict) -> dict:
        out = {}
        for k, v in d.items():
            if v is None or v == "":
                continue
            if isinstance(v, dict):
                v = compact(v)
                if not v:
                    continue
            out[k] = v
        return out

    def addr_to_dict(addr: BillingAddress | None) -> dict:
        if not addr:
            return {}
        # Normaliza país a ISO-2 (mayúsculas)
        country = (addr.country or "").strip().upper() or None
        return compact({
            "street": addr.street,
            "city": addr.city,
            "region": addr.region,   # provincia/estado
            "zip": addr.zip,
            "country": country,
        })

    bill = addr_to_dict(c.billing_address)

    # Payload base
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

    # Formato clásico: addresses[]
    if bill:
        payload["addresses"] = [{"type": "billing", **bill}]

    return compact(payload)

    # LOG útil para depurar (quitar si molesta)
    try:
        logger.debug("Holded payload contact => %s", payload)
    except Exception:
        pass

    return payload

    def create_contact(self, contact: Contact) -> str:
        payload = self._contact_payload(contact)
        ret = self._call("POST", "/contacts", payload=payload)
        return ret["id"]

    def update_contact(self, contact: Contact) -> str:
        assert contact.id, "update_contact requiere contact.id"
        payload = self._contact_payload(contact)
        ret = self._call("PUT", f"/contacts/{contact.id}", payload=payload)
        # algunas versiones devuelven el id, otras no; devolvemos id por coherencia
        return contact.id

    def pay_document(self, type: DocumentType, id: str, payment: Payment):
        return self._call(
            "POST",
            f"/documents/{type.value}/{id}/pay",
            payload={
                "date": int(payment.date.timestamp()),
                "desc": payment.desc,
                "amount": float(payment.amount),
            },
        )

    def send_document(self, type: DocumentType, id: str, emails: str):
        return self._call("POST", f"/documents/{type.value}/{id}/send", payload={"emails": emails})
