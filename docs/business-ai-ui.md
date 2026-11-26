# Comprefácil AI: interfaz experimental (COMP-S2-003 / COMP-S2-004)

Las alertas y los recordatorios del COMP-S2-005 se describen en
[`business-notifications.md`](business-notifications.md).

La ruta `/business-ai` aparece en la barra de módulos y en el menú. Muestra
consultas rápidas, mensajes de la sesión, estados de carga y error, fuentes
consultadas, períodos y limitaciones. El navegador solo envía `question`.
Next.js deriva una sesión opaca desde una cookie aleatoria y la comunica a
FastAPI. El backend conserva contexto breve durante 30 minutos de inactividad
para permitir preguntas de seguimiento. El historial visible vive solo en la
página y se pierde al recargar, aunque el contexto del backend puede continuar.

## Acceso y conexión

La interfaz está deshabilitada por defecto. En un entorno aislado, el servidor
Next.js necesita estas variables **privadas**, sin prefijo `NEXT_PUBLIC_`:

| Variable | Uso |
| --- | --- |
| `BUSINESS_AI_UI_ENABLED=true` | Habilita las rutas de Next.js. |
| `BUSINESS_AI_UI_ACCESS_CODE` | Código aleatorio de al menos 32 caracteres para el operador de prueba; distinto del token del backend. |
| `BUSINESS_AI_SESSION_SECRET` | Secreto aleatorio de al menos 32 caracteres para firmar la cookie. |
| `BUSINESS_AGENT_ACCESS_TOKEN` | Token que Next.js presenta al endpoint FastAPI. Debe coincidir con el configurado en la API. |
| `BUSINESS_AI_API_URL` | URL fija del endpoint FastAPI accesible solo desde el servidor Next.js. |

La página pide el código una vez y recibe una cookie firmada, HttpOnly,
SameSite Strict, limitada a `/api/business-ai` y con vencimiento a las ocho
horas. En producción la cookie exige HTTPS. Los POST comprueban el origen y la
ruta de chat exige una sesión válida.
El botón **Cerrar acceso** borra la cookie; **Nueva conversación** rota la
cookie y limpia los mensajes, iniciando un contexto nuevo.
La ruta de Next.js nunca acepta una URL de destino, token ni identidad de
negocio del navegador. Tampoco entrega la clave de OpenAI. Mantener FastAPI
protegido con su propia autenticación y rol PostgreSQL de solo lectura, según
`docs/business-agent.md`. El código experimental compartido no reemplaza la
autorización por usuario ni el control de tasa distribuido necesarios antes de
abrir la función a más personas.

No se renderizan indicadores ni gráficos a partir de la prosa del modelo. La
respuesta estructurada actual solo permite mostrar texto, nombres de
herramientas, períodos y limitaciones. Los datos comerciales enviados al
modelo y el manejo de privacidad siguen sujetos a las condiciones del backend.

## Pruebas sin LLM ni datos operativos

Desde `apps/web`:

```powershell
npm.cmd run test:business-ai:unit
$env:BUSINESS_AI_TEST_BROWSER='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
npm.cmd run test:business-ai:e2e
npm.cmd run typecheck
npm.cmd run build
```

La prueba E2E crea solo un mock HTTP en `127.0.0.1:4313` y un Next de prueba
en `127.0.0.1:3103`, con credenciales sintéticas. El navegador bloquea
solicitudes a otros hosts. Para usar Chromium instalado por Playwright, omitir
`BUSINESS_AI_TEST_BROWSER`. Las capturas y trazas de prueba quedan en
`test-results/business-ai/` (ignorado por Git), o en el directorio indicado
por `BUSINESS_AI_EVIDENCE_DIR`. No se carga `.env` operativo ni se llama al
modelo real.
