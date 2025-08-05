from . import common
from bs4 import BeautifulSoup

def detect(data: str) -> bool:
    soup = BeautifulSoup(data, "html.parser")
    try:
        if "SoluzionDigital" in soup.find("title").string:
            return True
        else:
            return False
    except:
        return False

def _normalize_price(price: str) -> float:
    return float(price.strip("€").strip().replace(",", "."))

def parse(data: str) -> common.Invoice:
    soup = BeautifulSoup(data, 'html.parser')
    prices_table = soup.find(class_="portlet box red")

    # Only shipping available
    shipping_price = _normalize_price(prices_table.find(id="ctl00_cphGen_LbPortes").string)

    product_rows = prices_table.find("tbody").find_all("tr")
    items = []
    for row in product_rows:
        inner = row.find_all("td")

        # Name Ref UnitPrice AmountDemanded AmountSupplied TotalPrice
        name = inner[0].find("span").string.strip()
        id = inner[1].find("span").string.strip()
        amount = int(inner[4].find("span").string.strip())
        price = _normalize_price(inner[2].find("span").string)

        items.append(common.Item(id, name, amount, price, False))
    
    invoice = common.Invoice(
        common.Provider("SoluzionDigital"),
        items,
        common.Shipping(shipping_price),
        None
    )

    return invoice
        
    

