"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Ellipsis, House, Menu } from "lucide-react";
import { useOperationalSession } from "@/components/OperationalSessionProvider";
import { useOpenSalesCount, useWorkContexts } from "@/lib/workContexts";
import { ContextBadge, MODULES, MODULE_ORDER, SECONDARY_LINKS, Sheet, StatusBadge, Toldo, TouchButton, moduleForPath } from "@/components/ui";

/** Jornada state, shown once per screen. Home shows it on the Caja card instead, so the header omits it there. */
function SessionBadge() {
  const { summary, loading, error } = useOperationalSession();
  const badge = loading ? <StatusBadge>Caja…</StatusBadge>
    : error ? <StatusBadge tone="danger">Sin conexión</StatusBadge>
    : summary ? <StatusBadge tone="success"><span className="ui-hide-movil">Caja </span>abierta</StatusBadge>
    : <StatusBadge tone="warning"><span className="ui-hide-movil">Caja </span>cerrada</StatusBadge>;
  return <Link href="/cash" className="ui-appbar__session" data-module="caja" aria-label="Estado de la caja. Ir a Caja">{badge}</Link>;
}

/**
 * Global bar. Home: brand + menu only (the six modules are the page). Elsewhere: Inicio, module navigation
 * (labels from 900px, icons from 640px, replaced by the menu sheet on phones) and the jornada state.
 */
export function AppHeader() {
  const pathname = usePathname();
  const isHome = pathname === "/";
  const current = moduleForPath(pathname);
  const openSales = useOpenSalesCount();
  const { purchase: activePurchase } = useWorkContexts();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [pathname]);

  return (
    <header className="ui-appbar">
      <div className="ui-appbar__inner">
        {isHome ? (
          <span className="ui-appbar__brand">Mi tienda</span>
        ) : (
          <>
            <Link href="/" className="ui-appbar__home" aria-label="Inicio">
              <House size={24} aria-hidden="true" />
              <span className="ui-appbar__label">Inicio</span>
            </Link>
            <nav className="ui-appbar__nav" aria-label="Módulos">
              {MODULE_ORDER.map(id => {
                const Icon = MODULES[id].icon;
                const active = current === id;
                const badge = id === "ventas" && openSales >= 2 ? openSales : null; // one open sale is the norm: no noise
                const name = badge ? `${MODULES[id].label}, ${badge} abiertas` : MODULES[id].label;
                return (
                  <Link key={id} href={MODULES[id].href} data-module={id} className="ui-appbar__link" aria-current={active ? "page" : undefined} aria-label={name} title={name}>
                    {active && <Toldo module={id} />}
                    <Icon size={24} aria-hidden="true" />
                    <span className="ui-appbar__label">{MODULES[id].label}</span>
                    {badge && <span className="ui-appbar__count ui-num" aria-hidden="true">{badge}</span>}
                  </Link>
                );
              })}
            </nav>
            {current && <span className="ui-appbar__context"><ContextBadge module={current} label={current === "ventas" && openSales >= 2 ? `Ventas · ${openSales}` : current === "compras" && activePurchase ? activePurchase.label : MODULES[current].label} /></span>}
          </>
        )}
        <span className="ui-appbar__spacer" />
        {!isHome && <SessionBadge />}
        <TouchButton variant="ghost" size="md" aria-label={isHome ? "Más opciones" : "Menú"} aria-haspopup="dialog" onClick={() => setMenu(true)}>
          {isHome ? <Ellipsis size={24} aria-hidden="true" /> : <><Menu size={24} aria-hidden="true" className="ui-only-movil" /><Ellipsis size={24} aria-hidden="true" className="ui-from-tablet" /></>}
          <span className="ui-appbar__label--menu">Más</span>
        </TouchButton>
      </div>

      <Sheet open={menu} title={isHome ? "Más opciones" : "Menú"} onClose={() => setMenu(false)}>
        {!isHome && (
          <div className="ui-menu-modules">
            <ul className="ui-menu-list">
              <li><Link href="/" className="ui-menu-link"><span className="ui-menu-link__icon"><House size={24} aria-hidden="true" /></span>Inicio</Link></li>
              {MODULE_ORDER.map(id => {
                const Icon = MODULES[id].icon;
                return (
                  <li key={id}>
                    <Link href={MODULES[id].href} className="ui-menu-link" data-module={id} aria-current={current === id ? "page" : undefined}>
                      <span className="ui-menu-link__icon"><Icon size={24} aria-hidden="true" /></span>
                      <span>{MODULES[id].label}<small>{MODULES[id].description}</small></span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <p className="ui-menu-section">Más</p>
          </div>
        )}
        <ul className="ui-menu-list">
          {SECONDARY_LINKS.map(link => (
            <li key={link.href}>
              <Link href={link.href} className="ui-menu-link" aria-current={pathname === link.href ? "page" : undefined}>
                <span>{link.label}<small>{link.description}</small></span>
              </Link>
            </li>
          ))}
        </ul>
      </Sheet>
    </header>
  );
}
