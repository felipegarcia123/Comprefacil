"use client";

import { useEffect, useState } from "react";
import { PenLine, ShieldAlert } from "lucide-react";
import { InlineMessage, Sheet, StatusBadge, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { dateMedium, dateTime, errorText, money } from "@/lib/format";
import type { InventoryValuation, PatrimonyDeclaration } from "@/lib/types";

const count = new Intl.NumberFormat("es-CO");
const percent = (value: string | number) => `${Number(value) > 0 ? "+" : ""}${new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 }).format(Number(value))}%`;
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random()}`);

export function variationText(data: { variation_amount?: string | number | null; variation_percent?: string | number | null }): string | null {
  if (data.variation_amount == null) return null;
  const amount = Number(data.variation_amount);
  return `${amount > 0 ? "+" : amount < 0 ? "−" : ""}${money.format(Math.abs(amount))}${data.variation_percent != null ? ` (${percent(data.variation_percent)})` : ""}`;
}

/**
 * VALOR CALCULADO POR EL POS — the inventory REGISTERED in the POS (stock × cost). Diagnostic only while the
 * official source is the owner's declaration: badge "No verificada", coverage first, never a "patrimonio" headline.
 */
export function RegisteredValuationBlock({ data }: { data: InventoryValuation }) {
  if (data.status === "NO_SNAPSHOT" || data.inventory_value == null) {
    return <p className="patrimony__coverage">Sin valoración registrada para esta jornada (se cerró antes de 0028).</p>;
  }
  const official = Boolean(data.is_official);
  return (
    <div className="patrimony patrimony--diagnostic">
      <p className="patrimony__diag-head">
        <StatusBadge tone={official ? "success" : "warning"}>{official ? "Oficial" : "No verificada"}</StatusBadge>
        <span>Valor calculado por el POS · inventario registrado al costo</span>
      </p>
      {!official && <InlineMessage tone="warning">{data.coverage_message ?? "Este valor es el inventario registrado en el POS, no la mercancía física de la tienda."}</InlineMessage>}
      <dl className="account-card__rows patrimony__diag-rows">
        <div><dt>Registrado en el POS</dt><dd className="ui-num">{money.format(Number(data.inventory_value))}</dd></div>
        <div><dt>De productos activos</dt><dd className="ui-num">{money.format(Number(data.active_value ?? 0))}</dd></div>
        <div><dt>De productos inactivos</dt><dd className="ui-num">{money.format(Number(data.inactive_value ?? 0))}</dd></div>
        <div><dt>Productos activos con existencias</dt><dd className="ui-num">{count.format(data.active_with_stock ?? 0)} de {count.format(data.active_catalog_count ?? 0)}</dd></div>
        {Boolean(data.products_missing_cost) && <div><dt>Con existencias sin costo</dt><dd className="ui-num">{count.format(data.products_missing_cost!)}</dd></div>}
      </dl>
      <p className="patrimony__coverage">Los productos con existencias en cero no necesariamente faltan en la tienda: el catálogo se cargó sin cantidades.</p>
    </div>
  );
}

/** VALOR DECLARADO POR EL DUEÑO of a closed jornada: value, variation vs the previous declaration, audited fixes. */
export function DeclaredPatrimonyReport({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<PatrimonyDeclaration | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<"declare" | "correct" | null>(null);
  useEffect(() => { setData(null); setError(""); api.sessionPatrimonyDeclaration(sessionId).then(setData).catch(cause => setError(errorText(cause))); }, [sessionId]);
  if (error) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!data) return <p className="recon-report__note" role="status">Cargando patrimonio…</p>;
  const variation = variationText(data);
  return (
    <div className="patrimony">
      {data.status === "DECLARED" ? <>
        <dl className="account-card__rows patrimony__declared">
          <div><dt>Valor declarado por el dueño · origen manual</dt><dd className="ui-num">{money.format(Number(data.declared_value))}</dd></div>
          <div><dt>Declaración anterior</dt><dd className="ui-num">{data.previous_value == null ? "Primera declaración" : `${money.format(Number(data.previous_value))}${data.previous_closed_at ? ` · ${dateMedium.format(new Date(data.previous_closed_at))}` : ""}`}</dd></div>
          <div><dt>Variación</dt><dd className={`ui-num ${variation ? (Number(data.variation_amount) < 0 ? "patrimony__var--down" : "patrimony__var--up") : ""}`}>{variation ?? "Sin declaración anterior para comparar"}</dd></div>
        </dl>
        {data.note && <p className="patrimony__coverage">Observación: {data.note}</p>}
        {data.corrections?.map(c => <p key={c.id} className="account-row__note">Corregido el {dateTime.format(new Date(c.created_at))}: {money.format(Number(c.old_value))} → {money.format(Number(c.new_value))} · {c.reason}</p>)}
        <div className="account-row__actions"><TouchButton variant="ghost" module="caja" icon={PenLine} onClick={() => setEditing("correct")}>Corregir valor declarado</TouchButton></div>
      </> : <>
        <InlineMessage tone="info" title="Sin valor declarado">Esta jornada no tiene valor de mercancía declarado. Es opcional; puedes registrarlo ahora.</InlineMessage>
        <div className="account-row__actions"><TouchButton variant="secondary" module="caja" icon={PenLine} onClick={() => setEditing("declare")}>Declarar valor de mercancía</TouchButton></div>
      </>}
      <DeclareSheet mode={editing} data={data} sessionId={sessionId} onClose={() => setEditing(null)} onDone={next => { setData(next); setEditing(null); }} />
    </div>
  );
}

function DeclareSheet({ mode, data, sessionId, onClose, onDone }: { mode: "declare" | "correct" | null; data: PatrimonyDeclaration; sessionId: string; onClose: () => void; onDone: (next: PatrimonyDeclaration) => void }) {
  const [value, setValue] = useState("");
  const [text, setText] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setValue(""); setText(""); setError(""); setKey(newKey()); }, [mode]);
  const number = Number(value);
  const valid = value.trim() !== "" && Number.isFinite(number) && number >= 0 && (mode !== "correct" || (text.trim().length >= 3 && number !== Number(data.declared_value)));

  async function submit() {
    setBusy(true); setError("");
    try {
      onDone(mode === "correct" && data.id
        ? await api.correctPatrimony(data.id, { new_value: number, reason: text.trim(), idempotency_key: key })
        : await api.declarePatrimony(sessionId, number, text.trim() || undefined));
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <Sheet open={mode !== null} title={mode === "correct" ? "Corregir valor declarado" : "Declarar valor de mercancía"} persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        {mode === "correct" && <p className="account-row__note">Declarado: <b className="ui-num">{money.format(Number(data.declared_value))}</b>. La corrección queda auditada (valor anterior, nuevo, motivo y fecha).</p>}
        <label className="ui-field"><span>{mode === "correct" ? "Valor correcto *" : "Valor total de la mercancía *"}</span>
          <input autoFocus type="number" inputMode="numeric" min="0" step="1" value={value} onChange={event => setValue(event.target.value)} /></label>
        <label className="ui-field"><span>{mode === "correct" ? "Motivo *" : "Observación (opcional)"}</span>
          <textarea rows={2} value={text} onChange={event => setText(event.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton module="caja" size="lg" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>{mode === "correct" ? "Guardar corrección" : "Guardar valor"}</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}

/** Small explanation used where both values appear: never mixed, the POS value is diagnostic. */
export function PatrimonyLegend() {
  return (
    <p className="patrimony__coverage"><ShieldAlert size={16} aria-hidden="true" /> El patrimonio oficial es el <b>valor declarado por el dueño</b>. El valor calculado por el POS solo incluye el inventario registrado y no está verificado.</p>
  );
}
