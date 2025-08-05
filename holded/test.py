from datetime import datetime
from holded import Holded, Contact, Invoice, Item
import json

api = Holded()

test_contact = Contact(
    custom_id="53942907H",
    name="Josep Mengual Benavente",
    email="tuemail@prueba.es",
    phone=None,
    mobile="+34604553234",
    nif="53942907H",
    type="client",
    isperson=True,
)

# api.create_contact(test_contact)

print(
    json.dumps(
        api.create_invoice(
            Invoice(
                number="4032",
                date=datetime.now(),
                buyer=test_contact,
                items=[
                    Item(
                        name="Prueba2",
                        units=2,
                        subtotal=100,
                        tax_percentage=21,
                        discount=0,
                        taxes=["s_iva_21"],
                    )
                ],
            )
        )
    )
)

# print(json.dumps(api.list_invoices()))
