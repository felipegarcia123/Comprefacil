# Alertas y recordatorios de Comprefácil AI (COMP-S2-005)

El centro de `/business-ai` muestra cuatro alertas calculadas al consultar: stock igual o inferior al mínimo **configurado** para productos con movimientos, compras recibidas a crédito con vencimiento registrado en los próximos tres días, entregas de compras programadas con fecha registrada en los próximos siete días y obligaciones ya vencidas. Un pago registrado elimina la alerta de obligación. Las claves estables por negocio, categoría y registro evitan duplicados en cada respuesta. Se muestran hasta 100 resultados por categoría y se indica cuando la lista se trunca. No se ejecuta el LLM para detectar alertas.

Los recordatorios personales se guardan en `business_reminders` con identificadores de negocio y usuario, fecha UTC, zona `America/Bogota`, estado y clave de idempotencia. Se admiten fechas con precisión de minutos entre uno y 365 días futuros. La lista muestra los próximos 100 pendientes. El comerciante puede consultar, crear, completar y cancelar; la interfaz pide confirmación explícita antes de cada cambio. Las escrituras usan rutas y un rol PostgreSQL separados del agente analítico. Ninguna ruta de recordatorios modifica ventas, inventario, caja ni compras.

## Configuración aislada

La función está deshabilitada por defecto. FastAPI y Next.js requieren `BUSINESS_NOTIFICATIONS_ENABLED=true`, el mismo `BUSINESS_NOTIFICATIONS_BUSINESS_ID` y `BUSINESS_NOTIFICATIONS_ACTORS_JSON` privado. Cada entrada del JSON contiene `id`, `business_id`, `user_id` (UUID de un usuario existente), `access_code` y `api_token` únicos. Los dos secretos de cada persona deben tener al menos 32 caracteres; el token debe diferir de `BUSINESS_AGENT_ACCESS_TOKEN`. El código personal abre una sesión firmada de ocho horas. El acceso experimental compartido anterior conserva solo el chat, sin centro personal.

FastAPI requiere además `BUSINESS_NOTIFICATIONS_ACTION_SECRET` (32 caracteres o más), `BUSINESS_AGENT_DATABASE_URL` para las lecturas y `BUSINESS_NOTIFICATIONS_DATABASE_URL` para las escrituras. Esta última debe ser PostgreSQL con un rol distinto del rol operativo y del analítico, con `SELECT`, `INSERT` y `UPDATE` sobre `business_reminders`, sin `DELETE`, `TRUNCATE` ni permisos de escritura sobre otras tablas o secuencias. Next.js requiere `BUSINESS_AI_NOTIFICATIONS_API_URL`, una URL fija del endpoint `/api/v1/notifications`; no recibe destinos ni tokens desde el navegador. La migración `0033_business_reminders.py` crea la tabla, pero **no** se ha aplicado a ninguna base operativa.

El despliegue actual de POS tiene un único negocio por instancia. El identificador de negocio en la configuración y en cada recordatorio deja preparada la separación para futuras instancias; los datos comerciales existentes no tienen columna de negocio. Para conectar otro negocio se necesita una instancia de datos separada. El acceso sigue limitado a operadores experimentales configurados; no se amplía automáticamente.

## Consultas y límites

“¿Qué situaciones requieren mi atención?” y “¿Qué tengo pendiente para mañana?” consultan el centro directamente, sin OpenAI. “Recuérdame revisar el pedido mañana a las 8 a. m.” prepara el formulario y exige confirmar. Para frases sin fecha u hora reconocibles se pide completarlas en el formulario. No hay envíos por WhatsApp, correo o push; el centro se actualiza mientras la página está abierta y se puede actualizar manualmente.

## Validación sintética

- 24 pruebas API relacionadas: alertas, deduplicación, zona horaria, aislamiento, permisos y regresión del agente; todas aprobadas.
- Migración `0033` y persistencia probadas en PostgreSQL 16 sintético, en red interna y con rol de escritura limitado; contenedor y red retirados.
- 4 pruebas unitarias web y 7 recorridos Playwright con Chrome local, incluidos escritorio, tablet y móvil; todos aprobados.
- `npm.cmd run typecheck` y `npm.cmd run build` aprobados. No se consumió OpenAI.
