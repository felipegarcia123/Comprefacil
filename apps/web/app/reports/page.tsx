"use client";

import { useState } from "react";
import { Toldo, TouchTabs } from "@/components/ui";
import { LatestTab } from "./_components/LatestTab";
import { MonthTab } from "./_components/MonthTab";
import { RangeTab } from "./_components/RangeTab";
import { SessionsTab } from "./_components/SessionsTab";
import { PatrimonyTab } from "./_components/PatrimonyTab";
import { AccountsTab } from "./_components/AccountsTab";

type Tab = "latest" | "sessions" | "month" | "range" | "patrimony" | "accounts";

export default function ReportsPage() {
  const [tab, setTab] = useState<Tab>("latest");
  return (
    <main className="reports purchase" data-module="informes">
      <Toldo module="informes" />
      <div className="purchase__head">
        <div className="purchase__head-title"><h1>Informes</h1><p className="cash-open-since">Histórico y analítica por jornada</p></div>
      </div>
      <TouchTabs ariaLabel="Vista de informes" value={tab} onChange={id => setTab(id as Tab)}
        items={[
          { id: "latest", label: "Última jornada", module: "informes" },
          { id: "sessions", label: "Jornadas", module: "informes" },
          { id: "month", label: "Mes", module: "informes" },
          { id: "range", label: "Rango", module: "informes" },
          { id: "patrimony", label: "Patrimonio", module: "informes" },
          { id: "accounts", label: "Cuentas", module: "informes" },
        ]} />
      <div className="reports-body">
        {tab === "latest" && <LatestTab />}
        {tab === "sessions" && <SessionsTab />}
        {tab === "month" && <MonthTab />}
        {tab === "range" && <RangeTab />}
        {tab === "patrimony" && <PatrimonyTab />}
        {tab === "accounts" && <AccountsTab />}
      </div>
    </main>
  );
}
