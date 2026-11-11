"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import type { ReceiptAnalysis, ReceiptCandidate, ReceiptCatalogDiagnostic, ReceiptLine, ReceiptRegion, Supplier } from "@/lib/types";
import "./receipt-ai.css";

type DraftLine = ReceiptLine & { selected?: ReceiptCandidate };

function asDraft(line: ReceiptLine): DraftLine { return { ...line }; }

export default function ReceiptAiPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [analysis, setAnalysis] = useState<ReceiptAnalysis | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [diagnostic, setDiagnostic] = useState<ReceiptCatalogDiagnostic | null>(null);
  const [vendor, setVendor] = useState("");
  const [region, setRegion] = useState<ReceiptRegion | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [savingEquivalence, setSavingEquivalence] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [supplierId, setSupplierId] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [creatingPurchase, setCreatingPurchase] = useState(false);
  const [createdPurchaseId, setCreatedPurchaseId] = useState<string | null>(null);

  useEffect(() => { void api.receiptCatalogDiagnostic().then(setDiagnostic).catch(() => undefined); }, []);
  useEffect(() => { void api.suppliers("", true).then(setSuppliers).catch(() => undefined); }, []);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  async function readFile(file: File) {
    setError(""); setNotice(""); setBusy(true);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(file.type.startsWith("image/") ? URL.createObjectURL(file) : "");
    try {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("No se pudo leer el archivo"));
        reader.onload = () => resolve(String(reader.result).split(",").pop() || "");
        reader.readAsDataURL(file);
      });
      const result = await api.analyzeReceipt({ filename: file.name, content_type: file.type || "application/octet-stream", content_base64: encoded, vendor_hint: vendor || undefined, region });
      setAnalysis(result); setLines(result.lines.map(asDraft)); setNotice(result.message);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  function addLine() {
    const line: DraftLine = { line_id: crypto.randomUUID(), original_text: "", supplier_code: null, interpreted_name: "", brand: null, variant: null, size: null, package_quantity: "1", units_per_package: "1", total_units: "1", unit_cost: null, discount: null, confidence: 0, review_state: "REVIEW_REQUIRED", region: null, candidates: [] };
    setLines(current => [...current, line]);
  }

  function updateLine(id: string, patch: Partial<DraftLine>) { setLines(current => current.map(line => line.line_id === id ? { ...line, ...patch } : line)); }

  async function findMatches(line: DraftLine) {
    if (!line.interpreted_name?.trim() && !line.supplier_code?.trim()) return;
    try { updateLine(line.line_id, { candidates: await api.receiptCandidates(line.interpreted_name || "", undefined, vendor || undefined, line.supplier_code || undefined) }); }
    catch (cause) { setError(errorText(cause)); }
  }

  async function confirmEquivalence(line: DraftLine) {
    if (!vendor.trim() || !line.supplier_code?.trim() || !line.selected) return;
    setSavingEquivalence(line.line_id); setError("");
    try {
      await api.confirmReceiptEquivalence({ provider: vendor.trim(), material_code: line.supplier_code.trim(), product_id: line.selected.product_id, source_description: line.original_text, source_brand: line.brand, source_variant: line.variant, source_size: line.size });
      updateLine(line.line_id, { review_state: "READY" });
      setNotice("Equivalencia confirmada y guardada para futuras recepciones; no se modificó inventario.");
    } catch (cause) { setError(errorText(cause)); }
    finally { setSavingEquivalence(null); }
  }

  const units = useMemo(() => lines.reduce((sum, line) => sum + Number(line.total_units || 0), 0), [lines]);
  const ready = lines.filter(line => line.review_state === "READY" && line.selected).length;
  const canCreatePurchase = Boolean(supplierId && invoiceNumber.trim() && lines.length && lines.every(line => line.selected && Number(line.total_units) > 0 && line.unit_cost !== null && Number(line.unit_cost) >= 0));

  async function createDraftPurchase() {
    if (!canCreatePurchase) return;
    setCreatingPurchase(true); setError(""); setCreatedPurchaseId(null);
    try {
      const purchase = await api.createReceiptPurchase({
        confirmed: true, supplier_id: supplierId, invoice_number: invoiceNumber.trim(), source_sha256: analysis?.sha256 || null,
        items: lines.map(line => ({ product_id: line.selected!.product_id, quantity: Number(line.total_units), unit_cost: Number(line.unit_cost) })),
      });
      setCreatedPurchaseId(purchase.id);
      setNotice("Compra creada como borrador. El inventario aún no cambia; revísala y pulsa Recibir en Compras.");
    } catch (cause) { setError(errorText(cause)); }
    finally { setCreatingPurchase(false); }
  }

  return <main className="receipt-ai-shell">
    <header className="receipt-ai-header">
      <div><Link href="/purchases">← Compras</Link><h1>Recepción inteligente · prototipo</h1><p>Fotografía → borrador revisable. Esta pantalla nunca modifica inventario, costos ni compras.</p></div>
      <span className="receipt-ai-badge">SIMULACIÓN / REVISIÓN HUMANA</span>
    </header>

    <section className="receipt-ai-grid">
      <aside className="receipt-ai-card receipt-ai-upload">
        <h2>1. Documento</h2>
        <label className="receipt-ai-drop"><input ref={inputRef} type="file" accept="image/*,.csv,.txt" onChange={event => { const file = event.target.files?.[0]; if (file) void readFile(file); }} />
          <strong>{busy ? "Procesando…" : "Subir fotografía o fixture CSV"}</strong><span>Colanta, Coca-Cola u otro proveedor</span>
        </label>
        <label className="receipt-ai-field"><span>Proveedor (opcional)</span><input value={vendor} onChange={event => setVendor(event.target.value)} placeholder="Colanta / Coca-Cola" /></label>
        <label className="receipt-ai-field"><span>Proveedor del catálogo</span><select value={supplierId} onChange={event => setSupplierId(event.target.value)}><option value="">Selecciona proveedor</option>{suppliers.map(supplier => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
        <label className="receipt-ai-field"><span>Número de factura</span><input value={invoiceNumber} onChange={event => setInvoiceNumber(event.target.value)} placeholder="Obligatorio para evitar duplicados" /></label>
        <div className="receipt-ai-region"><b>Región de productos (opcional)</b><span>Coordenadas relativas de la imagen, entre 0 y 1.</span><div><input aria-label="x" placeholder="x" inputMode="decimal" onChange={event => setRegion(current => ({ x:Number(event.target.value)||0, y:current?.y||0, width:current?.width||1, height:current?.height||1 }))} /><input aria-label="y" placeholder="y" inputMode="decimal" onChange={event => setRegion(current => ({ x:current?.x||0, y:Number(event.target.value)||0, width:current?.width||1, height:current?.height||1 }))} /><input aria-label="ancho" placeholder="ancho" inputMode="decimal" onChange={event => setRegion(current => ({ x:current?.x||0, y:current?.y||0, width:Number(event.target.value)||1, height:current?.height||1 }))} /><input aria-label="alto" placeholder="alto" inputMode="decimal" onChange={event => setRegion(current => ({ x:current?.x||0, y:current?.y||0, width:current?.width||1, height:Number(event.target.value)||1 }))} /></div></div>
        {previewUrl ? <img className="receipt-ai-preview" src={previewUrl} alt="Documento cargado" /> : <div className="receipt-ai-empty">La imagen se mostrará aquí. Puedes recortar en la siguiente iteración; la región queda registrada en cada línea.</div>}
        {analysis && <div className="receipt-ai-meta"><b>{analysis.filename}</b><span>Adaptador: {analysis.adapter}</span><span>SHA-256: {analysis.sha256.slice(0, 16)}…</span></div>}
        {diagnostic && <div className="receipt-ai-diagnostic"><b>Catálogo disponible para asociación</b><span>{diagnostic.active_products.toLocaleString("es-CO")} activos · {diagnostic.with_barcode.toLocaleString("es-CO")} con código</span><span>{diagnostic.without_barcode.toLocaleString("es-CO")} sin código · {diagnostic.duplicate_normalized_names} nombres potencialmente duplicados</span></div>}
      </aside>

      <section className="receipt-ai-card receipt-ai-lines">
        <div className="receipt-ai-section-head"><div><h2>2. Líneas detectadas</h2><p>Edita datos ambiguos; no se completan automáticamente.</p></div><button className="receipt-ai-button" onClick={addLine}>+ Agregar línea</button></div>
        {error && <div className="receipt-ai-alert danger">{error}</div>}
        {notice && <div className="receipt-ai-alert">{notice}</div>}
        {!lines.length ? <div className="receipt-ai-empty">Aún no hay líneas. Con imágenes, el adaptador multimodal debe estar configurado; mientras tanto puedes cargar un CSV de prueba o agregar líneas manualmente.</div> : <div className="receipt-ai-line-list">{lines.map(line => <LineCard key={line.line_id} line={line} update={patch => updateLine(line.line_id, patch)} find={() => void findMatches(line)} select={candidate => updateLine(line.line_id, { selected: candidate, review_state: "REVIEW_REQUIRED" })} confirm={() => void confirmEquivalence(line)} confirming={savingEquivalence === line.line_id} />)}</div>}
        <div className="receipt-ai-summary"><span><b>{lines.length}</b> líneas · <b>{units}</b> unidades propuestas</span><span><b>{ready}</b> listas para revisión final</span></div>
      </section>
    </section>
    <footer className="receipt-ai-footer"><b>Confirmación humana:</b> crear borrador no modifica inventario. {createdPurchaseId ? <Link href={`/purchases?id=${createdPurchaseId}`}>Abrir compra en Compras</Link> : <button className="receipt-ai-button" disabled={!canCreatePurchase || creatingPurchase} onClick={() => void createDraftPurchase()}>{creatingPurchase ? "Creando…" : "Crear compra para revisión"}</button>}</footer>
  </main>;
}

function LineCard({ line, update, find, select, confirm, confirming }: { line: DraftLine; update: (patch: Partial<DraftLine>) => void; find: () => void; select: (candidate: ReceiptCandidate) => void; confirm: () => void; confirming: boolean }) {
  return <article className={`receipt-ai-line ${line.review_state.toLowerCase()}`}>
    <div className="receipt-ai-line-top"><span className="receipt-ai-state">{line.review_state === "READY" ? "LISTA" : line.review_state === "UNMATCHED" ? "SIN COINCIDENCIA" : "REVISAR"}</span><small>{line.original_text || "Línea manual"}</small></div>
    <div className="receipt-ai-form-grid">
      <label className="receipt-ai-field wide"><span>Nombre interpretado</span><input value={line.interpreted_name || ""} onChange={event => update({ interpreted_name: event.target.value, review_state: "REVIEW_REQUIRED" })} /></label>
      <label className="receipt-ai-field"><span>Código proveedor</span><input value={line.supplier_code || ""} onChange={event => update({ supplier_code: event.target.value })} /></label>
      <label className="receipt-ai-field"><span>Paquetes</span><input inputMode="decimal" value={line.package_quantity || ""} onChange={event => update({ package_quantity: event.target.value })} /></label>
      <label className="receipt-ai-field"><span>Unid./paquete</span><input inputMode="decimal" value={line.units_per_package || ""} onChange={event => update({ units_per_package: event.target.value })} /></label>
      <label className="receipt-ai-field"><span>Total unidades</span><input inputMode="decimal" value={line.total_units || ""} onChange={event => update({ total_units: event.target.value })} /></label>
      <label className="receipt-ai-field"><span>Costo unitario</span><input inputMode="decimal" value={line.unit_cost || ""} onChange={event => update({ unit_cost: event.target.value })} placeholder="Pendiente" /></label>
    </div>
    <div className="receipt-ai-line-actions"><span>Confianza: {Math.round(line.confidence * 100)}%</span><button className="receipt-ai-button secondary" onClick={find}>Buscar equivalencias</button>{line.selected && line.supplier_code && <button className="receipt-ai-button" onClick={confirm} disabled={confirming}>{confirming ? "Guardando…" : "Confirmar equivalencia"}</button>}</div>
    {!!line.candidates.length && <div className="receipt-ai-candidates"><b>Candidatos del inventario</b>{line.candidates.map(candidate => <button key={candidate.product_id} className={line.selected?.product_id === candidate.product_id ? "selected" : ""} onClick={() => select(candidate)}><span>{candidate.name}</span><small>{candidate.category || "Sin categoría"} · {candidate.barcode || "sin código"} · stock {candidate.current_stock} · {Math.round(candidate.score * 100)}%</small><em>{candidate.reasons.join(" · ")}</em></button>)}</div>}
  </article>;
}

