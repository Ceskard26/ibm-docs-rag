# STATUS / Handoff — IBM Knowledge Agent

Documento de continuidad para retomar el proyecto en un chat nuevo. Léelo primero.

## Qué es
RAG sobre documentación de IBM (respuestas en lenguaje natural, citando la fuente),
100% IBM Cloud. Evoluciona hacia un **Asset Hub** para CSM + Client Engineering.
Autor: César Carrasco, equipo CSM de IBM Cloud.

## Cómo correrlo (local)
```bash
# Backend
cd backend && source ../venv/bin/activate && python -m uvicorn main:app --reload --port 8000
# Frontend
cd frontend && npm run dev   # http://localhost:5173
```

## Estado actual (hecho ✅)
- RAG multi-producto: watsonx, VPC, RabbitMQ, Kubernetes, Code Engine, Object Storage,
  Databases (~3.116 chunks). Contenido tomado de GitHub `ibm-cloud-docs` (markdown crudo)
  y watsonx docs vía Playwright.
- Embeddings: `ibm/granite-embedding-278m-multilingual` (768). Generación:
  `meta-llama/llama-3-3-70b-instruct` (chat + streaming). Vector store: PostgreSQL + pgvector.
- Prompt refinado (respuesta única, sin URLs en el texto, multilingüe), umbral de
  relevancia (`MIN_SIMILARITY=0.72`), manejo de saludos/charla (chitchat), fallback conversacional.
- Frontend Carbon (tema g100): chat con hilo (más nuevo arriba, historial colapsable),
  streaming animado, filtro por producto, selector de idioma + i18n completo (es/en),
  feedback 👍/👎, copiar, subida de PDF con barra de progreso + fases + cancelar, landing page.
- Memoria conversacional: backend condensa la pregunta con el historial + genera con
  historial; frontend persiste la conversación en localStorage y envía los últimos 6 turnos.
- Concurrencia: endpoints son `def` (no `async def`) para no bloquear el event loop.
- **Auth con IBM Cloud App ID FUNCIONANDO** (IBMid + Cloud Directory). Flujo:
  frontend redirige a App ID → vuelve con `code` → backend lo canjea con el secret
  (server-side, `/auth/exchange`) → valida el token (JWKS) → nombre/email del id_token.
- Dockerfiles de backend y frontend correctos (backend copia `main.py` + `auth.py`).

## Config de App ID (valores no secretos)
- Tenant: `a5ee625d-5102-40e2-9e98-0e706d0e26c3`
- App confidencial (Regular web app) client id: `9e933481-ed22-4127-a67f-f1cdbc76f2cc`
- `APPID_CLIENT_SECRET` está en `backend/.env` (NO en el repo).
- `AUTH_REQUIRED=false` (login opcional; la app corre anónima si no).
- Redirect URLs en App ID (Authentication settings): `http://localhost:5173` (falta la de Code Engine).

## Pendiente (próximos pasos)
1. **Memoria persistente por usuario** (el siguiente hito grande): guardar conversaciones
   en la BD ligadas al `sub` de IBMid en vez de localStorage. Tablas `conversations`/`messages`.
2. **Deploy a Code Engine**: imágenes ya listas. Falta: secret del backend con las vars
   de App ID (OAUTH_SERVER_URL, CLIENT_ID, CLIENT_SECRET, AUTH_REQUIRED) + watsonx/postgres;
   `API_URL` del frontend; y agregar la URL del frontend a los redirect URLs de App ID.
3. Dashboard de feedback/uso, reranking (watsonx), y Fase 2 Asset Hub (ver `docs/asset-hub-taxonomy.md`).

## Gotchas / decisiones (no repetir errores)
- **Cuota watsonx Lite = 300k tokens/mes.** Ingestas masivas la agotan. El proyecto actual
  tiene cuota fresca. Toda re-ingesta gasta tokens.
- **IBM Docs (cloud.ibm.com) tiene WAF** → el contenido se toma de GitHub `ibm-cloud-docs`.
  Las citas apuntan al blob de GitHub (siempre válido) porque la nomenclatura `?topic=` no
  siempre resuelve.
- **App ID token endpoint exige client secret** → el intercambio del código va por el backend
  (un SPA puro daba 401). Por eso la app en App ID es "Regular web application".
- **Chunks deben quedar < 512 tokens** (límite del modelo de embeddings). `chunk_text` usa
  400 chars + parte "palabras gigantes"; `embed_safe` divide si aún se pasa.
- **En Code Engine NO setear `POSTGRES_CERT`** (el Dockerfile ya la fija a /app/postgres_cert.pem).
- `_embeddings` se inicializa perezoso (un parpadeo de red no debe impedir arrancar el backend).

## Equipo / gobernanza
- `.claude/agents/`: frontend-lead, backend-lead, rag-lead, features-lead, devops-lead, qa-reviewer.
- `docs/GOVERNANCE.md`: roles, flujo, Definition of Done, contratos, ADRs.
- Modelo de trabajo: César = product owner (dice la intención); Claude = orquestador
  (descompone, despacha agentes, integra, revisa).
