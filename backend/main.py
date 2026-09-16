import io
import os
import re
import json
import time
import tempfile
import unicodedata
from contextlib import contextmanager
from dotenv import load_dotenv

# ANTES de importar auth: auth.py lee APPID_* al importarse. Con load_dotenv()
# después del import, App ID quedaba silenciosamente deshabilitado (sin login)
# en todo arranque que dependiera del .env.
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
import psycopg2
import psycopg2.pool
from psycopg2.extras import Json

import auth
from pgvector.psycopg2 import register_vector
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference, Embeddings
from pypdf import PdfReader

app = FastAPI(title="IBM Knowledge Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

credentials = Credentials(
    url=os.getenv("WATSONX_URL"),
    api_key=os.getenv("WATSONX_API_KEY")
)
project_id = os.getenv("WATSONX_PROJECT_ID")

# ── IBM Cloud Object Storage (COS) ──────────────────────────────────────────
# Opcional: si alguna de estas vars falta, COS_ENABLED=False y todo funciona
# exactamente como antes (los PDFs simplemente no se guardan en COS).
# El cliente se crea la primera vez que se necesita (instanciarlo hace red).
_COS_ENDPOINT = os.getenv("COS_ENDPOINT", "")
_COS_API_KEY = os.getenv("COS_API_KEY", "")
_COS_INSTANCE_CRN = os.getenv("COS_INSTANCE_CRN", "")
_COS_BUCKET = os.getenv("COS_BUCKET", "")

COS_ENABLED = bool(_COS_ENDPOINT and _COS_API_KEY and _COS_INSTANCE_CRN and _COS_BUCKET)

_cos_client = None


def _get_cos():
    """Devuelve el cliente COS, creándolo la primera vez (perezoso)."""
    global _cos_client
    if _cos_client is None:
        import ibm_boto3
        from ibm_botocore.client import Config
        _cos_client = ibm_boto3.client(
            "s3",
            ibm_api_key_id=_COS_API_KEY,
            ibm_service_instance_id=_COS_INSTANCE_CRN,
            config=Config(signature_version="oauth"),
            endpoint_url=_COS_ENDPOINT,
        )
    return _cos_client


def _cos_upload(filename: str, data: bytes) -> None:
    """Sube data a COS con key pdfs/<filename>. Best-effort: no lanza."""
    if not COS_ENABLED:
        return
    try:
        _get_cos().put_object(
            Bucket=_COS_BUCKET,
            Key=f"pdfs/{filename}",
            Body=data,
            ContentType="application/pdf",
        )
    except Exception as exc:
        print(f"[COS] warning: no se pudo subir '{filename}': {exc}")


def _cos_delete(filename: str) -> None:
    """Elimina pdfs/<filename> de COS. Best-effort: no lanza."""
    if not COS_ENABLED:
        return
    try:
        _get_cos().delete_object(Bucket=_COS_BUCKET, Key=f"pdfs/{filename}")
    except Exception as exc:
        print(f"[COS] warning: no se pudo eliminar '{filename}': {exc}")


# ────────────────────────────────────────────────────────────────────────────


# Pool de conexiones (medido 2026-09-15/16): abrir una conexión nueva a Postgres
# (TCP+TLS+auth contra un Postgres remoto de IBM Cloud) cuesta ~1s, cada vez —
# antes de este fix, CADA llamada a get_db() pagaba ese costo íntegro, y un solo
# request de un usuario logueado abre 3+ conexiones (retrieval, crear/obtener
# conversación, guardar mensaje) = 3+ segundos solo en handshakes. Perezoso, con
# el mismo motivo que embeddings/chat model: crearlo al importar rompería el
# arranque si hay un parpadeo de red. minconn=1 para no pagar el costo si el
# backend arranca y nadie pregunta nada todavía; maxconn=10 es generoso para el
# tráfico de una demo (ajustar si Code Engine escala a más de una instancia
# concurrente con carga real).
_db_pool = None


def _get_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(
            1, 10,
            os.getenv("POSTGRES_URL"),
            sslrootcert=os.getenv("POSTGRES_CERT"),
        )
    return _db_pool


def get_db():
    """Toma una conexión del pool (no abre una nueva salvo que el pool esté
    vacío/recién creado). `register_vector` se re-registra en cada préstamo:
    es una consulta local rápida (no un round-trip caro como el handshake) y
    garantiza que el adaptador esté activo sin importar qué conexión física del
    pool haya tocado — necesario porque distintos préstamos pueden ser
    conexiones físicas distintas."""
    conn = _get_pool().getconn()
    register_vector(conn)
    return conn


def _release_db(conn):
    """Devuelve la conexión al pool en vez de cerrarla (ver get_db/_get_pool).

    rollback() de seguridad ANTES de devolverla: si una query lanzó una
    excepción a mitad de una transacción (sin `conn.commit()` del llamador), la
    conexión queda en estado "transacción abortada" — con conexiones que se
    cerraban siempre (antes de este fix) daba igual, se descartaban. Reusándolas
    vía pool, una conexión así envenenaría al PRÓXIMO préstamo (todas sus
    queries fallarían con "current transaction is aborted" hasta un rollback).
    rollback() sobre una conexión sin transacción pendiente (ya comiteada o de
    solo lectura) es inofensivo — no deshace nada real."""
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        _get_pool().putconn(conn)
    except Exception:
        # Si el pool ya no existe (shutdown) o la conexión quedó en mal estado,
        # cerrarla de verdad es un fallback seguro — nunca dejar la conexión
        # colgando sin liberar.
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def db_cursor():
    """Conexión (del pool) + cursor que se liberan SIEMPRE, aunque una query
    lance excepción.

    Sin esto, cualquier error a mitad de función dejaba la conexión Postgres
    sin devolver al pool (fuga de conexiones hasta agotar el pool del servidor).
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        try:
            yield conn, cur
        finally:
            cur.close()
    finally:
        _release_db(conn)


def init_db():
    with db_cursor() as (conn, cur):
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                content TEXT,
                embedding vector(768),
                source TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                question TEXT,
                answer TEXT,
                rating TEXT,
                language TEXT,
                user_sub TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migración suave por si la tabla ya existía sin la columna.
        cur.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_sub TEXT")

        # Memoria persistente por usuario (ligada al `sub` de IBMid). Los usuarios
        # anónimos NUNCA escriben aquí (ver get_or_create_conversation/save_messages).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user_sub TEXT NOT NULL,
                title TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT,
                sources JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user_sub ON conversations(user_sub)")
        # Migración suave: columna `mode` en messages (modos de chat).
        cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS mode TEXT")
        # Migración suave: metadata por mensaje ({theme, presentation_opts} en modo
        # presentación) para re-exportar el PPTX con el tema original al recargar.
        cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS meta JSONB")

        # Migración suave: hash del texto completo de un PDF ingresado, para dedupe
        # de re-subidas con nombre distinto (ver /ingest, /ingest_stream y
        # find_source_by_hash). NULL para chunks de GitHub/scraper (no aplica).
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)")

        # Migración suave: categoría/producto opcional elegida al subir un PDF vía
        # /ingest o /ingest_stream (ver VALID_TAGS y product_filter_sql). NULL para
        # chunks de GitHub/scraper (no pasan por ese flujo) y para PDFs subidos sin
        # elegir categoría — sin cambio de comportamiento para esos casos.
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tag TEXT")

        # Índice full-text (léxico) sobre el contenido, para el retrieval híbrido
        # (RRF, ver hybrid_retrieve). Config 'simple' a propósito: el corpus es
        # bilingüe es/en y 'simple' no aplica stemming de un solo idioma (evita
        # sesgar el ranking hacia uno de los dos idiomas).
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_content_fts
            ON documents USING GIN (to_tsvector('simple', content))
        """)
        conn.commit()


# Modelo de embeddings reutilizable, inicializado de forma PEREZOSA: instanciarlo
# crea un token IAM (llamada de red). Hacerlo al importar el módulo provocaba que un
# parpadeo de red impidiera arrancar el backend. Ahora se crea en el primer uso.
_embeddings = None


def get_embeddings_model():
    global _embeddings
    if _embeddings is None:
        _embeddings = Embeddings(
            model_id="ibm/granite-embedding-278m-multilingual",
            credentials=credentials,
            project_id=project_id,
        )
    return _embeddings


# Chunking seguro para el límite de 512 tokens del modelo (igual que el scraper).
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
MAX_WORD_LEN = 100


def get_embedding(text: str):
    return get_embeddings_model().embed_documents(texts=[text])[0]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """Divide en chunks por palabra, troceando 'palabras' gigantes (código/tablas)."""
    raw_words = text.split()
    words = []
    for w in raw_words:
        if len(w) > MAX_WORD_LEN:
            words.extend(w[i:i + MAX_WORD_LEN] for i in range(0, len(w), MAX_WORD_LEN))
        else:
            words.append(w)

    chunks, current, current_len = [], [], 0
    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= size:
            chunks.append(" ".join(current).strip())
            keep, klen = [], 0
            for x in reversed(current):
                if klen >= overlap:
                    break
                keep.insert(0, x)
                klen += len(x) + 1
            current, current_len = keep, klen
    if current:
        tail = " ".join(current).strip()
        if tail:
            chunks.append(tail)
    return [c for c in chunks if c]


def _extract_title(text: str, fallback: str = "") -> str:
    """Título aproximado del documento, usado SOLO para prefijar el texto que se
    embebe (nunca el `content` almacenado — ver docs/GOVERNANCE.md).

    Markdown (GitHub docs): primera línea que empieza con '#'. Texto plano (PDFs,
    pypdf no preserva Markdown): primera línea no vacía, acotada a 150 caracteres
    para no inflar el presupuesto de tokens. Si no hay texto, usa `fallback`
    (p.ej. el nombre de archivo)."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()[:150]
        return line[:150]
    return fallback


def _needs_title_prefix(chunk: str, title: str) -> bool:
    """False si el chunk ya empieza con el título (típicamente el chunk #0, que
    arranca con '# Título...') — evita duplicarlo en el texto a embeber."""
    if not title:
        return False
    head = chunk[: len(title) + 20].lower()
    return title.lower() not in head


def _prefixed(chunk: str, title: str, product_name: str = "") -> str:
    """Texto a EMBEBER: `Nombre de producto — Título` + chunk. El `content`
    guardado en BD sigue siendo `chunk` sin modificar — el prefijo solo mejora el
    embedding, no la cita.

    El nombre de producto (ver PRODUCT_DISPLAY_NAMES/_product_title_prefix) NO
    está en el cuerpo del documento (solo vive en la URL del repo o el breadcrumb
    del sitio), así que SIEMPRE se antepone, incluso al chunk #0 — es señal nueva,
    no redundante. El título (H1) sí se omite si el chunk ya empieza con él."""
    parts = []
    if product_name:
        parts.append(product_name)
    if title and _needs_title_prefix(chunk, title):
        parts.append(title)
    if not parts:
        return chunk
    return " — ".join(parts) + "\n\n" + chunk


def embed_safe(content: str, title: str = "", product_name: str = "", depth: int = 0) -> list:
    """[(content, embedding)] embebiendo `producto — título + content`, dividiendo
    el CONTENIDO (no el prefijo) si el texto combinado excede el límite de tokens
    del modelo. `content` (lo que se guarda/cita) nunca lleva el prefijo."""
    embed_text = _prefixed(content, title, product_name)
    try:
        return [(content, get_embeddings_model().embed_documents(texts=[embed_text])[0])]
    except Exception:
        if depth > 6 or len(content) < 80:
            return []
        mid = len(content) // 2
        split = content.rfind(" ", 0, mid)
        if split <= 0:
            split = mid
        return (
            embed_safe(content[:split].strip(), title, product_name, depth + 1)
            + embed_safe(content[split:].strip(), title, product_name, depth + 1)
        )


def embed_chunks(chunks: list, title: str = "", product_name: str = "", batch: int = 64) -> list:
    """[(content, embedding)] en sub-lotes, embebiendo cada chunk con `producto —
    título` antepuesto (mejora la similitud de chunks que, aislados, pierden el
    tema y/o el contexto de producto — ver docs/GOVERNANCE.md). El `content`
    devuelto/almacenado es el chunk ORIGINAL, sin prefijo. Si un lote falla por un
    chunk denso, reintenta dividiendo vía embed_safe."""
    pairs = []
    for i in range(0, len(chunks), batch):
        group = chunks[i:i + batch]
        embed_texts = [_prefixed(c, title, product_name) for c in group]
        try:
            embeddings = get_embeddings_model().embed_documents(texts=embed_texts)
            pairs.extend(zip(group, embeddings))
        except Exception:
            for c in group:
                pairs.extend(embed_safe(c, title, product_name))
    return pairs


SYSTEM_PROMPT = (
    "You are an IBM technical assistant for students and engineers. "
    "Answer the user's question using ONLY the information in the provided context. "
    "If the context does not contain the answer, say so explicitly and do not invent details. "
    "Be concise and technical. "
    "Do NOT include URLs, links, or source references in your answer — the sources are "
    "shown to the user separately. Write only clean prose. "
    "Give a single, direct answer — do not generate additional questions or extra Q&A pairs."
)

# Por debajo de este umbral de similitud coseno consideramos que NO hay contenido
# relevante. Los embeddings de granite tienen un "piso" alto (~0.6 incluso para texto
# no relacionado), por eso un score de 60% no significa una buena coincidencia.
MIN_SIMILARITY = 0.72

# Instrucción de idioma según la selección del usuario. Se redacta como instrucción
# fuerte porque el modelo tiende a copiar el idioma de la pregunta si no se insiste.
LANGUAGE_INSTRUCTION = {
    "auto": "Write your answer in the SAME language as the question.",
    "es": "IMPORTANT: Write your ENTIRE answer in Spanish, regardless of the language of the question or the context.",
    "en": "IMPORTANT: Write your ENTIRE answer in English, regardless of the language of the question or the context.",
}


# Instrucciones adicionales por modo de chat. Se añaden al system prompt DESPUÉS de la
# instrucción de idioma. El RAG (retrieve + contexto) se mantiene igual en todos los modos.
MODE_INSTRUCTIONS = {
    "standard": "",
    "email": (
        "Format your response as a professional business email ready to send. "
        "Include a subject line on the first line starting with 'Subject:' (or 'Asunto:' in Spanish), "
        "then a blank line, then the full email body with greeting, body paragraphs, and sign-off. "
        "Base the content entirely on the IBM documentation context provided. "
        "Do not include any meta-commentary about the email; output only the email itself."
    ),
    "campaign": (
        "Format your response as a marketing/awareness campaign piece with this exact structure:\n"
        "**Headline:** <a short, punchy headline>\n"
        "**Key message:** <one sentence that captures the main value>\n"
        "**Why it matters:**\n"
        "- <value bullet 1>\n"
        "- <value bullet 2>\n"
        "- <value bullet 3>\n"
        "**Call to action:** <a short, actionable CTA>\n"
        "Base the content entirely on the IBM documentation context provided. "
        "Output only the campaign piece, no other commentary."
    ),
    "presentation": (
        "You are an IBM Client Engineering expert in high-impact business storytelling. "
        "Create a presentation with exactly {slides} slides. "
        "The system already generates a cover slide and a closing slide — you output ONLY the content slides.\n\n"
        "NARRATIVE ARC:\n"
        "Slide 1 — Client context/problem: open by connecting with the audience's real pain point, "
        "NOT with the product. Use their language, their world.\n"
        "Middle slides — Solution and value: explain how IBM solves that specific problem. "
        "Lead with CLIENT BENEFITS before features ('this lets you do X' before 'it has feature Y').\n"
        "Last content slide — Business value + specific call to action.\n\n"
        "LAYOUT VARIETY — use exactly these formats to create a professional deck with visual rhythm:\n"
        "1. SECTION DIVIDER: a slide with only '# Title' and NO bullets → renders as IBM Blue full-bleed "
        "divider slide. Use once or twice to break the deck into named sections.\n"
        "2. STATS SLIDE: 2-4 bullets in the form '- **XX%** short description' or "
        "'- **N million** impact phrase' → renders as large stat cards side by side. "
        "Use when you have 2-4 memorable numbers/metrics.\n"
        "3. NUMBERED STEPS SLIDE: an ORDERED markdown list '1. **Keyword:** short description' "
        "(or '1. **Keyword** short description') with 3-5 steps → renders as a numbered process "
        "with big step numbers. Use for procedures, phases, or sequences.\n"
        "4. CARDS SLIDE: exactly 3 or 4 bullets, ALL starting with a bold keyword "
        "'- **Keyword** short description' (no colon) → renders as a card grid. Use for parallel "
        "features/pillars/benefits that have no inherent order.\n"
        "5. DEFINITIONS SLIDE: bullets in the form '- **Term:** one-line description' (colon after "
        "the bold term) → renders as a clean definition list. Use for glossary or key concept slides.\n"
        "6. IMPACT QUOTE: EXACTLY ONE slide in the deck that is ONLY a markdown blockquote: "
        "'> Powerful phrase or key metric that will stick with the audience'. "
        "No title, no bullets on that slide. Place it at a climactic moment.\n"
        "7. NORMAL: '# Title' + up to 5 short bullets, each STILL starting with a bold keyword "
        "'- **Keyword** rest of the point' → clean title + bullet list with visual hierarchy. "
        "Use only when the content doesn't fit steps/cards/defs.\n\n"
        "HARD RULES (violating these produces a low-quality, non-IBM-grade deck):\n"
        "- EVERY content bullet or step description MUST start with a bold keyword "
        "('**Word(s)**' or '**Word(s):**'). Plain prose bullets with no bold lead-in are FORBIDDEN.\n"
        "- Maximum 12 words per bullet/step description. Be terse; this is a slide, not a paragraph.\n"
        "- Slide titles: maximum 6 words, no numbering ('Step 1: ...' is forbidden — the layout "
        "already renders numbers).\n"
        "- Do NOT repeat the same layout more than 2 times in a row.\n"
        "- Maximum 5 items per bullets/steps/cards slide. ONE idea per slide.\n"
        "- NEVER show raw asterisks in text: only use '**' for the bold-keyword patterns above.\n"
        "- Separate slides with a line containing only '---'.\n"
        "- Base content on the IBM documentation context provided.\n"
        "- Output ONLY the slide markdown. No intro, no outro text outside the slides.\n\n"
        "FEW-SHOT EXAMPLE (neutral topic 'IBM Cloud VPC' — study the STRUCTURE, not the topic; "
        "reproduce this level of polish for the real topic):\n\n"
        "# Your Network, Your Rules\n"
        "- **Shared infrastructure** feels risky for regulated workloads\n"
        "- **Manual network setup** slows every new project down\n"
        "- **Limited isolation** makes compliance audits painful\n"
        "---\n"
        "# The VPC Foundation\n"
        "---\n"
        "# What a VPC Gives You\n"
        "1. **Isolate:** deploy resources in a private, logically isolated network\n"
        "2. **Segment:** organize workloads with subnets across zones\n"
        "3. **Control:** enforce traffic rules with security groups and ACLs\n"
        "4. **Connect:** reach on-prem systems over private, encrypted links\n"
        "---\n"
        "# Why Teams Choose VPC\n"
        "- **Full isolation** no noisy neighbors, ever\n"
        "- **Granular control** rules per subnet and per instance\n"
        "- **Global reach** one network across multiple regions\n"
        "---\n"
        "# Proven at Scale\n"
        "- **99.99%** regional availability SLA\n"
        "- **5 min** average time to provision a new VPC\n"
        "- **300+** compliance controls supported out of the box\n"
        "---\n"
        "> A secure network shouldn't be the reason your next project is late.\n"
        "---\n"
        "# Key Terms to Know\n"
        "- **Subnet:** a segment of a VPC's IP range in one zone\n"
        "- **Security group:** stateful firewall applied to instances\n"
        "- **ACL:** stateless firewall applied to a subnet\n"
        "---\n"
        "# Start Your Migration\n"
        "1. **Assess:** map current workloads and dependencies\n"
        "2. **Design:** define subnets, zones, and security groups\n"
        "3. **Migrate:** move workloads in low-risk waves\n"
        "4. **Validate:** confirm compliance and performance targets\n\n"
        "END OF EXAMPLE. Now generate {slides} content slides for the real topic, in the "
        "language requested, following this exact structural discipline.\n"
        "{audience_instruction}"
    ),
    "conceptmap": (
        "Output ONLY a single valid JSON object — no prose, no markdown fences, no explanation. "
        "Schema: {\"title\": string, \"nodes\": [{\"id\": string, \"label\": string}], "
        "\"edges\": [{\"from\": string, \"to\": string, \"label\": string}]}. "
        "The title is the central concept. Nodes are key related concepts (5–10 nodes). "
        "Edges connect related nodes (include the 'from' node, other nodes, and the central title as node ids). "
        "Base the content entirely on the IBM documentation context provided."
    ),
}

# Instrucciones de sistema para los Casos A/B de _detect_ambiguity_or_scope (ver
# docs/GOVERNANCE.md). Ortogonales a MODE_INSTRUCTIONS: solo se usan en mode ==
# "standard", cuando el retrieval detecta ambigüedad real entre productos
# cubiertos (Caso A) o relevancia mecánica sin relación temática plausible
# (Caso B). Reemplazan TODO el system prompt normal para ese turno (no se
# concatenan con SYSTEM_PROMPT) — ver _build_messages.
SCOPE_INSTRUCTIONS = {
    "ambiguous": (
        "The retrieved context is split almost evenly between two different IBM "
        "products this assistant covers: {products}. Neither clearly dominates, so "
        "you cannot tell which product the user means. Do NOT answer the question "
        "and do NOT invent or mix content from either product. Instead, reply with "
        "ONLY a short, concrete clarifying question (1-2 sentences) that names both "
        "candidate products and asks the user which one they meant."
    ),
    "out_of_scope": (
        "The retrieved context does NOT plausibly answer the user's question — it "
        "only matched on generic wording, not on the actual topic. Be explicitly "
        "honest: clearly state that this specific topic is not among the products "
        "this assistant covers ({covered_products}), without pretending otherwise. "
        "Then, as possible partial help, briefly mention that there is a document "
        "about \"{closest_source}\" that MIGHT be tangentially related, making clear "
        "it is not a direct answer to the question and the user should double-check "
        "it actually applies to their case. Do not present that source's content as "
        "if it answered the question."
    ),
}

# --- Preguntas de seguimiento sugeridas (Caso C, mode == "standard") ---------
# Feature aditiva: NO gasta una llamada extra al LLM. Se le pide al modelo que,
# en la MISMA generación de la respuesta, emita el marcador SUGGESTIONS_MARKER
# seguido de 2-3 preguntas de seguimiento cortas. El backend hace streaming con
# "hold-back" del marcador (ver query_stream/event_stream) para que ni el
# marcador ni las sugerencias aparezcan mezclados en el texto visible mientras
# "escribe" — solo se emiten como una línea NDJSON aparte, al final. Si el
# modelo no lo genera o el parseo falla, simplemente no hay sugerencias: nunca
# se rompe ni se corta la respuesta principal. Solo se activa en mode ==
# "standard" y Caso C (ver _detect_ambiguity_or_scope) con contexto relevante
# (ver want_suggestions en /query, /query_stream) — nunca en chitchat,
# ambigüedad (Caso A), fuera de alcance (Caso B), ni otros `mode`.
SUGGESTIONS_MARKER = "---SUGERENCIAS---"

SUGGESTIONS_INSTRUCTION = (
    "After writing your COMPLETE answer, on a new line output exactly this marker "
    f"(verbatim, nothing before or after it on that line): {SUGGESTIONS_MARKER}\n"
    "Then, after the marker, list 2 to 3 short natural follow-up questions the user "
    "might reasonably ask next about this same topic, one per line, each starting "
    "with '1.', '2.', '3.'. Write the follow-up questions in the SAME language as "
    "your answer. Keep each under 12 words, and make them genuinely useful (not "
    "generic). Output nothing else after the last question."
)


def _parse_suggestions(raw: str) -> list:
    """Extrae hasta 3 preguntas de seguimiento del texto posterior al marcador
    (ver SUGGESTIONS_MARKER). Tolerante al formato exacto del modelo (numeración
    '1.'/'1)'/'-', comillas) — si no logra extraer nada devuelve []. Nunca lanza."""
    if not raw:
        return []
    items = []
    try:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^[\-\*\d]+[\.\)]?\s*", "", line).strip().strip("\"'")
            if line:
                items.append(line)
            if len(items) == 3:
                break
    except Exception:
        return []
    return items


# Instrucciones de tono por audiencia (presentación).
_AUDIENCE_INSTRUCTIONS = {
    "executive": (
        " Focus on business value, ROI, real-world use cases, and strategic outcomes. "
        "Avoid deep technical details; use accessible, executive-level language."
    ),
    "technical": (
        " Focus on architecture, integration patterns, implementation details, "
        "technical limits, APIs, and operational considerations."
    ),
    "sales": (
        " Focus on key differentiators, competitive benefits, customer success outcomes, "
        "and how to address common objections."
    ),
}
_VALID_AUDIENCES = {"executive", "technical", "sales"}
_VALID_SLIDES = {4, 6, 8, 10}


def _resolve_presentation_opts(opts: dict) -> tuple:
    """Valida y devuelve (n_slides, audience_instruction) a partir de presentation_opts."""
    if not opts:
        return 6, " Focus on business value, ROI, real-world use cases, and strategic outcomes. Avoid deep technical details; use accessible, executive-level language."
    raw_audience = opts.get("audience", "executive")
    audience = raw_audience if raw_audience in _VALID_AUDIENCES else "executive"
    raw_slides = opts.get("slides", 6)
    try:
        n_slides = int(raw_slides)
    except (TypeError, ValueError):
        n_slides = 6
    if n_slides not in _VALID_SLIDES:
        n_slides = 6
    return n_slides, _AUDIENCE_INSTRUCTIONS[audience]

# Saludos / charla trivial: se responde sin buscar ni gastar embeddings.
# (sin acentos: la entrada se normaliza antes de comparar)
GREETING_PHRASES = {
    "hola", "holi", "ola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "que tal", "que mas", "que hubo", "como estas", "como va", "como te va",
    "como andas", "todo bien", "saludos", "hey", "hello", "hi", "hiya", "hi there",
    "good morning", "good afternoon", "good evening", "how are you", "hows it going",
    "whats up", "sup", "gracias", "muchas gracias", "thanks", "thank you", "thx",
    "ty", "ok", "okay", "vale", "test", "prueba", "probando", "buen dia",
}
# Palabras sueltas que, si aparecen en una entrada corta, indican charla trivial.
GREETING_WORDS = {
    "hola", "holi", "buenas", "saludos", "gracias", "hello", "hi", "hey", "sup",
    "thanks", "thx",
}
WELCOME = {
    "es": "¡Hola! 👋 Soy el asistente de documentación de IBM. Puedo responder sobre "
          "watsonx.ai, VPC, Kubernetes, Code Engine, Object Storage, Databases y más. "
          "¿Qué te gustaría saber?",
    "en": "Hi! 👋 I'm the IBM documentation assistant. I can answer about watsonx.ai, "
          "VPC, Kubernetes, Code Engine, Object Storage, Databases and more. "
          "What would you like to know?",
}


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos y sin puntuación, para comparar de forma robusta."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")  # quita acentos
    return re.sub(r"[^\w\s]", "", text).strip()


def chitchat_reply(question: str, language: str):
    """Si la entrada es un saludo/charla trivial, devuelve un mensaje de bienvenida."""
    q = _normalize(question)
    if not q or len(q) > 40:
        return None
    words = set(q.split())
    is_greeting = (
        q in GREETING_PHRASES
        # empieza con un saludo y casi no hay nada más (evita "hola, what is a VPC?")
        or any(q.startswith(g) and len(q) - len(g) <= 6 for g in GREETING_PHRASES)
        # mensaje muy corto que contiene una palabra de saludo
        or (len(words) <= 3 and bool(words & GREETING_WORDS))
    )
    if not is_greeting:
        return None
    if language in WELCOME:
        lang = language
    else:  # auto: heurística por idioma de la entrada
        lang = "en" if words & {"hi", "hello", "hey", "thanks", "sup", "thank"} else "es"
    return WELCOME[lang]


# Cuántos turnos previos de la conversación se consideran (control de tokens).
HISTORY_TURNS = 6


def _clean_history(history):
    """Normaliza el historial recibido del cliente a [{role, content}] reciente."""
    if not history:
        return []
    out = []
    for h in history[-HISTORY_TURNS:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def condense_question(question: str, history):
    """Reescribe una pregunta de seguimiento como pregunta autónoma usando el historial.

    DEPRECATED para /query y /query_stream (ver search_query, que hace esto MISMO más
    la reducción a intención + sinónimos técnicos, en una sola llamada). Se conserva
    por compatibilidad/tests y porque encapsula solo la resolución de pronombres.

    Sin esto, '¿cuánto cuesta?' embebe mal (no menciona el tema). Si no hay historial,
    devuelve la pregunta tal cual (sin gastar una llamada extra).
    """
    hist = _clean_history(history)
    if not hist:
        return question
    convo = "\n".join(f"{h['role']}: {h['content']}" for h in hist)
    messages = [
        {
            "role": "system",
            "content": (
                "Rewrite the user's follow-up question into a fully standalone question "
                "using the conversation history (resolve pronouns like 'it', 'that'). "
                "Output ONLY the rewritten question, in the same language as the follow-up. "
                "If it is already standalone, return it unchanged."
            ),
        },
        {"role": "user", "content": f"History:\n{convo}\n\nFollow-up: {question}\n\nStandalone question:"},
    ]
    try:
        resp = _chat_model().chat(messages=messages, params={"max_tokens": 80, "temperature": 0})
        rewritten = resp["choices"][0]["message"]["content"].strip()
        return rewritten or question
    except Exception:
        return question


# Verbos de "envoltura de tarea" (ES/EN): si la pregunta empieza pidiendo un
# ENTREGABLE ("crea una presentación sobre X", "write an email about Y") en vez de
# preguntar algo directamente, el texto completo se aleja semánticamente del tema
# real X/Y. search_query() detecta esto para decidir si vale la pena llamar al LLM.
_TASK_VERBS = (
    "crea", "creame", "crear", "haz", "hazme", "hacer", "escribe", "escribeme",
    "escribir", "genera", "generame", "generar", "dame", "redacta", "redactame",
    "redactar", "elabora", "elaborame", "prepara", "preparame", "resume", "resumeme",
    "arma", "armame", "dime", "explicame", "explica",
    "create", "make", "write", "generate", "give me", "draft", "prepare",
    "summarize", "compose", "build me",
)


def _looks_like_simple_direct_question(question: str) -> bool:
    """Heurística barata (sin LLM) para decidir si la pregunta ya es una consulta de
    búsqueda razonable tal cual: corta y sin verbos de tarea al inicio.
    """
    q = _normalize(question)
    if not q:
        return True
    # Solo se salta la reescritura para queries tipo keyword ("que es code engine"):
    # cortas EN PALABRAS y sin verbo de tarea. Una paráfrasis de 8 palabras como
    # "comando para conectarme a una maquina virtual" embebe mal y SÍ necesita
    # reescritura (caso real medido: híbrido débil con la query cruda).
    if len(q.split()) > 5:
        return False
    return not any(q.startswith(v + " ") or q == v for v in _TASK_VERBS)


def search_query(question: str, history) -> str:
    """Produce la query de BÚSQUEDA (no la de generación, que sigue usando `question`
    tal cual). En una sola llamada al LLM (cuando hace falta) hace DOS cosas a la vez:

      1. Resuelve pronombres/referencias con el historial (como condense_question).
      2. Reduce la pregunta a su tema/intención esencial, quitando envolturas de
         tarea ("crea una presentación sobre X" -> "X") e incluyendo sinónimos
         técnicos clave (VSI/instancia/VM, ssh, etc.) cuando el tema lo amerite —
         esto alimenta mucho mejor al canal léxico del retrieval híbrido.

    Ahorro de cuota: si NO hay historial Y la pregunta ya es una pregunta directa y
    simple (heurística _looks_like_simple_direct_question), se salta la llamada al
    LLM y se devuelve la pregunta tal cual — igual que condense_question antes.
    """
    hist = _clean_history(history)
    if not hist and _looks_like_simple_direct_question(question):
        return question

    convo = f"History:\n" + "\n".join(f"{h['role']}: {h['content']}" for h in hist) if hist else "History: (none)"
    messages = [
        {
            "role": "system",
            "content": (
                "You rewrite a user message into a short SEARCH QUERY for a document "
                "retrieval system (semantic + full-text search over technical IBM Cloud "
                "documentation). Do two things at once:\n"
                "1) Resolve pronouns/references using the conversation history (e.g. 'it', "
                "'that', 'the same').\n"
                "2) Strip any task wrapper ('create a presentation about X', 'write an email "
                "about X', 'summarize X', 'dame un resumen de X') down to the essential topic "
                "X. If the message is already a direct question, keep its intent.\n"
                "Include key technical synonyms when relevant so the query matches literal "
                "terms in the docs (e.g. a question about connecting to a 'virtual machine' "
                "should mention VSI/instance/VM and ssh; a question about 'almacenamiento' "
                "should mention 'object storage'/COS if that's the topic). Do not invent "
                "products or facts not implied by the message.\n"
                "Output ONLY the resulting search query (a short phrase or question, same "
                "language as the message), nothing else."
            ),
        },
        {"role": "user", "content": f"{convo}\n\nMessage: {question}\n\nSearch query:"},
    ]
    try:
        resp = _chat_model().chat(messages=messages, params={"max_tokens": 60, "temperature": 0})
        rewritten = resp["choices"][0]["message"]["content"].strip()
        return rewritten or question
    except Exception:
        return question


def _build_messages(question: str, context: str, language: str, history=None, mode: str = "standard",
                     presentation_opts: dict = None, scope=None, want_suggestions: bool = False):
    lang_instruction = LANGUAGE_INSTRUCTION.get(language, LANGUAGE_INSTRUCTION["auto"])

    # Casos A/B (ver _detect_ambiguity_or_scope, docs/GOVERNANCE.md): reemplazan TODO
    # el prompt normal para este turno. Solo puede venir no-None en mode == "standard"
    # (el caller lo garantiza) — no se combina con MODE_INSTRUCTIONS.
    if scope is not None:
        scope_mode, scope_data = scope
        if scope_mode == "ambiguous":
            product_names = [PRODUCT_DISPLAY_NAMES.get(p, p) for p in scope_data["products"]]
            instruction = SCOPE_INSTRUCTIONS["ambiguous"].format(products=" / ".join(product_names))
            # Sin contexto: el modelo debe preguntar, no responder con contenido de
            # ninguno de los dos productos en pugna todavía.
            user_content = f"QUESTION: {question}"
        else:  # "out_of_scope"
            covered = ", ".join(PRODUCT_DISPLAY_NAMES.values())
            closest_source = short_source(scope_data["source"])
            instruction = SCOPE_INSTRUCTIONS["out_of_scope"].format(
                covered_products=covered, closest_source=closest_source,
            )
            user_content = f"CONTEXT:\n{context}\n\nQUESTION: {question}" if context.strip() else f"QUESTION: {question}"
        system = f"{instruction} {lang_instruction}"
        messages = [{"role": "system", "content": system}]
        messages.extend(_clean_history(history))
        messages.append({"role": "user", "content": user_content})
        return messages

    raw_mode_instruction = MODE_INSTRUCTIONS.get(mode, "")
    # Aplica presentation_opts solo en modo presentación
    if mode == "presentation" and raw_mode_instruction:
        n_slides, audience_instruction = _resolve_presentation_opts(presentation_opts or {})
        slides_str = f"exactly {n_slides}"
        mode_instruction = raw_mode_instruction.format(
            slides=slides_str,
            audience_instruction=audience_instruction,
        )
    else:
        mode_instruction = raw_mode_instruction
    if not context.strip() and mode_instruction:
        # Modos ≠ standard sin contexto (BD vacía / tema fuera del índice): el formato
        # del modo manda igual — el usuario pidió un entregable, no una charla. Se
        # genera desde conocimiento general, con afirmaciones técnicas conservadoras.
        system = (
            "You are an IBM documentation assistant. The indexed knowledge base has no "
            "sufficiently relevant content for this request, so base the content on your "
            "general knowledge of IBM products; be conservative with specific technical "
            "claims (no invented prices, limits or version numbers) and do not cite any "
            f"source. {lang_instruction} {mode_instruction}"
        )
        user_content = f"QUESTION: {question}"
    elif not context.strip():
        # Sin contexto relevante: el modelo debe avisar que no tiene información,
        # sin inventar nada y sin citar fuentes.
        system = (
            "You are a friendly IBM documentation assistant. The indexed knowledge base "
            "has no relevant content for this message. "
            "If the message is small talk or conversational (e.g. how are you, thanks, a "
            "greeting), reply naturally, warmly and briefly, then gently invite the user "
            "to ask about IBM products. "
            "If it is a real technical question you cannot answer from the docs, say so in "
            "one short sentence and suggest topics like watsonx.ai, VPC, Kubernetes, Code "
            "Engine, Object Storage or Databases. "
            "Do not invent technical facts and do not cite any source. Keep it short. "
            + lang_instruction
        )
        user_content = f"QUESTION: {question}"
    else:
        extra = f" {mode_instruction}" if mode_instruction else ""
        # Sugerencias de seguimiento: SOLO Caso C de mode=="standard" (ver
        # SUGGESTIONS_INSTRUCTION) — el caller ya garantiza want_suggestions=True
        # únicamente en ese caso, pero el chequeo de mode aquí es defensivo (nunca
        # debe colarse en email/campaign/presentation/conceptmap).
        suggestions_extra = f" {SUGGESTIONS_INSTRUCTION}" if (want_suggestions and mode == "standard") else ""
        system = f"{SYSTEM_PROMPT} {lang_instruction}{extra}{suggestions_extra}"
        user_content = f"CONTEXT:\n{context}\n\nQUESTION: {question}"
    messages = [{"role": "system", "content": system}]
    messages.extend(_clean_history(history))  # turnos previos para dar contexto
    messages.append({"role": "user", "content": user_content})
    return messages


# Modelo de chat reutilizable, igual patrón que get_embeddings_model() (línea
# ~212): instanciar ModelInference cuesta ~1.5-2s (probado 2026-09-15/16, no es
# la llamada de red del .chat() en sí —esa es ~0.4-0.5s—, es la construcción del
# objeto). Antes de este fix, `_chat_model()` reconstruía el objeto en CADA
# llamada, y una sola pregunta lo llama al menos 2 veces (search_query() +
# generate_response/_stream()) — pagaba ese costo 2 veces por request. Perezoso
# por el mismo motivo que embeddings: un objeto global creado al importar
# rompería el arranque del backend si hay un parpadeo de red.
_chat_model_instance = None


def _chat_model():
    global _chat_model_instance
    if _chat_model_instance is None:
        _chat_model_instance = ModelInference(
            model_id="meta-llama/llama-3-3-70b-instruct",
            credentials=credentials,
            project_id=project_id,
        )
    return _chat_model_instance


def _mode_params(mode: str, presentation_opts: dict = None, want_suggestions: bool = False) -> dict:
    """Devuelve los parámetros de inferencia según el modo.

    Para presentaciones con muchos slides (8 o 10) se sube max_tokens para evitar
    truncamiento prematuro. `want_suggestions` (solo mode=="standard") también
    sube un poco el techo para dejar presupuesto al marcador + 2-3 preguntas de
    seguimiento (ver SUGGESTIONS_INSTRUCTION) sin arriesgar cortar la respuesta
    principal.
    """
    if mode == "conceptmap":
        return {"max_tokens": 600, "temperature": 0}
    if mode == "presentation":
        n_slides = 6
        if presentation_opts:
            try:
                raw = int(presentation_opts.get("slides", 6))
                if raw in _VALID_SLIDES:
                    n_slides = raw
            except (TypeError, ValueError):
                pass
        max_tokens = 1300 if n_slides >= 8 else 900
        return {"max_tokens": max_tokens, "temperature": 0.3}
    if mode == "campaign":
        return {"max_tokens": 900, "temperature": 0.3}
    base = 500
    if mode == "standard" and want_suggestions:
        base += 80
    return {"max_tokens": base, "temperature": 0.3}


def generate_response(question: str, context: str, language: str = "auto", history=None, mode: str = "standard",
                       presentation_opts: dict = None, scope=None, want_suggestions: bool = False):
    response = _chat_model().chat(
        messages=_build_messages(question, context, language, history, mode, presentation_opts, scope, want_suggestions),
        params=_mode_params(mode, presentation_opts, want_suggestions),
    )
    return response["choices"][0]["message"]["content"].strip()


def generate_response_stream(question: str, context: str, language: str = "auto", history=None, mode: str = "standard",
                              presentation_opts: dict = None, scope=None, want_suggestions: bool = False):
    """Genera la respuesta token por token (para streaming)."""
    for chunk in _chat_model().chat_stream(
        messages=_build_messages(question, context, language, history, mode, presentation_opts, scope, want_suggestions),
        params=_mode_params(mode, presentation_opts, want_suggestions),
    ):
        try:
            delta = chunk["choices"][0]["delta"].get("content", "")
        except (KeyError, IndexError, TypeError):
            delta = ""
        if delta:
            yield delta


# Tags válidos para la columna `documents.tag` (categoría elegida al subir un PDF
# vía /ingest o /ingest_stream). Deben coincidir con los IDs del array PRODUCTS en
# frontend/src/App.jsx (sin 'all'). Whitelist estricta: ver _sanitize_tag.
VALID_TAGS = {
    "watsonx", "vpc", "messages-for-rabbitmq", "containers",
    "codeengine", "cloud-object-storage", "databases-for-postgresql",
}


def _sanitize_tag(tag):
    """Whitelist estricta: cualquier valor fuera de VALID_TAGS (incluido None,
    string vacío, o basura) se guarda como NULL. La ingesta nunca falla por un
    tag inválido — simplemente el chunk queda sin categoría, como hoy."""
    return tag if tag in VALID_TAGS else None


# Productos disponibles para filtrar. La clave identifica la fuente en la columna
# `source`: 'watsonx' vive en www.ibm.com/.../watsonx/...; el resto en GitHub
# ibm-cloud-docs/<producto>/... También matchea la columna `tag` (PDFs subidos
# manualmente con esa categoría vía /ingest*, ver VALID_TAGS) — así un PDF
# etiquetado "containers" aparece al filtrar por Kubernetes, igual que los docs
# de GitHub. Los chunks con tag NULL (GitHub/scraper, o PDFs sin categoría) siguen
# matcheando exactamente igual que antes, solo por el patrón de `source`.
def product_filter_sql(products):
    """Devuelve (clausula_WHERE, params) para filtrar por producto. [] = sin filtro."""
    if not products:
        return "", []
    likes, params = [], []
    for p in products:
        likes.append("(source LIKE %s OR tag = %s)")
        params.append("%/watsonx/%" if p == "watsonx" else f"%ibm-cloud-docs/{p}/%")
        params.append(p)
    return "WHERE (" + " OR ".join(likes) + ")", params


# Nombre legible por producto — el nombre real del servicio (p.ej. "Kubernetes
# Service" para el repo `containers`) NO aparece en el markdown/H1 del documento
# ni en el nombre de archivo de un PDF subido; solo vive en la URL del repo o en
# el breadcrumb del sitio. Se antepone al título al calcular el embedding de cada
# chunk (ver _prefixed) para que la similitud capture también el "de qué producto
# es esto", no solo el tema de la página — ver docs/GOVERNANCE.md.
PRODUCT_DISPLAY_NAMES = {
    "watsonx": "watsonx.ai",
    "vpc": "Virtual Private Cloud (VPC)",
    "messages-for-rabbitmq": "Messages for RabbitMQ",
    "containers": "Kubernetes Service",
    "codeengine": "Code Engine",
    "cloud-object-storage": "Cloud Object Storage",
    "databases-for-postgresql": "Databases for PostgreSQL",
}


def _detect_product(source: str, tag: str = None) -> str:
    """ID de producto (mismo espacio que VALID_TAGS) a partir de la fuente: `tag`
    explícito (PDF subido con categoría) tiene prioridad; si no, se infiere del
    patrón de URL — mismo criterio que product_filter_sql. None si no se puede
    determinar (p.ej. PDF sin tag y sin patrón de URL reconocible)."""
    if tag in VALID_TAGS:
        return tag
    if not source:
        return None
    if "/watsonx/" in source:
        return "watsonx"
    m = re.search(r"ibm-cloud-docs/([^/]+)/", source)
    if m and m.group(1) in VALID_TAGS:
        return m.group(1)
    return None


def _product_title_prefix(source: str, tag: str = None) -> str:
    """Nombre legible del producto (ver PRODUCT_DISPLAY_NAMES) para anteponer al
    título del chunk. Cadena vacía si no se puede determinar — no inventa."""
    return PRODUCT_DISPLAY_NAMES.get(_detect_product(source, tag), "")


def retrieve(question_embedding, products=None, limit=3):
    """Recupera los top-k chunks por similitud coseno pura, opcionalmente filtrados
    por producto. Se mantiene (sin usar en /query) como referencia/fallback y para
    comparación en pruebas — el retrieval en producción es hybrid_retrieve().
    """
    clause, params = product_filter_sql(products)
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
                FROM documents {clause}
                ORDER BY embedding <=> %s::vector
                LIMIT %s""",
            (question_embedding, *params, question_embedding, limit),
        )
        return cur.fetchall()


# RRF (Reciprocal Rank Fusion): k amortigua el peso de rankings bajos. El valor
# de facto de la literatura (Cormack et al. 2009, Elasticsearch/Weaviate) es
# k=60, pensado para la suma aditiva clásica sobre ramas de cientos/miles de
# candidatos. Con la fórmula max+bonus (ver hybrid_retrieve) y ramas angostas
# (HYBRID_BRANCH_LIMIT=15), k=60 aplana tanto la diferencia entre rank 1 y
# rank 15 (1/61 vs 1/75, ~23% de rango) que el término bonus (aun con un peso
# moderado) le gana a la diferencia real entre "excelente en un canal" y
# "mediocre en ambos" — el caso que esta fórmula debía arreglar. Bajado a k=8
# (ajustado empíricamente, ver docs/GOVERNANCE.md): separa mejor rank 1 de
# rank 15 (1/9 vs 1/23, ~2.5x) sin volverse tan agresivo que un rank 15 quede
# en score ~0 (que rompería la fusión con ramas cortas). Verificado que NO
# regresiona el caso léxico-puro (ver docstring de hybrid_retrieve).
RRF_K = 8
# Peso del canal "débil" en la fusión max+bonus (ver hybrid_retrieve). Sigue
# premiando el doble-match (ambos canales) por encima de un match único, sin
# dejar que dos matches mediocres le ganen a un match excelente en un solo canal.
RRF_BONUS = 0.2
# Cuántos candidatos se piden a cada rama (semántica/léxica) antes de fusionar.
HYBRID_BRANCH_LIMIT = 15
# Un match léxico se considera "fuerte" si cae en el top-N del ranking léxico
# (ts_rank > 0, es decir, matchea al menos un término de la query). Ver
# build_query_payload/RELEVANCE para el criterio completo de "relevante".
LEXICAL_STRONG_RANK = 3

# Stopwords ES/EN a excluir del tsquery OR (ver _or_tsquery). La config 'simple'
# de Postgres NO filtra stopwords (a propósito, para no perder términos técnicos
# cortos), así que sin esta lista palabras como "para"/"the"/"a" entrarían al OR
# y producirían falsos positivos léxicos (un chunk que solo comparte "para" o
# "the" con la pregunta no es una señal real de relevancia).
_STOPWORDS_ES_EN = {
    "a", "al", "algo", "algunas", "algunos", "ante", "antes", "como", "con",
    "contra", "cual", "cuando", "de", "del", "desde", "donde", "durante", "e",
    "el", "ella", "ellas", "ellos", "en", "entre", "era", "es", "esa", "esas",
    "ese", "eso", "esos", "esta", "estas", "este", "esto", "estos", "hay", "la",
    "las", "lo", "los", "mas", "mi", "mis", "mucho", "muy", "ni", "no", "nos",
    "nosotros", "o", "otra", "otro", "para", "pero", "poco", "por", "porque",
    "que", "quien", "se", "ser", "si", "sin", "sobre", "su", "sus", "también",
    "tanto", "te", "tu", "tus", "un", "una", "uno", "unos", "y", "ya",
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to",
    "of", "in", "on", "at", "by", "with", "about", "as", "is", "are", "was",
    "were", "be", "been", "being", "this", "that", "these", "those", "it",
    "its", "i", "you", "he", "she", "we", "they", "do", "does", "did", "can",
    "could", "should", "would", "will", "shall", "have", "has", "had", "me",
    "my", "your", "his", "her", "our", "their", "how", "what", "when", "where",
    "why", "which", "who", "whom",
}


def _or_tsquery(cur, text: str):
    """Construye un tsquery ('simple') que matchea CUALQUIERA de los términos de
    `text` (OR), no todos (AND), excluyendo stopwords ES/EN.

    websearch_to_tsquery/plainto_tsquery componen los términos con AND por
    default: para una query de 4-5 palabras ("ssh virtual machine connect") eso
    exige que TODAS aparezcan en el mismo chunk de ~400 palabras, lo cual casi
    nunca pasa aunque el chunk sea muy relevante (p.ej. dice "instance"/"VSI" en
    vez de "machine"). Un chunk que matchea a ssh o a virtual ya es una señal
    léxica útil para RRF — la fusión con el canal semántico se encarga de exigir
    relevancia conjunta. Se extraen los lexemes vía to_tsvector (mismo pipeline
    que el índice), se filtran stopwords (ver _STOPWORDS_ES_EN — la config
    'simple' no las filtra sola) y se unen con '|' en to_tsquery.
    """
    cur.execute("SELECT to_tsvector('simple', %s)", (text,))
    tsvector_str = cur.fetchone()[0]
    # tsvector_str: "'lexema1':1 'lexema2':2 ..." -> extraer lexemes únicos.
    lexemes = re.findall(r"'((?:[^'\\]|\\.)*)':\d", tsvector_str)
    if not lexemes:
        return None
    seen = []
    for lx in lexemes:
        if lx not in seen and lx.lower() not in _STOPWORDS_ES_EN:
            seen.append(lx)
    if not seen:
        return None
    return " | ".join(f"'{lx}'" for lx in seen)


def hybrid_retrieve(question: str, question_embedding, products=None, limit=5):
    """Retrieval híbrido: fusiona ranking semántico (coseno) y léxico (full-text)
    con Reciprocal Rank Fusion (RRF), luego deduplica por contenido idéntico.

    Por qué: la búsqueda puramente semántica falla cuando la pregunta parafraseada
    ("comando para conectarme a una máquina virtual") embebe lejos de un chunk
    literal ("ssh -i key.pem user@ip ..."). El canal léxico encuentra ese chunk
    por coincidencia de términos (con OR entre ellos, ver _or_tsquery) aunque el
    coseno sea bajo.

    Pasos:
      (a) top-HYBRID_BRANCH_LIMIT por coseno (como antes, más ancho).
      (b) top-HYBRID_BRANCH_LIMIT por ts_rank con un tsquery OR de los términos
          de la query (ver _or_tsquery), respetando el mismo filtro de producto.
      (c) fusión max+bonus (ver nota "Fórmula de fusión" abajo — reemplaza la suma
          RRF pura): score(doc) = max(sem, lex) + RRF_BONUS * min(sem, lex), donde
          sem = 1/(RRF_K+rank_semántico) si el doc apareció en ese canal, si no 0
          (mismo criterio para lex). Si un doc solo aparece en un canal, el otro
          término es 0 y el score queda en max(sem, lex) sin bonus.
      (d) dedupe por contenido idéntico (mismo texto de fuentes distintas — el caso
          del PDF subido dos veces — se queda con una sola entrada, la de mayor
          score fusionado).
      (e) devuelve el top-`limit` fusionado, cada fila con su similitud coseno
          (la real si vino del canal semántico; calculada on-the-fly si vino SOLO
          del léxico) y su rank/ts_rank léxico, para que build_query_payload pueda
          aplicar el criterio de relevancia documentado.

    Fórmula de fusión (max + bonus, NO suma RRF pura — cambio 2026-09,
    ver docs/GOVERNANCE.md): la suma aditiva clásica de RRF (score = sem + lex)
    favorece sistemáticamente a un chunk "decente en ambos canales" por encima de
    uno "excelente en un solo canal", porque dos términos medianos (p.ej.
    1/65 + 1/65 ≈ 0.031) superan fácilmente a un término alto en solitario
    (p.ej. 1/63 ≈ 0.0159 si el chunk NO aparece en el otro canal). Eso rompía el
    caso real de una pregunta de Kubernetes parafraseada: el chunk correcto
    rankeaba #3 puro-coseno (score semántico alto) pero su texto no contenía
    literalmente ningún lexema de la query (sin stemming, config 'simple'), así
    que perdía contra chunks con match léxico + semántico mediocres en ambos. A
    la vez, el canal léxico existe PRECISAMENTE para el caso opuesto (una
    pregunta parafraseada que embebe lejos de un chunk literal — p.ej. "comando
    para conectarme a una máquina virtual" vs. un chunk con "ssh -i key.pem...")
    y ESE caso necesita que un match léxico fuerte solo (sin apoyo semántico)
    también rankee alto. `max(sem, lex) + RRF_BONUS * min(sem, lex)` resuelve
    ambos: el máximo de los dos canales domina el score (premia la excelencia en
    cualquiera de los dos), y el término bonus (min * 0.4) sigue premiando —pero
    ya no permite ganar por sí solo— el caso de doble match, que sigue siendo la
    señal más fuerte cuando ambos canales concuerdan.

    Devuelve: lista de tuplas (content, source, similarity, lexical_rank) ordenada
    por score de fusión descendente. lexical_rank es None si el doc no apareció en
    el canal léxico (o no matcheó ningún término de la query).
    """
    clause, params = product_filter_sql(products)
    lexical_join = "AND" if clause else "WHERE"

    with db_cursor() as (conn, cur):
        # (a) Canal semántico: top-N por coseno.
        cur.execute(
            f"""SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
                FROM documents {clause}
                ORDER BY embedding <=> %s::vector
                LIMIT %s""",
            (question_embedding, *params, question_embedding, HYBRID_BRANCH_LIMIT),
        )
        semantic_rows = cur.fetchall()

        # (b) Canal léxico: top-N por ts_rank con tsquery OR (ver _or_tsquery).
        # Config 'simple': corpus bilingüe, sin stemming de un solo idioma.
        or_query = _or_tsquery(cur, question)
        if or_query is None:
            lexical_rows = []
        else:
            cur.execute(
                f"""SELECT content, source,
                           ts_rank(to_tsvector('simple', content), to_tsquery('simple', %s)) AS rank
                    FROM documents {clause} {lexical_join}
                         to_tsvector('simple', content) @@ to_tsquery('simple', %s)
                    ORDER BY rank DESC
                    LIMIT %s""",
                (or_query, *params, or_query, HYBRID_BRANCH_LIMIT),
            )
            lexical_rows = cur.fetchall()

    # (c) Fusión max+bonus (ver docstring arriba): acumular puntaje POR CANAL
    # (sem_score/lex_score, no una sola suma) por contenido (clave de dedupe =
    # texto exacto, ver (d)); el score final se calcula después de recorrer ambas
    # ramas. Guardamos también metadata (source, similarity, lexical_rank) de la
    # MEJOR aparición de cada contenido, priorizando: similarity conocida > mayor rank.
    fused = {}  # content -> {"sem_score", "lex_score", "source", "similarity", "lexical_rank"}

    for rank, row in enumerate(semantic_rows, start=1):
        content, source, similarity = row
        entry = fused.setdefault(
            content,
            {"sem_score": 0.0, "lex_score": 0.0, "source": source, "similarity": None, "lexical_rank": None},
        )
        # max(), no =: el mismo `content` puede aparecer 2 veces bajo `source`
        # distintos (PDF re-indexado con otro nombre, 42 casos en la BD hoy —
        # QA 2026-09-15) y este dict dedupea por content; con `=` gana la ÚLTIMA
        # aparición (el peor rank), penalizando contenido duplicado sin motivo.
        entry["sem_score"] = max(entry["sem_score"], 1.0 / (RRF_K + rank))
        # Si ya había una entrada (llegó primero por léxico) sin similarity, o esta
        # tiene mejor rank semántico, actualizamos similarity/source.
        if entry["similarity"] is None or similarity > entry["similarity"]:
            entry["similarity"] = float(similarity)
            entry["source"] = source

    for rank, row in enumerate(lexical_rows, start=1):
        content, source, ts_rank_val = row
        entry = fused.setdefault(
            content,
            {"sem_score": 0.0, "lex_score": 0.0, "source": source, "similarity": None, "lexical_rank": None},
        )
        entry["lex_score"] = max(entry["lex_score"], 1.0 / (RRF_K + rank))
        if entry["lexical_rank"] is None or rank < entry["lexical_rank"]:
            entry["lexical_rank"] = rank
        if entry["source"] is None:
            entry["source"] = source

    # Score final: max(canal fuerte) + bonus * min(canal débil). Si un doc solo
    # apareció en un canal, el otro queda en 0.0 y el score es solo el máximo (sin
    # penalidad ni bonus) — ver docstring "Fórmula de fusión" arriba.
    for entry in fused.values():
        entry["score"] = max(entry["sem_score"], entry["lex_score"]) + RRF_BONUS * min(
            entry["sem_score"], entry["lex_score"]
        )

    # Para las entradas que llegaron SOLO por el canal léxico (similarity=None),
    # calculamos su similitud coseno real contra la pregunta embebida, para no
    # romper el contrato de respuesta (sources[].similarity siempre es un float).
    missing_sim = [c for c, e in fused.items() if e["similarity"] is None]
    if missing_sim:
        with db_cursor() as (conn, cur):
            cur.execute(
                """SELECT content, 1 - (embedding <=> %s::vector) AS similarity
                   FROM documents WHERE content = ANY(%s)""",
                (question_embedding, missing_sim),
            )
            for content, similarity in cur.fetchall():
                if content in fused:
                    fused[content]["similarity"] = float(similarity)

    # (d) Ya deduplicado por construcción (dict keyed por content). Ordenar por
    # score RRF descendente y devolver el top-`limit`.
    ranked = sorted(fused.items(), key=lambda kv: kv[1]["score"], reverse=True)[:limit]
    return [
        (content, e["source"], e["similarity"] if e["similarity"] is not None else 0.0, e["lexical_rank"])
        for content, e in ranked
    ]


def short_source(url: str) -> str:
    """Etiqueta corta de la fuente para el contexto (sin URL completa)."""
    m = re.search(r"ibm-cloud-docs/([^/]+)/blob/[^/]+/(.+)\.md$", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if "topic=" in url:
        return "watsonx/" + url.split("topic=")[-1]
    return url


def _is_relevant_row(row) -> bool:
    """Criterio de "relevante" (contrato — ver docs/GOVERNANCE.md):

    Un chunk es relevante si CUALQUIERA de estas dos condiciones se cumple:
      (1) similitud coseno >= MIN_SIMILARITY (criterio de siempre), O
      (2) match léxico fuerte: el chunk cae en el top-LEXICAL_STRONG_RANK del
          ranking léxico (ts_rank > 0, es decir matcheó al menos un término real
          de la query vía websearch_to_tsquery) — típicamente un término técnico
          exacto (ssh, comando, nombre de producto) que el embedding parafraseado
          no acerca lo suficiente en el espacio semántico.

    row = (content, source, similarity, lexical_rank). lexical_rank es None si el
    chunk no vino del canal léxico (rows de retrieve() clásico, sin 4º elemento,
    también se soportan: se asume lexical_rank=None vía _row_lexical_rank).
    """
    similarity = row[2]
    lexical_rank = row[3] if len(row) > 3 else None
    if similarity >= MIN_SIMILARITY:
        return True
    return lexical_rank is not None and lexical_rank <= LEXICAL_STRONG_RANK


def build_query_payload(results, lenient=False):
    """Construye sources + flags de relevancia a partir de las filas recuperadas.

    `results` acepta filas de 3 elementos (content, source, similarity) — retrieval
    clásico — o de 4 (content, source, similarity, lexical_rank) — hybrid_retrieve().
    El contrato de respuesta (sources[] con similarity/relevant, forma) NO cambia.

    Criterio de relevancia: ver _is_relevant_row (similitud >= MIN_SIMILARITY O
    match léxico fuerte, top-LEXICAL_STRONG_RANK con ts_rank > 0).

    lenient=True (modos ≠ standard): usa TODO el top-k como contexto aunque no supere
    el umbral. La envoltura de tarea ("crea una presentación sobre X") baja la similitud
    absoluta ~0.1 aunque los chunks recuperados sí sean del tema; el umbral estricto
    dejaría el contexto vacío y el output saldría sin grounding.
    """
    relevant = [r for r in results if _is_relevant_row(r)]
    context_rows = results if (lenient and results) else relevant
    context = "\n\n".join([f"[{short_source(r[1])}]: {r[0]}" for r in context_rows])
    return {
        "context": context,
        "relevant": bool(context_rows),
        # max() real sobre TODAS las filas, no la similitud del ganador por fusión:
        # con RRF_K=8 el rank-1 fusionado puede ser un chunk léxico-puro cuya
        # similitud coseno es MENOR a la de otra fila del propio top-k — mostrar
        # results[0][2] subestimaba max_similarity en la UI (QA 2026-09-15).
        "max_similarity": max((float(r[2]) for r in results), default=0.0),
        "sources": [
            {
                "content": r[0],
                "source": r[1],
                "similarity": float(r[2]),
                "relevant": _is_relevant_row(r),
            }
            for r in results
        ],
    }


# --- Ambigüedad / fuera-de-alcance real (Casos A/B, ver docs/GOVERNANCE.md) ---
# Aplica SOLO en mode == "standard" (/query, /query_stream la invocan condicionadas
# a eso). Es una capa ORTOGONAL a `mode` — no agrega un nuevo modo de chat, solo
# cambia el system prompt de generación para ese turno (ver SCOPE_INSTRUCTIONS).

# Caso B (fuera de alcance real): banda "débil" de similitud del top-1 relevante
# —cerca de MIN_SIMILARITY pero no muy por encima— en la que un match mecánico
# (léxico genérico o semántico al límite) es más probable que sea temáticamente
# espurio que un match sólido.
OUT_OF_SCOPE_SIMILARITY_LOW = MIN_SIMILARITY  # 0.72
OUT_OF_SCOPE_SIMILARITY_HIGH = 0.78

# Caso A (ambigüedad): diferencia de similitud entre el top-1 de un producto y el
# top-1 de otro producto distinto, ambos dentro del top-3 fusionado, por debajo de
# la cual se considera que "compiten" de verdad (ninguno domina claramente).
AMBIGUITY_SIMILARITY_GAP = 0.03
# Piso de similitud del producto mejor rankeado para que la ambigüedad cuente como
# "real" (Caso A) en vez de ruido mecánico (Caso B). A PROPÓSITO igual a
# OUT_OF_SCOPE_SIMILARITY_HIGH: los dos casos quedan mutuamente excluyentes por
# construcción (rangos [0.72, 0.78] = Caso B "banda débil" vs > 0.78 = Caso A
# "confianza real") — evita el falso positivo detectado en pruebas: la pregunta
# de MongoDB (fuera de alcance real) generaba dos matches genéricos por la
# palabra "backup" (RabbitMQ ~0.736, Cloud Object Storage ~0.725, gap < 0.03)
# que con un piso más bajo (0.65) se leían como "ambigüedad real" entre esos dos
# productos — incorrecto: ninguno de los dos tiene relación real con la
# pregunta, es el caso B, no el A. Con el piso en 0.78 ese caso ahora cae
# correctamente en Caso B (ver _detect_ambiguity_or_scope).
AMBIGUITY_MIN_SIMILARITY = OUT_OF_SCOPE_SIMILARITY_HIGH

# Sinónimos/variantes específicas por producto para detectar si la pregunta del
# usuario nombra a alguno de los 7 productos cubiertos (Caso B). A propósito NO
# incluye términos genéricos (p.ej. "base de datos"/"database" para Postgres,
# "servidor"/"nube" para cualquiera): un término genérico haría que casi
# cualquier pregunta "mencione" un producto y anularía la heurística — caso real
# que motivó esto: "¿cómo hago backup de una base de datos MongoDB en IBM
# Cloud?" NO debe contar como mención de Postgres solo por decir "base de datos".
_PRODUCT_SYNONYMS = {
    "watsonx": ["watsonx", "watson x"],
    "vpc": ["vpc", "virtual private cloud", "red privada virtual"],
    "messages-for-rabbitmq": ["rabbitmq", "rabbit mq"],
    "containers": ["kubernetes", "k8s", "iks"],
    "codeengine": ["code engine", "codeengine"],
    "cloud-object-storage": ["cloud object storage", "object storage", "almacenamiento de objetos"],
    "databases-for-postgresql": ["postgresql", "postgres"],
}


def _question_mentions_known_product(question: str) -> bool:
    """True si la pregunta nombra explícitamente a alguno de los 7 productos
    cubiertos (nombre visible en PRODUCT_DISPLAY_NAMES o una variante conocida
    en _PRODUCT_SYNONYMS). Comparación insensible a acentos/mayúsculas (_normalize).

    Se usa para el Caso B (fuera de alcance real): sin esto, cualquier pregunta
    con vocabulario técnico genérico podría marcarse como fuera de alcance de más
    — falso positivo, el peor error posible en esta heurística (ver
    docs/GOVERNANCE.md, más conservador es mejor).
    """
    q = _normalize(question)
    if not q:
        return False
    for names in _PRODUCT_SYNONYMS.values():
        for name in names:
            if _normalize(name) in q:
                return True
    for display in PRODUCT_DISPLAY_NAMES.values():
        if _normalize(display) in q:
            return True
    return False


def _detect_ambiguity_or_scope(rows, question: str):
    """Detecta, sobre el top de `rows` (salida de hybrid_retrieve, ya ordenada por
    score de fusión descendente), si aplica el Caso A (ambigüedad real entre 2+
    productos cubiertos) o el Caso B (relevante=True mecánico pero sin relación
    temática plausible con la pregunta). Devuelve `None` si no aplica ninguno
    (Caso C: comportamiento normal, sin cambios).

    Devuelve una tupla `(modo, data)`:
      - `("ambiguous", {"products": [id1, id2]})`
      - `("out_of_scope", {"source": source_del_top1_relevante, "similarity": float})`

    SOLO debe llamarse en mode == "standard" (ver /query, /query_stream) — los
    demás modos generan un entregable a partir de la tarea pedida, no una
    respuesta conversacional, y no deben pedir aclaración ni activar el aviso de
    fuera-de-alcance.

    Heurística deliberadamente conservadora (ver docs/GOVERNANCE.md): un falso
    positivo (marcar Caso A/B en una pregunta que en realidad es normal) es peor
    que dejar pasar algún caso límite al comportamiento normal (Caso C) — por eso
    ambos casos exigen primero que algo haya cruzado el umbral de relevancia
    mecánicamente (ver _is_relevant_row), igual que hoy.
    """
    if not rows:
        return None

    relevant_rows = [r for r in rows if _is_relevant_row(r)]
    if not relevant_rows:
        # Nada cruzó el umbral: es el flujo normal de "sin información" que ya
        # maneja _build_messages (contexto vacío) — no es un Caso A/B.
        return None

    # --- Caso A: ambigüedad real entre 2+ productos cubiertos ---
    # Primer top-1 por producto distinto entre los primeros 3 resultados
    # fusionados (ya vienen ordenados por score de fusión, no por similitud
    # pura — aproximación razonable de "qué tan arriba salió cada producto").
    seen = []
    seen_ids = set()
    for row in rows[:3]:
        source, similarity = row[1], row[2]
        product = _detect_product(source)
        if product and product not in seen_ids:
            seen_ids.add(product)
            seen.append((product, similarity))
    if len(seen) >= 2:
        best_sim, second_sim = seen[0][1], seen[1][1]
        # abs(): con RRF_K=8 un chunk léxico-puro puede subir al rank 1 con
        # similitud MENOR a la del rank 2 (fusión por score, no por similitud) —
        # sin abs(), ese caso da un gap negativo que "cuela" como < GAP y dispara
        # ambigüedad aunque el segundo producto domine claramente (QA 2026-09-15).
        gap_real = abs(best_sim - second_sim)
        # Si la pregunta ya nombra explícitamente a UNO de los dos productos en
        # pugna, no hay ambigüedad real — el usuario ya dijo cuál quiere (QA
        # 2026-09-15: "¿cómo escalo un cluster de Kubernetes?" no debería
        # preguntar "¿Kubernetes o VPC?").
        q_norm = _normalize(question)
        named = {
            pid for pid in (seen[0][0], seen[1][0])
            if any(_normalize(n) in q_norm for n in _PRODUCT_SYNONYMS.get(pid, []) + [PRODUCT_DISPLAY_NAMES.get(pid, "")])
        }
        if best_sim >= AMBIGUITY_MIN_SIMILARITY and gap_real < AMBIGUITY_SIMILARITY_GAP and not named:
            return ("ambiguous", {"products": [seen[0][0], seen[1][0]]})

    # --- Caso B: fuera de alcance real — DESACTIVADO (QA 2026-09-15) ---
    # Sondeo empírico contra la BD real (12 preguntas): ~27% de falsos positivos
    # en preguntas perfectamente en alcance y cortas ("como despliego una app",
    # "cuanto cuesta el servicio") — la banda [0.72, 0.78] no distingue un match
    # débil real de un buen match en español; el único filtro efectivo era que
    # el usuario nombrara el producto literalmente. Auto-contradictorio en la UI
    # (dice "no cubro esto" y muestra fuentes del producto correcto debajo) —
    # riesgo inaceptable para la demo al CTO. Se deja el código y las constantes
    # (OUT_OF_SCOPE_SIMILARITY_LOW/HIGH más arriba) para retomarlo después del
    # 17 con una heurística mejor calibrada (ver docs/GOVERNANCE.md), en vez de
    # borrarlo. El flujo "sin información" ya validado (contexto vacío en
    # _build_messages) sigue cubriendo el caso real fuera de alcance (MongoDB).
    return None


def get_or_create_conversation(user_sub: str, conversation_id, question: str) -> tuple:
    """Reusa `conversation_id` si es del usuario; si no, crea una conversación nueva.

    El título es la pregunta truncada (~60 chars), solo para mostrar en la lista.
    Solo se llama para usuarios autenticados (ver `authenticated` en /query*).
    Devuelve (id, created): `created` indica si la conversación se creó en ESTE
    request, para poder limpiarla si la generación falla antes de guardar mensajes.
    """
    with db_cursor() as (conn, cur):
        if conversation_id:
            cur.execute(
                "SELECT id FROM conversations WHERE id = %s AND user_sub = %s",
                (conversation_id, user_sub),
            )
            row = cur.fetchone()
            if row:
                return row[0], False
        title = (question or "").strip()[:60] or "Nueva conversación"
        cur.execute(
            "INSERT INTO conversations (user_sub, title) VALUES (%s, %s) RETURNING id",
            (user_sub, title),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id, True


def delete_conversation_if_empty(conversation_id: int):
    """Borra una conversación que quedó sin mensajes (falló el LLM antes de guardar).

    La conversación se crea ANTES de generar para poder emitir conversation_id
    temprano en el stream NDJSON; si la generación lanza, save_messages nunca
    corre y quedaría una fila huérfana visible en "Mis conversaciones".
    Se eligió esta limpieza (y no guardar el turno del usuario antes de generar)
    porque no cambia el contrato de save_messages (turno completo user+assistant)
    ni deja preguntas sin respuesta en el historial. El NOT EXISTS protege contra
    borrar una conversación que sí llegó a tener mensajes.
    """
    with db_cursor() as (conn, cur):
        cur.execute(
            "DELETE FROM conversations WHERE id = %s "
            "AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id = %s)",
            (conversation_id, conversation_id),
        )
        conn.commit()


def save_messages(conversation_id: int, question: str, answer: str, sources: list, mode: str = "standard", meta: dict = None):
    """Persiste el turno (user + assistant) y toca `updated_at` de la conversación."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
            (conversation_id, "user", question),
        )
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, sources, mode, meta) VALUES (%s, %s, %s, %s, %s, %s)",
            (conversation_id, "assistant", answer, Json(sources or []), mode, Json(meta) if meta else None),
        )
        cur.execute("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (conversation_id,))
        conn.commit()


# ── Helpers para la exportación a PPTX ──────────────────────────────────────

def _slugify(text: str) -> str:
    """Convierte un título en un slug seguro para headers HTTP."""
    text = unicodedata.normalize("NFD", (text or "untitled").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-{2,}", "-", text)
    return text[:80] or "presentation"


def _parse_bullet_parts(raw: str) -> list:
    """Parsea un texto de bullet a partes [(text, bold)] para render tipográfico.

    Ejemplos:
      '**80%** menos tiempo'  → [('80%', True), (' menos tiempo', False)]
      '**Término:** desc'     → [('Término:', True), (' desc', False)]
      'Texto normal'          → [('Texto normal', False)]
    """
    parts = []
    rest = raw
    pattern = re.compile(r"\*\*(.+?)\*\*")
    last = 0
    for m in pattern.finditer(rest):
        before = rest[last:m.start()]
        if before:
            parts.append((before, False))
        parts.append((m.group(1), True))
        last = m.end()
    tail = rest[last:]
    if tail:
        parts.append((tail, False))
    return parts if parts else [(raw, False)]


def _parse_slides(markdown: str) -> list:
    """Devuelve lista de dicts a partir del markdown de slides.

    Layout types detectados (campo 'layout'), en este orden de precedencia:
      'divider'   — Solo # Título, sin bullets ni párrafos. Divisor de sección IBM Blue.
      'stats'     — Bullets con patron **cifra** descripción (2-4 bullets). Tarjetas de estadística.
      'impact'    — Solo un blockquote (> ...). Slide de frase de alto impacto.
      'steps'     — Lista ORDENADA (1. 2. ...) de 3-5 items. Pasos numerados estilo Carbon.
      'defs'      — Bullets con patron **Término:** descripción (todos con dos puntos). Lista de definiciones.
      'cards'     — 3-4 bullets, todos **Keyword** descripción (sin dos puntos). Grid de tarjetas.
      'normal'    — Contenido estándar: título + bullets (enriquecido con marcador visual).

    Cada slide es un dict con:
      layout      str   — uno de los valores arriba
      title       str   — H1 de la slide
      bullets     list  — [(raw_text, nivel)] — nivel 1 o 2 (bullets '-'/'*')
      steps       list  — [(raw_text)] en orden (solo layout='steps')
      paragraphs  list  — párrafos no-bullet
      impact_text str   — texto del blockquote (solo layout='impact')
    """
    _STAT_RE   = re.compile(r"^\*\*([^*]+)\*\*\s+(.+)$")
    # Defs: **Término:** desc — el colon debe estar DENTRO o INMEDIATAMENTE tras el bold.
    _DEF_RE    = re.compile(r"^\*\*([^*]+?):?\*\*:\s*(.+)$|^\*\*([^*]+):\*\*\s*(.+)$")
    # Cards/normal keyword lead-in: **Keyword** desc (SIN colon).
    _KW_RE     = re.compile(r"^\*\*([^*]+)\*\*\s+(.+)$")

    raw_slides = re.split(r"(?m)^---\s*$", markdown)
    slides = []
    for n, block in enumerate(raw_slides, 1):
        block = block.strip()
        if not block:
            continue

        # ── Layout: impacto — SOLO una línea blockquote ──────────────────
        non_empty = [l for l in block.splitlines() if l.strip()]
        if len(non_empty) == 1 and non_empty[0].strip().startswith(">"):
            slides.append({
                "layout": "impact",
                "title": "",
                "bullets": [],
                "steps": [],
                "paragraphs": [],
                "impact_text": non_empty[0].strip().lstrip(">").strip(),
            })
            continue

        # Extraer H1 y líneas de contenido
        lines = block.splitlines()
        title = f"Slide {n}"
        content_lines = []
        for i, line in enumerate(lines):
            mh = re.match(r"^#\s+(.+)", line)
            if mh and i == 0:
                title = mh.group(1).strip()
            else:
                content_lines.append(line)

        bullets = []
        ordered = []
        paragraphs = []
        for line in content_lines:
            stripped = line.strip()
            if not stripped:
                continue
            sub = re.match(r"^(?:\s{2,}|\t)[-*]\s+(.+)", line)
            if sub:
                bullets.append((sub.group(1).strip(), 2))
                continue
            mo = re.match(r"^\d+[.)]\s+(.+)", stripped)
            if mo:
                ordered.append(mo.group(1).strip())
                continue
            mb = re.match(r"^[-*]\s+(.+)", stripped)
            if mb:
                bullets.append((mb.group(1).strip(), 1))
            else:
                paragraphs.append(stripped)

        # ── Layout: divisor — solo título, sin nada más ──────────────────
        if not bullets and not paragraphs and not ordered:
            slides.append({
                "layout": "divider",
                "title": title,
                "bullets": [],
                "steps": [],
                "paragraphs": [],
                "impact_text": "",
            })
            continue

        # ── Layout: stats — 2-4 bullets con **cifra** descripción ────────
        lvl1 = [(t, lv) for t, lv in bullets if lv == 1]
        stat_matches = [_STAT_RE.match(t) for t, _ in lvl1]
        # Distinguir stats de defs/cards: stats tienen cifra corta (≤14 chars, con dígito)
        def _is_stat(m):
            if not m:
                return False
            token = m.group(1).strip()
            return bool(re.search(r"\d", token)) and len(token) <= 14

        if (lvl1 and 2 <= len(lvl1) <= 4
                and all(stat_matches)
                and all(_is_stat(sm) for sm in stat_matches)):
            stats = [{"figure": sm.group(1).strip(), "desc": sm.group(2).strip()}
                     for sm in stat_matches]
            slides.append({
                "layout": "stats",
                "title": title,
                "bullets": bullets,
                "steps": [],
                "paragraphs": paragraphs,
                "impact_text": "",
                "stats": stats,
            })
            continue

        # ── Layout: steps — lista ordenada (1. 2. ...) con 2-6 items ──────
        if 2 <= len(ordered) <= 6 and not bullets:
            steps = []
            for raw in ordered:
                m = _KW_RE.match(raw)
                if m:
                    steps.append({"keyword": m.group(1).strip().rstrip(":"), "desc": m.group(2).strip()})
                else:
                    steps.append({"keyword": "", "desc": raw})
            slides.append({
                "layout": "steps",
                "title": title,
                "bullets": [],
                "steps": steps,
                "paragraphs": paragraphs,
                "impact_text": "",
            })
            continue

        # ── Layout: defs — bullets con **Término:** descripción (con colon) ─
        def _def_match(t):
            m = _DEF_RE.match(t)
            if not m:
                return None
            term = m.group(1) if m.group(1) is not None else m.group(3)
            desc = m.group(2) if m.group(2) is not None else m.group(4)
            return term.strip(), desc.strip()

        def_matches = [_def_match(t) for t, lv in lvl1] if lvl1 else []
        if len(lvl1) >= 2 and len(def_matches) == len(lvl1) and all(def_matches):
            defs = [{"term": term, "desc": desc} for term, desc in def_matches]
            slides.append({
                "layout": "defs",
                "title": title,
                "bullets": bullets,
                "steps": [],
                "paragraphs": paragraphs,
                "impact_text": "",
                "defs": defs,
            })
            continue

        # ── Layout: cards — 3-4 bullets, todos **Keyword** desc (sin colon) ─
        kw_matches = [_KW_RE.match(t) for t, _ in lvl1] if lvl1 else []
        def _is_card_kw(m):
            if not m:
                return False
            token = m.group(1).strip()
            return not token.endswith(":") and len(token) <= 28

        if (3 <= len(lvl1) <= 4
                and all(kw_matches)
                and all(_is_card_kw(km) for km in kw_matches)):
            cards = [{"keyword": km.group(1).strip(), "desc": km.group(2).strip()}
                     for km in kw_matches]
            slides.append({
                "layout": "cards",
                "title": title,
                "bullets": bullets,
                "steps": [],
                "paragraphs": paragraphs,
                "impact_text": "",
                "cards": cards,
            })
            continue

        # ── Layout: normal ───────────────────────────────────────────────
        slides.append({
            "layout": "normal",
            "title": title,
            "bullets": bullets,
            "steps": [],
            "paragraphs": paragraphs,
            "impact_text": "",
        })
    return slides


# ── Paleta IBM / Carbon ──────────────────────────────────────────────────────
# Portada: siempre IBM Blue. Slides contenido: dark (default) o light.

_IBM_BLUE    = "0F62FE"   # IBM Blue 60 — portada + acentos
_IBM_DARK_BG = "161616"   # Carbon g100 — fondo dark
_IBM_LIGHT_BG = "FFFFFF"  # blanco — fondo light
_IBM_WHITE   = "FFFFFF"   # texto sobre azul/dark
_IBM_NEAR_WHITE = "F4F4F4"  # bullets dark
_IBM_DARK_TITLE  = "161616"  # título en light
_IBM_DARK_BODY   = "393939"  # bullets en light
_IBM_ACCENT_DARK  = "4589FF"  # acento sobre dark (mayor contraste que 0F62FE)
_IBM_SUBTEXT_DARK = "A8C8FF"  # subtexto/bullets secundarios dark
_IBM_GRAY_SUB  = "6F6F6F"  # subtítulo gris en light

# ── Mapa determinista keyword → icono SVG ────────────────────────────────────
# Rutas relativas a backend/; solo archivos que existen verificados.
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_ICON_CACHE  = os.path.join(_ASSETS_DIR, "icon-cache")
os.makedirs(_ICON_CACHE, exist_ok=True)

_ICON_MAP = {
    # AI / watsonx
    "watsonx":     "architecture-icons/svg/AI/watsonx-ai.svg",
    "ai":          "architecture-icons/svg/AI/AI.svg",
    "machine learning": "architecture-icons/svg/AI/machine-learning.svg",
    "governance":  "architecture-icons/svg/AI/ai-governance--lifecycle.svg",
    # Compute / containers
    "kubernetes":  "architecture-icons/svg/Compute/Kubernetes.svg",
    "code engine": "architecture-icons/svg/DevOps/Serverless Application.svg",
    "serverless":  "architecture-icons/svg/DevOps/Serverless Application.svg",
    "containers":  "architecture-icons/svg/Compute/Kubernetes Cluster.svg",
    "openshift":   "architecture-icons/svg/Compute/Open Shift.svg",
    # Networking / VPC
    "vpc":         "architecture-icons/svg/Networking/VPC.svg",
    "red":         "architecture-icons/svg/Networking/VPC.svg",
    "network":     "architecture-icons/svg/Networking/hybrid-networking.svg",
    "load balancer": "architecture-icons/svg/Networking/load-balancer--application.svg",
    # Data & Storage
    "object storage": "architecture-icons/svg/Data & Storage/Object Storage Application.svg",
    "cos":         "architecture-icons/svg/Data & Storage/Object Bucket.svg",
    "database":    "architecture-icons/svg/Data & Storage/database--postgreSQL.svg",
    "postgresql":  "architecture-icons/svg/Data & Storage/database--postgreSQL.svg",
    "almacenamiento": "architecture-icons/svg/Data & Storage/File Storage Application.svg",
    # Observability
    "observability": "architecture-icons/svg/Observability/cloud--monitoring.svg",
    "monitoring":  "architecture-icons/svg/Observability/cloud--monitoring.svg",
    "logging":     "architecture-icons/svg/Observability/cloud--logging.svg",
    "logs":        "architecture-icons/svg/Observability/cloud--logging.svg",
    # Security / IAM
    "security":    "architecture-icons/svg/Security/Identity and Access Management.svg",
    "iam":         "architecture-icons/svg/Security/Identity and Access Management.svg",
    "auth":        "architecture-icons/svg/Security/two-factor-authentication.svg",
    "seguridad":   "architecture-icons/svg/Security/security-services.svg",
    # DevOps
    "devops":      "architecture-icons/svg/DevOps/Continuous Delivery.svg",
    "toolchain":   "architecture-icons/svg/DevOps/continuous-integration.svg",
    "ci/cd":       "architecture-icons/svg/DevOps/continuous-integration.svg",
    # Actors / Users
    "usuarios":    "architecture-icons/svg/Actors/User.svg",
    "users":       "architecture-icons/svg/Actors/User.svg",
    "actores":     "architecture-icons/svg/Actors/Group.svg",
    "enterprise":  "architecture-icons/svg/Actors/Enterprise.svg",
    # Applications
    "api":         "architecture-icons/svg/Applications/API 1.svg",
    "app":         "architecture-icons/svg/Applications/Application.svg",
    "aplicacion":  "architecture-icons/svg/Applications/Web Application.svg",
    "web":         "architecture-icons/svg/Applications/Web Application.svg",
}


def _svg_to_png_cached(svg_rel_path: str) -> str | None:
    """Convierte SVG a PNG (caché en icon-cache/). Devuelve ruta al PNG o None si falla."""
    svg_path = os.path.join(_ASSETS_DIR, svg_rel_path)
    if not os.path.exists(svg_path):
        return None
    # Nombre de caché = hash del path relativo
    cache_name = re.sub(r"[^\w]", "_", svg_rel_path) + ".png"
    cache_path = os.path.join(_ICON_CACHE, cache_name)
    if not os.path.exists(cache_path):
        try:
            import cairosvg
            cairosvg.svg2png(url=svg_path, write_to=cache_path, output_width=96, output_height=96)
        except Exception as exc:
            print(f"[pptx] icon convert failed {svg_rel_path}: {exc}")
            return None
    return cache_path


def _match_icon(slide_title: str) -> str | None:
    """Devuelve ruta SVG relativa si el título matchea un keyword, o None."""
    normalized = unicodedata.normalize("NFD", slide_title.lower())
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    for keyword, svg_rel in _ICON_MAP.items():
        kw_norm = unicodedata.normalize("NFD", keyword.lower())
        kw_norm = "".join(c for c in kw_norm if unicodedata.category(c) != "Mn")
        if kw_norm in normalized:
            return svg_rel
    return None


def _rgb(hex6: str):
    """Convierte hex color (6 dígitos, sin #) a RGBColor de pptx."""
    from pptx.dml.color import RGBColor
    return RGBColor(int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16))


def _set_bg(slide, hex6: str):
    """Rellena el fondo de la slide con el color sólido dado."""
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(hex6)


def _add_rect(slide, left_emu, top_emu, width_emu, height_emu, hex6: str):
    """Añade un rectángulo sólido sin borde."""
    shape = slide.shapes.add_shape(1, left_emu, top_emu, width_emu, height_emu)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(hex6)
    shape.line.fill.background()
    return shape


def _add_textbox_run(slide, left, top, width, height, text: str, size_pt,
                     color_hex: str, bold: bool = False, font: str = "IBM Plex Sans",
                     wrap: bool = True):
    """Añade un textbox de una sola línea/párrafo."""
    from pptx.util import Pt
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color_hex)
    run.font.name = font
    return tb


def _theme_colors(theme: str) -> dict:
    """Devuelve el conjunto de colores para el tema dado."""
    if theme == "light":
        return {
            "bg": _IBM_LIGHT_BG,
            "title": _IBM_DARK_TITLE,
            "body": _IBM_DARK_BODY,
            "sub": _IBM_GRAY_SUB,
            "accent": _IBM_BLUE,
            "bar": _IBM_BLUE,
        }
    # dark (default)
    return {
        "bg": _IBM_DARK_BG,
        "title": _IBM_WHITE,
        "body": _IBM_NEAR_WHITE,
        "sub": _IBM_SUBTEXT_DARK,
        "accent": _IBM_ACCENT_DARK,
        "bar": _IBM_BLUE,
    }


def _add_slide_chrome(sl, slide_num: int, SLIDE_W, SLIDE_H, tc: dict,
                      logo_path: str | None = None):
    """Añade elementos comunes a todas las slides de contenido y cierre:
    franja superior, franja inferior, numeración discreta abajo-derecha,
    pie 'IBM Knowledge Agent' abajo-izquierda en gris, logo (si existe).
    """
    from pptx.util import Inches, Pt
    # Franja superior IBM Blue
    _add_rect(sl, Inches(0), Inches(0), SLIDE_W, Inches(0.06), _IBM_BLUE)
    # Franja inferior IBM Blue
    _add_rect(sl, Inches(0), SLIDE_H - Inches(0.06), SLIDE_W, Inches(0.06), _IBM_BLUE)
    # Pie izquierdo
    tb_foot = sl.shapes.add_textbox(Inches(0.6), SLIDE_H - Inches(0.38), Inches(6), Inches(0.3))
    tf_foot = tb_foot.text_frame
    p_foot = tf_foot.paragraphs[0]
    r_foot = p_foot.add_run()
    r_foot.text = "IBM Knowledge Agent"
    r_foot.font.size = Pt(8)
    r_foot.font.bold = False
    r_foot.font.color.rgb = _rgb(_IBM_GRAY_SUB)
    r_foot.font.name = "IBM Plex Sans"
    # Numeración abajo-derecha
    tb_num = sl.shapes.add_textbox(SLIDE_W - Inches(1.1), SLIDE_H - Inches(0.38),
                                   Inches(0.8), Inches(0.3))
    tf_num = tb_num.text_frame
    p_num = tf_num.paragraphs[0]
    from pptx.enum.text import PP_ALIGN
    p_num.alignment = PP_ALIGN.RIGHT
    r_num = p_num.add_run()
    r_num.text = str(slide_num)
    r_num.font.size = Pt(8)
    r_num.font.bold = False
    r_num.font.color.rgb = _rgb(_IBM_GRAY_SUB)
    r_num.font.name = "IBM Plex Sans"


def _render_icon(sl, title: str, theme: str, SLIDE_W) -> bool:
    """Renderiza el icono de arquitectura arriba-derecha. Devuelve True si lo puso."""
    from pptx.util import Inches
    svg_rel = _match_icon(title)
    if not svg_rel:
        return False
    png_path = _svg_to_png_cached(svg_rel)
    if not png_path:
        return False
    icon_sz = Inches(0.55)
    icon_r  = SLIDE_W - Inches(0.75)
    icon_t  = Inches(0.22)
    if theme == "dark":
        pad = Inches(0.06)
        _add_rect(sl, icon_r - pad, icon_t - pad,
                  icon_sz + 2 * pad, icon_sz + 2 * pad, _IBM_WHITE)
    sl.shapes.add_picture(png_path, icon_r, icon_t, icon_sz, icon_sz)
    return True


def _render_title_bar(sl, title: str, has_icon: bool, tc: dict, SLIDE_W):
    """Renderiza el título y la línea de acento debajo."""
    from pptx.util import Inches, Pt, Emu
    MARGIN_L  = Inches(0.6)
    CONTENT_W = SLIDE_W - MARGIN_L - Inches(0.6)
    title_w   = CONTENT_W - (Inches(0.75) if has_icon else Inches(0))
    tb = sl.shapes.add_textbox(MARGIN_L, Inches(0.18), title_w, Inches(0.82))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    r.font.size = Pt(27)
    r.font.bold = False
    r.font.color.rgb = _rgb(tc["title"])
    r.font.name = "IBM Plex Sans Light"
    # Línea separadora IBM Blue debajo del título
    _add_rect(sl, MARGIN_L, Inches(1.08), CONTENT_W, Emu(65000), _IBM_BLUE)
    return MARGIN_L, CONTENT_W


def _render_bullets_rich(tf_body, bullets, paragraphs, tc):
    """Escribe bullets con parseo bold `**texto**` y párrafos al text frame dado.

    Estilo Carbon: marcador cuadrado en color acento (en vez de • genérico), y
    generoso interlineado. Nivel 1 usa marcador sólido; nivel 2 un guión corto.
    """
    from pptx.util import Pt
    first = True

    for bullet_raw, level in bullets:
        para = tf_body.paragraphs[0] if first else tf_body.add_paragraph()
        first = False
        para.level = level - 1

        # Marcador Carbon: cuadrado acento (nivel 1) o guión (nivel 2), como run
        # separado para poder darle el color de acento sin afectar el texto.
        marker_run = para.add_run()
        marker_run.text = "■  " if level == 1 else "–  "
        marker_run.font.size = Pt(17 if level == 1 else 14)
        marker_run.font.bold = False
        marker_run.font.color.rgb = _rgb(tc["accent"])
        marker_run.font.name = "IBM Plex Sans"

        # Parsear bold en el texto del bullet
        parts = _parse_bullet_parts(bullet_raw)
        prefix_color = tc["body"] if level == 1 else tc["sub"]

        for part_text, is_bold in parts:
            run = para.add_run()
            run.text = part_text
            run.font.size = Pt(17 if level == 1 else 14)
            run.font.bold = is_bold
            if is_bold:
                run.font.color.rgb = _rgb(tc["title"])
            else:
                run.font.color.rgb = _rgb(prefix_color)
            run.font.name = "IBM Plex Sans"

        para.space_before = Pt(14) if level == 1 else Pt(6)
        para.space_after  = Pt(2)
        para.line_spacing = 1.15

    for para_text in paragraphs:
        para = tf_body.paragraphs[0] if first else tf_body.add_paragraph()
        first = False
        run = para.add_run()
        run.text = para_text
        run.font.size = Pt(15)
        run.font.bold = False
        run.font.color.rgb = _rgb(tc["sub"])
        run.font.name = "IBM Plex Sans"
        para.space_before = Pt(6)
        para.line_spacing = 1.15


def _render_steps(sl, MARGIN_L, CONTENT_W, top, height, steps, tc, theme):
    """Renderiza pasos numerados estilo Carbon: número grande a la izquierda,
    keyword en bold + descripción en gris a la derecha, filas apiladas.
    """
    from pptx.util import Inches, Pt

    from pptx.util import Emu

    n = max(len(steps), 1)
    # Altura de fila: reparte el espacio disponible, con techo razonable.
    row_h = Emu(min(int(Inches(1.35)), int(height) // n))
    num_w = Inches(1.3)
    num_color = _IBM_ACCENT_DARK if theme == "dark" else _IBM_BLUE

    for i, step in enumerate(steps):
        ty = top + i * row_h
        # Número grande estilo Carbon (01, 02, ...)
        tb_num = sl.shapes.add_textbox(MARGIN_L, ty, num_w, row_h)
        tf_num = tb_num.text_frame
        tf_num.word_wrap = False
        p_num = tf_num.paragraphs[0]
        r_num = p_num.add_run()
        r_num.text = f"{i + 1:02d}"
        r_num.font.size = Pt(36)
        r_num.font.bold = False
        r_num.font.color.rgb = _rgb(num_color)
        r_num.font.name = "IBM Plex Sans Light"

        # Texto del paso: keyword bold como título + descripción en gris
        text_l = MARGIN_L + num_w + Inches(0.15)
        text_w = CONTENT_W - num_w - Inches(0.15)
        tb_txt = sl.shapes.add_textbox(text_l, ty + Inches(0.08), text_w, row_h)
        tf_txt = tb_txt.text_frame
        tf_txt.word_wrap = True
        p_txt = tf_txt.paragraphs[0]
        p_txt.line_spacing = 1.1
        keyword = step.get("keyword") or ""
        desc = step.get("desc") or ""
        if keyword:
            r_kw = p_txt.add_run()
            r_kw.text = keyword
            r_kw.font.size = Pt(16)
            r_kw.font.bold = True
            r_kw.font.color.rgb = _rgb(tc["title"])
            r_kw.font.name = "IBM Plex Sans"
            if desc:
                r_sep = p_txt.add_run()
                r_sep.text = "  —  "
                r_sep.font.size = Pt(14)
                r_sep.font.color.rgb = _rgb(tc["sub"])
                r_sep.font.name = "IBM Plex Sans"
        if desc:
            r_desc = p_txt.add_run()
            r_desc.text = desc
            r_desc.font.size = Pt(14)
            r_desc.font.bold = False
            r_desc.font.color.rgb = _rgb(tc["sub"])
            r_desc.font.name = "IBM Plex Sans"

        # Separador sutil entre filas
        if i < len(steps) - 1:
            _add_rect(sl, MARGIN_L, ty + row_h - Inches(0.06), CONTENT_W, Emu(28000),
                      "2D2D2D" if theme == "dark" else "E0E0E0")


def _render_cards(sl, MARGIN_L, CONTENT_W, top, height, cards, tc, theme):
    """Renderiza un grid de tarjetas 2x2 (o fila de 3) estilo Carbon:
    fondo sutil, borde superior de acento azul, keyword bold + descripción gris.
    """
    from pptx.util import Inches, Pt, Emu

    n = len(cards)
    cols = 3 if n == 3 else 2
    rows = 1 if n == 3 else 2
    gap = Inches(0.3)
    card_w = (CONTENT_W - gap * (cols - 1)) / cols
    # Techo de altura por tarjeta: con solo keyword + 1-2 líneas de desc no hace
    # falta llenar todo el body (evita cajas con aire vacío al fondo).
    max_card_h = Inches(1.7) if rows == 1 else Inches(2.15)
    card_h = min((height - gap * (rows - 1)) / rows, max_card_h)
    grid_h = card_h * rows + gap * (rows - 1)
    top = top + max(Inches(0), (height - grid_h) / 2)
    card_bg = "262626" if theme == "dark" else "F4F4F4"

    for i, card in enumerate(cards):
        col = i % cols
        row = i // cols
        cx = MARGIN_L + col * (card_w + gap)
        cy = top + row * (card_h + gap)
        _add_rect(sl, cx, cy, card_w, card_h, card_bg)
        # Borde superior de acento
        _add_rect(sl, cx, cy, card_w, Emu(38000), _IBM_BLUE)

        pad = Inches(0.22)
        tb = sl.shapes.add_textbox(cx + pad, cy + Inches(0.18), card_w - 2 * pad, card_h - Inches(0.3))
        tf = tb.text_frame
        tf.word_wrap = True
        p_kw = tf.paragraphs[0]
        p_kw.line_spacing = 1.1
        r_kw = p_kw.add_run()
        r_kw.text = card.get("keyword") or ""
        r_kw.font.size = Pt(16)
        r_kw.font.bold = True
        r_kw.font.color.rgb = _rgb(tc["title"])
        r_kw.font.name = "IBM Plex Sans"

        desc = card.get("desc") or ""
        if desc:
            p_desc = tf.add_paragraph()
            p_desc.space_before = Pt(6)
            p_desc.line_spacing = 1.15
            r_desc = p_desc.add_run()
            r_desc.text = desc
            r_desc.font.size = Pt(13.5)
            r_desc.font.bold = False
            r_desc.font.color.rgb = _rgb(tc["sub"])
            r_desc.font.name = "IBM Plex Sans"


def build_pptx(
    markdown: str,
    title: str = "",
    theme: str = "dark",
    presenter: dict | None = None,
    eyebrow: str = "IBM CLOUD",
) -> bytes:
    """Genera el .pptx IBM-branded a partir del markdown de presentación.

    Layouts detectados automáticamente (orden de precedencia):
      'divider' — solo # Título: slide divisor fondo IBM Blue completo.
      'stats'   — bullets **cifra** descripción (2-4): tarjetas de estadística lado a lado.
      'impact'  — solo > frase: slide de alto impacto (frase enorme + barra azul).
      'steps'   — lista ordenada 1./2./... (2-6 items): pasos numerados estilo Carbon.
      'defs'    — bullets **Término:** descripción (todos con dos puntos): lista de definiciones.
      'cards'   — 3-4 bullets **Keyword** descripción (sin dos puntos): grid de tarjetas.
      'normal'  — título + bullets estándar con marcador Carbon y aire.

    Args:
        markdown:  slides separadas por '---'.
        title:     título del deck (portada). Default: H1 de primera slide.
        theme:     'dark' (default) o 'light'. La portada siempre es IBM Blue.
        presenter: dict {name, role, email} para el bloque de presenter en portada.
        eyebrow:   texto eyebrow en portada (default 'IBM CLOUD').
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN

    if theme not in ("dark", "light"):
        theme = "dark"

    slides_data = _parse_slides(markdown)

    if not title and slides_data:
        title = slides_data[0]["title"]
    if not title:
        title = "IBM Knowledge Agent"

    prs = Presentation()
    prs.slide_width  = Inches(13.333)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]
    SLIDE_W = prs.slide_width
    SLIDE_H = prs.slide_height

    pres_name  = (presenter or {}).get("name", "IBM Knowledge Agent") or "IBM Knowledge Agent"
    pres_role  = (presenter or {}).get("role", "IBM Cloud") or "IBM Cloud"
    pres_email = (presenter or {}).get("email", "") or ""
    tc = _theme_colors(theme)
    logo_path = os.path.join(_ASSETS_DIR, "ibm-logo.emf")
    # Versión blanca con transparencia (derivada del EMF oficial): va DIRECTA sobre
    # fondos oscuros/azules, como en la plantilla real — sin caja blanca detrás.
    logo_white = os.path.join(_ASSETS_DIR, "ibm-logo-white.png")

    # ── PORTADA IBM Blue ──────────────────────────────────────────────────────
    cover = prs.slides.add_slide(blank_layout)
    _set_bg(cover, _IBM_BLUE)

    # Banda azul-oscura sutil en la parte inferior (contraste con logo)
    _add_rect(cover, Inches(0), SLIDE_H - Inches(1.2), SLIDE_W, Inches(1.2), "003A6D")

    # Eyebrow arriba-izquierda
    _add_textbox_run(
        cover, Inches(0.6), Inches(0.35), Inches(10), Inches(0.42),
        (eyebrow or "IBM CLOUD").upper(), 14, _IBM_WHITE, bold=False,
    )

    # Línea de acento bajo el eyebrow
    _add_rect(cover, Inches(0.6), Inches(0.82), Inches(4), Emu(45000), "A56EFF")

    # Título enorme: Plex Sans Light, blanco, ~52pt
    tx_cov = cover.shapes.add_textbox(Inches(0.6), Inches(1.05), Inches(10.5), Inches(3.8))
    tf_cov = tx_cov.text_frame
    tf_cov.word_wrap = True
    p_cov = tf_cov.paragraphs[0]
    r_cov = p_cov.add_run()
    r_cov.text = title
    r_cov.font.size = Pt(52)
    r_cov.font.bold = False
    r_cov.font.color.rgb = _rgb(_IBM_WHITE)
    r_cov.font.name = "IBM Plex Sans Light"

    # Bloque presenter (3 líneas, 12pt, blanco)
    presenter_lines = [pres_name, pres_role] + ([pres_email] if pres_email else [])
    tx_pres = cover.shapes.add_textbox(Inches(0.6), Inches(5.55), Inches(8), Inches(1.3))
    tf_pres = tx_pres.text_frame
    tf_pres.word_wrap = False
    for i, line in enumerate(presenter_lines):
        para = tf_pres.paragraphs[0] if i == 0 else tf_pres.add_paragraph()
        r = para.add_run()
        r.text = line
        r.font.size = Pt(12)
        r.font.bold = (i == 0)   # nombre en negrita, rol/email normales
        r.font.color.rgb = _rgb(_IBM_WHITE)
        r.font.name = "IBM Plex Sans"

    # Logo IBM abajo-derecha: blanco directo sobre la banda oscura (como la
    # plantilla oficial); si no existe la versión blanca, cae al EMF sobre caja blanca.
    if os.path.exists(logo_white):
        lw, lh = Inches(1.5), Inches(0.56)
        ll = SLIDE_W - lw - Inches(0.5)
        lt = SLIDE_H - lh - Inches(0.32)
        cover.shapes.add_picture(logo_white, ll, lt, lw, lh)
    elif os.path.exists(logo_path):
        lw, lh = Inches(1.7), Inches(0.63)
        ll = SLIDE_W - lw - Inches(0.5)
        lt = SLIDE_H - lh - Inches(0.3)
        pad = Inches(0.1)
        _add_rect(cover, ll - pad, lt - pad, lw + 2 * pad, lh + 2 * pad, _IBM_WHITE)
        cover.shapes.add_picture(logo_path, ll, lt, lw, lh)

    # ── SLIDES DE CONTENIDO ──────────────────────────────────────────────────
    slide_num = 0
    for sd in slides_data:
        slide_num += 1
        layout = sd.get("layout", "normal")
        sl = prs.slides.add_slide(blank_layout)

        # ── Layout: DIVISOR de sección (fondo IBM Blue completo) ─────────
        if layout == "divider":
            _set_bg(sl, _IBM_BLUE)
            _add_rect(sl, Inches(0), Inches(0), SLIDE_W, Inches(0.06), "003A6D")
            _add_rect(sl, Inches(0), SLIDE_H - Inches(0.06), SLIDE_W, Inches(0.06), "003A6D")
            # Número de sección pequeño arriba-izquierda
            tb_sec = sl.shapes.add_textbox(Inches(0.6), Inches(0.25), Inches(3), Inches(0.5))
            tf_sec = tb_sec.text_frame
            p_sec = tf_sec.paragraphs[0]
            r_sec = p_sec.add_run()
            r_sec.text = f"0{slide_num}"
            r_sec.font.size = Pt(13)
            r_sec.font.bold = False
            r_sec.font.color.rgb = _rgb("A56EFF")
            r_sec.font.name = "IBM Plex Sans"
            # Título enorme, centrado verticalmente
            tx_div = sl.shapes.add_textbox(Inches(0.6), Inches(2.2), Inches(11.5), Inches(3.2))
            tf_div = tx_div.text_frame
            tf_div.word_wrap = True
            p_div = tf_div.paragraphs[0]
            r_div = p_div.add_run()
            r_div.text = sd["title"]
            r_div.font.size = Pt(48)
            r_div.font.bold = False
            r_div.font.color.rgb = _rgb(_IBM_WHITE)
            r_div.font.name = "IBM Plex Sans Light"
            # Línea de acento
            _add_rect(sl, Inches(0.6), Inches(1.9), Inches(3), Emu(50000), "A56EFF")
            continue

        # ── Todas las demás layouts: fondo del tema ───────────────────────
        _set_bg(sl, tc["bg"])
        _add_slide_chrome(sl, slide_num, SLIDE_W, SLIDE_H, tc, logo_path)

        # ── Layout: IMPACTO (> frase) ─────────────────────────────────────
        if layout == "impact":
            # Franja lateral IBM Blue (2/3 de altura)
            _add_rect(sl, Inches(0.5), Inches(1.6), Inches(0.14), Inches(3.8), _IBM_BLUE)
            tx_imp = sl.shapes.add_textbox(Inches(0.9), Inches(1.4), Inches(11.5), Inches(4.5))
            tf_imp = tx_imp.text_frame
            tf_imp.word_wrap = True
            p_imp = tf_imp.paragraphs[0]
            r_imp = p_imp.add_run()
            r_imp.text = sd["impact_text"]
            r_imp.font.size = Pt(38)
            r_imp.font.bold = False
            r_imp.font.color.rgb = _rgb(tc["title"])
            r_imp.font.name = "IBM Plex Sans Light"
            continue

        # ── Layout: STATS — tarjetas de estadística lado a lado ──────────
        if layout == "stats":
            # Título (sin icono en stats — el visual son los números)
            MARGIN_L  = Inches(0.6)
            CONTENT_W = SLIDE_W - MARGIN_L - Inches(0.6)
            tb_st = sl.shapes.add_textbox(MARGIN_L, Inches(0.18), CONTENT_W, Inches(0.82))
            tf_st = tb_st.text_frame
            tf_st.word_wrap = True
            p_st = tf_st.paragraphs[0]
            r_st = p_st.add_run()
            r_st.text = sd["title"]
            r_st.font.size = Pt(27)
            r_st.font.bold = False
            r_st.font.color.rgb = _rgb(tc["title"])
            r_st.font.name = "IBM Plex Sans Light"
            _add_rect(sl, MARGIN_L, Inches(1.05), CONTENT_W, Emu(65000), _IBM_BLUE)

            stats = sd.get("stats", [])
            n = len(stats)
            card_w  = Inches(11.8 / n) if n else Inches(3)
            card_gap = Inches(0.25)
            card_top = Inches(1.4)
            card_h   = Inches(4.9)
            for idx, stat in enumerate(stats):
                cx = MARGIN_L + idx * (card_w + card_gap)
                # Caja de fondo por tarjeta
                card_bg = "1E1E1E" if theme == "dark" else "F4F4F4"
                _add_rect(sl, cx, card_top, card_w - card_gap, card_h, card_bg)
                # Cifra enorme
                fig_color = _IBM_ACCENT_DARK if theme == "dark" else _IBM_BLUE
                tb_fig = sl.shapes.add_textbox(
                    cx + Inches(0.2), card_top + Inches(0.35),
                    card_w - card_gap - Inches(0.4), Inches(1.8),
                )
                tf_fig = tb_fig.text_frame
                tf_fig.word_wrap = True
                p_fig = tf_fig.paragraphs[0]
                r_fig = p_fig.add_run()
                r_fig.text = stat["figure"]
                r_fig.font.size = Pt(52)
                r_fig.font.bold = True
                r_fig.font.color.rgb = _rgb(fig_color)
                r_fig.font.name = "IBM Plex Sans"
                # Descripción
                tb_desc = sl.shapes.add_textbox(
                    cx + Inches(0.2), card_top + Inches(2.35),
                    card_w - card_gap - Inches(0.4), Inches(2.4),
                )
                tf_desc = tb_desc.text_frame
                tf_desc.word_wrap = True
                p_desc = tf_desc.paragraphs[0]
                r_desc = p_desc.add_run()
                r_desc.text = stat["desc"]
                r_desc.font.size = Pt(15)
                r_desc.font.bold = False
                r_desc.font.color.rgb = _rgb(tc["sub"])
                r_desc.font.name = "IBM Plex Sans"
            continue

        # ── Layout: DEFS — lista de definiciones ─────────────────────────
        if layout == "defs":
            has_icon = _render_icon(sl, sd["title"], theme, SLIDE_W)
            MARGIN_L, CONTENT_W = _render_title_bar(sl, sd["title"], has_icon, tc, SLIDE_W)
            defs = sd.get("defs", [])
            body_top = Inches(1.3)
            item_h   = min(Inches(1.0), Inches(5.5 / max(len(defs), 1)))
            for i, dfn in enumerate(defs):
                ty = body_top + i * (item_h + Inches(0.18))
                # Término en bold color título
                tb_term = sl.shapes.add_textbox(MARGIN_L, ty, CONTENT_W, Inches(0.42))
                tf_term = tb_term.text_frame
                tf_term.word_wrap = False
                p_term = tf_term.paragraphs[0]
                r_term = p_term.add_run()
                r_term.text = dfn["term"]
                r_term.font.size = Pt(16)
                r_term.font.bold = True
                r_term.font.color.rgb = _rgb(tc["title"])
                r_term.font.name = "IBM Plex Sans"
                # Descripción debajo en gris
                tb_desc = sl.shapes.add_textbox(MARGIN_L + Inches(0.15), ty + Inches(0.38),
                                                CONTENT_W - Inches(0.15), item_h - Inches(0.38))
                tf_desc = tb_desc.text_frame
                tf_desc.word_wrap = True
                p_desc = tf_desc.paragraphs[0]
                r_desc = p_desc.add_run()
                r_desc.text = dfn["desc"]
                r_desc.font.size = Pt(14)
                r_desc.font.bold = False
                r_desc.font.color.rgb = _rgb(tc["sub"])
                r_desc.font.name = "IBM Plex Sans"
                # Línea separadora sutil
                if i < len(defs) - 1:
                    _add_rect(sl, MARGIN_L, ty + item_h + Inches(0.06),
                              CONTENT_W, Emu(28000),
                              "2D2D2D" if theme == "dark" else "E0E0E0")
            continue

        # ── Layout: STEPS — pasos numerados estilo Carbon ─────────────────
        if layout == "steps":
            has_icon = _render_icon(sl, sd["title"], theme, SLIDE_W)
            MARGIN_L, CONTENT_W = _render_title_bar(sl, sd["title"], has_icon, tc, SLIDE_W)
            body_top = Inches(1.35)
            body_h   = Inches(5.7)
            _render_steps(sl, MARGIN_L, CONTENT_W, body_top, body_h, sd.get("steps", []), tc, theme)
            continue

        # ── Layout: CARDS — grid de tarjetas 2x2 o fila de 3 ──────────────
        if layout == "cards":
            has_icon = _render_icon(sl, sd["title"], theme, SLIDE_W)
            MARGIN_L, CONTENT_W = _render_title_bar(sl, sd["title"], has_icon, tc, SLIDE_W)
            body_top = Inches(1.35)
            body_h   = Inches(5.7)
            _render_cards(sl, MARGIN_L, CONTENT_W, body_top, body_h, sd.get("cards", []), tc, theme)
            continue

        # ── Layout: NORMAL — título + bullets con aire ────────────────────
        has_icon = _render_icon(sl, sd["title"], theme, SLIDE_W)
        MARGIN_L, CONTENT_W = _render_title_bar(sl, sd["title"], has_icon, tc, SLIDE_W)
        tx_body = sl.shapes.add_textbox(MARGIN_L, Inches(1.28), CONTENT_W, Inches(5.85))
        tf_body = tx_body.text_frame
        tf_body.word_wrap = True
        _render_bullets_rich(tf_body, sd["bullets"], sd["paragraphs"], tc)

    # ── SLIDE DE CIERRE ──────────────────────────────────────────────────────
    slide_num += 1
    closing = prs.slides.add_slide(blank_layout)
    _set_bg(closing, tc["bg"])
    _add_slide_chrome(closing, slide_num, SLIDE_W, SLIDE_H, tc, logo_path)

    # Barra lateral izquierda IBM Blue
    _add_rect(closing, Inches(0.5), Inches(1.9), Inches(0.14), Inches(3.5), _IBM_BLUE)

    tx_thanks = closing.shapes.add_textbox(Inches(0.9), Inches(2.3), Inches(11.5), Inches(1.6))
    tf_thanks = tx_thanks.text_frame
    tf_thanks.word_wrap = True
    p_t = tf_thanks.paragraphs[0]
    r_t = p_t.add_run()
    r_t.text = "Gracias / Thank you"
    r_t.font.size = Pt(44)
    r_t.font.bold = False
    r_t.font.color.rgb = _rgb(tc["title"])
    r_t.font.name = "IBM Plex Sans Light"

    tx_cta = closing.shapes.add_textbox(Inches(0.9), Inches(4.0), Inches(11.5), Inches(0.6))
    tf_cta = tx_cta.text_frame
    p_cta = tf_cta.paragraphs[0]
    r_cta = p_cta.add_run()
    r_cta.text = "Generado por IBM Knowledge Agent"
    r_cta.font.size = Pt(13)
    r_cta.font.bold = False
    r_cta.font.color.rgb = _rgb(tc["accent"])
    r_cta.font.name = "IBM Plex Sans"

    # Logo en cierre: blanco directo en theme oscuro; EMF negro directo en claro.
    lw2, lh2 = Inches(1.5), Inches(0.56)
    ll2 = SLIDE_W - lw2 - Inches(0.5)
    lt2 = SLIDE_H - lh2 - Inches(0.35)
    if theme != "light" and os.path.exists(logo_white):
        closing.shapes.add_picture(logo_white, ll2, lt2, lw2, lh2)
    elif os.path.exists(logo_path):
        closing.shapes.add_picture(logo_path, ll2, lt2, lw2, lh2)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ── Endpoint export PPTX ────────────────────────────────────────────────────

@app.post("/export/pptx")
def export_pptx(payload: dict):
    """Genera un archivo .pptx IBM-branded a partir del markdown del modo presentación.

    Payload:
      markdown  str             — slides separadas por '---' (obligatorio).
      title     str             — título del deck (opcional; default: H1 de primera slide).
      theme     'dark'|'light'  — tema de slides de contenido (default 'dark').
      eyebrow   str             — texto eyebrow de portada (default 'IBM CLOUD').
      presenter {name, role, email} — bloque de presenter en portada (todos opcionales).

    Respuesta: binario PPTX (Content-Type pptx, Content-Disposition: attachment).
    Endpoint `def` (no async) — convención de concurrencia del proyecto.
    """
    markdown = (payload.get("markdown") or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="El campo 'markdown' no puede estar vacío.")
    # Tope defensivo: el endpoint es público (invitados también exportan) y corre
    # parsing + render en el threadpool; un markdown gigante sería DoS gratis.
    if len(markdown) > 50_000:
        raise HTTPException(status_code=413, detail="El markdown excede el tamaño máximo (50k).")
    title    = (payload.get("title") or "").strip()
    raw_theme = payload.get("theme", "dark")
    theme    = raw_theme if raw_theme in ("dark", "light") else "dark"
    eyebrow  = (payload.get("eyebrow") or "IBM CLOUD").strip()
    # Coacción de tipo: payload arbitrario con presenter no-dict tumbaba build_pptx (500).
    presenter = payload.get("presenter")
    if not isinstance(presenter, dict):
        presenter = None

    pptx_bytes = build_pptx(markdown, title, theme=theme, presenter=presenter, eyebrow=eyebrow)
    slug = _slugify(title or "presentation")

    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{slug}.pptx"'},
    )


@app.on_event("startup")
async def startup():
    # No tumbamos el arranque por un parpadeo de red/BD: si falla, se reintenta en
    # la primera petición (init_db es idempotente: CREATE TABLE IF NOT EXISTS).
    try:
        init_db()
    except Exception as e:
        print(f"[startup] init_db falló (se reintentará en la 1ª petición): {e}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/config")
def auth_config():
    """Config no-secreta para que el frontend inicie el login con App ID/IBMid."""
    return auth.public_config()


@app.get("/me")
def me(user: dict = Depends(auth.get_current_user)):
    """Devuelve el usuario autenticado (o anónimo si el login está desactivado)."""
    return user


@app.post("/auth/exchange")
def auth_exchange(payload: dict):
    """Intercambia el código de App ID por un token (server-side, con el secret)."""
    code = payload.get("code")
    redirect_uri = payload.get("redirect_uri")
    if not code or not redirect_uri:
        raise HTTPException(status_code=400, detail="Falta code o redirect_uri")
    try:
        tokens = auth.exchange_code(code, redirect_uri)
        access = tokens.get("access_token")
        id_token = tokens.get("id_token")
        auth.verify_token(access)  # el access token debe ser válido (se usa como Bearer)
        # El perfil (nombre, email) vive en el id_token, no en el access token.
        claims = auth.verify_token(id_token) if id_token else auth.verify_token(access)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"No se pudo autenticar: {e}")
    return {"token": access, "user": auth.user_from_claims(claims)}


def _pdf_content_hash(full_text: str) -> str:
    """sha256 del texto completo extraído del PDF (no de los bytes crudos): dos PDFs
    con el mismo contenido pero distinta compresión/metadata deben deduplicarse igual.
    """
    import hashlib
    return hashlib.sha256(full_text.encode("utf-8", errors="ignore")).hexdigest()


def _find_source_by_hash(content_hash: str, exclude_filename: str):
    """Devuelve el `source` (nombre de archivo) de un PDF YA indexado con el mismo
    content_hash, si existe y es un nombre distinto al que se está subiendo ahora.
    None si no hay duplicado. Ver criterio de dedupe en docs/GOVERNANCE.md.
    """
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT DISTINCT source FROM documents WHERE content_hash = %s AND source != %s LIMIT 1",
            (content_hash, exclude_filename),
        )
        row = cur.fetchone()
        return row[0] if row else None


@app.post("/ingest")
def ingest(file: UploadFile = File(...), tag: str = Form(None)):
    tag = _sanitize_tag(tag)
    contents = file.file.read()
    # Subida a COS best-effort (antes de procesar, para no perder el original si falla el parsing).
    _cos_upload(file.filename, contents)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    reader = PdfReader(tmp_path)
    full_text = "\n".join(
        page.extract_text() for page in reader.pages if page.extract_text()
    )
    os.unlink(tmp_path)

    # Dedupe de contenido (barato, sin gastar cuota): un PDF ya indexado con OTRO
    # nombre pero el MISMO texto extraído reemplaza al viejo en vez de duplicar
    # (ver docs/GOVERNANCE.md, criterio de content_hash).
    content_hash = _pdf_content_hash(full_text)
    old_source = _find_source_by_hash(content_hash, file.filename)
    if old_source:
        with db_cursor() as (conn, cur):
            cur.execute("DELETE FROM documents WHERE source = %s", (old_source,))
            conn.commit()
        _cos_delete(old_source)

    # Chunking seguro (no se pasa de 512 tokens) y embeddings en lote (rápido + resiliente).
    # Título aproximado del PDF (primera línea de texto extraído, o el nombre de
    # archivo si no hay texto) + nombre de producto si el tag lo permite deducirlo
    # — se antepone SOLO al calcular el embedding de cada chunk, para que chunks
    # intermedios no pierdan el tema ni el producto del documento.
    title = _extract_title(full_text, fallback=os.path.splitext(file.filename)[0])
    product_name = _product_title_prefix(file.filename, tag)
    chunks = chunk_text(full_text)
    pairs = embed_chunks(chunks, title=title, product_name=product_name) if chunks else []

    with db_cursor() as (conn, cur):
        # Re-ingesta idempotente: subir el mismo PDF reemplaza en vez de duplicar.
        cur.execute("DELETE FROM documents WHERE source = %s", (file.filename,))
        for chunk, embedding in pairs:
            cur.execute(
                "INSERT INTO documents (content, embedding, source, content_hash, tag) VALUES (%s, %s, %s, %s, %s)",
                (chunk, embedding, file.filename, content_hash, tag),
            )
        conn.commit()

    return {"message": f"{len(pairs)} chunks indexados", "source": file.filename}


@app.post("/ingest_stream")
def ingest_stream(file: UploadFile = File(...), tag: str = Form(None)):
    """Igual que /ingest pero reporta progreso por lote (para barra de progreso).

    Contrato NDJSON (ver docs/GOVERNANCE.md): el StreamingResponse se crea de
    inmediato y TODO el trabajo pesado (subida a COS, escritura de tempfile,
    extracción de texto del PDF, chunking, dedupe por hash, embeddings) ocurre
    dentro del generador, para que el frontend reciba señales de progreso reales
    en vez de quedarse "colgado" tras terminar la subida de bytes:

      1. {"type": "received", "bytes": N}          -- justo tras leer el upload
      2. {"type": "phase", "name": "storing"}      -- solo si COS_ENABLED
      3. {"type": "phase", "name": "extracting"}   -- antes de parsear/chunkear
      4. {"type": "start", "total": N, "source": ...}
      5. {"type": "progress", "done": N, "total": N} (repetido)
      6. {"type": "done", "count": N, "source": ...}

    Los clientes deben ignorar cualquier `type` que no reconozcan (compatibilidad
    hacia adelante si se añaden más fases).

    IMPORTANT: `file.file.read()` debe ocurrir ANTES de crear el generador —
    UploadFile se cierra en cuanto esta función retorna el StreamingResponse.
    """
    contents = file.file.read()
    filename = file.filename
    tag = _sanitize_tag(tag)

    def gen():
        tmp_path = None
        try:
            yield json.dumps({"type": "received", "bytes": len(contents)}) + "\n"

            if COS_ENABLED:
                yield json.dumps({"type": "phase", "name": "storing"}) + "\n"
                _cos_upload(filename, contents)

            yield json.dumps({"type": "phase", "name": "extracting"}) + "\n"
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contents)
                tmp_path = tmp.name
            reader = PdfReader(tmp_path)
            full_text = "\n".join(
                page.extract_text() for page in reader.pages if page.extract_text()
            )
            title = _extract_title(full_text, fallback=os.path.splitext(filename)[0])
            product_name = _product_title_prefix(filename, tag)
            chunks = chunk_text(full_text)
            content_hash = _pdf_content_hash(full_text)

            # Dedupe de contenido: mismo texto ya indexado con otro nombre -> reemplaza.
            old_source = _find_source_by_hash(content_hash, filename)
            if old_source:
                with db_cursor() as (conn, cur):
                    cur.execute("DELETE FROM documents WHERE source = %s", (old_source,))
                    conn.commit()
                _cos_delete(old_source)

            total = len(chunks)
            yield json.dumps({"type": "start", "total": total, "source": filename}) + "\n"
            with db_cursor() as (conn, cur):
                cur.execute("DELETE FROM documents WHERE source = %s", (filename,))
                conn.commit()
                done = 0
                batch = 32
                for i in range(0, total, batch):
                    group = chunks[i:i + batch]
                    for chunk, embedding in embed_chunks(group, title=title, product_name=product_name):
                        cur.execute(
                            "INSERT INTO documents (content, embedding, source, content_hash, tag) VALUES (%s, %s, %s, %s, %s)",
                            (chunk, embedding, filename, content_hash, tag),
                        )
                    conn.commit()
                    done += len(group)
                    yield json.dumps({"type": "progress", "done": done, "total": total}) + "\n"
            yield json.dumps({"type": "done", "count": total, "source": filename}) + "\n"
        finally:
            # El tempfile no debe quedar huérfano si algo lanza a mitad de camino.
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/ingest_cancel")
def ingest_cancel(payload: dict):
    """Elimina los fragmentos parciales de un PDF cuya indexación se canceló."""
    source = payload.get("source")
    if not source:
        return {"deleted": 0}
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM documents WHERE source = %s", (source,))
        deleted = cur.rowcount
        conn.commit()
    # Elimina también el objeto de COS (best-effort).
    _cos_delete(source)
    return {"deleted": deleted}


@app.get("/files/{filename}")
def serve_file(filename: str):
    """Sirve un PDF almacenado en COS.

    - 503 si COS no está configurado.
    - 400 si el filename contiene rutas relativas/absolutas (sanitización de seguridad).
    - 404 si el objeto no existe en COS.
    - 200 con el PDF como stream inline si todo va bien.

    El endpoint es `def` (no `async def`) — convención de concurrencia del proyecto.
    """
    if not COS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="El almacenamiento de archivos (COS) no está configurado en este entorno.",
        )
    # Sanitización: rechazamos cualquier intento de leer keys arbitrarias del bucket.
    # Solo se permiten nombres de archivo planos (sin '/' ni '..').
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido.")
    # Adicionalmente rechazamos nombres vacíos o que sean solo puntos.
    clean = filename.strip()
    if not clean or clean in (".", ".."):
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido.")

    key = f"pdfs/{clean}"
    try:
        obj = _get_cos().get_object(Bucket=_COS_BUCKET, Key=key)
    except Exception as exc:
        # El SDK de COS (botocore) lanza ClientError con código 'NoSuchKey' cuando
        # el objeto no existe. Cualquier otro error también se traduce a 404.
        try:
            error_code = exc.response["Error"]["Code"]  # type: ignore[attr-defined]
        except Exception:
            error_code = ""
        if error_code != "NoSuchKey":
            print(f"[COS] error al obtener '{key}': {exc}")
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")

    pdf_bytes = obj["Body"].read()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{clean}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@app.post("/query")
def query(payload: dict, user: dict = Depends(auth.get_current_user)):
    question = payload.get("question")
    language = payload.get("language", "auto")
    products = payload.get("products")  # lista opcional de productos a filtrar
    history = payload.get("history")  # turnos previos: [{role, content}]
    mode = payload.get("mode", "standard")  # modo de chat: standard|email|campaign|presentation|conceptmap
    # Opciones de presentación (solo modo presentation): {audience, slides}
    presentation_opts = payload.get("presentation_opts") if mode == "presentation" else None
    # Metadata a persistir con el mensaje: el theme y las opciones con que se generó
    # el deck, para que al recargar la conversación el PPTX salga con el tema original.
    msg_meta = {"theme": payload.get("theme"), "presentation_opts": presentation_opts} if mode == "presentation" else None

    # Memoria persistente en BD: SOLO para usuarios autenticados. Los anónimos
    # (sub == "anonymous", incluye AUTH_REQUIRED=false sin token) siguen igual
    # que antes — nada se escribe en `conversations`/`messages`.
    authenticated = user.get("sub") != "anonymous"
    conversation_id = payload.get("conversation_id") if authenticated else None
    conv_created = False  # si se creó en ESTE request (para limpiar si falla la generación)
    if authenticated:
        conversation_id, conv_created = get_or_create_conversation(user["sub"], conversation_id, question)

    # Chitchat solo aplica en modo standard; los demás modos tratan la entrada como tarea.
    if mode == "standard":
        greeting = chitchat_reply(question, language)
        if greeting is not None:
            if authenticated:
                save_messages(conversation_id, question, greeting, [], mode)
            result = {"answer": greeting, "relevant": True, "chitchat": True,
                      "max_similarity": 0.0, "threshold": MIN_SIMILARITY, "sources": [],
                      "suggestions": []}
            if authenticated:
                result["conversation_id"] = conversation_id
            return result

    # Reescribe la pregunta como query de búsqueda (memoria + reducción a intención +
    # sinónimos técnicos) y recupera con retrieval híbrido (semántico + léxico, RRF).
    try:
        search_q = search_query(question, history)
        results = hybrid_retrieve(search_q, get_embedding(search_q), products)
        data = build_query_payload(results, lenient=(mode != "standard"))
        # Casos A/B (ambigüedad / fuera de alcance real, ver docs/GOVERNANCE.md):
        # capa ortogonal a `mode`, solo aplica en standard (los demás modos generan
        # un entregable a partir de la tarea pedida, no una respuesta conversacional).
        scope = _detect_ambiguity_or_scope(results, question) if mode == "standard" else None
        # Sugerencias de seguimiento: SOLO Caso C de mode=="standard" (ni chitchat —
        # ya manejado arriba—, ni Caso A/B, ni otros `mode`) con contexto relevante.
        want_suggestions = mode == "standard" and scope is None and data["relevant"]
        raw_answer = generate_response(question, data["context"], language, history, mode,
                                        presentation_opts, scope, want_suggestions)
        if want_suggestions and SUGGESTIONS_MARKER in raw_answer:
            idx = raw_answer.index(SUGGESTIONS_MARKER)
            answer = raw_answer[:idx].rstrip()
            suggestions = _parse_suggestions(raw_answer[idx + len(SUGGESTIONS_MARKER):])
        else:
            answer = raw_answer
            suggestions = []
    except Exception:
        # Sin esto quedaría una conversación vacía en "Mis conversaciones"
        # (ver delete_conversation_if_empty). El 500 sigue propagándose igual.
        if conv_created:
            delete_conversation_if_empty(conversation_id)
        raise

    if authenticated:
        save_messages(conversation_id, question, answer, data["sources"], mode, msg_meta)

    result = {
        "answer": answer,
        "relevant": data["relevant"],
        "max_similarity": data["max_similarity"],
        "threshold": MIN_SIMILARITY,
        "sources": data["sources"],
        # Campo aditivo (ver docs/GOVERNANCE.md): True solo en el Caso A (pregunta
        # de aclaración por ambigüedad entre productos). No cambia la forma del
        # contrato existente; clientes que lo ignoren no se ven afectados.
        "clarification": bool(scope) and scope[0] == "ambiguous",
        # Campo aditivo: preguntas de seguimiento sugeridas (ver
        # docs/GOVERNANCE.md, "Sugerencias de seguimiento"). Lista vacía si no
        # aplica (chitchat/Caso A/B/otros modos) o si el parseo falló.
        "suggestions": suggestions,
    }
    if authenticated:
        result["conversation_id"] = conversation_id
    return result


@app.post("/query_stream")
def query_stream(payload: dict, user: dict = Depends(auth.get_current_user)):
    question = payload.get("question")
    language = payload.get("language", "auto")
    products = payload.get("products")
    history = payload.get("history")  # turnos previos: [{role, content}]
    mode = payload.get("mode", "standard")  # modo de chat: standard|email|campaign|presentation|conceptmap
    # Opciones de presentación (solo modo presentation): {audience, slides}
    presentation_opts = payload.get("presentation_opts") if mode == "presentation" else None
    # Metadata a persistir con el mensaje: el theme y las opciones con que se generó
    # el deck, para que al recargar la conversación el PPTX salga con el tema original.
    msg_meta = {"theme": payload.get("theme"), "presentation_opts": presentation_opts} if mode == "presentation" else None

    authenticated = user.get("sub") != "anonymous"
    conversation_id = payload.get("conversation_id") if authenticated else None
    conv_created = False  # si se creó en ESTE request (para limpiar si falla la generación)
    if authenticated:
        conversation_id, conv_created = get_or_create_conversation(user["sub"], conversation_id, question)

    # Chitchat solo aplica en modo standard; los demás modos tratan la entrada como tarea.
    if mode == "standard":
        greeting = chitchat_reply(question, language)
        if greeting is not None:
            def chitchat_stream():
                # Línea aparte (simple de consumir en streaming) con el id de conversación,
                # antes que nada más, para que el frontend lo guarde cuanto antes.
                if authenticated:
                    yield json.dumps({"type": "conversation", "conversation_id": conversation_id}) + "\n"
                yield json.dumps({"type": "meta", "relevant": True, "chitchat": True,
                                  "max_similarity": 0.0, "threshold": MIN_SIMILARITY,
                                  "sources": []}) + "\n"
                for word in greeting.split(" "):
                    yield json.dumps({"type": "token", "text": word + " "}) + "\n"
                    time.sleep(0.04)  # efecto "escribiendo" (el saludo es texto fijo, sin latencia del LLM)
                if authenticated:
                    save_messages(conversation_id, question, greeting, [], mode)
                yield json.dumps({"type": "done"}) + "\n"
            return StreamingResponse(chitchat_stream(), media_type="application/x-ndjson")

    # Reescribe la pregunta como query de búsqueda (memoria + reducción a intención +
    # sinónimos técnicos) y recupera con retrieval híbrido (semántico + léxico, RRF).
    try:
        search_q = search_query(question, history)
        results = hybrid_retrieve(search_q, get_embedding(search_q), products)
        data = build_query_payload(results, lenient=(mode != "standard"))
        # Casos A/B (ambigüedad / fuera de alcance real, ver docs/GOVERNANCE.md):
        # capa ortogonal a `mode`, solo aplica en standard (los demás modos generan
        # un entregable a partir de la tarea pedida, no una respuesta conversacional).
        scope = _detect_ambiguity_or_scope(results, question) if mode == "standard" else None
        # Sugerencias de seguimiento: SOLO Caso C de mode=="standard" (ni chitchat —
        # ya manejado arriba—, ni Caso A/B, ni otros `mode`) con contexto relevante.
        want_suggestions = mode == "standard" and scope is None and data["relevant"]
    except Exception:
        # Falla antes de arrancar el stream: sin esto quedaría una conversación
        # vacía en "Mis conversaciones" (ver delete_conversation_if_empty).
        if conv_created:
            delete_conversation_if_empty(conversation_id)
        raise

    def event_stream():
        # 0) id de conversación (solo autenticados), antes que nada más
        if authenticated:
            yield json.dumps({"type": "conversation", "conversation_id": conversation_id}) + "\n"
        # 1) metadata (fuentes + relevancia + mode) como primera línea NDJSON
        yield json.dumps({
            "type": "meta",
            "relevant": data["relevant"],
            "max_similarity": data["max_similarity"],
            "threshold": MIN_SIMILARITY,
            "sources": data["sources"],
            "mode": mode,
            # Campo aditivo (ver docs/GOVERNANCE.md): True solo en el Caso A
            # (pregunta de aclaración por ambigüedad entre productos).
            "clarification": bool(scope) and scope[0] == "ambiguous",
        }) + "\n"
        # 2) tokens de la respuesta a medida que se generan (con historial).
        # Si want_suggestions, el modelo puede emitir SUGGESTIONS_MARKER + preguntas
        # al final de la MISMA generación (ver SUGGESTIONS_INSTRUCTION) — se hace
        # streaming con "hold-back" de los últimos len(marcador)-1 caracteres del
        # buffer sin flushear (algoritmo estándar de delimitador en streaming): así
        # el marcador nunca puede aparecer parcialmente en el texto visible, ni
        # aunque llegue partido entre dos deltas consecutivos del LLM. Cuando
        # want_suggestions es False, hold=0 y el comportamiento es IDÉNTICO al
        # anterior (flush inmediato, sin latencia añadida).
        acc = []
        buf = ""
        marker_found = False
        suggestions_buf = ""
        hold = (len(SUGGESTIONS_MARKER) - 1) if want_suggestions else 0
        try:
            for delta in generate_response_stream(question, data["context"], language, history, mode,
                                                    presentation_opts, scope, want_suggestions):
                if marker_found:
                    suggestions_buf += delta
                    continue
                buf += delta
                if want_suggestions and SUGGESTIONS_MARKER in buf:
                    idx = buf.index(SUGGESTIONS_MARKER)
                    visible = buf[:idx]
                    if visible:
                        acc.append(visible)
                        yield json.dumps({"type": "token", "text": visible}) + "\n"
                    marker_found = True
                    suggestions_buf = buf[idx + len(SUGGESTIONS_MARKER):]
                    buf = ""
                    continue
                safe_len = len(buf) - hold
                if safe_len > 0:
                    visible = buf[:safe_len]
                    buf = buf[safe_len:]
                    acc.append(visible)
                    yield json.dumps({"type": "token", "text": visible}) + "\n"
            # Cola final: si nunca apareció el marcador, es texto visible normal.
            if buf and not marker_found:
                acc.append(buf)
                yield json.dumps({"type": "token", "text": buf}) + "\n"
        except BaseException:
            # BaseException y no Exception: cubre también GeneratorExit (cliente
            # desconectado a mitad del stream), que igualmente deja la conversación
            # sin mensajes. Solo se limpia y se re-lanza; no se hace yield aquí.
            if conv_created:
                delete_conversation_if_empty(conversation_id)
            raise
        clean_answer = "".join(acc)
        # 3) persistir el turno completo (solo autenticados) una vez terminó de generar
        # — el marcador/las sugerencias NUNCA se persisten en messages.content.
        if authenticated:
            save_messages(conversation_id, question, clean_answer, data["sources"], mode, msg_meta)
        # 4) sugerencias de seguimiento, como línea aparte AL FINAL del stream (ver
        # docs/GOVERNANCE.md). Solo se emite si se logró parsear al menos una —
        # nunca rompe el flujo si el modelo no las generó o el parseo falló.
        if marker_found:
            suggestion_items = _parse_suggestions(suggestions_buf)
            if suggestion_items:
                yield json.dumps({"type": "suggestions", "items": suggestion_items}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/feedback")
def feedback(payload: dict, user: dict = Depends(auth.get_current_user)):
    with db_cursor() as (conn, cur):
        cur.execute(
            "INSERT INTO feedback (question, answer, rating, language, user_sub) VALUES (%s, %s, %s, %s, %s)",
            (payload.get("question"), payload.get("answer"),
             payload.get("rating"), payload.get("language"), user.get("sub")),
        )
        conn.commit()
    return {"ok": True}


def _require_login(user: dict):
    """Estos endpoints solo tienen sentido con un usuario real (no anónimo)."""
    if user.get("sub") == "anonymous":
        raise HTTPException(status_code=401, detail="Esta función requiere iniciar sesión")


# Cuántos 👎 recientes devuelve /feedback/stats (ver docstring del endpoint).
FEEDBACK_STATS_RECENT_LIMIT = 50

# Admins de /feedback/stats: lee preguntas/respuestas de TODOS los usuarios (posible
# PII/contenido de cliente en los 👎), así que no basta con "estar logueado" (QA
# 2026-09-15) — allowlist por email, configurable vía env, con default seguro.
_FEEDBACK_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.getenv("FEEDBACK_ADMIN_EMAILS", "cesar.carrasco@ibm.com").split(",")
    if e.strip()
}


def _require_feedback_admin(user: dict):
    _require_login(user)
    email = (user.get("email") or "").strip().lower()
    if email not in _FEEDBACK_ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="No autorizado para ver estas estadísticas")


@app.get("/feedback/stats")
def feedback_stats(user: dict = Depends(auth.get_current_user)):
    """Uso interno (no para la demo, sin botón en el header — ver docs/GOVERNANCE.md
    y docs/STATUS.md): agrega la tabla `feedback` para que César pueda revisar qué
    respuestas fallaron. Requiere login Y estar en `_FEEDBACK_ADMIN_EMAILS` (401 si
    anónimo, 403 si logueado pero no admin — ver docs/GOVERNANCE.md). Solo lectura."""
    _require_feedback_admin(user)
    with db_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM feedback WHERE rating = 'up'")
        total_up = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM feedback WHERE rating = 'down'")
        total_down = cur.fetchone()[0]
        cur.execute(
            """SELECT COALESCE(language, 'unknown') AS lang,
                      COUNT(*) FILTER (WHERE rating = 'up') AS up,
                      COUNT(*) FILTER (WHERE rating = 'down') AS down
               FROM feedback GROUP BY lang ORDER BY lang"""
        )
        by_language = [{"language": r[0], "up": r[1], "down": r[2]} for r in cur.fetchall()]
        cur.execute(
            """SELECT question, answer, created_at FROM feedback
               WHERE rating = 'down' ORDER BY created_at DESC LIMIT %s""",
            (FEEDBACK_STATS_RECENT_LIMIT,),
        )
        recent_negative = [
            {"question": r[0], "answer": r[1], "created_at": r[2].isoformat()}
            for r in cur.fetchall()
        ]
    return {
        "total_up": total_up,
        "total_down": total_down,
        "by_language": by_language,
        "recent_negative": recent_negative,
    }


@app.get("/conversations")
def list_conversations(user: dict = Depends(auth.get_current_user)):
    """Lista las conversaciones del usuario actual, más recientes primero."""
    _require_login(user)
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT id, title, updated_at FROM conversations WHERE user_sub = %s ORDER BY updated_at DESC",
            (user["sub"],),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2].isoformat()} for r in rows]


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    """Devuelve una conversación con sus mensajes, si es del usuario actual."""
    _require_login(user)
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT id, title FROM conversations WHERE id = %s AND user_sub = %s",
            (conversation_id, user["sub"]),
        )
        conv = cur.fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        cur.execute(
            "SELECT role, content, sources, mode, meta FROM messages WHERE conversation_id = %s ORDER BY id ASC",
            (conversation_id,),
        )
        msgs = cur.fetchall()
    return {
        "id": conv[0],
        "title": conv[1],
        "messages": [
            {"role": m[0], "content": m[1], "sources": m[2] or [], "mode": m[3] or "standard", "meta": m[4]}
            for m in msgs
        ],
    }


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    """Borra una conversación (y sus mensajes, por ON DELETE CASCADE), si es dueño."""
    _require_login(user)
    with db_cursor() as (conn, cur):
        cur.execute(
            "DELETE FROM conversations WHERE id = %s AND user_sub = %s",
            (conversation_id, user["sub"]),
        )
        deleted = cur.rowcount
        conn.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return {"ok": True}