from dataclasses import dataclass
from functools import cache
from typing import Any
import requests
from datetime import datetime
import json
from time import sleep

# Docs: https://api-docs.repairdesk.co

BASE_URL = "https://api.repairdesk.co/api/web/v1"


# NOTE: Has many more fields but unused at the moment
@dataclass
class Item:
    id: str
    name: str
    sku: str
    quantity: int
    price: float | None  # Not tax included
    tax: float | None
    total: float | None
    tax_class: int | None
    tax_percent: float


@dataclass
class Payment:
    id: int
    amount: float
    date: datetime
    method: str
    notes: str


@dataclass
class Customer:
    full_name: str
    id: str
    address: str
    mobile: str
    email: str
    city: str
    state: str
    country: str
    nif: str | None


@dataclass
class Store:
    name: str
    mobile: str
    phone: str
    address: str
    city: str
    state: str
    country: str


@dataclass
class BasicCustomer:
    id: str
    name: str


@dataclass
class BasicInvoice:
    id: str
    order_id: str
    date: datetime
    status: str
    customer: BasicCustomer


@dataclass
class Invoice:
    id: int
    order_id: str
    date: datetime
    subtotal: float
    total_tax: float
    total: float
    notes: str
    customer: Customer
    status: str
    items: list[Item]
    payments: list[Payment]


class ItemNotFound(Exception):
    pass


@dataclass(frozen=True)
class RepairDesk:
    api_key: str

    # TODO: Proper rate limiting
    def _call(self, endpoint: str, params: dict[str, Any]) -> dict:
        try:
            return requests.get(
                BASE_URL + endpoint, params=(params | {"api_key": self.api_key})
            ).json()
        except:
            sleep(10)
            return self._call(endpoint, params)

    # Searches an item by either name or SKU
    @cache
    def search_item(self, query: str) -> Item:
        print("Searching for:", query)
        res = self._call("/inventory", {"keyword": query})
        items = res["data"]["inventoryListData"]

        match = None
        for item in items:
            if item["sku"] == query or item["name"] == query:
                match = item
                break

        if match is None:
            raise ItemNotFound
        else:
            return Item(
                id=match["id"],
                name=match["name"],
                sku=match["sku"],
                quantity=None,
                price=None,
                tax=None,
                total=None,
                tax_percent=None,
                tax_class=None,
            )

    def invoices(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> list[BasicInvoice]:
        if from_date is not None:
            from_date = int(from_date.timestamp())
        if to_date is not None:
            to_date = int(to_date.timestamp())

        res = self._call(
            "/invoices",
            {
                "from_datetime": from_date,
                "to_datetime": to_date,
                "status": status,
                "keyword": keyword,
            },
        )

        invoices = []
        try:
            for invoice in res["data"]["invoiceData"]:
                invoices.append(
                    BasicInvoice(
                        id=invoice["summary"]["id"],
                        order_id=invoice["summary"]["order_id"],
                        date=datetime.fromtimestamp(invoice["summary"]["created_date"]),
                        status=invoice["summary"]["status"],
                        customer=BasicCustomer(
                            id=invoice["summary"]["customer"]["id"],
                            name=invoice["summary"]["customer"]["fullName"],
                        ),
                    )
                )
            return invoices
        except:
            print("ERROR while reading invoices:", res)
            raise

    def invoice_by_id(self, id: int) -> Invoice:
        inv = self._call("/invoices/{}".format(id), {"Invoice-Id": id})["data"]

        items = []
        for item in inv["items"]:
            if item["tax_class"]["tax_percent"] is None:
                item["tax_class"]["tax_percent"] = 0
            items.append(
                Item(
                    id=item["id"],
                    name=item["name"],
                    sku=item["sku"],
                    quantity=item["quantity"],
                    price=float(item["price"]),
                    tax=float(item["gst"]),
                    total=float(item["total"]),
                    tax_class=item["tax_class"]["id"],
                    tax_percent=float(item["tax_class"]["tax_percent"]),
                )
            )

        payments = []
        for payment in inv["summary"]["payments"]:
            payments.append(
                Payment(
                    id=payment["id"],
                    amount=float(payment["amount"]),
                    date=datetime.fromtimestamp(payment["payment_date"]),
                    method=payment["method"],
                    notes=payment["notes"],
                )
            )

        return Invoice(
            id=inv["summary"]["id"],
            order_id=inv["summary"]["order_id"],
            date=datetime.fromtimestamp(inv["summary"]["created_date"]),
            subtotal=float(inv["summary"]["subtotal_without_symbol"]),
            total_tax=float(inv["summary"]["total_tax_without_symbol"]),
            total=float(inv["summary"]["total_without_symbol"]),
            customer=Customer(
                full_name=inv["summary"]["customer"]["fullName"],
                id=inv["summary"]["customer"]["cid"],
                mobile=inv["summary"]["customer"]["mobile"],
                address=inv["summary"]["customer"]["address1"],
                email=inv["summary"]["customer"]["email"],
                city=inv["summary"]["customer"]["city"],
                state=inv["summary"]["customer"]["state"],
                country=inv["summary"]["customer"]["country"],
                nif=next(
                    filter(
                        lambda i: i["name"] == "nif",
                        inv["summary"]["customer"]["custom_fields"],
                    ),
                    {"value": None},
                )["value"],
            ),
            status=inv["summary"]["status"],
            items=items,
            payments=payments,
            notes=inv["summary"]["notes"],
        )
