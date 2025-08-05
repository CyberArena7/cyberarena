import repairdesk
import os
from datetime import datetime

client = repairdesk.RepairDesk(api_key=os.environ['API_KEY'])
client.invoices(from_date=datetime(2025, 6, 27, 11, 30), to_date=datetime.now())
