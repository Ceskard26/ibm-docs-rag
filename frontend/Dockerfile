# Imágenes base parametrizadas. Por defecto Docker Hub (funciona en Code Engine).
# En local, si Docker Hub te limita (429), apunta al mirror de Google con:
#   --build-arg NODE_IMAGE=mirror.gcr.io/library/node:24-alpine
#   --build-arg NGINX_IMAGE=mirror.gcr.io/library/nginx:1.27-alpine
ARG NODE_IMAGE=node:24-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

# --- Etapa 1: build del bundle de React con Vite ---
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- Etapa 2: servir los estáticos con nginx ---
FROM ${NGINX_IMAGE}
# Config de nginx (SPA + puerto 8080). Reemplaza la default que escucha en 80.
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Bundle compilado.
COPY --from=build /app/dist /usr/share/nginx/html
# Entrypoint que inyecta $API_URL en runtime.
COPY docker-entrypoint.sh /docker-entrypoint.d/40-inject-api-url.sh
RUN chmod +x /docker-entrypoint.d/40-inject-api-url.sh

EXPOSE 8080

# La imagen base de nginx ya tiene un entrypoint que ejecuta los scripts de
# /docker-entrypoint.d/ antes de arrancar nginx, así que solo definimos el CMD.
CMD ["nginx", "-g", "daemon off;"]
