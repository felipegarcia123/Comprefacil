// Verifies the accessibility contract of the Mostrador color tokens (see /CLAUDE.md).
// Run: npm run check:tokens   (exits 1 if any required pair falls below its minimum contrast)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "app", "tokens.css"), "utf8");
const colors = Object.fromEntries([...css.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map(m => [m[1], m[2]]));

const channel = c => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const luminance = hex => { const n = parseInt(hex.slice(1), 16); return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255); };
const ratio = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

const checks = []; // [foreground, background, minimum, description]
const need = (fg, bg, min, why) => checks.push([fg, bg, min, why]);

need("ink", "paper", 4.5, "body text on screen");
need("ink", "surface", 4.5, "body text on panels");
need("ink-muted", "paper", 4.5, "secondary text");
need("ink-muted", "surface", 4.5, "secondary text on panels");
need("line-strong", "paper", 3, "control borders on screen");
need("line-strong", "surface", 3, "control borders on panels");
need("lcd-ok", "lcd", 4.5, "display digits");
need("lcd-amber", "lcd", 4.5, "display digits (amber)");
need("lcd-muted", "lcd", 4.5, "display labels");
for (const s of ["success", "warning", "danger", "info"]) {
  need(s, "surface", 4.5, `${s} text on panels`);
  need(s, `${s}-tint`, 4.5, `${s} text on its tint`);
}
for (const m of ["ventas", "inventario", "compras", "proveedores", "caja", "informes"]) {
  need(`${m}-on`, m, 4.5, `${m}: label text on strong fill`);
  need(`${m}-deep`, `${m}-tint`, 4.5, `${m}: deep text on tint`);
  need(`${m}-deep`, "paper", 4.5, `${m}: deep text on screen`);
  need(`${m}-deep`, "surface", 4.5, `${m}: deep text on panels`);
}

let failed = 0;
for (const [fg, bg, min, why] of checks) {
  if (!colors[fg] || !colors[bg]) { console.error(`MISSING token for ${fg} / ${bg}`); failed++; continue; }
  const value = ratio(colors[fg], colors[bg]);
  const ok = value >= min;
  if (!ok) failed++;
  console.log(`${ok ? "ok  " : "FAIL"} ${value.toFixed(2).padStart(5)}:1 (min ${min}) ${fg} on ${bg} — ${why}`);
}
console.log(failed ? `\n${failed} contrast check(s) failed` : `\nAll ${checks.length} contrast checks passed`);
process.exit(failed ? 1 : 0);
