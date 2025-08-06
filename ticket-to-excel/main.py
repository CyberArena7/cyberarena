from datetime import datetime
from ratelimit.exception import RateLimitException
import repairdesk
from openpyxl import Workbook, load_workbook
from time import sleep
import re
import os

api = repairdesk.RepairDesk(os.environ["API_KEY"])


def sheet_to_dict_array(sheet):
    rows = list(sheet.iter_rows(values_only=True))
    headers = rows[0]

    data = []
    for row in rows[1:]:
        row_dict = dict(zip(headers, row))
        data.append(row_dict)

    return data


def main():
    rd_workbook = load_workbook(filename="tickets.xlsx").active
    data = sheet_to_dict_array(rd_workbook)

    wb = Workbook()
    ws = wb.active
    assert ws is not None

    ws.append(
        ["N Ticket", "Fecha", "Dispositivo", "Estado", "Técnico", "Partes", "Pagado", "Estimado"]
    )

    for line in data:
        # number = input("Ticket number: ")
        # if number == "":
        #     break

        id = line["Ticket ID"]

        ticket_id = api._call("/tickets", params={"keyword": id})["data"]["ticketData"][0][
            "summary"
        ]["id"]
        ticket = api._call("/tickets/{}".format(ticket_id), params={})["data"]
        device = ticket["devices"][0]

        if device["deviceCategoryName"] != "PATINES":
            print(
                "Skipping:",
                line["Ticket ID"],
                line["Device Manufacturer"],
                line["Devices Category"],
            )
            continue

        name = device["name_with_device_and_manufacturer"]

        status = device["status"]["name"]

        created = datetime.fromtimestamp(ticket["summary"]["created_date"])

        tech = 0
        parts = 0
        for part in device["parts"]:
            if part["name"] == "Reparación Nivel 4" or part["name"] == "Reparacióin Nivel 4":
                tech += 25 * int(part["quantity"])
            else:
                parts += float(part["price"]) * int(part["quantity"])

        print(id, name, status, tech, parts, line["Paid"], line["Total"])
        ws.append([id, created, name, status, tech, parts, line["Paid"], line["Total"]])
        wb.save("output.xlsx")


if __name__ == "__main__":
    main()
