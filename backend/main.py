import os
import tempfile
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
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
    conn.commit()
    cur.close()
    conn.close()


def get_embedding(text: str):
    embedding_model = Embeddings(
        model_id="ibm/granite-embedding-278m-multilingual",
        credentials=credentials,
        project_id=project_id
    )
    result = embedding_model.embed_documents(texts=[text])
    return result[0]


SYSTEM_PROMPT = (
    "You are an IBM technical assistant for students and engineers. "
    "Answer the user's question using ONLY the information in the provided context. "
    "If the context does not contain the answer, say so explicitly and do not invent details. "
    "Be concise and technical. Cite the source in brackets (e.g. [source]) when you use it. "
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


def generate_response(question: str, context: str, language: str = "auto"):
    model = ModelInference(
        model_id="meta-llama/llama-3-3-70b-instruct",
        credentials=credentials,
        project_id=project_id,
    )
    lang_instruction = LANGUAGE_INSTRUCTION.get(language, LANGUAGE_INSTRUCTION["auto"])

    if not context.strip():
        # Sin contexto relevante: el modelo debe avisar que no tiene información,
        # sin inventar nada y sin citar fuentes.
        system = (
            "You are an IBM technical assistant. The indexed knowledge base contains "
            "no information relevant to the question. Tell the user briefly that you "
            "don't have information about this topic in the documentation. Do not "
            "invent an answer and do not cite any source. " + lang_instruction
        )
        user_content = f"QUESTION: {question}"
    else:
        system = f"{SYSTEM_PROMPT} {lang_instruction}"
        user_content = f"CONTEXT:\n{context}\n\nQUESTION: {question}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    response = model.chat(
        messages=messages,
        params={"max_tokens": 500, "temperature": 0.3},
    )
    return response["choices"][0]["message"]["content"].strip()


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    reader = PdfReader(tmp_path)
    chunks = []
    for page in reader.pages:
        text = page.extract_text()
        if text and text.strip():
            for i in range(0, len(text), 500):
                chunk = text[i:i+500].strip()
                if chunk:
                    chunks.append(chunk)

    conn = get_db()
    cur = conn.cursor()
    for chunk in chunks:
        embedding = get_embedding(chunk)
        cur.execute(
            "INSERT INTO documents (content, embedding, source) VALUES (%s, %s, %s)",
            (chunk, embedding, file.filename)
        )
    conn.commit()
    cur.close()
    conn.close()
    os.unlink(tmp_path)

    return {"message": f"{len(chunks)} chunks indexados", "source": file.filename}


@app.post("/query")
async def query(payload: dict):
    question = payload.get("question")
    language = payload.get("language", "auto")
    question_embedding = get_embedding(question)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
           FROM documents
           ORDER BY embedding <=> %s::vector
           LIMIT 3""",
        (question_embedding, question_embedding)
    )
    results = cur.fetchall()
    cur.close()
    conn.close()

    # Solo usamos como contexto los chunks que superan el umbral de relevancia.
    # Si ninguno lo supera, el contexto va vacío y el modelo dirá que no tiene info.
    relevant = [r for r in results if r[2] >= MIN_SIMILARITY]
    context = "\n\n".join([f"[{r[1]}]: {r[0]}" for r in relevant])
    answer = generate_response(question, context, language)

    max_similarity = float(results[0][2]) if results else 0.0
    return {
        "answer": answer,
        "relevant": bool(relevant),
        "max_similarity": max_similarity,
        "threshold": MIN_SIMILARITY,
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