from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any
import requests
from datetime import datetime
from time import sleep
from decimal import Decimal

# Docs: https://developers.holded.com/reference

# TODO: Error handling
BASE_URL = "https://api.holded.com/api/invoicing/v1"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# TODO: convert into an enum with all possible errors
@dataclass
class ApiError(Exception):
    info: str


@dataclass
class Contact:
    id: str
    custom_id: str
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


@dataclass
class Payment:
    date: datetime
    desc: str | None
    amount: Decimal


@dataclass
class Document:
    type: DocumentType
    id: str
    number: str
    date: datetime
    # Either the full contact or just the id
    buyer: Contact | str
    items: list[Item]
    custom_fields: dict[str, str] | None
    tags: list[str]
    notes: str | None
    numbering_series_id: str | None
    payments: list[Payment]
    total: Decimal
    paid: Decimal
    pending: Decimal


@dataclass(frozen=True)
class Holded:
    api_key: str

    def _call(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ):
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
            return ret.json()
        except Exception as e:
            logger.error("Error on request {}".format(e))
            sleep(10)
            return self._call(method=method, endpoint=endpoint, params=params, payload=payload)

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
            "/documents/{}".format(type.value),
            params={
                "starttmp": start.timestamp() if start is not None else None,
                "endtmp": end.timestamp() if end is not None else None,
                "contactId": contact_id,
                "sort": sort.value if sort is not None else None,
                "paid": paid.value if paid is not None else None,
            },
        )

        return list(
            map(
                lambda i: Document(
                    type=type,
                    id=i["id"],
                    number=i["docNumber"],
                    date=datetime.fromtimestamp(i["date"]),
                    buyer=i["contact"],
                    items=list(
                        map(
                            lambda p: Item(
                                name=p["name"],
                                desc=p["desc"],
                                units=p["units"],
                                taxes=p["taxes"],
                                subtotal=Decimal(p["price"]),
                                discount=Decimal(p["discount"]),
                                tax_percentage=Decimal(p["tax"]),
                            ),
                            i["products"],
                        )
                    ),
                    tags=i["tags"],
                    custom_fields=None,
                    # There's no trivial way to get this
                    numbering_series_id=None,
                    notes=i["notes"],
                    payments=list(
                        map(
                            lambda p: Payment(
                                date=datetime.fromtimestamp(p["date"]),
                                amount=Decimal(p["amount"]),
                                desc=None,
                            ),
                            # Uhh, pretty weird that this is needed but ok
                            i.get("paymentsDetail", []),
                        )
                    ),
                    total=Decimal(i["total"]),
                    paid=Decimal(i["paymentsTotal"]),
                    pending=Decimal(i["paymentsPending"]),
                ),
                ret,
            )
        )

    def create_document(self, document: Document, draft: bool = True) -> str:
        payload = {
            "language": "es",
            "contactId": document.buyer.id,
            "date": int(document.date.timestamp()),
            "items": list(
                map(
                    lambda i: {
                        "name": i.name,
                        "desc": i.desc,
                        "units": i.units,
                        "subtotal": i.subtotal,
                        "discount": i.discount,
                        "tax": i.tax_percentage,
                        "taxes": i.taxes,
                    },
                    document.items,
                )
            ),
            "invoiceNum": document.number,
            "currency": "eur",
            "currencyChange": 1,
            "tags": document.tags,
            "customFields": document.custom_fields,
            "notes": document.notes,
            "approveDoc": not draft,
        }
        ret = self._call(
            "POST",
            "/documents/{}".format(document.type.value),
            payload=payload,
        )
        if ret["status"] != 1:
            raise ApiError(ret["info"])
        return ret["id"]

    def delete_document(self, document: Document):
        assert document.id is not None
        ret = self._call("DELETE", "/documents/{}/{}".format(document.type.value, document.id))
        if ret["status"] != 1:
            raise ApiError(info=ret["info"])

    def _into_contact(self, response: dict[str, Any]):
        return Contact(
            id=response["id"],
            custom_id=response["customId"],
            name=response["name"],
            nif=response["code"],
            email=response["email"],
            mobile=response["mobile"],
            phone=response["phone"],
            type=response["type"],
            isperson=bool(response["isperson"]),
        )

    def get_contact_by_id(self, id: str) -> Contact | None:
        try:
            return self._into_contact(
                self._call(
                    "GET",
                    "/contacts/{}".format(id),
                )
            )
        except IndexError:
            return None

    def get_contact_by_mobile(self, mobile: str) -> Contact | None:
        try:
            return self._into_contact(self._call("GET", "/contacts", params={"mobile": mobile})[0])
        except IndexError:
            return None

    def get_contact_by_custom_id(self, custom_id: str) -> Contact | None:
        try:
            return self._into_contact(
                self._call("GET", "/contacts", params={"customId": [custom_id]})[0]
            )
        except IndexError:
            return None

    def list_contacts(self) -> list[Contact]:
        return list(map(self._into_contact, self._call("GET", "/contacts")))

    def create_contact(self, contact: Contact):
        return self._call(
            "POST",
            "/contacts",
            payload={
                "CustomId": contact.custom_id,
                "name": contact.name,
                "code": contact.nif,
                "email": contact.email,
                "mobile": contact.mobile,
                "phone": contact.phone,
                "type": contact.type,
                "isperson": contact.isperson,
            },
        )["id"]

    def update_contact(self, contact: Contact):
        return self._call(
            "PUT",
            "/contacts/{}".format(contact.id),
            payload={
                "name": contact.name,
                "code": contact.nif,
                "email": contact.email,
                "mobile": contact.mobile,
                "phone": contact.phone,
                "type": contact.type,
                "isperson": contact.isperson,
            },
        )["id"]

    def pay_document(self, type: DocumentType, id: str, payment: Payment):
        return self._call(
            "POST",
            "/documents/{}/{}/pay".format(type.value, id),
            payload={
                "date": int(payment.date.timestamp()),
                "desc": payment.desc,
                "amount": payment.amount,
            },
        )
