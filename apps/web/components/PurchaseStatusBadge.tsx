import { StatusBadge } from "@/components/ui";
import type { PurchaseStatus } from "@/lib/types";
import { purchaseStatusLabel } from "@/lib/smartPurchasing";

/** Shared by purchases and suppliers. */
export function PurchaseStatusBadge({ status }: { status: PurchaseStatus }) {
  return <StatusBadge tone={status === "RECEIVED" ? "success" : status === "CANCELLED" ? "neutral" : "info"}>{purchaseStatusLabel[status]}</StatusBadge>;
}
