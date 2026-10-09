# POS Colombia — Foundation + POS MVP

Vertical slice API-first para un punto de venta minorista: varias ventas abiertas persistentes, búsqueda/escaneo HID, inventario por ledger, sesión de caja y checkout transaccional.

## Arquitectura

```text
apps/
  web/       Next.js + React + TypeScript (cliente táctil)
  api/       FastAPI + SQLAlchemy (API y dominio)
  android/   placeholder del futuro adaptador SUNMI
packages/
  ui/        espacio para componentes compartidos
  types/     contratos TypeScript compartidos
  config/    configuración compartida futura
database/
  migrations/  migraciones Alembic
```

La API es la fuente de verdad. `sales` conserva tanto borradores (`OPEN`) como ventas finalizadas. Los artículos guardan snapshots de nombre, precio y costo. El inventario actual se deriva de la suma de cantidades firmadas en `inventory_movements` (entradas positivas, salidas negativas). Los pagos y movimientos físicos de caja son conceptos separados.

## Ejecutar en Windows 11

Requisitos: Docker Desktop con Docker Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Abra <http://localhost:3000>. La API y su documentación estarán en <http://localhost:8000/docs>. La migración y el seed se ejecutan automáticamente al iniciar la API.

Para reiniciar completamente los datos de demostración:

```powershell
docker compose down -v
docker compose up --build
```

## Venta de prueba

1. En la venta inicial escriba `7702004001234` y pulse Enter. También puede buscar por nombre y tocar un resultado.
2. Escanee otra vez para incrementar la cantidad, o use los botones `−` y `+`.
3. Cree tantas ventas como necesite con **Nueva venta** y alterne entre pestañas.
4. Elija efectivo, tarjeta o transferencia. Para efectivo ingrese el dinero recibido.
5. Pulse **COBRAR**. En desarrollo se usa `MockHardwareService`; sólo registra la impresión y apertura del cajón en la consola.

La primera venta en efectivo abre automáticamente una caja con saldo inicial cero para que el demo sea inmediato. La API, usada directamente, exige abrirla explícitamente en `POST /api/v1/cash-sessions/open`.

## Productos seed

| Código | Producto | Precio |
|---|---|---:|
| 7702004001234 | Café colombiano 500 g | $18.500 |
| 7702011000015 | Leche entera 1 L | $4.800 |
| 7702020000451 | Pan tajado | $7.200 |
| 7702030000786 | Chocolate de mesa | $9.500 |
| 7702040000990 | Agua 600 ml | $2.500 |

## Garantías y límites del MVP

- El checkout toma bloqueos de fila, valida existencia y confirma pago, ledger, caja y estado en una única transacción PostgreSQL.
- Sólo efectivo genera `cash_movements`; tarjeta y transferencia siguen registrados en `payments`.
- No incluye autenticación, impuestos/factura electrónica, devoluciones, cierre UI de caja ni hardware real.
- `ElectronicInvoiceProvider` y `HardwareService` son contratos, sin dependencia de Alegra o SUNMI.
- Para mayor escala, el saldo del ledger podrá proyectarse en una vista materializada sin alterar el modelo de dominio.

## Facturación electrónica (Alegra) — primera fase

Base para convertir una `Sale COMPLETED` en factura electrónica vía Alegra (`https://api.alegra.com/api/v1`, Basic Auth `email:token`). Deshabilitada por defecto y sin generación automática desde checkout — la emisión es siempre manual/controlada mientras se valida contra la cuenta real.

Variables de entorno (`.env`, nunca se hardcodean ni se loguean):

```
ALEGRA_ENABLED=false
ALEGRA_API_URL=https://api.alegra.com/api/v1
ALEGRA_EMAIL=
ALEGRA_TOKEN=
ALEGRA_NUMBER_TEMPLATE_ID=
```

`ALEGRA_NUMBER_TEMPLATE_ID` debe ser el `id` de una numeración existente con `isElectronic: true` (`GET /number-templates?documentType=invoice` en la cuenta real) — el POS nunca crea una numeración.

Antes de emitir una factura, cada producto que se quiera facturar necesita mapeo explícito (`PATCH /products/{id}/alegra-mapping` con `alegra_item_id`/`alegra_tax_id`); no hay sincronización masiva de catálogo. `GET /electronic-invoices/account-audit` hace una auditoría de solo lectura de la cuenta (empresa, numeraciones, impuestos, contactos/items/facturas de muestra) sin crear nada.

## Pruebas locales del backend

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH = "."
.venv\Scripts\pytest
```
