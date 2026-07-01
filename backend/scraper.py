"""Scraper de IBM Docs con Playwright (renderiza JavaScript).

IBM Docs carga el contenido dinámicamente con JS, por lo que requests + BeautifulSoup
devuelven páginas vacías. Playwright lanza un Chromium headless, espera a que el
contenido se renderice y extrae el texto del <main>/<article>.
"""

import os
import re
import sys

import requests
from playwright.sync_api import sync_playwright

# Tamaño de chunk en caracteres y solapamiento entre chunks consecutivos.
# El modelo de embeddings (granite-278m) corta en 512 tokens. En markdown denso
# (código/tablas) los tokens por carácter suben mucho, así que mantenemos chunks
# conservadores; ingest_texts() además divide cualquier chunk que aún se pase.
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
# Tope duro de caracteres por "palabra": un token sin espacios (bloque de código,
# fila de tabla, URL larga) se trocea para no exceder el límite de tokens del modelo.
# Observado: contenido denso llega a ~1.1 tokens/carácter, así que el peor chunk
# (CHUNK_SIZE + MAX_WORD_LEN ≈ 500 chars) queda con margen bajo el límite de 512.
MAX_WORD_LEN = 100


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """Divide el texto en chunks respetando límites de palabra, con solapamiento."""
    # Trocea cualquier "palabra" gigante (sin espacios) para que ningún chunk se
    # dispare en tokens por un solo token enorme.
    raw_words = text.split()
    words = []
    for w in raw_words:
        if len(w) > MAX_WORD_LEN:
            for i in range(0, len(w), MAX_WORD_LEN):
                words.append(w[i:i + MAX_WORD_LEN])
        else:
            words.append(w)

    chunks = []
    current = []
    current_len = 0

    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= size:
            chunks.append(" ".join(current).strip())
            # Mantener las últimas palabras como solapamiento para no perder contexto.
            overlap_words = []
            overlap_len = 0
            for w in reversed(current):
                if overlap_len >= overlap:
                    break
                overlap_words.insert(0, w)
                overlap_len += len(w) + 1
            current = overlap_words
            current_len = overlap_len

    if current:
        tail = " ".join(current).strip()
        if tail:
            chunks.append(tail)

    return [c for c in chunks if c]


def scrape_page(page, url: str) -> str:
    """Carga una URL en una página de Playwright y devuelve el texto principal.

    Usamos 'domcontentloaded' (no 'networkidle') porque IBM Docs mantiene tráfico
    de red continuo —analytics, telemetría— y la red nunca queda inactiva.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # IBM Docs envuelve el contenido en <main> o <article>; esperamos a que se renderice.
    for selector in ("main", "article"):
        try:
            page.wait_for_selector(selector, timeout=15000)
            text = page.inner_text(selector)
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    # Fallback: todo el body.
    try:
        return page.inner_text("body").strip()
    except Exception:
        return ""


def scrape_and_ingest(urls: list):
    import psycopg2
    from pgvector.psycopg2 import register_vector
    from dotenv import load_dotenv
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import Embeddings

    load_dotenv()

    credentials = Credentials(
        url=os.getenv("WATSONX_URL"),
        api_key=os.getenv("WATSONX_API_KEY"),
    )
    project_id = os.getenv("WATSONX_PROJECT_ID")

    conn = psycopg2.connect(
        os.getenv("POSTGRES_URL"),
        sslrootcert=os.getenv("POSTGRES_CERT"),
    )
    register_vector(conn)
    cur = conn.cursor()

    embedding_model = Embeddings(
        model_id="ibm/granite-embedding-278m-multilingual",
        credentials=credentials,
        project_id=project_id,
    )

    total_chunks = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (compatible; IBMKnowledgeAgent/1.0)")

        for url in urls:
            print(f"Scraping: {url}")
            try:
                texto = scrape_page(page, url)
            except Exception as e:
                print(f"  Error: {e} — saltando")
                continue

            if not texto or len(texto) < 50:
                print("  Sin contenido suficiente — saltando")
                continue

            chunks = chunk_text(texto)
            print(f"  {len(chunks)} chunks")

            # Re-ingesta idempotente: elimina chunks previos de esta misma URL para
            # no acumular duplicados al re-ejecutar el scraper.
            cur.execute("DELETE FROM documents WHERE source = %s", (url,))

            # Embeddings en lote (más rápido y menos llamadas a la API).
            embeddings = embedding_model.embed_documents(texts=chunks)
            for chunk, embedding in zip(chunks, embeddings):
                cur.execute(
                    "INSERT INTO documents (content, embedding, source) VALUES (%s, %s, %s)",
                    (chunk, embedding, url),
                )

            conn.commit()
            total_chunks += len(chunks)
            print("  Indexado")

        browser.close()

    cur.close()
    conn.close()
    print(f"\nTotal: {total_chunks} chunks indexados de {len(urls)} URLs")


# ---------------------------------------------------------------------------
# Ingesta desde GitHub (fuente oficial de los docs de IBM Cloud)
#
# Los docs de cloud.ibm.com son open source en github.com/ibm-cloud-docs (un repo
# por producto, un .md por tema). Traer el markdown crudo es muchísimo mejor que
# scrapear el HTML: sin WAF, sin JavaScript, y obtenemos el doc set completo.
# ---------------------------------------------------------------------------

# (etiqueta de producto, nombre del repo en ibm-cloud-docs)
GITHUB_PRODUCTS = [
    ("vpc", "vpc"),
    ("messages-for-rabbitmq", "messages-for-rabbitmq"),
    ("containers", "containers"),
    ("codeengine", "codeengine"),
    ("cloud-object-storage", "cloud-object-storage"),
    ("databases-for-postgresql", "databases-for-postgresql"),
]

# Tope de archivos por producto (controla costo de embeddings). Súbelo para más cobertura.
MAX_FILES_PER_PRODUCT = 30

# Temas conceptuales/de inicio primero; archivos de ruido al final o excluidos.
PRIORITY_PREFIXES = ("getting-started", "about", "overview", "what-is", "plan",
                     "faqs", "tutorial", "manage", "create", "use")
SKIP_SUBSTRINGS = ("relnotes", "release-notes", "api-change-log", "changelog", "responsibilities")


def clean_markdown(md: str) -> str:
    """Limpia el markdown de IBM Docs para dejar texto apto para embeddings."""
    md = re.sub(r"^\s*---\n.*?\n---\n", "", md, count=1, flags=re.DOTALL)  # frontmatter
    md = re.sub(r"\{\{[^}]*\}\}", "", md)        # variables {{site.data.keyword.*}}
    md = re.sub(r"\{:[^}]*\}", "", md)           # anotaciones {: #anchor} {: shortdesc}
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)  # imágenes
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def list_repo_topics(repo: str, branch: str = "master") -> list:
    """Lista los .md de nivel superior (temas reales) de un repo de ibm-cloud-docs."""
    url = f"https://api.github.com/repos/ibm-cloud-docs/{repo}/git/trees/{branch}?recursive=1"
    tree = requests.get(url, timeout=30).json().get("tree", [])
    files = []
    for t in tree:
        path = t.get("path", "")
        if not path.endswith(".md") or "/" in path:
            continue
        name = path[:-3]
        if name.startswith("_") or name == "toc":
            continue
        if any(s in name for s in SKIP_SUBSTRINGS):
            continue
        files.append(name)

    def rank(name):
        for i, p in enumerate(PRIORITY_PREFIXES):
            if name.startswith(p):
                return i
        return len(PRIORITY_PREFIXES)

    files.sort(key=lambda n: (rank(n), n))
    return files


def fetch_raw_md(repo: str, name: str, branch: str = "master") -> str:
    url = f"https://raw.githubusercontent.com/ibm-cloud-docs/{repo}/{branch}/{name}.md"
    r = requests.get(url, timeout=30)
    return r.text if r.ok else ""


def doc_url(repo: str, name: str, branch: str = "master") -> str:
    """URL de la fuente: el archivo en GitHub que realmente usamos como data source.

    Antes construíamos la URL de cloud.ibm.com por convención (<producto>-<archivo>),
    pero esa convención no siempre calza con el topic real -> a veces daba 404. El
    blob de GitHub SIEMPRE existe (es lo que descargamos) y además renderiza el
    markdown de forma legible para el usuario.
    """
    return f"https://github.com/ibm-cloud-docs/{repo}/blob/{branch}/{name}.md"


def ingest_github(products=None, max_files: int = MAX_FILES_PER_PRODUCT):
    """Trae el markdown de cada producto desde GitHub, lo limpia e indexa."""
    products = products or GITHUB_PRODUCTS
    items = []  # (source_url, texto_limpio)
    for product, repo in products:
        topics = list_repo_topics(repo)[:max_files]
        print(f"{product}: {len(topics)} temas")
        for name in topics:
            raw = fetch_raw_md(repo, name)
            text = clean_markdown(raw)
            if len(text) >= 200:
                items.append((doc_url(repo, name), text))
    print(f"Total de documentos a indexar: {len(items)}")
    ingest_texts(items)


def embed_safe(embedding_model, text: str, depth: int = 0) -> list:
    """Devuelve [(texto, embedding)], dividiendo el texto si excede el límite de tokens."""
    try:
        emb = embedding_model.embed_documents(texts=[text])[0]
        return [(text, emb)]
    except Exception:
        if depth > 6 or len(text) < 80:
            return []  # fragmento irreducible: se descarta
        mid = len(text) // 2
        split = text.rfind(" ", 0, mid)
        if split <= 0:
            split = mid
        return (
            embed_safe(embedding_model, text[:split].strip(), depth + 1)
            + embed_safe(embedding_model, text[split:].strip(), depth + 1)
        )


def ingest_texts(items: list):
    """Indexa una lista de (source_url, texto): chunk -> embedding -> PostgreSQL."""
    import psycopg2
    from pgvector.psycopg2 import register_vector
    from dotenv import load_dotenv
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import Embeddings

    load_dotenv()
    credentials = Credentials(url=os.getenv("WATSONX_URL"), api_key=os.getenv("WATSONX_API_KEY"))
    project_id = os.getenv("WATSONX_PROJECT_ID")
    conn = psycopg2.connect(os.getenv("POSTGRES_URL"), sslrootcert=os.getenv("POSTGRES_CERT"))
    register_vector(conn)
    cur = conn.cursor()
    embedding_model = Embeddings(
        model_id="ibm/granite-embedding-278m-multilingual",
        credentials=credentials,
        project_id=project_id,
    )

    total = 0
    for source, text in items:
        chunks = chunk_text(text)
        if not chunks:
            continue
        # Embedding en lote; si el lote falla (algún chunk muy denso), se reintenta
        # chunk por chunk dividiendo los que excedan el límite de tokens.
        try:
            embeddings = embedding_model.embed_documents(texts=chunks)
            pairs = list(zip(chunks, embeddings))
        except Exception:
            pairs = []
            for c in chunks:
                pairs.extend(embed_safe(embedding_model, c))

        cur.execute("DELETE FROM documents WHERE source = %s", (source,))
        for chunk, embedding in pairs:
            cur.execute(
                "INSERT INTO documents (content, embedding, source) VALUES (%s, %s, %s)",
                (chunk, embedding, source),
            )
        conn.commit()
        total += len(pairs)
        print(f"  {len(pairs):>3} chunks  {source.split('topic=')[-1]}")

    cur.close()
    conn.close()
    print(f"\nTotal: {total} chunks indexados de {len(items)} documentos")


# URLs de IBM Docs (SPA) para indexar vía Playwright. Se usa para watsonx.ai, que NO
# está en github.com/ibm-cloud-docs. Para productos de cloud.ibm.com usa ingest_github().
DEFAULT_URLS = [
    # --- watsonx.ai (www.ibm.com/docs) ---
    "https://www.ibm.com/docs/en/watsonx/saas?topic=overview-watsonx",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-retrieval-augmented-generation",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-supported-foundation-models",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=models-choosing-model",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=models-third-party-foundation",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=models-prompting-granite-code",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=models-slate-125m-v2-embedding-model-card",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=prompts-prompt-lab",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=ai-prompt-foundation-model-prompt-lab",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=resources-python-library",
    "https://www.ibm.com/docs/en/watsonx/saas?topic=governing-ai",
]

if __name__ == "__main__":
    # Modo GitHub:  python scraper.py --github [max_archivos_por_producto]
    #               (indexa productos de cloud.ibm.com desde github.com/ibm-cloud-docs)
    # Modo SPA:     python scraper.py [url1 url2 ...]   (Playwright; watsonx por defecto)
    if len(sys.argv) > 1 and sys.argv[1] == "--github":
        max_files = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_FILES_PER_PRODUCT
        ingest_github(max_files=max_files)
    else:
        urls = sys.argv[1:] or DEFAULT_URLS
        scrape_and_ingest(urls)
