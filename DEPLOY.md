# Deploy en IBM Code Engine

Guía para desplegar el **backend** (FastAPI + RAG) en IBM Code Engine.
El frontend se despliega aparte (ver sección final).

## Prerrequisitos

```bash
# 1. Instalar el CLI de IBM Cloud + plugin de Code Engine (una sola vez)
ibmcloud plugin install code-engine

# 2. Login
ibmcloud login --sso          # o: ibmcloud login -apikey <API_KEY>
ibmcloud target -r us-south   # misma región que watsonx (Dallas)
```

## 1. Crear proyecto de Code Engine

```bash
ibmcloud ce project create --name ibm-knowledge-agent
ibmcloud ce project select --name ibm-knowledge-agent
```

## 2. Crear los secrets (credenciales — NO van en la imagen)

```bash
ibmcloud ce secret create --name watsonx-creds \
  --from-literal WATSONX_API_KEY="<tu_api_key>" \
  --from-literal WATSONX_PROJECT_ID="<tu_project_id>" \
  --from-literal WATSONX_URL="https://us-south.ml.cloud.ibm.com" \
  --from-literal POSTGRES_URL="<tu_postgres_url>"
```

## 3. Desplegar el backend (build desde el Dockerfile)

Code Engine puede construir la imagen directamente desde el código fuente:

```bash
cd backend
ibmcloud ce application create \
  --name knowledge-agent-api \
  --build-source . \
  --port 8080 \
  --cpu 1 --memory 2G \
  --env-from-secret watsonx-creds \
  --min-scale 1 --max-scale 3
```

Al terminar, el CLI imprime la URL pública (algo como
`https://knowledge-agent-api.<hash>.us-south.codeengine.appdomain.cloud`).

Verifica:

```bash
curl https://<URL>/health
```

## 4. Actualizar el backend tras cambios

```bash
cd backend
ibmcloud ce application update --name knowledge-agent-api --build-source .
```

## 5. Frontend (contenedor nginx en Code Engine)

El frontend está contenerizado ([frontend/Dockerfile](frontend/Dockerfile)): build de Vite +
nginx sirviendo en el puerto 8080. La URL del backend se inyecta **en runtime** vía la
variable `API_URL` (no hay que reconstruir la imagen si cambia el backend).

```bash
cd frontend
ibmcloud ce application create \
  --name knowledge-agent-web \
  --build-source . \
  --port 8080 \
  --cpu 0.5 --memory 1G \
  --env API_URL="https://<URL-PUBLICA-DEL-BACKEND>" \
  --min-scale 1 --max-scale 3
```

El CLI imprime la URL pública del frontend → esa es la que abre el usuario final.

Actualizar tras cambios:
```bash
cd frontend
ibmcloud ce application update --name knowledge-agent-web --build-source . \
  --env API_URL="https://<URL-PUBLICA-DEL-BACKEND>"
```

---

## Variables de entorno por servicio

### Backend (`knowledge-agent-api`) — como **secret**
| Variable | Valor | Notas |
|---|---|---|
| `WATSONX_API_KEY` | tu API key | secreto |
| `WATSONX_PROJECT_ID` | tu project ID | |
| `WATSONX_URL` | `https://us-south.ml.cloud.ibm.com` | |
| `POSTGRES_URL` | `postgres://...?sslmode=verify-full` | secreto |
| `POSTGRES_CERT` | **NO la pongas** | el Dockerfile ya la fija a `/app/postgres_cert.pem`; si la sobreescribes con la ruta del Mac, el contenedor falla |

### Frontend (`knowledge-agent-web`) — como **env normal**
| Variable | Valor | Notas |
|---|---|---|
| `API_URL` | URL pública https del backend | se inyecta en runtime; sin barra final |

---

## ¿Funciona en internet con ambos desplegados?

**Sí.** El flujo es: navegador → frontend (Code Engine, https) → backend (Code Engine,
https) → watsonx.ai + PostgreSQL. Checklist para que funcione:

- ✅ **HTTPS en ambos**: Code Engine da URLs https, así que no hay "mixed content".
- ✅ **CORS**: el backend tiene `allow_origins=["*"]`, así que el frontend puede llamarlo.
  Para producción real, restríngelo a la URL del frontend en `backend/main.py`.
- ✅ **`API_URL` del frontend** apuntando a la URL pública del backend (no a localhost).
- ✅ **Secrets del backend** completos y `POSTGRES_CERT` SIN sobreescribir.
- ✅ **`--min-scale 1`** en ambos para evitar cold starts en la demo.
- ⚠️ **PostgreSQL accesible**: IBM Cloud Databases expone endpoint público; Code Engine
  llega sin problema. Si activaste allowlists de IP en la BD, agrega las de Code Engine.
- ⚠️ La ingesta de contenido (`scraper.py` con Playwright) corre **offline desde tu Mac**,
  no en el contenedor del backend. La BD ya queda poblada para todos los usuarios.
