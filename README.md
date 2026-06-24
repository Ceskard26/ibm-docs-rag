# IBM Knowledge Agent

RAG sobre documentación técnica de IBM. El usuario pregunta en lenguaje natural,
el sistema busca en documentos indexados y responde **citando la fuente**.
Stack 100% IBM Cloud.

## Arquitectura

```
Frontend React + IBM Carbon Design System  (frontend/)
    │  fetch
    ▼
Backend FastAPI  (backend/main.py)
    ├── watsonx.ai (us-south)
    │     ├── Embeddings: ibm/granite-embedding-278m-multilingual (768 dims)
    │     └── Generación: meta-llama/llama-3-3-70b-instruct (vía chat API)
    └── PostgreSQL + pgvector (IBM Cloud Databases)
          └── tabla documents(id, content, embedding vector(768), source, created_at)
```

## Componentes

| Ruta | Qué hace |
|---|---|
| `backend/main.py` | API FastAPI: `/health`, `/ingest` (PDF), `/query` (RAG) |
| `backend/scraper.py` | Indexa IBM Docs con **Playwright** (renderiza JS), chunking con solapamiento, re-ingesta idempotente |
| `backend/Dockerfile` | Imagen de producción del API (ver `DEPLOY.md`) |
| `frontend/` | App React + Carbon: pregunta, respuesta, fuentes con % de similitud, subida de PDF |
| `DEPLOY.md` | Guía de despliegue en IBM Code Engine |

## Cómo correr en local

### Backend
```bash
cd backend
source ../venv/bin/activate
python -m uvicorn main:app --reload --port 8000
```

### Indexar contenido de IBM Docs
```bash
cd backend
source ../venv/bin/activate
python scraper.py                       # usa las URLs por defecto
python scraper.py "https://www.ibm.com/docs/en/watsonx/saas?topic=..."   # URLs propias
```

### Frontend
```bash
cd frontend
npm install        # solo la primera vez
npm run dev        # http://localhost:5173
```
Para apuntar a un backend distinto: `VITE_API_URL=https://... npm run dev`.

## Variables de entorno (`backend/.env`)

```
WATSONX_API_KEY=...
WATSONX_PROJECT_ID=...
WATSONX_URL=https://us-south.ml.cloud.ibm.com
POSTGRES_URL=postgres://USER:PASS@host:port/ibmclouddb?sslmode=verify-full
POSTGRES_CERT=/ruta/absoluta/a/backend/postgres_cert.pem
```

## Estado

- [x] Backend RAG funcionando (embeddings + retrieval + generación con citas)
- [x] Prompt refinado: respuesta única, multilingüe, sin alucinar
- [x] Scraping de IBM Docs con JS (Playwright) — **202 chunks de 11 fuentes**
- [x] Umbral de relevancia (`MIN_SIMILARITY`): evita falsos positivos por el "piso" de similitud
- [x] Selector de idioma (Auto/Español/English) en el frontend
- [x] Frontend React + Carbon Design System
- [x] Dockerfile validado + guía de Code Engine
- [ ] Deploy real en Code Engine (requiere login del usuario — ver `DEPLOY.md`)
- [ ] Integración con Seismic (pendiente acceso API)

> Nota sobre fuentes: IBM Docs es una SPA cuyo menú no expone enlaces crawleables
> (sin `<a href>` con topic, sin endpoint JSON de TOC). Por eso se usa una lista
> semilla curada en `scraper.py` (`DEFAULT_URLS`) en vez de un crawler automático.
> Para agregar contenido: añade URLs a esa lista, o sube PDFs por la app.
