import { BarChart3, Handshake, Landmark, Package, PackageOpen, ShoppingCart, type LucideIcon } from "lucide-react";

export type ModuleId = "ventas" | "inventario" | "compras" | "proveedores" | "caja" | "informes";

export type ModuleMeta = {
  id: ModuleId;
  label: string;
  /** Very short, plain-language description (Home cards, menus). */
  description: string;
  /** Verb-first call to action shown on the card. */
  cta: string;
  icon: LucideIcon;
  /** Route where the module lives today (legacy routes are kept until each module is migrated). */
  href: string;
};

/** One identity per module: color (data-module) + icon + title + toldo pattern (data-pattern = id). */
export const MODULES: Record<ModuleId, ModuleMeta> = {
  ventas: { id: "ventas", label: "Ventas", description: "Registrar una venta", cta: "Vender", icon: ShoppingCart, href: "/sales" },
  inventario: { id: "inventario", label: "Inventario", description: "Productos y existencias", cta: "Ver productos", icon: Package, href: "/inventory" },
  compras: { id: "compras", label: "Compras", description: "Recibir mercancía", cta: "Recibir", icon: PackageOpen, href: "/purchases" },
  proveedores: { id: "proveedores", label: "Proveedores", description: "Gestionar proveedores", cta: "Ver proveedores", icon: Handshake, href: "/suppliers" },
  caja: { id: "caja", label: "Caja", description: "Jornada y movimientos", cta: "Ver caja", icon: Landmark, href: "/cash" },
  informes: { id: "informes", label: "Informes", description: "Ver resultados", cta: "Ver informes", icon: BarChart3, href: "/reports" },
};

export const MODULE_ORDER: ModuleId[] = ["ventas", "inventario", "compras", "proveedores", "caja", "informes"];

/** Which module a pathname belongs to (null for secondary pages such as /operations or /payment-channels). */
export function moduleForPath(pathname: string): ModuleId | null {
  const starts = (base: string) => pathname === base || pathname.startsWith(`${base}/`);
  if (starts("/sales")) return "ventas";
  if (starts("/inventory")) return "inventario";
  if (starts("/purchases")) return "compras";
  if (starts("/suppliers")) return "proveedores";
  if (starts("/cash")) return "caja";
  if (starts("/reports") || starts("/dashboard")) return "informes";
  return null;
}

/** Secondary destinations kept reachable from the "Más" menu (they are not Home modules). */
export const SECONDARY_LINKS = [
  { href: "/operations", label: "Resumen del día", description: "Ventas, gastos y efectivo de hoy" },
  { href: "/payment-channels", label: "Medios de pago", description: "Efectivo, transferencias y tarjetas" },
  { href: "/customers", label: "Clientes", description: "Para factura electrónica (Alegra)" },
  { href: "/electronic-invoicing", label: "Facturación electrónica", description: "Estado de facturas y preparación del cambio" },
] as const;
