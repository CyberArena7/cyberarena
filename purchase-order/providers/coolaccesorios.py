from . import common
from bs4 import BeautifulSoup

def detect(data: str) -> bool:
    soup = BeautifulSoup(data, "html.parser")
    try:
        if "Cool Accesorios" in soup.find("title").string:
            return True
        else:
            return False
    except:
        return False

def _normalize_price(price: str) -> float:
    return float(price.strip().strip("€").strip().replace(",", "."))

def parse(data: str) -> common.Invoice:
    soup = BeautifulSoup(data, 'html.parser')
    prices_table = soup.find(id="order-detail-content")

    # Items, Items+VAT, Shipping, Total
    footer_rows = prices_table.find("tfoot").find_all("tr")
    total_price = _normalize_price(footer_rows[0].find_all("td")[1].find("span").string)

    # Impossible to get as it's not correct
    shipping_price = None

    product_rows = prices_table.find("tbody").find_all("tr")
    items = []
    for row in product_rows:
        inner = row.find_all("td")

        # Photo Ref Name Amount UnitPrice TotalPrice
        name = inner[2].find("label").string.strip() # We use SKU
        id = inner[1].find("label").string.strip()
        amount = int(inner[3].find("label").find("span").string.strip())
        price = _normalize_price(inner[4].find("label").string)

        items.append(common.Item(id, name, amount, price, False))
    
    invoice = common.Invoice(
        common.Provider("Cool Accesorios"),
        items,
        common.Shipping(shipping_price),
        total_price * 1.21
    )

    return invoice
        
    

