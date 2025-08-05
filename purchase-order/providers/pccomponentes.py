from . import common
from bs4 import BeautifulSoup

def detect(data: str) -> bool:
    soup = BeautifulSoup(data, "html.parser")
    try:
        if "PcComponentes" in soup.find("title").string:
            return True
        else:
            return False
    except:
        return False

def _normalize_price(price: str) -> float:
    return float(price.strip().strip("€").strip().replace(",", "."))

def parse(data: str) -> common.Invoice:
    soup = BeautifulSoup(data, 'html.parser')
    items = map(lambda i: i.parent.parent, soup.find("span", string="Ocultar detalles y seguimiento").parent.parent.parent.parent.parent.find_all("img"))
    #print(items)

    prices_table = soup.find(id="order-detail-content")

    parsed_items = []
    for item in items:
        inner = item.find_all("div")[1].find_all("div")
        print("BANANA ", inner)


        # WONTFIX: extract sku (follow link, impossible due to captcha)
        continue
    
    invoice = common.Invoice(
        common.Provider("Pc Componentes"),
        parsed_items,
        common.Shipping(None),
        None
    )

    return invoice