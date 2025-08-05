from . import common
from bs4 import BeautifulSoup


def detect(data: str) -> bool:
    soup = BeautifulSoup(data, "html.parser")
    try:
        if "SpainSellers" in soup.find("title").string:
            return True
        else:
            return False
    except:
        return False


def parse(data: str) -> common.Invoice:
    soup = BeautifulSoup(data, "html.parser")
    prices_table = soup.find(id="order-detail-content").contents[0]

    # Items, Items+VAT, Shipping, Total
    footer_rows = prices_table.find("tfoot").contents
    shipping_price = float(footer_rows[2].contents[1].contents[1].string.strip().strip("€"))
    total_price = float(footer_rows[3].contents[1].contents[1].string.strip().strip("€"))

    # When not paid, checkboxes are missing and indices are offset by one
    if len(prices_table.find("thead").find("tr").find_all("th")) == 5:
        offset = 0
    else:
        offset = 1

    product_rows = prices_table.find("tbody").contents
    items = []
    for row in product_rows:
        inner = row.contents

        try:
            # Checkbox Reference Name Amount UnitPrice TotalPrice
            id = inner[0 + offset].contents[0].string
            name = inner[1 + offset].contents[1].string.strip()
            amount = int(inner[2 + offset].find("label").contents[0].string)
            price = float(inner[3 + offset].contents[1].string.strip().strip("€"))

            items.append(common.Item(id, name, amount, price, False))
        except:
            print("Error while parsing", row)

    invoice = common.Invoice(
        common.Provider("SpainSellers"), items, common.Shipping(shipping_price), total_price
    )

    return invoice
