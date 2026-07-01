import os
import re
import json
import time
import tempfile
import unicodedata
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import psycopg2

import auth
from pgvector.psycopg2 import register_vector
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference, Embeddings
from pypdf import PdfReader

load_dotenv()

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


def get_db():
    conn = psycopg2.connect(
        os.getenv("POSTGRES_URL"),
        sslrootcert=os.getenv("POSTGRES_CERT")
    )
    register_vector(conn)
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
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
    conn.commit()
    cur.close()
    conn.close()


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


def embed_safe(text: str, depth: int = 0) -> list:
    """[(texto, embedding)], dividiendo si el chunk excede el límite de tokens."""
    try:
        return [(text, get_embeddings_model().embed_documents(texts=[text])[0])]
    except Exception:
        if depth > 6 or len(text) < 80:
            return []
        mid = len(text) // 2
        split = text.rfind(" ", 0, mid)
        if split <= 0:
            split = mid
        return embed_safe(text[:split].strip(), depth + 1) + embed_safe(text[split:].strip(), depth + 1)


def embed_chunks(chunks: list, batch: int = 64) -> list:
    """Embeddings en sub-lotes; si un lote falla por un chunk denso, reintenta dividiendo."""
    pairs = []
    for i in range(0, len(chunks), batch):
        group = chunks[i:i + batch]
        try:
            embeddings = get_embeddings_model().embed_documents(texts=group)
            pairs.extend(zip(group, embeddings))
        except Exception:
            for c in group:
                pairs.extend(embed_safe(c))
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


def _build_messages(question: str, context: str, language: str, history=None):
    lang_instruction = LANGUAGE_INSTRUCTION.get(language, LANGUAGE_INSTRUCTION["auto"])
    if not context.strip():
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
        system = f"{SYSTEM_PROMPT} {lang_instruction}"
        user_content = f"CONTEXT:\n{context}\n\nQUESTION: {question}"
    messages = [{"role": "system", "content": system}]
    messages.extend(_clean_history(history))  # turnos previos para dar contexto
    messages.append({"role": "user", "content": user_content})
    return messages


def _chat_model():
    return ModelInference(
        model_id="meta-llama/llama-3-3-70b-instruct",
        credentials=credentials,
        project_id=project_id,
    )


def generate_response(question: str, context: str, language: str = "auto", history=None):
    response = _chat_model().chat(
        messages=_build_messages(question, context, language, history),
        params={"max_tokens": 500, "temperature": 0.3},
    )
    return response["choices"][0]["message"]["content"].strip()


def generate_response_stream(question: str, context: str, language: str = "auto", history=None):
    """Genera la respuesta token por token (para streaming)."""
    for chunk in _chat_model().chat_stream(
        messages=_build_messages(question, context, language, history),
        params={"max_tokens": 500, "temperature": 0.3},
    ):
        try:
            delta = chunk["choices"][0]["delta"].get("content", "")
        except (KeyError, IndexError, TypeError):
            delta = ""
        if delta:
            yield delta


# Productos disponibles para filtrar. La clave identifica la fuente en la columna
# `source`: 'watsonx' vive en www.ibm.com/.../watsonx/...; el resto en GitHub
# ibm-cloud-docs/<producto>/...
def product_filter_sql(products):
    """Devuelve (clausula_WHERE, params) para filtrar por producto. [] = sin filtro."""
    if not products:
        return "", []
    likes, params = [], []
    for p in products:
        likes.append("source LIKE %s")
        params.append("%/watsonx/%" if p == "watsonx" else f"%ibm-cloud-docs/{p}/%")
    return "WHERE (" + " OR ".join(likes) + ")", params


def retrieve(question_embedding, products=None, limit=3):
    """Recupera los top-k chunks, opcionalmente filtrados por producto."""
    clause, params = product_filter_sql(products)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
            FROM documents {clause}
            ORDER BY embedding <=> %s::vector
            LIMIT %s""",
        (question_embedding, *params, question_embedding, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def short_source(url: str) -> str:
    """Etiqueta corta de la fuente para el contexto (sin URL completa)."""
    m = re.search(r"ibm-cloud-docs/([^/]+)/blob/[^/]+/(.+)\.md$", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if "topic=" in url:
        return "watsonx/" + url.split("topic=")[-1]
    return url


def build_query_payload(results):
    """Construye sources + flags de relevancia a partir de las filas recuperadas."""
    relevant = [r for r in results if r[2] >= MIN_SIMILARITY]
    context = "\n\n".join([f"[{short_source(r[1])}]: {r[0]}" for r in relevant])
    return {
        "context": context,
        "relevant": bool(relevant),
        "max_similarity": float(results[0][2]) if results else 0.0,
        "sources": [
            {
                "content": r[0],
                "source": r[1],
                "similarity": float(r[2]),
                "relevant": r[2] >= MIN_SIMILARITY,
            }
            for r in results
        ],
    }


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


@app.post("/ingest")
def ingest(file: UploadFile = File(...)):
    contents = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    reader = PdfReader(tmp_path)
    full_text = "\n".join(
        page.extract_text() for page in reader.pages if page.extract_text()
    )
    os.unlink(tmp_path)

    # Chunking seguro (no se pasa de 512 tokens) y embeddings en lote (rápido + resiliente).
    chunks = chunk_text(full_text)
    pairs = embed_chunks(chunks) if chunks else []

    conn = get_db()
    cur = conn.cursor()
    # Re-ingesta idempotente: subir el mismo PDF reemplaza en vez de duplicar.
    cur.execute("DELETE FROM documents WHERE source = %s", (file.filename,))
    for chunk, embedding in pairs:
        cur.execute(
            "INSERT INTO documents (content, embedding, source) VALUES (%s, %s, %s)",
            (chunk, embedding, file.filename),
        )
    conn.commit()
    cur.close()
    conn.close()

    return {"message": f"{len(pairs)} chunks indexados", "source": file.filename}


@app.post("/ingest_stream")
def ingest_stream(file: UploadFile = File(...)):
    """Igual que /ingest pero reporta progreso por lote (para barra de progreso)."""
    contents = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    reader = PdfReader(tmp_path)
    full_text = "\n".join(
        page.extract_text() for page in reader.pages if page.extract_text()
    )
    os.unlink(tmp_path)

    chunks = chunk_text(full_text)
    filename = file.filename

    def gen():
        total = len(chunks)
        yield json.dumps({"type": "start", "total": total, "source": filename}) + "\n"
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM documents WHERE source = %s", (filename,))
        conn.commit()
        done = 0
        batch = 32
        for i in range(0, total, batch):
            group = chunks[i:i + batch]
            for chunk, embedding in embed_chunks(group):
                cur.execute(
                    "INSERT INTO documents (content, embedding, source) VALUES (%s, %s, %s)",
                    (chunk, embedding, filename),
                )
            conn.commit()
            done += len(group)
            yield json.dumps({"type": "progress", "done": done, "total": total}) + "\n"
        cur.close()
        conn.close()
        yield json.dumps({"type": "done", "count": total, "source": filename}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/ingest_cancel")
def ingest_cancel(payload: dict):
    """Elimina los fragmentos parciales de un PDF cuya indexación se canceló."""
    source = payload.get("source")
    if not source:
        return {"deleted": 0}
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM documents WHERE source = %s", (source,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": deleted}


@app.post("/query")
def query(payload: dict):
    question = payload.get("question")
    language = payload.get("language", "auto")
    products = payload.get("products")  # lista opcional de productos a filtrar
    history = payload.get("history")  # turnos previos: [{role, content}]

    greeting = chitchat_reply(question, language)
    if greeting is not None:
        return {"answer": greeting, "relevant": True, "chitchat": True,
                "max_similarity": 0.0, "threshold": MIN_SIMILARITY, "sources": []}

    # Reescribe la pregunta con el historial antes de buscar (memoria conversacional).
    search_q = condense_question(question, history)
    results = retrieve(get_embedding(search_q), products)
    data = build_query_payload(results)
    answer = generate_response(question, data["context"], language, history)

    return {
        "answer": answer,
        "relevant": data["relevant"],
        "max_similarity": data["max_similarity"],
        "threshold": MIN_SIMILARITY,
        "sources": data["sources"],
    }


@app.post("/query_stream")
def query_stream(payload: dict):
    question = payload.get("question")
    language = payload.get("language", "auto")
    products = payload.get("products")
    history = payload.get("history")  # turnos previos: [{role, content}]

    greeting = chitchat_reply(question, language)
    if greeting is not None:
        def chitchat_stream():
            yield json.dumps({"type": "meta", "relevant": True, "chitchat": True,
                              "max_similarity": 0.0, "threshold": MIN_SIMILARITY,
                              "sources": []}) + "\n"
            for word in greeting.split(" "):
                yield json.dumps({"type": "token", "text": word + " "}) + "\n"
                time.sleep(0.04)  # efecto "escribiendo" (el saludo es texto fijo, sin latencia del LLM)
            yield json.dumps({"type": "done"}) + "\n"
        return StreamingResponse(chitchat_stream(), media_type="application/x-ndjson")

    # Memoria conversacional: reescribe la pregunta con el historial antes de buscar.
    search_q = condense_question(question, history)
    results = retrieve(get_embedding(search_q), products)
    data = build_query_payload(results)

    def event_stream():
        # 1) metadata (fuentes + relevancia) como primera línea NDJSON
        yield json.dumps({
            "type": "meta",
            "relevant": data["relevant"],
            "max_similarity": data["max_similarity"],
            "threshold": MIN_SIMILARITY,
            "sources": data["sources"],
        }) + "\n"
        # 2) tokens de la respuesta a medida que se generan (con historial)
        for delta in generate_response_stream(question, data["context"], language, history):
            yield json.dumps({"type": "token", "text": delta}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/feedback")
def feedback(payload: dict, user: dict = Depends(auth.get_current_user)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO feedback (question, answer, rating, language, user_sub) VALUES (%s, %s, %s, %s, %s)",
        (payload.get("question"), payload.get("answer"),
         payload.get("rating"), payload.get("language"), user.get("sub")),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}