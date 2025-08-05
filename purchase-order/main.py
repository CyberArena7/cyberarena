from flask import Flask, request, make_response
from datetime import datetime
import xlwt
import io
import os

import providers
from repairdesk import RepairDesk, ItemNotFound

VAT_MULT = 1.21

app = Flask(__name__)
api = RepairDesk(api_key=os.environ["REPAIRDESK_API_KEY"])


@app.route("/")
def index():
    return open("static/index.html")


@app.route("/upload", methods=["POST"])
def upload_invoice():
    data = request.files["invoice"].read()
    invoice = providers.parse(data)

    output = io.BytesIO()
    workbook = xlwt.Workbook()
    ws = workbook.add_sheet("Sheet1")

    for i, col in enumerate(["Sku/Upc/Id", "Description", "Qty", "Price"]):
        ws.write(0, i, col)

    for i, item in enumerate(invoice.items):
        try:
            if item.id is not None:
                match = api.search_item(item.id)
            else:
                match = api.search_item(item.name)
        except ItemNotFound:
            return "NO SE HA ENCONTRADO EL ITEM: {}  SKU: {}".format(item.name, item.id)

        print(match.name, "-", match.sku)

        if item.vat_included:
            real_price = item.price
        else:
            real_price = item.price * VAT_MULT

        for j, value in enumerate([match.id, "", item.amount, real_price]):
            ws.write(i + 1, j, value)

    # Add shipping as a separate item because it cannot be included properly in the Excel
    if invoice.shipping.price is not None:
        for i, value in enumerate(["339584223", "", 1, invoice.shipping.price]):
            ws.write(len(invoice.items) + 1, i, value)

    workbook.save(output)

    res = make_response(output.getvalue())
    res.headers["Content-Disposition"] = "attachment; filename={}-{}.xls".format(
        invoice.provider.name, datetime.today().strftime("%d-%m")
    )
    res.headers["Content-type"] = "application/vnd.ms-excel"
    return res


if __name__ == "__main__":
    app.run(debug=True)
