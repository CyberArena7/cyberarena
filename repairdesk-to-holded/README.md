This program syncs invoices from RepairDesk over to Holded

## Flowchart
```mermaid
flowchart TD
  sanity{Pasa saneamiento?} -->|Sí| ticket{Tiene un ticket asociado?}
  ticket -->|No| associated{Factura asociada en Holded?}
  ticket -->|Sí| ticketdone{Es el estado de todos los dispositivos en el ticket en la categoría 'Closed'}
  ticketdone -->|Sí| associated
  ticketdone -->|No| _end[Fin]
  sanity --> |No| warning[Crea un aviso]
  warning --> _end
  associated --> |Sí| changes{Hay cambios?}
  associated --> |No| rebu{Es REBU?}
  rebu --> |Sí| createdraft[Crea un borrador]
  createdraft --> warning
  rebu --> |No| create[Crea la factura]
  changes --> |No| syncpayments[Sincroniza los pagos]
  create --> _end
  syncpayments --> _end
  changes --> |Sí| try_edit{Se puede editar la factura?}
  try_edit --> |"No (aprobada)"| warning
  try_edit --> |"Sí (borrador)"| create
```

## Configuration
A sample configuration file can be found [here](./example.conf.jsonc), it must be located at `/etc/repairdesk-to-holded.conf.json` and must contain **no comments**
