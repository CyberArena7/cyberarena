This program syncs invoices from RepairDesk over to Holded

## Flowchart
```mermaid
flowchart TD
  sanity{Pasa saneamiento?} -->|Sí| associated{Factura asociada en Holded?}
  sanity --> |No| warning[Crea un aviso]
  warning --> _end[Fin]
  associated --> |Sí| changes{Hay cambios?}
  associated --> |No| rebu{Es REBU?}
  rebu --> |Sí| createdraft[Crea un borrador]
  createdraft --> warning
  rebu --> |No| create[Crea la factura]
  create --> _end
  changes --> |No| syncpayments[Sincroniza los pagos]
  syncpayments --> _end
  changes --> |Sí| try_edit{Se puede editar la factura?}
  try_edit --> |"No (aprobada)"| warning
  try_edit --> |"Sí (borrador)"| create
```

## Configuration
A sample configuration file can be found [here](./example.conf.jsonc), it must be located at `/etc/repairdesk-to-holded.conf.json` and must contain **no comments**
