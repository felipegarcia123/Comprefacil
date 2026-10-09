"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import type { BusinessAccount, PaymentChannel, PaymentMethod } from "@/lib/types";

type Form = { name: string; payment_type: PaymentMethod; display_order: number; description: string; provider: string; account_reference: string; alegra_payment_method: string; default_account_id: string; requires_destination_account: boolean };
type Body = { name: string; payment_type: PaymentMethod; display_order: number; description: string | null; provider: string | null; account_reference: string | null; alegra_payment_method: string | null; default_account_id: string | null; requires_destination_account: boolean };
const BLANK: Form = { name: "", payment_type: "TRANSFER", display_order: 0, description: "", provider: "", account_reference: "", alegra_payment_method: "", default_account_id: "", requires_destination_account: true };

/** Medio de pago DIAN que se declara en la factura (mismo catálogo que services/payment_methods.py). Solo configuración: el cajero nunca lo ve. */
const INVOICE_METHODS: { value: string; label: string }[] = [
  { value: "CASH", label: "Efectivo" },
  { value: "CREDIT_TRANSFER", label: "Transferencia" },
  { value: "BANK_DEPOSIT", label: "Consignación bancaria" },
  { value: "ACH_CREDIT", label: "Crédito ACH" },
  { value: "ACH_DEBIT", label: "Débito ACH" },
  { value: "CHECK", label: "Cheque" },
];

type Props = { target: PaymentChannel | null | undefined; busy: boolean; onClose: () => void; onSubmit: (body: Body) => Promise<void> };

/** Configuración de canales de pago — no es operación diaria (esa vive en Caja/Resumen), solo el catálogo. */
export function ChannelFormSheet({ target, busy, onClose, onSubmit }: Props) {
  const [form, setForm] = useState<Form>(BLANK);
  const [error, setError] = useState("");
  const [accounts, setAccounts] = useState<BusinessAccount[]>([]);
  useEffect(() => { api.businessAccounts(true).then(setAccounts).catch(() => setAccounts([])); }, []);

  useEffect(() => {
    setForm(target ? { name: target.name, payment_type: target.payment_type, display_order: target.display_order,
      description: target.description ?? "", provider: target.provider ?? "", account_reference: target.account_reference ?? "",
      alegra_payment_method: target.alegra_payment_method ?? "", default_account_id: target.default_account_id ?? "",
      requires_destination_account: Boolean(target.requires_destination_account) } : BLANK);
    setError("");
  }, [target]);

  const valid = form.name.trim().length >= 2;

  async function submit() {
    if (!valid) return;
    setError("");
    try {
      await onSubmit({ ...form, name: form.name.trim(), description: form.description.trim() || null,
        provider: form.provider.trim() || null, account_reference: form.account_reference.trim() || null,
        alegra_payment_method: form.payment_type === "CARD" ? null : form.alegra_payment_method || null,
        default_account_id: form.payment_type === "CASH" ? null : form.default_account_id || null,
        requires_destination_account: form.payment_type !== "CASH" && form.requires_destination_account });
    } catch (cause) { setError(errorText(cause)); }
  }

  return (
    <Sheet open={target !== undefined} title={target ? "Editar canal" : "Nuevo canal"} persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        <label className="ui-field"><span>Nombre</span><input autoFocus value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
        <div className="ui-form__channels">
          <label className="ui-field"><span>Tipo</span>
            <select className="inventory-select" value={form.payment_type} onChange={e => setForm({ ...form, payment_type: e.target.value as PaymentMethod })}>
              <option value="CASH">Efectivo</option>
              <option value="TRANSFER">Transferencia / digital</option>
              <option value="CARD">Tarjeta</option>
            </select>
          </label>
          <label className="ui-field"><span>Orden</span><input type="number" inputMode="numeric" min="0" value={form.display_order} onChange={e => setForm({ ...form, display_order: Number(e.target.value) })} /></label>
        </div>
        <label className="ui-field"><span>Descripción</span><input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
        <label className="ui-field"><span>Proveedor</span><input value={form.provider} onChange={e => setForm({ ...form, provider: e.target.value })} /></label>
        <label className="ui-field"><span>Referencia de cuenta</span><input value={form.account_reference} onChange={e => setForm({ ...form, account_reference: e.target.value })} /></label>
        {form.payment_type === "CARD" ? (
          <p className="channels-hint">Factura electrónica: en tarjeta el cajero elige Débito o Crédito al cobrar.</p>
        ) : (
          <label className="ui-field"><span>Se declara en la factura electrónica como</span>
            <select className="inventory-select" value={form.alegra_payment_method} onChange={e => setForm({ ...form, alegra_payment_method: e.target.value })}>
              <option value="">Sin definir (la factura queda bloqueada)</option>
              {INVOICE_METHODS.map(method => <option key={method.value} value={method.value}>{method.label}</option>)}
            </select>
          </label>
        )}
        {form.payment_type !== "CASH" && (
          <>
            <label className="sale-detail__check">
              <input type="checkbox" checked={form.requires_destination_account} onChange={e => setForm({ ...form, requires_destination_account: e.target.checked })} />
              Al cobrar, pedir a qué cuenta llegó el dinero (Nequi, Davivienda, Nu o DaviPlata)
            </label>
            <label className="ui-field"><span>Cuenta que se preselecciona al cobrar</span>
              <select className="inventory-select" value={form.default_account_id} onChange={e => setForm({ ...form, default_account_id: e.target.value })}>
                <option value="">Ninguna (el cajero elige)</option>
                {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
              </select>
            </label>
            <p className="channels-hint">El medio de pago no decide la cuenta: es solo una preselección y el cajero puede cambiarla. Desactiva la opción si el dinero de este canal no llega a ninguna de estas cuentas.</p>
          </>
        )}
        <p className="channels-hint">"Afecta caja física" se calcula solo: es Efectivo, o no lo es.</p>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="caja" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Guardar</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
