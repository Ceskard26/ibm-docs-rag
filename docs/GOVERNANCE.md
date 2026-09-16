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
- **API (uso interno, requiere login Y ser admin — 401 si `sub == "anonymous"`,
  403 si logueado pero no está en `_FEEDBACK_ADMIN_EMAILS`):** `GET /feedback/stats`
  → agrega la tabla `feedback` (solo lectura): `{total_up, total_down,
  by_language: [{language, up, down}], recent_negative: [{question, answer,
  created_at}]}` — `recent_negative` son los últimos `FEEDBACK_STATS_RECENT_LIMIT`
  (50) feedbacks 👎, más recientes primero. `_FEEDBACK_ADMIN_EMAILS` (env var
  `FEEDBACK_ADMIN_EMAILS`, coma-separado; default `cesar.carrasco@ibm.com`) —
  agregado 2026-09-15 tras QA: el endpoint exponía preguntas/respuestas de
  TODOS los usuarios a cualquiera con sesión válida, no solo a César. NO tiene
  botón/enlace en el header (decisión "menos es más" del PO, ver
  `docs/STATUS.md`) — el frontend lo expone solo por acceso directo a
  `#dashboard` (`frontend/src/Dashboard.jsx`).
- **Sugerencias de seguimiento (`/query`, `/query_stream`) — SOLO Caso C de
  `mode == "standard"`** (ni chitchat, ni Caso A "ambiguous", ni Caso B
  "out_of_scope" — ver `_detect_ambiguity_or_scope` más abajo—, ni otros `mode`,
  y solo si hubo contexto relevante): el modelo genera 2-3 preguntas de
  seguimiento en la MISMA llamada de generación de la respuesta (sin costo de
  una llamada extra al LLM), delimitadas al final por el marcador literal
  `SUGGESTIONS_MARKER = "---SUGERENCIAS---"` seguido de hasta 3 líneas numeradas
  (ver `SUGGESTIONS_INSTRUCTION`, `_parse_suggestions`). El backend NUNCA deja
  que el marcador ni el texto posterior lleguen al cliente como parte del texto
  visible: en `/query_stream` se hace streaming con "hold-back" (retiene en
  buffer los últimos `len(marcador)-1` caracteres sin flushear hasta confirmar
  que NO son el inicio del marcador — algoritmo estándar de delimitador en
  streaming, robusto aunque el marcador llegue partido entre dos deltas del
  LLM); cuando `want_suggestions` es `False` (Caso A/B, chitchat, otros `mode`)
  el hold es 0 y el comportamiento es idéntico al de antes de esta feature (sin
  latencia añadida). Contrato de salida: `/query` devuelve el campo aditivo
  `suggestions: string[]` (vacío si no aplica o si el parseo falló — nunca
  rompe la respuesta principal); `/query_stream` emite una línea NDJSON
  `{"type":"suggestions","items": string[]}` DESPUÉS del último `token` y ANTES
  de `done`, solo si se parseó al menos una sugerencia. El marcador y las
  sugerencias NUNCA se persisten en `messages.content` (se separan del texto
  limpio antes de `save_messages`) — no reaparecen al recargar una conversación
  guardada, son efímeras del turno en vivo. Frontend: chips `Button kind="ghost"
  size="sm"` debajo de la respuesta (máx. 3), clic = `ask(pregunta)`.
- **Ambigüedad / fuera-de-alcance (`_detect_ambiguity_or_scope`) — SOLO
  `mode == "standard"`, después de `hybrid_retrieve` y antes de generar:**
  **Caso A "ambiguous"** (activo): el top-3 fusionado se reparte entre 2+
  productos distintos con similitudes muy parecidas (`abs(gap) < AMBIGUITY_SIMILARITY_GAP`
  = 0.03, `best_sim >= AMBIGUITY_MIN_SIMILARITY` = 0.78) y la pregunta NO nombra
  ya explícitamente a ninguno de los dos candidatos (si lo hace, no hay
  ambigüedad real — se usa `abs()` en el gap y ese guard de nombre porque una
  primera versión, sin ellos, podía disparar con gap negativo o preguntar por
  un producto que el usuario ya había nombrado; ver `docs/STATUS.md`). El
  system prompt de generación cambia a `SCOPE_INSTRUCTIONS["ambiguous"]`
  (pregunta de aclaración breve mencionando los 2 productos, sin inventar
  contenido de ninguno). Campo aditivo `clarification: bool` en la respuesta
  de `/query` y en la línea `meta` de `/query_stream` (`true` solo en Caso A).
  **Caso B "out_of_scope" — DESACTIVADO (2026-09-15):** el código y las
  constantes (`OUT_OF_SCOPE_SIMILARITY_LOW/HIGH`) se conservan, pero la
  función ya no lo dispara. QA pre-deploy encontró ~27% de falsos positivos en
  preguntas cortas y perfectamente en alcance ("como despliego una app",
  "cuanto cuesta el servicio") — la banda [0.72, 0.78] no distingue un match
  débil real de un buen match típico en español, y la UI quedaba
  autocontradictoria (decía "no cubro esto" mostrando debajo las fuentes
  correctas). El flujo "sin información" pre-existente (contexto vacío en
  `_build_messages`) sigue cubriendo el caso real fuera de alcance (ej.
  MongoDB, que no es ninguno de los 7 productos) de forma honesta, sin la
  heurística de similitud que causaba los falsos positivos. Retomar el Caso B
  requiere una heurística mejor calibrada, no solo ajustar la banda (ver
  comentario en `main.py` junto a `_detect_ambiguity_or_scope`).
- **API (archivos — COS opcional):** `GET /files/{filename}` → sirve el PDF
  almacenado en COS con `Content-Disposition: inline`. 503 si COS no está
  configurado; 400 si el filename contiene `/`, `\` o `..`; 404 si el objeto no
  existe. El objeto vive en COS con key `pdfs/<filename>`. La feature se activa
  solo si las cuatro vars `COS_ENDPOINT`, `COS_API_KEY`, `COS_INSTANCE_CRN` y
  `COS_BUCKET` están presentes; en su ausencia el comportamiento es idéntico al
  anterior (sin COS).
- **Datos:** tabla `documents(id, content, embedding vector(768), source, created_at,
  content_hash, tag)` y `feedback(...)`. Fuente (`source`) = URL de GitHub, de IBM Docs, o
  nombre de archivo PDF subido manualmente. `content_hash` (TEXT, nullable) = sha256
  del texto completo extraído de un PDF subido vía `/ingest`/`/ingest_stream` (NULL
  para chunks de GitHub/scraper, que no pasan por ese flujo); usado para dedupe de
  re-subidas con nombre distinto (ver "Dedupe de ingesta" más abajo). `tag` (TEXT,
  nullable) = categoría/producto opcional elegida al subir un PDF vía `/ingest` o
  `/ingest_stream` (ver contrato de esos endpoints más abajo); NULL para chunks de
  GitHub/scraper y para PDFs subidos sin elegir categoría. Índice GIN
  `idx_documents_content_fts` sobre `to_tsvector('simple', content)` para el canal
  léxico del retrieval híbrido.
- **`/ingest` y `/ingest_stream` — campo `tag` (siempre auto-detectado, 2026-09-16):**
  además de `file`, ambos aceptan un campo `tag` opcional con un ID de producto —
  el espacio de IDs válidos es `VALID_TAGS` en `main.py` (10 al momento de escribir
  esto: `watsonx`, `vpc`, `messages-for-rabbitmq`, `containers`, `codeengine`,
  `cloud-object-storage`, `databases-for-postgresql`, `appid`, `Cloudant`
  —mayúscula, ver más abajo—, `key-protect`). Whitelist estricta en
  `_sanitize_tag`/`VALID_TAGS`: cualquier valor fuera de esa lista (incluido
  vacío/ausente) se sanea a `None` — la ingesta NUNCA falla por un tag inválido.
  **El frontend YA NO ofrece un dropdown para elegir categoría manualmente**
  (eliminado 2026-09-16 por instrucción directa: "elimina la opción de agregar una
  etiqueta, haz que el auto-detect esté por default" — ver `docs/STATUS.md`), así
  que en la práctica `tag` siempre llega vacío desde el frontend y el backend
  SIEMPRE auto-detecta con `_auto_detect_tag(full_text)`: una sola llamada corta
  al `_chat_model()` (singleton compartido, no se instancia uno nuevo) sobre los
  primeros ~3000 caracteres del texto YA extraído del PDF (mismo `full_text`/`text`
  que ya se usa para trocear — no se vuelve a parsear nada), `max_tokens=20`,
  `temperature=0`, pidiendo SOLO uno de los IDs de `VALID_TAGS` o la palabra
  `none`. La respuesta del modelo pasa por la MISMA whitelist estricta
  (`_sanitize_tag`) — cualquier cosa que no sea exactamente uno de los IDs cae a
  `NULL`, nunca bloquea ni falla la ingesta. El campo `tag` en el payload del
  endpoint se mantiene por compatibilidad/uso programático (p.ej. scripts), pero
  la UI no lo expone. El tag resultante se guarda en cada chunk insertado de ese
  PDF y se usa en `product_filter_sql` (ver retrieval híbrido) para que el PDF
  aparezca al filtrar por ese producto, igual que los docs de GitHub. Esta
  auto-clasificación ocurre SOLO en el camino de ingesta (operación puntual) — no
  agrega ninguna llamada ni latencia a `/query`/`/query_stream`.
- **Expansión de productos vía GitHub — prueba controlada (2026-09-16):** el WAF de
  `cloud.ibm.com/docs`/`www.ibm.com/docs` se re-verificó en vivo (curl Y Playwright
  headless) y sigue bloqueando scraping — ver `docs/STATUS.md` para el detalle. El
  mirror `github.com/ibm-cloud-docs` tiene 230 repos de producto; se agregaron 3
  (`appid`, `Cloudant`, `key-protect`) a `GITHUB_PRODUCTS` (`scraper.py`),
  `VALID_TAGS`/`PRODUCT_DISPLAY_NAMES` (`main.py`) y el dropdown de filtro
  (`frontend/src/App.jsx`) como prueba end-to-end antes de una expansión mayor.
  **Gotcha de mayúsculas**: `_detect_product` matchea el `tag` contra el segmento
  `ibm-cloud-docs/<repo>/` de la URL vía regex, sensible a mayúsculas — el ID en
  `VALID_TAGS` debe ser EXACTAMENTE el nombre del repo en GitHub. El repo de
  Cloudant se llama `Cloudant` (mayúscula), así que el tag ID es `"Cloudant"`, no
  `"cloudant"` — si no calzan, `_detect_product` devuelve `None` para esos chunks
  (sin filtro de producto, sin prefijo de nombre en el embedding) sin error visible.
  `SKIP_SUBSTRINGS` en `scraper.py` (lista de sufijos de archivo a excluir de la
  ingesta) ahora también excluye `readme` — el `README.md` de cada repo es la
  descripción del repo, no documentación real, y antes pasaba el filtro de
  longitud (≥200 chars) y contaminaba el retrieval (se encontró y limpiaron 4
  chunks de README ya indexados de los 6 productos originales durante esta prueba).
- **Retrieval híbrido (semántico + léxico, fusión max+bonus) — contrato de
  `/query`/`/query_stream`:** el retrieval en producción es `hybrid_retrieve()`
  (no `retrieve()`, que se conserva como fallback/comparación). Fusiona (a) top-15
  por similitud coseno y (b) top-15 por ranking léxico full-text —
  `to_tsquery('simple', ...)` con los lexemes de la query unidos por OR (no AND:
  `websearch_to_tsquery` exige que TODOS los términos aparezcan en el mismo chunk,
  lo que casi nunca pasa con una query de 4-5 palabras), excluyendo stopwords
  ES/EN (`_STOPWORDS_ES_EN`). Deduplica por contenido idéntico ANTES de devolver
  el top-5 (mismo texto de fuentes/nombres de archivo distintos cuenta como una
  sola entrada — cubre el caso de un PDF indexado dos veces con nombres
  diferentes). Config `simple` en todo el canal léxico a propósito: el corpus es
  bilingüe es/en y `simple` no aplica stemming de un solo idioma.
  **Fórmula de fusión (cambio 2026-09 — reemplaza la suma RRF pura):**
  `score = max(sem, lex) + RRF_BONUS * min(sem, lex)`, donde `sem = 1/(RRF_K +
  rank_semántico)` si el chunk apareció en ese canal (si no, 0; mismo criterio
  para `lex`). `RRF_K = 8`, `RRF_BONUS = 0.2` (ajustados empíricamente — ver
  ADR/commit; no son los valores "estándar de literatura" k=60, elegidos a
  propósito para esta rama angosta de 15 candidatos). Motivo: la suma RRF
  aditiva clásica (`score = sem + lex`) favorecía sistemáticamente a un chunk
  "decente en ambos canales" por encima de uno "excelente en un solo canal" —
  con `HYBRID_BRANCH_LIMIT=15` candidatos por rama y k=60, la diferencia de
  score entre rank 1 y rank 15 es muy chica (~23%), así que dos apariciones
  mediocres casi siempre superan a una aparición excelente. Esto rompía un caso
  real: una pregunta de Kubernetes parafraseada ("¿cómo escalo un cluster de
  Kubernetes en IBM Cloud?") cuyo chunk correcto rankeaba #3 puro-coseno pero no
  contenía ningún lexema literal de la query (sin stemming) — perdía contra
  chunks con match léxico+semántico mediocres en ambos canales. A la vez, el
  canal léxico existe PRECISAMENTE para el caso opuesto — una pregunta
  parafraseada que embebe lejos de un chunk literal (`"comando para conectarme a
  una máquina virtual"` vs. un chunk con `ssh -i key.pem...`) — y ESE caso
  necesita que un match léxico fuerte, sin apoyo semántico, también rankee alto.
  `max(sem, lex)` resuelve ambos casos (premia la excelencia en CUALQUIERA de
  los dos canales); el término `RRF_BONUS * min(sem, lex)` sigue premiando el
  doble-match (la señal más fuerte, cuando ambos canales concuerdan) sin dejar
  que por sí solo le gane a la excelencia en un solo canal. Verificado sin
  regresión contra el caso léxico-puro (SSH) y el barrido de 7 productos — ver
  `docs/STATUS.md`.
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
- **Diseño "nivel 2" del PPTX (2026-09-16):** feedback directo del usuario tras 3 fixes de
  bugs de layout fue "el nivel es el mismo, no le agregaste absolutamente nada" — los fixes
  anteriores eran correcciones, no una mejora visual real. Se añadió una capa de diseño con
  profundidad real (helpers `_add_gradient_bg`, `_add_soft_circle`, `_add_rounded_rect`,
  `_add_shadow`, `_set_color_alpha` — python-pptx no expone gradientes/sombras/transparencia
  en su API pública, se manipula el XML `effectLst`/`gradFill`/`alpha` directamente):
  portada y `divider` con fondo degradado (IBM Blue → azul oscuro) + círculos translúcidos
  decorativos en vez de color plano; `cards`/`stats` con tarjetas de esquina redondeada y
  sombra suave (antes rectángulos planos sin jerarquía); icon chip (`_render_icon`) subido de
  badge de 0.55" casi invisible a tarjeta de ~1.15" con sombra — aprovecha que los iconos
  oficiales de arquitectura IBM ya traen su propio color de categoría, dando variedad
  cromática al deck sin salirse de marca; `impact` con una comilla gigante translúcida de
  fondo como ancla visual; cierre con el mismo círculo de la portada (bookend). El cambio de
  mayor impacto: `stats` con cifra porcentual (`**72%** desc`) renderiza un **gráfico
  doughnut nativo** (anillo de progreso real vía `chart.XL_CHART_TYPE.DOUGHNUT`, no texto) con
  el % superpuesto en el centro — cifras no-porcentuales (`5 min`, `300+`) siguen como número
  grande centrado. Verificado visualmente (LibreOffice headless → PDF → inspección de imagen)
  en ambos temas y con casos límite (100%, 0.5%, grids 2x2 y fila de 3) antes de dar por
  cerrado — mismo protocolo de verificación que los fixes anteriores de esta sesión.
- **Embeddings:** `ibm/granite-embedding-278m-multilingual` (768 dim). Cambiarlo
  invalida los vectores existentes → requiere reindexar (ADR obligatorio).

## 7. Riesgos / restricciones conocidas

- **Cuota watsonx** — instancia `watsonx-ika` en plan `v2-standard` (pay-as-you-go, sin
  techo mensual fijo desde 2026-09-15; antes Lite se agotó en un día de testing intenso).
- El **backend desplegado** debe redeployarse con credenciales nuevas tras rotarlas.
- IBM Docs (cloud.ibm.com) tiene WAF → el contenido se toma de GitHub `ibm-cloud-docs`.
- **`auth.get_current_user` con `AUTH_REQUIRED=false` (login opcional, como está hoy):**
  un token presente pero inválido/vencido cae a `ANONYMOUS`, NO devuelve 401 — bug real
  detectado 2026-09-16 (sesión IBMid vencida a mitad de uso hacía que `/query_stream`,
  que no exige login, devolviera 401 y pareciera que "el backend no responde"; el diseño
  de la app es justamente que corra anónima sin login válido). Endpoints que SÍ exigen
  login (`_require_login`: `/conversations*`, `/feedback/stats`) siguen devolviendo su
  propio 401/403 claro al recibir el usuario anónimo resultante — no se pierde
  protección ahí. Solo con `AUTH_REQUIRED=true` un token inválido sigue dando 401 duro.
  Frontend: `clearSession()` (exportada de `auth.js`) + `handleAuthExpired()` en
  `App.jsx` limpian el estado local (sin recargar la página, a diferencia de `logout()`)
  si `/conversations` responde 401 — evita mostrar "sesión iniciada" con una sesión ya
  muerta por detrás.

## 8. Roadmap (alto nivel)

- **Fase 1 (actual):** RAG multi-producto sólido (hecho/en curso).
- **Fase 2:** Asset Hub — `area`/`asset_type`, CSM + Client Engineering (ver `docs/asset-hub-taxonomy.md`).
- **Fase 3:** auth, dashboard de uso/feedback, integración Seismic. Reranking:
  parcialmente cubierto por el retrieval híbrido RRF (ver contrato arriba); un
  reranker dedicado (cross-encoder watsonx) sobre el top-N fusionado sigue pendiente.
