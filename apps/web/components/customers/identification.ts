import type { IdentificationType, KindOfPerson } from "@/lib/types";

/** Alegra's Colombian identification types, most common first. */
export const ID_TYPES: { value: IdentificationType; label: string }[] = [
  { value: "CC", label: "CC · Cédula" },
  { value: "NIT", label: "NIT" },
  { value: "CE", label: "CE · Extranjería" },
  { value: "PP", label: "PP · Pasaporte" },
  { value: "TI", label: "TI · Tarjeta ident." },
  { value: "NUIP", label: "NUIP" },
  { value: "RC", label: "RC · Registro civil" },
  { value: "TE", label: "TE · Tarjeta extranj." },
  { value: "DIE", label: "DIE · Doc. extranjero" },
  { value: "FOREIGN_NIT", label: "NIT extranjero" },
];

/** DIAN's generic identification of Consumidor final: shown, never typed by the cashier. */
export const FINAL_CONSUMER_ID = "222222222222";

export const defaultKind = (type: IdentificationType): KindOfPerson => (type === "NIT" ? "LEGAL_ENTITY" : "PERSON_ENTITY");

const groupDigits = (value: string) => (/^\d+$/.test(value) ? Number(value).toLocaleString("es-CO") : value);

/** "CC 1.234.567.890" · "NIT 900.123.456-8" */
export function formatIdentification(type: string, number: string, dv?: string | null): string {
  const label = type === "FOREIGN_NIT" ? "NIT ext." : type;
  return `${label} ${groupDigits(number)}${dv ? `-${dv}` : ""}`;
}
