from . import common
from bs4 import BeautifulSoup

def detect(data: str) -> bool:
    soup = BeautifulSoup(data, "html.parser")
    try:
        if "PCXeon" in soup.find("title").string:
            return True
        else:
            return False
    except:
        return False

def _normalize_price(price: str) -> float:
    return float(price.strip("€").strip().replace(",", ".").replace("Gratis", "0"))

def parse(data: str) -> common.Invoice:
    soup = BeautifulSoup(data, 'html.parser')
    prices_table = soup.find(id="order-products")


    # Subtotal, Discount, Shipping, Tax, Total
    footer_rows = prices_table.find("tfoot").find_all("tr")
    shipping_price = _normalize_price(footer_rows[2].find_all("td")[1].string) + _normalize_price(footer_rows[4].find_all("td")[1].string.split("Gastos Administrativos:")[1].strip(")"))
    total_price = _normalize_price(footer_rows[4].find_all("td")[1].string.split("(")[0].strip())

    product_rows = prices_table.find("tbody").find_all("tr")
    items = []
    for row in product_rows:
        inner = row.find_all("td")

        # Name Amount UnitPrice TotalPrice
        name = inner[0].find("a").string.strip()
        amount = int(inner[1].string.strip())
        price = _normalize_price(inner[2].string)

        items.append(common.Item(None, name, amount, price, True))
    
    invoice = common.Invoice(
        common.Provider("Kaquucomponentes"),
        items,
        common.Shipping(shipping_price),
        total_price
    )

    return invoice
        
    

