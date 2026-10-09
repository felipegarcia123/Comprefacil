import { Display, InlineMessage } from "@/components/ui";
import { money } from "@/lib/format";
import type { ReconciliationBlock1 } from "@/lib/types";

const CATEGORY_LABEL: Record<string, string> = {
  CIGARROS: "Cigarros", MASCOTAS_CON_CODIGO: "Mascotas con código", LICORES: "Licores",
  MASCOTAS_SIN_CODIGO: "Mascotas sin código — no báscula",
};

/** Missing scale closing: what the operator must complete (never assumed 0). */
export const SCALE_MISSING = "Falta registrar el cierre de la báscula para completar el cuadre parcial.";

/**
 * Block 1 of the day's closing, read-only: scale closing, POS cash of the block-1 categories, partial subtotal and the
 * granel the POS observed. A PARTIAL reconciliation subtotal — never "esperado", "diferencia", "sobrante" or "faltante".
 */
export function Block1Report({ data, showScale = true }: { data: ReconciliationBlock1; showScale?: boolean }) {
  const amount = (value: string | null | undefined) => money.format(Number(value ?? 0));
  return (
    <div className="recon-report">
      {showScale && (
        <section className="recon-report__block" aria-label="Báscula">
          <h3>Báscula</h3>
          {data.scale ? (
            <>
              <p className="operations-row"><span>Total registrado</span><b className="ui-num">{amount(data.scale.total)}</b></p>
              <p className="operations-row"><span>Pagos digitales</span><b className="ui-num">{amount(data.scale.digital)}</b></p>
              <p className="operations-row recon-report__sum"><span>Efectivo báscula</span><b className="ui-num">{amount(data.scale.cash)}</b></p>
            </>
          ) : <InlineMessage tone="warning">{SCALE_MISSING}</InlineMessage>}
        </section>
      )}
      <section className="recon-report__block" aria-label="Efectivo POS — bloque parcial">
        <h3>Efectivo POS — bloque parcial</h3>
        {data.pos_cash_by_category.map(row => (
          <p key={row.category_code} className="operations-row"><span>{CATEGORY_LABEL[row.category_code] ?? row.category_code}</span><b className="ui-num">{amount(row.amount)}</b></p>
        ))}
        <p className="operations-row recon-report__sum"><span>Subtotal POS</span><b className="ui-num">{amount(data.pos_cash_subtotal)}</b></p>
      </section>
      <Display size="figure" label="Subtotal parcial del cuadre"
        value={data.block1_subtotal === null ? "Falta la báscula" : amount(data.block1_subtotal)} />
      <p className="recon-report__note">Este es un subtotal parcial de conciliación. No representa todavía el efectivo final de caja.</p>
      <section className="recon-report__block recon-report__control" aria-label="Control informativo">
        <h3>Control informativo</h3>
        <p className="operations-row"><span>Granel observado por el POS</span><b className="ui-num">{amount(data.observed.granel_external_scale)}</b></p>
      </section>
      {Number(data.returns_cash_total ?? 0) > 0 && (
        <section className="recon-report__block recon-report__control" aria-label="Devoluciones en efectivo de la jornada">
          <h3>Devoluciones en efectivo (aparte)</h3>
          {(data.returns_cash_by_category ?? []).filter(row => Number(row.amount) > 0).map(row => (
            <p key={row.category_code} className="operations-row"><span>{CATEGORY_LABEL[row.category_code] ?? row.category_code}</span><b className="ui-num">{amount(row.amount)}</b></p>
          ))}
          <p className="recon-report__note">Se muestran aparte: no se restan del subtotal del bloque 1.</p>
        </section>
      )}
      {Number(data.exchange_difference_cash_total ?? 0) > 0 && (
        <section className="recon-report__block recon-report__control" aria-label="Diferencias de cambios cobradas en efectivo">
          <h3>Diferencias cobradas en cambios, en efectivo (aparte)</h3>
          {(data.exchange_difference_cash_by_category ?? []).filter(row => Number(row.amount) > 0).map(row => (
            <p key={row.category_code} className="operations-row"><span>{CATEGORY_LABEL[row.category_code] ?? row.category_code}</span><b className="ui-num">{amount(row.amount)}</b></p>
          ))}
          <p className="recon-report__note">Dinero real que entró al cajón por cambios de producto. Se muestra aparte: no se suma al subtotal del bloque 1. Un cambio sin diferencia no aparece aquí.</p>
        </section>
      )}
      {Number(data.exchange_refund_cash_total ?? 0) > 0 && (
        <section className="recon-report__block recon-report__control" aria-label="Diferencias devueltas en cambios en efectivo">
          <h3>Diferencias devueltas en cambios, en efectivo (aparte)</h3>
          {(data.exchange_refund_cash_by_category ?? []).filter(row => Number(row.amount) > 0).map(row => (
            <p key={row.category_code} className="operations-row"><span>{CATEGORY_LABEL[row.category_code] ?? row.category_code}</span><b className="ui-num">{amount(row.amount)}</b></p>
          ))}
          <p className="recon-report__note">Dinero real que salió del cajón por cambios de producto más baratos. Se muestra aparte: no se resta del subtotal del bloque 1.</p>
        </section>
      )}
    </div>
  );
}
