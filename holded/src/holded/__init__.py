from dataclasses import dataclass
import json
import logging
from typing import Any
import requests
from datetime import datetime
from time import sleep

# Docs: https://developers.holded.com/reference

BASE_URL = "https://api.holded.com/api/invoicing/v1"

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)


@dataclass
class Contact:
    id: str
    custom_id: str
    name: str
    nif: str
    email: str
    phone: str | None
    mobile: str
    type: str
    isperson: bool


@dataclass
class Item:
    name: str
    units: int
    subtotal: float
    discount: float
    tax_percentage: float
    taxes: list[str]


@dataclass
class Invoice:
    id: str
    number: str
    date: datetime
    buyer: Contact
    items: list[Item]
    custom_fields: dict[str, str] | None
    notes: str | None
    paid: float
    pending: float


@dataclass
class Payment:
    date: datetime
    desc: str
    amount: float


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
            return requests.request(
                method,
                BASE_URL + endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Key": self.api_key,
                },
                json=payload,
                params=params,
            ).json()
        except:
            sleep(10)
            return self._call(method=method, endpoint=endpoint, params=params, payload=payload)

    # TODO: Some fields are not translated
    # `sort` is either `created-asc` or `created-desc`
    # `paid`: 0 = Not paid, 1 = Paid, 2 = Partially paid
    def list_invoices(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        sort: str | None = None,
        paid: int | None = None,
    ) -> list[Invoice]:
        if start is not None:
            start = int(start.timestamp())
        if end is not None:
            end = int(end.timestamp())

        ret = self._call(
            "GET",
            "/documents/invoice",
            params={"starttmp": start, "endtmp": end, "sort": sort, "paid": paid},
        )

        return list(
            map(
                lambda i: Invoice(
                    id=i["id"],
                    number=i["docNumber"],
                    date=datetime.fromtimestamp(i["date"]),
                    buyer=None,
                    items=None,
                    custom_fields=None,
                    notes=i["notes"],
                    paid=i["paymentsTotal"],
                    pending=i["paymentsPending"],
                ),
                ret,
            )
        )

    def create_invoice(self, invoice: Invoice) -> str:
        payload = {
            "language": "es",
            "contactId": invoice.buyer.id,
            # "contactCode": invoice.buyer.nif,
            "date": int(invoice.date.timestamp()),
            "items": list(
                map(
                    lambda i: {
                        "name": i.name,
                        "units": i.units,
                        "subtotal": i.subtotal,
                        "discount": i.discount,
                        "tax": i.tax_percentage,
                        "taxes": i.taxes,
                    },
                    invoice.items,
                )
            ),
            "invoiceNum": invoice.number,
            "currency": "eur",
            "currencyChange": 1,
            "customFields": invoice.custom_fields,
            "notes": invoice.notes,
        }
        return self._call(
            "POST",
            "/documents/invoice",
            payload=payload,
        )["id"]

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

    def pay_invoice(self, id: str, payment: Payment):
        return self._call(
            "POST",
            "/documents/invoice/{}/pay".format(id),
            payload={
                "date": int(payment.date.timestamp()),
                "desc": payment.desc,
                "amount": payment.amount,
            },
        )
