import "@fontsource-variable/bricolage-grotesque/wght.css";
import "@fontsource-variable/atkinson-hyperlegible-next/wght.css";
import "./globals.css";
import { AppHeader } from "@/components/AppHeader";
import { ContextBar } from "@/components/ContextBar";
import { OperationalSessionProvider } from "@/components/OperationalSessionProvider";

export const metadata = { title: "POS Colombia", description: "Punto de venta" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body><OperationalSessionProvider><AppHeader /><ContextBar />{children}</OperationalSessionProvider></body></html>;
}
