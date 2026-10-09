import { redirect } from "next/navigation";

/**
 * Folded into Informes (Fase 5, sección 15): the flexible date-range comparison this page offered
 * (`GET /reports/dashboard`, today/yesterday/last7/month/custom vs. previous period) now lives as
 * the "Rango" tab of /reports — the other three tabs there are jornada-based (Última jornada,
 * Jornadas, Mes) and never covered a calendar range, so nothing was actually duplicated by keeping
 * both; this route only stopped being a second analytics surface. The backend endpoint is untouched.
 */
export default function DashboardPage() {
  redirect("/reports");
}
