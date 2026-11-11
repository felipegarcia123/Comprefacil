"use client";

import Link from "next/link";
import { ChangeEvent, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ProductImportPreview, ProductImportResult, ProductImportRow } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_DEMO_MODE === "1" ? "" : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1");
const fields = [
  ["", "Ignorar columna"], ["barcode", "Código"], ["name", "Nombre"], ["category", "Categoría"],
  ["supplier", "Proveedor"], ["purchase_cost", "Costo de compra"], ["sale_price", "Precio de venta"],
  ["initial_stock", "Stock inicial"], ["minimum_stock", "Stock mínimo"], ["unit", "Unidad"],
  ["description", "Descripción"], ["active", "Activo"],
] as const;
const aliases: Record<string,string[]> = {
  barcode:["codigo","cod","codigo de barras","barcode","ean","sku"], name:["nombre","producto","descripcion","descripcion producto"],
  category:["categoria","category"], supplier:["proveedor","supplier"], purchase_cost:["costo","precio compra","costo compra","purchase cost"],
  sale_price:["precio","precio venta","venta","sale price"], initial_stock:["existencia","existencias","stock","cantidad","stock inicial"],
  minimum_stock:["stock minimo","minimo"], unit:["unidad","unit"], description:["observacion","detalle"], active:["activo","active","estado"],
};

export default function ProductImportPage() {
  const [file, setFile] = useState<File|null>(null), [content, setContent] = useState(""), [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string,string>>({}), [delimiter, setDelimiter] = useState<"auto"|","|";">("auto");
  const [preview, setPreview] = useState<ProductImportPreview|null>(null), [result, setResult] = useState<ProductImportResult|null>(null);
  const [filter, setFilter] = useState<"ALL"|ProductImportRow["status"]>("ALL"), [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const shown = useMemo(() => (preview?.rows ?? []).filter(row => filter === "ALL" || row.status === filter).slice(0,50), [preview,filter]);

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0]; setError(""); setPreview(null); setResult(null);
    if (!selected) return;
    if (!selected.name.toLowerCase().endsWith(".csv")) return setError("Selecciona un archivo con extensión .csv");
    if (selected.size > 10*1024*1024) return setError("El archivo supera el límite de 10 MB");
    try {
      const text = new TextDecoder("utf-8", { fatal:true }).decode(await selected.arrayBuffer()).replace(/^\uFEFF/,"");
      const detected = detectDelimiter(text), parsedHeaders = parseLine(text.split(/\r?\n/,1)[0], detected);
      if (!parsedHeaders.length) throw new Error("El archivo no contiene encabezados");
      const suggested:Record<string,string> = {}; const used = new Set<string>();
      parsedHeaders.forEach(header => { const key=normal(header); const target=Object.entries(aliases).find(([,names])=>names.includes(key))?.[0] ?? ""; suggested[header]=target && !used.has(target)?target:""; if(suggested[header])used.add(suggested[header]); });
      setFile(selected); setContent(text); setHeaders(parsedHeaders); setMapping(suggested); setDelimiter("auto");
    } catch { setError("No fue posible leer el CSV como UTF-8. Revisa la codificación del archivo."); }
  }
  function map(header:string,target:string) {
    if (target && Object.entries(mapping).some(([key,value]) => key!==header && value===target)) return setError("Un campo del sistema no puede asignarse a dos columnas.");
    setError(""); setMapping({...mapping,[header]:target}); setPreview(null);
  }
  async function validate() {
    if(!file)return; setBusy(true);setError("");
    try{setPreview(await api.previewProductImport({filename:file.name,content,mapping,delimiter}));}
    catch(e){setError(textError(e));}finally{setBusy(false);}
  }
  async function importProducts() {
    if(!file)return; setBusy(true);setError("");
    try{setResult(await api.confirmProductImport({filename:file.name,content,mapping,delimiter}));setConfirming(false);}
    catch(e){setError(textError(e));}finally{setBusy(false);}
  }
  function downloadErrors() {
    const rows=(result?.rows ?? preview?.rows.filter(row=>row.status==="ERROR") ?? []);
    const csv=["row_number,barcode,name,error",...rows.map(row=>[row.row_number,safe(row.parsed_data.barcode),safe(row.parsed_data.name),safe(row.errors.join(" | "))].join(","))].join("\n");
    const link=document.createElement("a");link.href=URL.createObjectURL(new Blob(["\uFEFF"+csv],{type:"text/csv;charset=utf-8"}));link.download="errores-importacion.csv";link.click();URL.revokeObjectURL(link.href);
  }

  return <main className="import-shell"><section className="import-header"><div><Link href="/inventory">← Volver al inventario</Link><h1>Importar productos desde CSV</h1><p>Mapea, valida y revisa antes de crear productos.</p></div><a className="template-link" href={`${API}/products/import/template`}>Descargar plantilla CSV</a></section>
    <nav className="import-steps"><b className={file?"done":"active"}>1. Archivo</b><b className={file&&!preview?"active":""}>2. Mapeo</b><b className={preview&&!result?"active":""}>3. Validación</b><b className={result?"active":""}>4. Resultado</b></nav>
    {error&&<div className="import-error" role="alert">{error}</div>}
    {result?.imported_count===0&&<div className="import-error" role="alert">No se importó ningún producto. Descarga los errores para revisar la causa; ninguna fila fue persistida. <button onClick={()=>{setResult(null);setPreview(null)}}>Volver a validar</button></div>}
    {!file&&<section className="import-card upload-card"><h2>Selecciona un CSV</h2><p>UTF-8 · coma o punto y coma · máximo 10 MB y 20.000 filas</p><label>Seleccionar archivo<input type="file" accept=".csv,text/csv" onChange={chooseFile}/></label></section>}
    {file&&!preview&&<section className="import-card"><div className="file-summary"><div><b>{file.name}</b><small>{formatBytes(file.size)} · {headers.length} columnas detectadas</small></div><button onClick={()=>{setFile(null);setContent("");setHeaders([])}}>Cambiar archivo</button></div><div className="delimiter"><label>Delimitador<select value={delimiter} onChange={e=>setDelimiter(e.target.value as typeof delimiter)}><option value="auto">Detectar automáticamente</option><option value=",">Coma (,)</option><option value=";">Punto y coma (;)</option></select></label></div><h2>Mapeo de columnas</h2><div className="mapping-table"><div className="mapping-row heading"><span>COLUMNA CSV</span><span>CAMPO DEL SISTEMA</span></div>{headers.map(header=><div className="mapping-row" key={header}><code>{header}</code><select value={mapping[header]??""} onChange={e=>map(header,e.target.value)}>{fields.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div>)}</div><div className="import-actions"><button disabled={busy} className="primary" onClick={()=>void validate()}>{busy?"Validando…":"Validar y previsualizar"}</button></div></section>}
    {preview&&!result&&<section className="import-card"><div className="metric-grid"><Metric label="TOTAL" value={preview.total_rows}/><Metric label="LISTOS" value={preview.ready_count} kind="ready"/><Metric label="ADVERTENCIAS" value={preview.warning_count} kind="warning"/><Metric label="ERRORES" value={preview.error_count} kind="error"/></div><div className="preview-toolbar"><div>{([['ALL','Todos'],['READY','Listos'],['WARNING','Advertencias'],['ERROR','Errores']] as const).map(([value,label])=><button className={filter===value?"selected":""} onClick={()=>setFilter(value)} key={value}>{label}</button>)}</div>{preview.error_count>0&&<button onClick={downloadErrors}>Descargar errores CSV</button>}</div><div className="preview-table"><div className="preview-row heading"><span>FILA</span><span>CÓDIGO</span><span>NOMBRE</span><span>CATEGORÍA</span><span>STOCK</span><span>VENTA</span><span>ESTADO</span></div>{shown.map(row=><div className="preview-row" key={row.row_number}><span>{row.row_number}</span><code>{String(row.parsed_data.barcode??"—")}</code><span>{String(row.parsed_data.name??"—")}</span><span>{String(row.parsed_data.category??"—")}</span><span>{String(row.parsed_data.initial_stock??0)}</span><span>{cop(row.parsed_data.sale_price)}</span><span><em className={`row-status ${row.status.toLowerCase()}`}>{row.status}</em>{[...row.warnings,...row.errors].map(x=><small key={x}>{x}</small>)}</span></div>)}</div><p className="preview-note">Se muestran máximo 50 filas. Las filas con advertencias se importarán; las filas con error se excluirán.</p><div className="import-actions"><button onClick={()=>setPreview(null)}>Volver al mapeo</button><button className="primary" disabled={busy||preview.ready_count+preview.warning_count===0} onClick={()=>setConfirming(true)}>Importar {preview.ready_count+preview.warning_count} productos</button></div>{confirming&&<div className="confirm-box"><b>Confirmar importación</b><p>Se crearán hasta {preview.ready_count+preview.warning_count} productos. {preview.error_count} filas serán ignoradas.</p><button onClick={()=>setConfirming(false)}>Cancelar</button><button className="primary" disabled={busy} onClick={()=>void importProducts()}>{busy?"Importando…":"Confirmar importación"}</button></div>}</section>}
    {result&&<section className="import-card result-card"><div className="result-icon">✓</div><h2>Importación completada</h2><div className="metric-grid"><Metric label="PRODUCTOS CREADOS" value={result.imported_count} kind="ready"/><Metric label="NO IMPORTADOS" value={result.skipped_count} kind="error"/><Metric label="CON ADVERTENCIAS" value={result.warning_count} kind="warning"/><Metric label="STOCK INICIAL TOTAL" value={result.initial_stock_total}/></div><div className="import-actions">{result.rows.length>0&&<button onClick={downloadErrors}>Descargar errores</button>}<Link className="primary-link" href="/inventory">Ver inventario</Link></div></section>}
  </main>;
}

function Metric({label,value,kind=""}:{label:string;value:number;kind?:string}){return <div className={`metric ${kind}`}><small>{label}</small><b>{new Intl.NumberFormat("es-CO").format(value)}</b></div>}
function normal(value:string){return value.normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().toLowerCase().replace(/\s+/g," ")}
function detectDelimiter(text:string):","|";"{const line=text.replace(/^\uFEFF/,"").split(/\r?\n/,1)[0];return (line.match(/;/g)?.length??0)>(line.match(/,/g)?.length??0)?";":","}
function parseLine(line:string,delimiter:string){const result:string[]=[];let value="",quoted=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'&&line[i+1]==='"'){value+='"';i++;}else if(c==='"')quoted=!quoted;else if(c===delimiter&&!quoted){result.push(value.trim());value="";}else value+=c;}result.push(value.trim());return result;}
function formatBytes(value:number){return value<1024?`${value} B`:value<1048576?`${(value/1024).toFixed(1)} KB`:`${(value/1048576).toFixed(1)} MB`}
function cop(value:unknown){const n=Number(value);return Number.isFinite(n)?new Intl.NumberFormat("es-CO",{style:"currency",currency:"COP",maximumFractionDigits:0}).format(n):"—"}
function safe(value:unknown){let text=String(value??"");if(/^[=+\-@]/.test(text))text="'"+text;return `"${text.replaceAll('"','""')}"`}
function textError(error:unknown){return error instanceof Error?error.message:"No fue posible procesar el archivo"}
