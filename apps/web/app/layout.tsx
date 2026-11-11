import "@fontsource-variable/bricolage-grotesque/wght.css";
import "@fontsource-variable/atkinson-hyperlegible-next/wght.css";
import "./globals.css";
import { AppHeader } from "@/components/AppHeader";
import { ContextBar } from "@/components/ContextBar";
import { OperationalSessionProvider } from "@/components/OperationalSessionProvider";

const environmentLabel = process.env.NEXT_PUBLIC_ENVIRONMENT_LABEL;
const isDemo = process.env.NEXT_PUBLIC_DEMO_MODE === "1";
export const metadata = { title: environmentLabel ? `${environmentLabel} · Comprefácil` : "Comprefácil · POS", description: "Punto de venta" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{(environmentLabel || isDemo) && <div className="environment-banner" role="status">{environmentLabel || "DEMO · datos ficticios"} · Datos aislados de producción</div>}<OperationalSessionProvider><AppHeader /><ContextBar />{children}</OperationalSessionProvider></body></html>;
}
