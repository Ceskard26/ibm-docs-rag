#!/bin/sh
# La imagen base de nginx ejecuta este script (está en /docker-entrypoint.d/) ANTES
# de arrancar nginx. Solo generamos env-config.js con la URL del backend ($API_URL)
# y retornamos; nginx lo arranca el entrypoint de la imagen base.
set -e

CONFIG_FILE=/usr/share/nginx/html/env-config.js
echo "window.__API_URL__ = \"${API_URL}\";" > "$CONFIG_FILE"
echo "[entrypoint] env-config.js -> API_URL=${API_URL}"
