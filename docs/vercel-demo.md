# Comprefácil · demo aislada en Vercel

## Configuración del proyecto

- Root Directory: `apps/web`
- Framework preset: `Next.js`
- Install Command: `npm ci`
- Build Command: `npm run build`
- Output: el predeterminado de Next.js; no usar Docker ni `apps/api`.

## Variables para la demo

Configurar en Preview y Production:

```text
NEXT_PUBLIC_DEMO_MODE=1
NEXT_PUBLIC_ENVIRONMENT_LABEL=DEMO · datos ficticios
```

No configurar `NEXT_PUBLIC_API_URL` en la demo. No agregar `DATABASE_URL`, claves OpenAI, Alegra, credenciales ni variables de `pos_sim`.

En este modo se conservan las páginas y componentes originales del POS (`AppHeader`, `ContextBar`, `TouchButton`, `Sheet`, tablas, formularios y paneles). `lib/api.ts` dirige sus llamadas a `lib/demoApi.ts`, que mantiene datos ficticios en memoria y simula los flujos de Informes, POS, Inventario, Compras, Proveedores y Recepción Inteligente. Las acciones no escriben en ningún servicio y se reinician al recargar.

## Compatibilidad y límites

`apps/web` usa Next.js 16, React 19 y salida `standalone`; Vercel debe construir desde `apps/web`. Las demás rutas del frontend siguen siendo la aplicación operativa y requieren un FastAPI público protegido, pero no deben enlazarse desde esta demo aislada.
