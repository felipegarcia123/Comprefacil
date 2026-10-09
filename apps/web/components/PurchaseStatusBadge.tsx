import { StatusBadge } from "@/components/ui";
import type { PurchaseStatus } from "@/lib/types";

/** DRAFT = editable draft; RECEIVED/CANCELLED are read-only history. Shared by /purchases and /suppliers. */
export function PurchaseStatusBadge({ status }: { status: PurchaseStatus }) {
  if (status === "DRAFT") return <StatusBadge tone="info">Borrador</StatusBadge>;
  if (status === "RECEIVED") return <StatusBadge tone="success">Recibida</StatusBadge>;
  return <StatusBadge tone="neutral">Cancelada</StatusBadge>;
}
