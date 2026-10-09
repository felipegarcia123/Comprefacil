"use client";

import { useState } from "react";
import { InlineMessage, TouchButton } from "@/components/ui";
import type { ModuleId } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { errorText } from "@/lib/format";
import type { Customer, CustomerCreateResult, CustomerInput, IdentificationType, KindOfPerson } from "@/lib/types";
import { ID_TYPES, defaultKind } from "./identification";

type Props = {
  module?: ModuleId;
  /** Pre-fills the document number (e.g. what the cashier already typed in the search). */
  initialNumber?: string;
  onCreated: (customer: Customer, outcome: CustomerCreateResult["outcome"]) => void;
  onCancel: () => void;
  /** Extra action shown next to an Alegra error (e.g. "Volver a Consumidor final" in Venta). */
  errorAction?: { label: string; onClick: () => void };
};

/**
 * Only what Alegra requires to create a contact (document type + number, kind of person, and
 * names or razón social) plus optional contact details. The backend creates it in Alegra FIRST;
 * nothing is stored locally if Alegra rejects it, so the form keeps the data for a retry.
 */
export function CustomerCreateForm({ module = "ventas", initialNumber = "", onCreated, onCancel, errorAction }: Props) {
  const [type, setType] = useState<IdentificationType>("CC");
  const [kind, setKind] = useState<KindOfPerson>("PERSON_ENTITY");
  const [number, setNumber] = useState(/^[\d.\s-]+$/.test(initialNumber.trim()) ? initialNumber.trim() : "");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [company, setCompany] = useState(/^[\d.\s-]+$/.test(initialNumber.trim()) ? "" : initialNumber.trim());
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const person = kind === "PERSON_ENTITY";
  const valid = number.trim().length > 0 && (person ? firstName.trim() && lastName.trim() : company.trim());

  function changeType(next: IdentificationType) { setType(next); setKind(defaultKind(next)); }

  async function submit() {
    if (!valid || busy) return;
    setBusy(true); setError("");
    const body: CustomerInput = {
      identification_type: type, identification_number: number.trim(), kind_of_person: kind,
      ...(person ? { first_name: firstName.trim(), last_name: lastName.trim() } : { name: company.trim() }),
      email: email.trim() || undefined, phone: phone.trim() || undefined, address: address.trim() || undefined,
    };
    try { const result = await api.createCustomer(body); onCreated(result.customer, result.outcome); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <form className="ui-form customer-form" data-module={module} onSubmit={event => { event.preventDefault(); void submit(); }}>
      <div className="ui-form__channels">
        <label className="ui-field"><span>Tipo de documento</span>
          <select className="inventory-select" value={type} onChange={event => changeType(event.target.value as IdentificationType)}>
            {ID_TYPES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className="ui-field"><span>Número{type === "NIT" ? " (sin o con DV)" : ""}</span>
          <input autoFocus inputMode={type === "PP" || type === "FOREIGN_NIT" || type === "DIE" ? "text" : "numeric"} autoComplete="off" value={number} onChange={event => setNumber(event.target.value)} placeholder={type === "NIT" ? "900123456-8" : "1234567890"} />
        </label>
      </div>
      <div className="ui-choice" role="radiogroup" aria-label="Tipo de persona">
        {([["PERSON_ENTITY", "Persona natural", "Nombres y apellidos"], ["LEGAL_ENTITY", "Empresa", "Razón social"]] as const).map(([value, label, hint]) => (
          <button key={value} type="button" role="radio" aria-checked={kind === value} className={cx("ui-choice__item", kind === value && "ui-choice__item--on")} onClick={() => setKind(value)}>
            {label}<small>{hint}</small>
          </button>
        ))}
      </div>
      {person ? (
        <div className="ui-form__channels">
          <label className="ui-field"><span>Nombres</span><input autoComplete="off" value={firstName} onChange={event => setFirstName(event.target.value)} /></label>
          <label className="ui-field"><span>Apellidos</span><input autoComplete="off" value={lastName} onChange={event => setLastName(event.target.value)} /></label>
        </div>
      ) : (
        <label className="ui-field"><span>Razón social</span><input autoComplete="off" value={company} onChange={event => setCompany(event.target.value)} /></label>
      )}
      <label className="ui-field"><span>Correo (recibe la factura)</span><input type="email" inputMode="email" autoComplete="off" value={email} onChange={event => setEmail(event.target.value)} /></label>
      <div className="ui-form__channels">
        <label className="ui-field"><span>Teléfono (opcional)</span><input inputMode="tel" autoComplete="off" value={phone} onChange={event => setPhone(event.target.value)} /></label>
        <label className="ui-field"><span>Dirección (opcional)</span><input autoComplete="off" value={address} onChange={event => setAddress(event.target.value)} /></label>
      </div>
      {error && (
        <InlineMessage tone="danger" title="No se creó el cliente" actions={errorAction ? <TouchButton variant="secondary" onClick={errorAction.onClick}>{errorAction.label}</TouchButton> : undefined}>
          {error}. Corrige los datos e intenta de nuevo.
        </InlineMessage>
      )}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton type="submit" size="lg" module={module} disabled={!valid} loading={busy}>Crear cliente</TouchButton>
      </div>
    </form>
  );
}
