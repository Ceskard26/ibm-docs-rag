# Gobernanza del proyecto — IBM Knowledge Agent → Asset Hub

De MVP a producto. Este documento define **cómo se organiza, decide y ejecuta** el
trabajo. Es la fuente de verdad del equipo (junto con el tablero de tareas, los ADRs
y la memoria).

---

## 1. Roles (equipo de agentes)

| Rol | Dueño de | Cuándo se despacha |
|---|---|---|
| **Orquestador / Product Owner** (humano: César) | Visión, prioridades, integración, aprobación final | Siempre — coordina al resto |
| **frontend-lead** | `frontend/` (React + Carbon, UX, i18n) | Cambios de UI, componentes, estado, accesibilidad |
| **backend-lead** | `backend/main.py` (API FastAPI, endpoints, concurrencia, BD) | Endpoints, contratos de API, rendimiento, esquema de datos |
| **rag-lead** | Embeddings, chunking, retrieval, `backend/scraper.py`, ingesta | Calidad de respuestas, fuentes, umbral, reranking, chunking |
| **features-lead** | Funcionalidades transversales (feedback, streaming, filtros, idioma) | Features que cruzan frontend+backend |
| **devops-lead** | `Dockerfile`s, Code Engine, secrets, CI/CD, despliegue | Build, deploy, infraestructura, observabilidad |
| **qa-reviewer** | Revisión de código, tests, seguridad | Antes de cada merge a `main` (puerta de calidad) |

> Los roles son **agentes de Claude** definidos en `.claude/agents/`. Son stateless:
> se les da un brief autocontenido por tarea; la memoria compartida son estos docs.

## 2. Secciones del producto

```
Producto: IBM Knowledge Agent (RAG)  →  evoluciona a  →  Asset Hub
├── Frontend (Carbon)            → frontend-lead
├── Backend / API (FastAPI)      → backend-lead
├── RAG / ML (embeddings, retrieval, ingesta) → rag-lead
├── Funcionalidades de producto  → features-lead
├── Infra / Deploy (Code Engine) → devops-lead
└── Calidad (review, tests, sec) → qa-reviewer
```

## 3. Flujo de trabajo (cadencia semanal)

1. **Planear** — el orquestador define el hito y descompone en tareas con criterio de aceptación.
2. **Contratos** — si la tarea cruza áreas, primero se acuerda la interfaz (API/datos).
3. **Asignar** — cada tarea a su agente-dueño, en su propia **rama** (`feat/<area>-<tarea>`).
4. **Construir en paralelo** — los agentes trabajan en sus fronteras.
5. **Integrar** — el orquestador mergea contra los contratos.
6. **Revisar** — `qa-reviewer` revisa antes de `main` (correctitud, seguridad, tests).
7. **Demo / cierre del hito.**

## 4. Reglas de oro (Definition of Done)

Una tarea está "lista" solo si:
- [ ] Cumple su criterio de aceptación.
- [ ] No rompe el build (frontend `npm run build`, backend importa y arranca).
- [ ] Verificada de verdad (no solo "debería funcionar").
- [ ] Pasó revisión de `qa-reviewer`.
- [ ] Sin secretos en el repo; CORS y permisos revisados si aplica.
- [ ] Documentada si cambió un contrato (API/datos) → actualizar `docs/`.

## 5. Decisiones (ADRs)

Toda decisión arquitectónica relevante (elegir una librería, cambiar el modelo de
datos, un patrón) se registra como un ADR corto en `docs/adr/NNNN-titulo.md`:
**Contexto → Decisión → Alternativas → Consecuencias.** Quién decide: el dueño del
área propone; el orquestador aprueba.

## 6. Contratos vigentes (no romper sin ADR)

- **API:** `/query`, `/query_stream`, `/ingest`, `/ingest_stream`, `/ingest_cancel`,
  `/feedback`, `/health`. Formato de respuesta de query: `{answer, relevant, max_similarity, threshold, sources[]}`.
  `/query` y `/query_stream` aceptan `conversation_id` opcional en el payload y, SOLO
  si el usuario está autenticado (`user.sub != "anonymous"`), lo devuelven de vuelta
  (`conversation_id` en el JSON de `/query`; línea NDJSON `{"type":"conversation",
  "conversation_id":...}` en `/query_stream`, antes de la línea `meta`). Para usuarios
  anónimos el comportamiento es idéntico a antes de esta feature (sin `conversation_id`).
- **API (memoria persistente, requiere login — 401 si `sub == "anonymous"`):**
  `GET /conversations` → `[{id, title, updated_at}]` del usuario, más recientes
  primero. `GET /conversations/{id}` → `{id, title, messages: [{role, content,
  sources}]}` (404 si no es del usuario). `DELETE /conversations/{id}` → borra
  (404 si no es del usuario).
- **API (archivos — COS opcional):** `GET /files/{filename}` → sirve el PDF
  almacenado en COS con `Content-Disposition: inline`. 503 si COS no está
  configurado; 400 si el filename contiene `/`, `\` o `..`; 404 si el objeto no
  existe. El objeto vive en COS con key `pdfs/<filename>`. La feature se activa
  solo si las cuatro vars `COS_ENDPOINT`, `COS_API_KEY`, `COS_INSTANCE_CRN` y
  `COS_BUCKET` están presentes; en su ausencia el comportamiento es idéntico al
  anterior (sin COS).
- **Datos:** tabla `documents(id, content, embedding vector(768), source, created_at,
  content_hash)` y `feedback(...)`. Fuente (`source`) = URL de GitHub, de IBM Docs, o
  nombre de archivo PDF subido manualmente. `content_hash` (TEXT, nullable) = sha256
  del texto completo extraído de un PDF subido vía `/ingest`/`/ingest_stream` (NULL
  para chunks de GitHub/scraper, que no pasan por ese flujo); usado para dedupe de
  re-subidas con nombre distinto (ver "Dedupe de ingesta" más abajo). Índice GIN
  `idx_documents_content_fts` sobre `to_tsvector('simple', content)` para el canal
  léxico del retrieval híbrido.
- **Retrieval híbrido (semántico + léxico, RRF) — contrato de `/query`/`/query_stream`:**
  el retrieval en producción es `hybrid_retrieve()` (no `retrieve()`, que se conserva
  como fallback/comparación). Fusiona (a) top-15 por similitud coseno y (b) top-15 por
  ranking léxico full-text — `to_tsquery('simple', ...)` con los lexemes de la query
  unidos por OR (no AND: `websearch_to_tsquery` exige que TODOS los términos aparezcan
  en el mismo chunk, lo que casi nunca pasa con una query de 4-5 palabras), excluyendo
  stopwords ES/EN (`_STOPWORDS_ES_EN`) — vía Reciprocal Rank Fusion (RRF, k=60,
  constante estándar de la literatura). Deduplica por contenido idéntico ANTES de
  devolver el top-5 (mismo texto de fuentes/nombres de archivo distintos cuenta como
  una sola entrada — cubre el caso de un PDF indexado dos veces con nombres diferentes).
  Config `simple` en todo el canal léxico a propósito: el corpus es bilingüe es/en y
  `simple` no aplica stemming de un solo idioma.
  **Criterio de "relevante" (`_is_relevant_row`)** — reemplaza al criterio anterior
  (solo `similarity >= MIN_SIMILARITY`): un chunk es relevante si CUALQUIERA se cumple:
  (1) similitud coseno >= `MIN_SIMILARITY` (0.72, como antes), O (2) el chunk cae en
  el top-3 (`LEXICAL_STRONG_RANK`) del ranking léxico fusionado (match léxico fuerte:
  matcheó al menos un término real de la query vía full-text). El contrato de
  respuesta (`sources[]` con `{content, source, similarity, relevant}`) NO cambia de
  forma — solo cambia cómo se calcula `relevant`.
  **Reescritura de query de búsqueda:** `search_query()` (reemplaza a `condense_question`
  en estos dos endpoints) resuelve pronombres con el historial Y reduce la pregunta a
  su intención esencial (quita envolturas de tarea tipo "crea una presentación sobre
  X" → "X"), agregando sinónimos técnicos clave cuando aplica (VSI/instancia/VM, ssh,
  etc.) para alimentar mejor al canal léxico. Una sola llamada LLM (max_tokens=60,
  temperature=0); se salta si no hay historial Y la pregunta ya es corta y sin verbo
  de tarea al inicio (`_looks_like_simple_direct_question`, heurística sin costo). La
  pregunta ORIGINAL (sin reescribir) se sigue usando para GENERAR la respuesta —
  `search_query()` solo afecta qué se busca, no qué se le pide al modelo.
- **`/ingest_stream` — fases NDJSON reales (evita la UI "colgada" en la subida):**
  el `StreamingResponse` se crea inmediatamente después de leer los bytes del
  upload (`file.file.read()` — debe ocurrir ANTES de crear el generador, porque
  `UploadFile` se cierra al retornar la respuesta); TODO el trabajo pesado (subida
  a COS, tempfile, `PdfReader`/extracción, `chunk_text`, dedupe por hash, embeddings)
  ocurre DENTRO del generador, emitiendo fases a medida que ocurren:
  `{"type":"received","bytes":N}` (justo tras leer el upload, confirma al cliente
  que la transferencia de bytes terminó de verdad) → `{"type":"phase","name":"storing"}`
  (solo si `COS_ENABLED`) → `{"type":"phase","name":"extracting"}` → `{"type":"start",
  "total":N,"source":...}` → `{"type":"progress","done":N,"total":N}` (repetido) →
  `{"type":"done","count":N,"source":...}`. Los clientes DEBEN ignorar cualquier
  `type` desconocido (compatibilidad hacia adelante si se agregan más fases; el
  parser NDJSON del frontend en `handleUpload` ya tiene un `continue`/rama por
  defecto para esto). El tempfile se limpia en un `finally` dentro del generador
  para no dejarlo huérfano si algo lanza a mitad de camino. `/ingest` (no-stream)
  no tiene este contrato de fases — sigue siendo síncrono de punta a punta.
- **Dedupe de ingesta por contenido:** `/ingest` y `/ingest_stream` calculan
  `sha256(full_text)` (texto extraído del PDF, no los bytes crudos — dos PDFs con
  igual contenido pero distinta compresión/metadata deben deduplicarse igual) ANTES
  de indexar. Si otro `source` (nombre de archivo) YA indexado tiene el mismo hash,
  se borran sus chunks y su objeto COS (`_cos_delete`) — el nuevo PDF lo REEMPLAZA en
  vez de duplicarlo. Este chequeo es adicional al dedupe-por-nombre-exacto que ya
  existía (`DELETE FROM documents WHERE source = %s`).
- **Datos (memoria persistente):** `conversations(id, user_sub, title, created_at,
  updated_at)` y `messages(id, conversation_id → conversations.id ON DELETE CASCADE,
  role, content, sources JSONB, created_at)`. Solo se escribe para usuarios
  autenticados (IBMid); usuarios anónimos no generan filas aquí (siguen usando
  localStorage en el frontend).
- **Modos de chat (`mode`):** `/query` y `/query_stream` aceptan `mode` opcional en el payload
  (valores: `standard` | `email` | `campaign` | `presentation` | `conceptmap`; default `standard`).
  La línea NDJSON `meta` incluye `"mode"` para que el frontend lo confirme. En `presentation` el
  modelo genera slides en markdown separadas por `---` (primera línea de cada slide = `# Título`).
  En `conceptmap` el modelo devuelve ONLY JSON `{"title", "nodes":[{"id","label"}], "edges":[{"from","to","label?"}]}`.
  Columna `messages.mode TEXT` (migración `ADD COLUMN IF NOT EXISTS`); devuelta por `GET /conversations/{id}`
  en cada mensaje para re-renderizar slides/mapas al recargar.
  Retrieval en modos ≠ `standard`: el contexto usa TODO el top-k recuperado aunque no supere
  `MIN_SIMILARITY` (la envoltura de tarea "crea una presentación sobre X" baja la similitud
  absoluta ~0.1 aunque los chunks sí sean del tema); y si aun así no hay contexto, la
  instrucción del modo prevalece (genera el entregable desde conocimiento general, sin citar
  fuentes) en vez del mensaje "no tengo información".
- **`presentation_opts` en `/query` y `/query_stream`:** campo opcional del payload, solo
  aplicable cuando `mode == "presentation"`. Estructura: `{"audience": "executive"|"technical"|"sales",
  "slides": 4|6|8|10}`. El backend valida con whitelist; valores inválidos o ausentes usan
  defaults (`executive`, `6`). Ajusta la instrucción del sistema (número exacto de slides, tono
  por audiencia) y `max_tokens` (900 para 4-6 slides, 1300 para 8-10).
- **`POST /export/pptx`:** genera un archivo `.pptx` IBM-branded descargable a partir del markdown
  de presentación. Payload extendido: `{"markdown": str, "title": str (opcional), "theme":
  "dark"|"light" (default "dark"), "eyebrow": str (default "IBM CLOUD"), "presenter": {"name",
  "role", "email"} (todos opcionales)}`. Responde con el binario del deck. Retorna 400 si
  `markdown` vacío. Formato 16:9 (13.333 × 7.5 in). Portada siempre IBM Blue #0F62FE con
  eyebrow, título 52pt Plex Light, bloque presenter, logo IBM (sobre rect blanco — logo es trazo
  negro). Layouts de slides detectados automáticamente desde el markdown, en este ORDEN de
  precedencia: `divider` (solo # Título sin bullets → slide fondo IBM Blue completo) > `stats`
  (bullets `**cifra** desc` con 2-4 items → tarjetas de estadística lado a lado) > `impact`
  (solo `> frase` → frase enorme con barra de acento azul) > `steps` (lista ordenada `1.` `2.` …
  → filas con número grande 01/02/03 en IBM Blue estilo Carbon; `**bold**` inicial = título del
  paso) > `defs` (TODOS los bullets `**Término:** desc` → lista de definiciones tipográfica) >
  `cards` (3-4 bullets, todos con `**keyword**` inicial → grid de tarjetas 2x2 con borde superior
  de acento azul) > `normal` (título + bullets con marcador cuadrado azul e icono de arquitectura
  si keyword matchea). El carrusel del frontend (`SlideDeck.jsx`/`SlideDeck.css`) renderiza los
  mismos layouts. Chrome común: franja azul superior/inferior, numeración discreta abajo-derecha,
  pie "IBM Knowledge Agent" abajo-izquierda gris, logo en portada y cierre. Assets en
  `backend/assets/`: logo `ibm-logo.emf` (negro) + `ibm-logo-white.png` (blanco con transparencia,
  para fondos oscuros), 265 iconos SVG en `architecture-icons/svg/` (caché PNG en `icon-cache/`).
  Endpoint `def` (no `async def`) — convención del proyecto.
  El prompt de `MODE_INSTRUCTIONS["presentation"]` incluye un EJEMPLO FEW-SHOT completo (mini-deck
  neutro que ejercita todos los formatos) y reglas duras: bullets de frase plana sin bold están
  prohibidos (todo bullet es def, stat, paso numerado o `**keyword**` inicial), máx 12 palabras
  por bullet, títulos máx 6 palabras, arco narrativo contexto/problema → solución + valor → CTA,
  solo UNA slide de impacto (`>`) por deck.
- **Embeddings:** `ibm/granite-embedding-278m-multilingual` (768 dim). Cambiarlo
  invalida los vectores existentes → requiere reindexar (ADR obligatorio).

## 7. Riesgos / restricciones conocidas

- **Cuota watsonx** (plan Lite = 300k tokens/mes). Toda ingesta masiva la aprueba el orquestador.
- El **backend desplegado** debe redeployarse con credenciales nuevas tras rotarlas.
- IBM Docs (cloud.ibm.com) tiene WAF → el contenido se toma de GitHub `ibm-cloud-docs`.

## 8. Roadmap (alto nivel)

- **Fase 1 (actual):** RAG multi-producto sólido (hecho/en curso).
- **Fase 2:** Asset Hub — `area`/`asset_type`, CSM + Client Engineering (ver `docs/asset-hub-taxonomy.md`).
- **Fase 3:** auth, dashboard de uso/feedback, integración Seismic. Reranking:
  parcialmente cubierto por el retrieval híbrido RRF (ver contrato arriba); un
  reranker dedicado (cross-encoder watsonx) sobre el top-N fusionado sigue pendiente.
