"""Controlled first emission (manual, explicit). Usage inside the api container:

  python -m app.scripts.controlled_issue V-XXXXXXXX                 # checks + /invoices/preview only
  python -m app.scripts.controlled_issue V-XXXXXXXX --confirm --expected-total 6800   # REAL invoice
  python -m app.scripts.controlled_issue V-XXXXXXXX --stamp-existing                   # checks of a remote draft (GET)
  python -m app.scripts.controlled_issue V-XXXXXXXX --stamp-existing --confirm --expected-total 4500  # stamp THAT draft

The --confirm run POSTs a real invoice (and, by default, asks Alegra to send it to
DIAN). Only run it after reviewing the preview and with the owner's explicit order.
"""
import argparse
import json
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import Sale
from app.services.controlled_issue import run, run_stamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sale", help="número de venta (V-...) o sale_id")
    parser.add_argument("--confirm", action="store_true", help="emite la factura REAL tras las comprobaciones")
    parser.add_argument("--expected-total", type=Decimal, help="total POS confirmado por el operador (obligatorio con --confirm)")
    parser.add_argument("--no-stamp", action="store_true", help="no pedir a Alegra el envío a DIAN")
    parser.add_argument("--stamp-existing", action="store_true",
                        help="timbrar el borrador que la venta YA tiene en Alegra (nunca crea otra factura)")
    args = parser.parse_args()
    with Session(engine) as db:
        if len(args.sale) == 36:
            sale = db.get(Sale, UUID(args.sale))
        else:
            sale = db.scalar(select(Sale).where(Sale.number == args.sale))
        if sale is None:
            print("Venta no encontrada"); return 2
        if args.stamp_existing:
            report = run_stamp(db, sale, confirm=args.confirm, expected_total=args.expected_total)
        else:
            report = run(db, sale, confirm=args.confirm, expected_total=args.expected_total,
                         generate_stamp=not args.no_stamp)
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
