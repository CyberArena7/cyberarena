from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import Any
import requests
from datetime import datetime
from time import sleep
from decimal import Decimal
import logging

# Docs: https://api-docs.repairdesk.co

logger = logging.getLogger(__name__)

BASE_URL = "https://api.repairdesk.co/api/web/v1"


@dataclass
class TicketStatus:
    name: str
    color: str
    type: str


# NOTE: Has many more fields but unused at the moment
@dataclass
class Item:
    id: str
    name: str
    sku: str
    notes: str | None  # Invoice notes
    quantity: int
    price: Decimal | None  # Not tax included
    tax: Decimal | None
    total: Decimal | None
    tax_class: int | None
    tax_percent: Decimal


@dataclass
class Payment:
    id: int
    amount: Decimal
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
    customer_group_id: str | None


@dataclass
class Store:
    name: str
    mobile: str
    phone: str
    address: str
    city: str
    state: str
    country: str


class InvoiceStatus(Enum):
    PAID = "Paid"
    UNPAID = "UnPaid"
    PARTIAL = "Partial"
    REFUND = "Refund"
    OVERPAID = "OverPaid"


@dataclass
class BasicCustomer:
    id: str
    name: str


@dataclass
class BasicInvoice:
    id: str
    order_id: str
    date: datetime
    customer: BasicCustomer
    status: InvoiceStatus | None


# TODO: lots of missing fields
@dataclass
class Device:
    id: str
    name: str
    status: str


# TODO: lots of missing fields
@dataclass
class Ticket:
    id: str
    created_date: datetime
    order_id: str
    devices: list[Device]


@dataclass
class Invoice:
    id: int
    order_id: str
    ticket: Ticket | None
    date: datetime
    subtotal: Decimal
    total_tax: Decimal
    total: Decimal
    notes: str
    customer: Customer
    status: InvoiceStatus | None
    items: list[Item]
    payments: list[Payment]


class ItemNotFound(Exception):
    pass


@dataclass
class ApiError(Exception):
    status_code: int
    message: str


@dataclass(frozen=True)
class RepairDesk:
    api_key: str

    def _call(self, endpoint: str, params: dict[str, Any]) -> dict:
        try:
            ret = requests.get(
                BASE_URL + endpoint, params=(params | {"api_key": self.api_key})
            ).json()
        except Exception as e:
            logger.warning("Request failed, retrying: {}".format(e))
            sleep(10)
            return self._call(endpoint, params)

        if (
            not ret["success"]
            and ret["statusCode"] != 100  # Status code 100 is returned on empty invoice list
        ):
            raise ApiError(ret["statusCode"], ret["message"])
        return ret["data"]

    def ticket_statuses(self) -> list[TicketStatus]:
        ret = self._call("/statuses", {})
        return list(
            map(lambda s: TicketStatus(name=s["name"], color=s["color"], type=s["type"]), ret)
        )

    # Searches an item by either name or SKU
    def search_item(self, query: str) -> Item:
        res = self._call("/inventory", {"keyword": query})
        items = res["inventoryListData"]

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
                notes=None,
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
        status: InvoiceStatus | None = None,
        keyword: str | None = None,
        page_size: int = 50,
    ) -> list[BasicInvoice]:
        res = self._call(
            "/invoices",
            {
                "from_date": int(from_date.timestamp()) if from_date is not None else None,
                "to_date": int(to_date.timestamp()) if to_date is not None else None,
                "status": status.value if status is not None else None,
                "keyword": keyword,
                "pagesize": page_size,
            },
        )

        # When no invoices are found a empty list is returned
        if type(res) is list:
            return []

        invoices = []
        for invoice in res["invoiceData"]:
            invoices.append(
                BasicInvoice(
                    id=invoice["summary"]["id"],
                    order_id=invoice["summary"]["order_id"],
                    date=datetime.fromtimestamp(invoice["summary"]["created_date"]),
                    # Sometimes RepairDesk returns no status for some reason
                    status=InvoiceStatus(invoice["summary"]["status"])
                    if "status" in invoice["summary"].keys()
                    else None,
                    customer=BasicCustomer(
                        id=invoice["summary"]["customer"]["id"],
                        name=invoice["summary"]["customer"]["fullName"],
                    ),
                )
            )
        return invoices

    def ticket_by_id(self, id: str) -> Ticket:
        ticket = self._call("/tickets/{}".format(id), {})
        return Ticket(
            id=ticket["summary"]["id"],
            order_id=ticket["summary"]["order_id"],
            devices=list(
                map(
                    lambda d: Device(
                        id=d["device"]["id"], name=d["device"]["name"], status=d["status"]["name"]
                    ),
                    ticket["devices"],
                )
            ),
            created_date=datetime.fromtimestamp(ticket["summary"]["created_date"]),
        )

    def invoice_by_id(self, id: str) -> Invoice:
        inv = self._call("/invoices/{}".format(id), {})

        if inv["summary"]["ticket"]["isTicket"]:
            ticket = self.ticket_by_id(inv["summary"]["ticket"]["id"])
        else:
            ticket = None

        items = []
        for item in inv["items"]:
            if item["tax_class"]["tax_percent"] is None:
                item["tax_class"]["tax_percent"] = 0
            items.append(
                Item(
                    id=item["id"],
                    name=item["name"],
                    sku=item["sku"],
                    notes=item["notes"],
                    quantity=item["quantity"],
                    price=Decimal(item["price"]),
                    tax=Decimal(item["gst"]),
                    total=Decimal(item["total"]),
                    tax_class=item["tax_class"]["id"],
                    tax_percent=Decimal(item["tax_class"]["tax_percent"]),
                )
            )

        payments = []
        for payment in inv["summary"]["payments"]:
            payments.append(
                Payment(
                    id=payment["id"],
                    amount=Decimal(payment["amount"]),
                    date=datetime.fromtimestamp(payment["payment_date"]),
                    method=payment["method"],
                    notes=payment["notes"],
                )
            )

        return Invoice(
            id=inv["summary"]["id"],
            order_id=inv["summary"]["order_id"],
            ticket=ticket,
            date=datetime.fromtimestamp(inv["summary"]["created_date"]),
            subtotal=Decimal(inv["summary"]["subtotal_without_symbol"]),
            total_tax=Decimal(inv["summary"]["total_tax_without_symbol"]),
            total=Decimal(inv["summary"]["total_without_symbol"]),
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
                        inv["summary"]["customer"]["custom_fields"]
                        if "custom_fields" in inv["summary"]["customer"].keys()
                        else [],
                    ),
                    {"value": None},
                )["value"],
                customer_group_id=inv["summary"]["customer"]["cus_group_id"]
                if "cus_group_id" in inv["summary"]["customer"].keys()
                else None,
            ),
            status=InvoiceStatus(inv["summary"]["status"])
            if "status" in inv["summary"].keys()
            else None,
            items=items,
            payments=payments,
            notes=inv["summary"]["notes"],
        )
