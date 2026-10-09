"use client";

import { FormEvent, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { cx } from "@/lib/cx";
import { money } from "@/lib/format";
import type { ManualItemKind } from "@/lib/types";

type Group = "SUPER" | "MASCOTAS";

/**
 * "Producto manual": adds ONE line without catalog product to the current sale and returns to the cart.
 * It never charges: payment and electronic invoice are decided once, at COBRAR, for the whole sale.
 * Súper -> category Súper; Mascotas -> granel (scale), bulto or otro (bags, mats…): all Mascotas sin código.
 */
export function ManualItemSheet({ busy, close, confirm }: {
  busy: boolean;
  close: () => void;
  confirm: (body: { kind: ManualItemKind; description: string; amount: number }) => Promise<void>;
}) {
  const [group, setGroup] = useState<Group>("SUPER");
  const [petKind, setPetKind] = useState<"GRANEL" | "BULTO" | "PET_OTHER">("GRANEL");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const kind: ManualItemKind = group === "SUPER" ? "SUPER" : petKind;
  const valid = Boolean(description.trim()) && Number(amount) > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    void confirm({ kind, description: description.trim(), amount: Number(amount) });
  }

  return (
    <Sheet open title="Producto manual" persistent onClose={close} module="ventas">
      <form className="ui-form manual-item" onSubmit={submit}>
        <div className="ui-choice" role="group" aria-label="Categoría del producto manual">
          <button type="button" aria-pressed={group === "SUPER"} className={cx("ui-choice__item", group === "SUPER" && "ui-choice__item--on")} onClick={() => setGroup("SUPER")}>Súper<small>Producto sin código</small></button>
          <button type="button" aria-pressed={group === "MASCOTAS"} className={cx("ui-choice__item", group === "MASCOTAS" && "ui-choice__item--on")} onClick={() => setGroup("MASCOTAS")}>Mascotas<small>Granel, bulto u otro sin código</small></button>
        </div>
        {group === "MASCOTAS" && (
          <div className="ui-choice manual-item__pet" role="group" aria-label="¿Qué tipo de producto?">
            <button type="button" aria-pressed={petKind === "GRANEL"} className={cx("ui-choice__item", petKind === "GRANEL" && "ui-choice__item--on")} onClick={() => setPetKind("GRANEL")}>Granel<small>Comida por peso (báscula)</small></button>
            <button type="button" aria-pressed={petKind === "BULTO"} className={cx("ui-choice__item", petKind === "BULTO" && "ui-choice__item--on")} onClick={() => setPetKind("BULTO")}>Bulto<small>Comida en bulto</small></button>
            <button type="button" aria-pressed={petKind === "PET_OTHER"} className={cx("ui-choice__item", petKind === "PET_OTHER" && "ui-choice__item--on")} onClick={() => setPetKind("PET_OTHER")}>Otro<small>Bolsa, tapete, accesorio…</small></button>
          </div>
        )}
        <label className="ui-field"><span>Descripción</span><input autoFocus value={description} maxLength={180} onChange={event => setDescription(event.target.value)} placeholder={group === "SUPER" ? "Ej.: Dulce surtido" : petKind === "PET_OTHER" ? "Ej.: Bolsas para mascota" : "Ej.: Concentrado perro"} /></label>
        <label className="ui-field"><span>Valor</span><input inputMode="numeric" value={amount} onChange={event => setAmount(event.target.value.replace(/\D/g, ""))} placeholder="$ 0" aria-describedby="manual-item-value" /></label>
        <p id="manual-item-value" className="manual-item__value ui-num" aria-live="polite">{Number(amount) > 0 ? money.format(Number(amount)) : " "}</p>
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={close} disabled={busy}>Volver</TouchButton>
          <TouchButton size="lg" type="submit" disabled={!valid || busy} loading={busy}>Agregar a la venta</TouchButton>
        </div>
      </form>
    </Sheet>
  );
}
