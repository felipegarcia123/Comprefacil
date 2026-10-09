# Ilustraciones estáticas

Assets locales (SVG o WebP) para los casos que no necesitan color dinámico. Sin URLs externas.

- `home/`: ilustraciones del Home
- `empty/`: estados vacíos
- `help/`: ayuda y onboarding

Se sirven como `/illustrations/<carpeta>/<archivo>`. Las ilustraciones con color por módulo van como componentes en `components/illustrations/`.
Nota: el `Dockerfile` del web copia `public/` a la imagen final para que estos archivos se sirvan en producción.
