from dataclasses import dataclass

VAT_MULT = 1.21

@dataclass
class Item:
    id: str
    name: str
    amount: int
    price: float
    vat_included: bool

@dataclass
class Shipping:
    price: float | None

@dataclass
class Provider:
    name: str

@dataclass
class Invoice:
    provider: Provider
    items: list[Item]
    shipping: Shipping
    total: float | None


# TODO: turn into a Invoice method
# Sanity check on whether price matches sum of items and shipping
def check_total_price(invoice: Invoice) -> bool:
    if invoice.total is None:
        return True

    if invoice.shipping.price is not None:
        total = invoice.shipping.price
    else:
        total = 0
    for item in invoice.items:
        if item.vat_included:
            total += item.price * item.amount
        else:
            total += item.price * item.amount * VAT_MULT
    # TODO: For now this will do
    return (abs(total - invoice.total) < 0.01)
    
