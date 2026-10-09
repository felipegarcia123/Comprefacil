"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import type { PaymentChannel } from "@/lib/types";
import { errorText } from "@/lib/format";
import { DataTable, InlineMessage, StatusBadge, Td, Th, Toldo, TouchButton, Tr } from "@/components/ui";
import { useBreakpoint } from "@/lib/useBreakpoint";
import { ChannelFormSheet } from "./_components/ChannelFormSheet";

const TYPE_LABEL: Record<string, string> = { CASH: "Efectivo", TRANSFER: "Transferencia / digital", CARD: "Tarjeta" };

/** Configuración, no operación diaria (section 29): el catálogo de canales que Venta, Caja y Resumen ya consumen; nada de esto cambia sus reglas. */
export default function PaymentChannelsPage() {
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [editing, setEditing] = useState<PaymentChannel | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const compact = useBreakpoint() === "movil";

  async function load() { try { setChannels(await api.paymentChannels()); } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); } }
  useEffect(() => { void load(); }, []);

  async function toggle(channel: PaymentChannel) {
    try { await api.setPaymentChannelActive(channel.id, !channel.active); await load(); setNotice({ tone: "success", text: channel.active ? "Canal desactivado" : "Canal activado" }); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function submit(body: Parameters<typeof api.createPaymentChannel>[0]) {
    setBusy(true);
    try {
      if (editing) await api.updatePaymentChannel(editing.id, body); else await api.createPaymentChannel(body);
      setEditing(undefined); await load(); setNotice({ tone: "success", text: "Canal guardado correctamente" });
    } finally { setBusy(false); }
  }

  return (
    <main className="channels purchase" data-module="caja">
      <Toldo module="caja" />
      <div className="purchase__head">
        <div className="purchase__head-title"><h1>Medios de pago</h1><p className="cash-open-since">Efectivo, billeteras y datáfonos · el resumen de ventas vive en Resumen y Caja</p></div>
        <TouchButton module="caja" icon={Plus} onClick={() => setEditing(null)}>Nuevo canal</TouchButton>
      </div>

      <div className="purchase__notice">{notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}</div>

      <div className="channels-body">
        {channels.length ? (
          <DataTable caption="Medios de pago" className="channels-cart" module="caja" density="dense" stack={compact}>
            <thead><tr><Th>Canal</Th><Th>Tipo</Th>{!compact && <Th>Caja física</Th>}{!compact && <Th numeric>Orden</Th>}<Th>Estado</Th><Th>Acciones</Th></tr></thead>
            <tbody>
              {channels.map(channel => (
                <Tr key={channel.id}>
                  <Td label="Canal"><span className="sale-name">{channel.name}{channel.provider && <small className="ui-td--muted">{channel.provider}</small>}</span></Td>
                  <Td label="Tipo" muted>{TYPE_LABEL[channel.payment_type] ?? channel.payment_type}</Td>
                  {!compact && <Td label="Caja física">{channel.affects_cash ? "Sí" : "No"}</Td>}
                  {!compact && <Td numeric label="Orden" className="ui-num">{channel.display_order}</Td>}
                  <Td label="Estado"><StatusBadge tone={channel.active ? "success" : "neutral"}>{channel.active ? "Activo" : "Inactivo"}</StatusBadge></Td>
                  <Td label="Acciones"><div className="ui-row">
                    <TouchButton variant="secondary" size="md" onClick={() => setEditing(channel)}>Editar</TouchButton>
                    <TouchButton variant={channel.active ? "danger" : "primary"} size="md" onClick={() => void toggle(channel)}>{channel.active ? "Desactivar" : "Activar"}</TouchButton>
                  </div></Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="ui-empty-hint">Todavía no hay medios de pago configurados.</p>}
      </div>

      <ChannelFormSheet target={editing} busy={busy} onClose={() => setEditing(undefined)} onSubmit={submit} />
    </main>
  );
}
