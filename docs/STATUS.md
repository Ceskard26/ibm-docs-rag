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
- **Modos de chat**: selector en el header (Estándar / Correo / Campaña / Presentación / Mapa conceptual).
  El parámetro `mode` se envía en `/query` y `/query_stream`; el backend aplica un system-prompt adicional
  por modo, ajusta `max_tokens`/`temperature`, y salta el chitchat en modos no estándar. Persistido en
  `messages.mode`. El frontend renderiza: `standard/email/campaign` como texto; `presentation` como
  carrusel de slides navegable (`SlideDeck.jsx`); `conceptmap` como diagrama SVG radial (`ConceptMap.jsx`)
  con fallback elegante si el JSON no parsea.
- **Árbol de gobernanza (mockup) — retirado del UI**: existía un switcher Chat / Árbol de
  gobernanza en el header (`HeaderGlobalBar`) que mostraba un organigrama CSS puro (sin
  librerías externas), nodos colapsables, resaltado del usuario logueado, banner de datos
  de demo. Decisión del PO: quitarlo del header ("menos es más" — no aportaba valor con
  datos falsos). Se removieron del UI: los dos botones del switcher, el estado `activeView`,
  el import y el render condicional de `OrgTreeView`, y los imports de iconos `Wikis`/`Chat`
  que quedaron sin uso. El código se CONSERVA intacto para cuando haya datos reales del
  directorio: `frontend/src/OrgTreeView.jsx/css`, `frontend/src/orgTree.js` (mock data),
  `docs/org-tree.md` (contrato de datos). Las claves i18n `orgDemoTitle`/`orgDemoSubtitle`/
  `orgYou` se conservan porque `OrgTreeView.jsx` las sigue usando; se eliminaron `viewChat`/
  `viewOrgTree` (ya sin referencias, eran solo labels del switcher retirado).
- **Links de PDFs vía `/files`**: fuentes citadas que no empiezan con `http` se enlazan a
  `${API_BASE}/files/${filename}` (endpoint `GET /files/{filename}` aportado por backend).
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
- **Memoria persistente por usuario (BD)**: usuarios logueados con IBMid tienen sus
  conversaciones guardadas en Postgres (tablas `conversations`/`messages`, ligadas al
  `sub`), visibles desde cualquier dispositivo (`GET/DELETE /conversations`,
  `GET /conversations/{id}`; `/query` y `/query_stream` aceptan/devuelven
  `conversation_id`). Los usuarios anónimos siguen igual que antes (solo localStorage).
- Concurrencia: endpoints son `def` (no `async def`) para no bloquear el event loop.
- **Auth con IBM Cloud App ID FUNCIONANDO** (IBMid + Cloud Directory). Flujo:
  frontend redirige a App ID → vuelve con `code` → backend lo canjea con el secret
  (server-side, `/auth/exchange`) → valida el token (JWKS) → nombre/email del id_token.
- Dockerfiles de backend y frontend correctos (backend copia `main.py` + `auth.py`).
- **COS (IBM Cloud Object Storage):** PDFs subidos vía `/ingest` e `/ingest_stream`
  se almacenan en COS con key `pdfs/<filename>`. Nuevo endpoint `GET /files/{filename}`
  los sirve inline al navegador. Feature opcional: si las vars `COS_ENDPOINT`,
  `COS_API_KEY`, `COS_INSTANCE_CRN` y `COS_BUCKET` no están, el backend funciona
  exactamente igual que antes. Cancelar una ingesta (`/ingest_cancel`) también borra
  el objeto de COS. La subida/borrado es best-effort (no rompe la ingesta si falla).
- **Retrieval híbrido (semántico + léxico) con RRF**: `hybrid_retrieve()` reemplaza a
  `retrieve()` (que se conserva como fallback/referencia) en `/query` y `/query_stream`.
  Fusiona top-15 por coseno + top-15 por full-text (`to_tsquery('simple', ...)` con
  OR entre lexemes, no AND — ver `_or_tsquery`, filtra stopwords ES/EN) vía Reciprocal
  Rank Fusion (k=60), deduplica por contenido idéntico (el caso del mismo PDF subido
  dos veces) y devuelve el top-5. Nuevo criterio de "relevante": similitud >=
  `MIN_SIMILARITY` **o** el chunk cae en el top-3 del ranking léxico (`_is_relevant_row`).
  Índice GIN `idx_documents_content_fts` sobre `to_tsvector('simple', content)`.
  Corrige el caso real: preguntas parafraseadas ("comando para conectarme a una
  máquina virtual") que embeben lejos de un chunk literal con "ssh" ahora se
  encuentran por el canal léxico.
- **Reescritura de query de búsqueda**: `search_query()` (reemplaza a `condense_question`
  en `/query`/`/query_stream`) resuelve pronombres con el historial Y reduce la pregunta
  a su intención esencial quitando envolturas de tarea ("crea una presentación sobre X"
  → "X"), agregando sinónimos técnicos (VSI/instancia/VM, ssh) que alimentan al canal
  léxico. Una sola llamada LLM (max_tokens=60, temp=0). Si no hay historial Y la
  pregunta ya es corta y sin verbo de tarea (heurística `_looks_like_simple_direct_question`,
  sin costo), se salta la llamada. La query original sigue usándose para generar.
- **Dedupe de ingesta por contenido**: `/ingest`/`/ingest_stream` calculan
  `sha256(full_text)` del PDF y, si otro archivo YA indexado con OTRO nombre tiene el
  mismo hash, borra sus chunks y su objeto COS antes de indexar el nuevo (reemplazo,
  no duplicado). Columna `documents.content_hash TEXT` (migración `ADD COLUMN IF NOT
  EXISTS`). No se limpiaron los duplicados YA existentes en la BD (`secure-infrastructure-vpc.pdf`
  vs `secure-infrastructure-vpc (1).pdf`) — el dedupe aplica a ingestas futuras; el
  retrieval híbrido ya neutraliza el impacto de los duplicados existentes vía RRF.
- **Fix: `/ingest_stream` ya no se queda "colgado" tras terminar la subida.** Antes,
  todo el trabajo pesado (subida a COS, tempfile, `PdfReader`/extracción, chunking,
  dedupe por hash) corría de forma síncrona ANTES de crear el `StreamingResponse` —
  el navegador terminaba de transferir bytes hace rato pero la UI seguía en
  "Uploading… X MB / X MB" varios segundos sin feedback. Ahora el generador se crea
  de inmediato (`file.file.read()` sigue siendo síncrono, antes del generador —
  requerido porque `UploadFile` se cierra al retornar la respuesta) y emite fases
  reales a medida que ocurren: `received` (bytes confirmados) → `phase: storing`
  (solo si COS activo) → `phase: extracting` → `start`/`progress`/`done` (sin cambios).
  Tempfile se limpia en `finally`. Frontend (`handleUpload` en `App.jsx`) mapea cada
  fase a una etiqueta visible ("Recibido, guardando…" / "Guardando en Object
  Storage…" / "Extrayendo texto…") con barra indeterminada; ignora tipos NDJSON
  desconocidos (compatibilidad hacia adelante). Contrato completo en
  `docs/GOVERNANCE.md` sección 6.
- **Etiquetado por producto al subir un PDF (`documents.tag`)**: `/ingest`/`/ingest_stream`
  aceptan un campo `tag` opcional (form-data) con uno de los 7 IDs de producto del
  frontend; whitelist estricta (`VALID_TAGS`/`_sanitize_tag`) — cualquier otro valor
  se guarda `NULL` sin fallar la ingesta. `product_filter_sql` ahora matchea por
  `source LIKE ...` **o** `tag = <producto>`, así un PDF subido con categoría aparece
  al filtrar por ese producto igual que los docs de GitHub. Frontend: nuevo `Dropdown`
  "Categoría (opcional)" junto al uploader de PDF (reusa el array `PRODUCTS`, opción
  por defecto "Sin categoría"). Motivado por la contaminación de retrieval detectada
  el 2026-09-15 (~2225 chunks de PDFs de prueba sin relación con los 7 productos);
  el tagueo evita que vuelva a pasar sin tener que borrar/reindexar todo.
- **Preguntas de seguimiento sugeridas (2026-09-15)**: tras una respuesta normal
  (Caso C de `mode == "standard"` — ni chitchat, ni ambigüedad Caso A, ni fuera de
  alcance Caso B, ni otros `mode`), aparecen 2-3 chips clicables debajo de la
  respuesta con preguntas de seguimiento relacionadas; clic = se auto-envían con la
  `ask()` existente. Sin costo de LLM extra: el modelo las genera en la MISMA
  llamada de generación, delimitadas por un marcador (`---SUGERENCIAS---`) que el
  backend separa del texto visible ANTES de que llegue al cliente (streaming con
  "hold-back" del marcador — nunca aparecen a medias ni se mezclan con la
  respuesta). Contrato completo en `docs/GOVERNANCE.md`. Frontend: chips Carbon
  (`Button kind="ghost" size="sm"`) en `App.jsx`, sin dependencias nuevas.
- **Panel de feedback — uso interno de César (2026-09-15)**: nuevo endpoint
  `GET /feedback/stats` (requiere login, 401 si anónimo) agrega la tabla
  `feedback`: totales 👍/👎, desglose por idioma, últimos 50 👎 con
  pregunta/respuesta/fecha para revisar qué falló. Solo lectura. Frontend: vista
  `frontend/src/Dashboard.jsx`, accesible SOLO por URL directa (`#dashboard`) —
  a propósito SIN botón en el header, respetando la decisión "menos es más" ya
  tomada al retirar el switcher del árbol de gobernanza (ver más abajo). Requiere
  sesión iniciada; sin sesión muestra un botón de login simple.

## Config de COS (vars de entorno — todas secretas, van al secret de Code Engine)

| Variable | Descripción | Ejemplo |
|---|---|---|
| `COS_ENDPOINT` | Endpoint regional del bucket | `https://s3.us-south.cloud-object-storage.appdomain.cloud` |
| `COS_API_KEY` | API key de la service credential con rol Writer | `<ibm_api_key>` |
| `COS_INSTANCE_CRN` | CRN de la instancia de COS | `crn:v1:bluemix:public:cloud-object-storage:...` |
| `COS_BUCKET` | Nombre del bucket donde viven los PDFs | `knowledge-agent-pdfs` |

Si alguna de estas vars falta (o todas), la feature queda desactivada y el backend
funciona igual que antes (sin COS). `GET /files/<nombre>` devuelve 503 en ese caso.

## Config de App ID (valores no secretos)
- Tenant: `a5ee625d-5102-40e2-9e98-0e706d0e26c3`
- App confidencial (Regular web app) client id: `9e933481-ed22-4127-a67f-f1cdbc76f2cc`
- `APPID_CLIENT_SECRET` está en `backend/.env` (NO en el repo).
- `AUTH_REQUIRED=false` (login opcional; la app corre anónima si no).
- Redirect URLs en App ID (Authentication settings): `http://localhost:5173` (falta la de Code Engine).

- **Exportar presentación a .pptx (IBM Brand v2)**: endpoint `POST /export/pptx` (python-pptx)
  genera un deck IBM-branded profesional 16:9. Portada: fondo IBM Blue #0F62FE, eyebrow, título
  52pt Plex Light, bloque presenter (nombre/rol/email), logo IBM (negro, sobre rect blanco). Layouts
  de contenido detectados automáticamente: `divider` (divisor de sección fondo azul completo),
  `stats` (tarjetas de estadística con cifra gigante + descripción), `defs` (lista de definiciones
  término+descripción), `impact` (frase de alto impacto + barra azul), `normal` (título+bullets
  con icono arquitectura IBM Cloud si keyword matchea). Chrome común en todas las slides: franjas
  IBM Blue superior/inferior, numeración abajo-derecha, pie "IBM Knowledge Agent" gris. Temas:
  `dark` (#161616) y `light` (#FFFFFF), seleccionable desde el frontend. Payload extendido:
  `{markdown, title, theme, eyebrow, presenter:{name,role,email}}`. Assets: logo `ibm-logo.emf`
  (trazo negro verificado via qlmanage/PDF), 265 iconos SVG en `backend/assets/architecture-icons/`,
  caché PNG en `backend/assets/icon-cache/`. `cairosvg` añadido a requirements.
- **Controles de presentación (header)**: cuando `mode === 'presentation'` aparecen tres
  dropdowns compactos: Audiencia (Ejecutiva/Técnica/Comercial), Slides (4/6/8/10), y Tema
  (Oscuro/Claro, i18n es/en). El tema se pasa a `SlideDeck.jsx` y al POST de `/export/pptx`.
  Si hay `authUser`, el POST incluye `presenter:{name, role:'IBM Cloud', email}` automáticamente.
- **Prompt storytelling de presentación**: `MODE_INSTRUCTIONS["presentation"]` actualizado con
  arco narrativo (contexto/problema → solución+valor → CTA), layout variety (instruye al LLM a
  usar divisores, stats `**cifra**`, defs `**Término:**`, solo 1 quote `>` por deck), max 5
  bullets por slide, idioma de la audiencia.
- **Slides nivel IBM v3 (feedback "muy simples")**: dos layouts nuevos — `steps` (listas
  ordenadas `1.` → filas con número grande 01/02/03 azul estilo Carbon) y `cards` (3-4 bullets
  `**keyword**` → grid de tarjetas 2x2 con borde de acento); `normal` enriquecido (marcador
  cuadrado azul por bullet). Prompt con EJEMPLO FEW-SHOT completo + regla dura: prohibidos los
  bullets de frase plana sin bold (Llama ignoraba las convenciones solo descritas). Logo blanco
  `ibm-logo-white.png` directo sobre fondos oscuros (sin caja blanca). Mismos layouts en el
  carrusel de la UI (`SlideDeck.jsx/css`). Verificación visual por thumbnail (qlmanage) de cada
  layout en dark y light.

- **Expiración de sesión (~2h) + fix de nombre ausente**: `frontend/src/auth.js` guarda
  `ika-login-at` (timestamp) junto al token/perfil. `initAuth()` invalida la sesión
  restaurada (limpia localStorage y vuelve a mostrar la landing) si: no hay
  `ika-login-at`, pasaron más de `SESSION_MAX_AGE_MS` (2h), o el perfil restaurado no
  trae `name`. Antes, un `ika-user` corrupto/ausente dejaba la app "logueada" sin
  mostrar nombre en el header. `logout()` también limpia `ika-login-at`.
- **Progreso de subida de PDF honesto**: la fase "uploading" ya no salta a 100% falso
  por el buffer de red del SO. La barra queda topada a 95% (bytes reales, label
  "Subiendo… X.X MB / Y.Y MB") hasta que llega la primera línea NDJSON `{"type":"start"}`
  (servidor confirma que ya tiene el archivo completo), momento en que se marca 100% y
  se pasa a la fase "processing" (sin cambios). Si `e.lengthComputable` es `false`, la
  `ProgressBar` de Carbon se muestra en modo indeterminado en vez de 0% congelado.
  Cancelar (abort del xhr + `/ingest_cancel`) intacto.
- **Auto-clasificación de PDFs por IA (2026-09-16)**: el `tag` de `/ingest`/`/ingest_stream`
  pasa de "categoría que el usuario elige" a **override opcional** — si no se manda `tag`
  (o llega vacío/inválido), el backend llama a `_auto_detect_tag(full_text)` (una sola
  llamada corta al `_chat_model()` singleton, `max_tokens=20`, `temperature=0`, sobre los
  primeros ~3000 caracteres del texto YA extraído — no se vuelve a parsear el PDF) pidiendo
  SOLO uno de los 7 IDs de `VALID_TAGS` o `none`. Misma whitelist estricta que el tag manual
  (`_sanitize_tag`): cualquier respuesta fuera de los 7 IDs cae a `NULL`, la ingesta nunca
  falla por esto. Si el usuario SÍ eligió una categoría en el dropdown, esa gana siempre
  (el auto-detect ni se llama). Solo toca el camino de ingesta — `/query`/`/query_stream` no
  se tocaron, verificado sin regresión de latencia (barrido de 7 productos, ~4.3s promedio
  en estado estable, igual que antes). Frontend: copy del dropdown actualizado a "Categoría
  (se detecta automáticamente; elige una para forzarla)" / opción por defecto ahora
  "Detectar automáticamente" en vez de "Sin categoría" (mismo `id: ''`, sin cambio de
  comportamiento de envío del form). Contrato completo en `docs/GOVERNANCE.md`.

## Pendiente (próximos pasos)
1. **Deploy a Code Engine**: imágenes ya listas. Falta: secret del backend con las vars
   de App ID (OAUTH_SERVER_URL, CLIENT_ID, CLIENT_SECRET, AUTH_REQUIRED) + watsonx/postgres;
   `API_URL` del frontend; y agregar la URL del frontend a los redirect URLs de App ID.
   Añadir también las 4 vars de COS al secret (ver sección "Config de COS" más abajo).
2. **Dashboard de feedback (hecho ✅, ver bullet arriba)** — pendiente aún: dashboard
   de USO (no solo feedback) y Fase 2 Asset Hub (ver `docs/asset-hub-taxonomy.md`).
   Reranking: parcialmente cubierto por el retrieval híbrido RRF (ver bullet arriba);
   un reranker dedicado (watsonx, cross-encoder) sobre el top-N fusionado sigue pendiente.

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
- **`secure-infrastructure-vpc.pdf` eliminado de la BD (2026-09-16, 685 chunks).**
  César probó producción y el link de esta fuente daba 404 — investigado: el
  archivo se subió el 2026-07-02 durante el testing de la feature de Object
  Storage/subida de PDFs (mismo patrón que los otros archivos de ruido
  limpiados el 2026-09-15), NUNCA fue algo que César subiera a propósito, y
  sus bytes nunca llegaron a COS (bucket confirmado vacío en `pdfs/`). Ni
  César ni Claude tienen el PDF original. Se borró en vez de intentar
  recuperarlo — la cobertura de VPC sigue sólida vía los 935 docs oficiales
  de GitHub (`ibm-cloud-docs/vpc/...`), que sí tienen links funcionales.
  Verificado sin regresión: preguntas de VPC siguen respondiendo con
  similitud >=0.72 citando solo fuentes de GitHub.
- **Latencia (medido 2026-09-16): instanciar `ModelInference`/abrir una conexión Postgres
  cuestan ~1-2s CADA VEZ, sin importar que sea el mismo proceso** — no es la llamada de
  red en sí (~0.4-0.5s), es la construcción del objeto/el handshake. `_chat_model()` ahora
  es un singleton perezoso (igual patrón que `_embeddings`) y Postgres usa un
  `ThreadedConnectionPool` (`_get_pool()`/`_release_db()`) en vez de abrir/cerrar una
  conexión por query. Antes de este fix una sola pregunta pagaba el costo de construir el
  modelo 2 veces (reescritura + generación) y abrir 3+ conexiones (retrieval, conversación,
  guardar mensaje) — total ~11-12s por pregunta. Después: ~4.5-5s en estado estable (la
  primera pregunta tras un arranque/redeploy sigue "calentando" los singletons, ~8s).
  **Ojo con el pool al reusar conexiones**: `_release_db` hace `conn.rollback()` antes de
  devolver la conexión — sin esto, una query que falla a mitad de una transacción deja la
  conexión en estado "abortada" y envenena al PRÓXIMO request que la tome del pool (antes
  no importaba porque la conexión se cerraba y se descartaba).

## Equipo / gobernanza
- `.claude/agents/`: frontend-lead, backend-lead, rag-lead, features-lead, devops-lead, qa-reviewer.
- `docs/GOVERNANCE.md`: roles, flujo, Definition of Done, contratos, ADRs.
- Modelo de trabajo: César = product owner (dice la intención); Claude = orquestador
  (descompone, despacha agentes, integra, revisa).
