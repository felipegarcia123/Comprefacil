# Ilustraciones (componentes SVG inline)

Ilustraciones reutilizables como componentes React (SVG inline, sin red). Úsalas en Home, estados vacíos y ayuda/onboarding.
No en Venta, Inventario ni tablas operativas.

Reglas (ver `/CLAUDE.md`, sección "POS UI / UX DESIGN SYSTEM"):

- Un archivo por ilustración: `EmptyBox.tsx`, `HomeCounter.tsx`, … exportadas desde `index.ts`.
- Máximo ~6 KB por SVG. Colores solo con variables (`var(--mod-strong)`, `var(--mod-tint)`, `var(--color-ink)`), nunca hex sueltos.
- Decorativas: `aria-hidden="true"`. Informativas: `role="img"` + `<title>`.
- Estilo común: trazo 2 px, esquinas redondeadas, sin degradados ni sombras.

Todavía no hay ilustraciones: se crean en la fase que las use (Home en Fase 1, estados vacíos con cada pantalla).
Los SVG estáticos sin color dinámico viven en `public/illustrations/{home,empty,help}/`.
