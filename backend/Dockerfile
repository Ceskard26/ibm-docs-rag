FROM python:3.11-slim

WORKDIR /app

# Dependencias de runtime del API (no incluye el scraper).
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Código del API y certificado de PostgreSQL (CA pública de IBM Cloud Databases).
COPY main.py .
COPY postgres_cert.pem .

# Code Engine inyecta el puerto en $PORT (8080 por defecto).
ENV PORT=8080
EXPOSE 8080

# Las credenciales (WATSONX_API_KEY, WATSONX_PROJECT_ID, POSTGRES_URL) se inyectan
# como variables de entorno / secrets en Code Engine, NO se hornean en la imagen.
# POSTGRES_CERT apunta al cert copiado dentro del contenedor.
ENV POSTGRES_CERT=/app/postgres_cert.pem

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
