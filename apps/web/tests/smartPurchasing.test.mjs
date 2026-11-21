import assert from "node:assert/strict";
import test from "node:test";
import { purchaseStatusLabel, receiptPreview, receiptRequestIdentity, scheduleErrors } from "../lib/smartPurchasing.ts";

const purchase = {
  id: "purchase", items: [{ product_id: "product", product_name: "Leche",
    quantity: "1.555", unit_cost: "0.01", ordered_quantity: "1.555", ordered_unit_cost: "0.01" }],
};
const line = { product_id: "product", received_quantity: "1.555", actual_unit_cost: "0.01", difference_reason: null };

test("todos los estados de compra tienen nombre distinto", () => {
  assert.equal(purchaseStatusLabel.SCHEDULED, "Programado");
  assert.equal(new Set(Object.values(purchaseStatusLabel)).size, 4);
});

test("la vista previa redondea cada línea como FastAPI", () => {
  const result = receiptPreview(purchase, [line]);
  assert.deepEqual(result.errors, []);
  assert.equal(result.totalCents, 2n);
});

test("faltantes, cero y discrepancias exigen motivo y mercancía", () => {
  assert.match(receiptPreview(purchase, [{ ...line, received_quantity: "1" }]).errors[0], /Explica/);
  assert.match(receiptPreview(purchase, [{ ...line, received_quantity: "0", actual_unit_cost: null,
    difference_reason: "No llegó" }]).errors[0], /sin mercancía/);
  assert.deepEqual(receiptPreview(purchase, [{ ...line, received_quantity: "1", difference_reason: "Faltante" }]).errors, []);
});

test("la solicitud de recepción conserva su clave al reintentar el mismo contenido", () => {
  const first = receiptRequestIdentity(null, "same-body", () => "id-1");
  assert.equal(receiptRequestIdentity(first, "same-body", () => "id-2").id, "id-1");
  assert.equal(receiptRequestIdentity(first, "changed-body", () => "id-2").id, "id-2");
});

test("la programación valida vencimiento y descuentos contra el coste estimado", () => {
  const base = { expected_delivery_date: "2026-12-10", payment_terms: "CREDIT",
    payment_due_date: "2026-12-10", offers: [] };
  assert.match(scheduleErrors(purchase, base)[0], /posterior/);
  assert.deepEqual(scheduleErrors(purchase, { ...base, payment_due_date: "2027-01-01",
    offers: [{ product_id: "product", list_unit_cost: "1.01", discount_per_unit: "1.00" }] }), []);
  assert.match(scheduleErrors(purchase, { ...base, payment_due_date: "2027-01-01",
    offers: [{ product_id: "product", list_unit_cost: "1.01", discount_per_unit: "0.50" }] })[0], /descuento/);
});
