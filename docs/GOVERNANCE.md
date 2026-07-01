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
- **Datos:** tabla `documents(id, content, embedding vector(768), source, created_at)`
  y `feedback(...)`. Fuente (`source`) = URL de GitHub o de IBM Docs.
- **Embeddings:** `ibm/granite-embedding-278m-multilingual` (768 dim). Cambiarlo
  invalida los vectores existentes → requiere reindexar (ADR obligatorio).

## 7. Riesgos / restricciones conocidas

- **Cuota watsonx** (plan Lite = 300k tokens/mes). Toda ingesta masiva la aprueba el orquestador.
- El **backend desplegado** debe redeployarse con credenciales nuevas tras rotarlas.
- IBM Docs (cloud.ibm.com) tiene WAF → el contenido se toma de GitHub `ibm-cloud-docs`.

## 8. Roadmap (alto nivel)

- **Fase 1 (actual):** RAG multi-producto sólido (hecho/en curso).
- **Fase 2:** Asset Hub — `area`/`asset_type`, CSM + Client Engineering (ver `docs/asset-hub-taxonomy.md`).
- **Fase 3:** auth, dashboard de uso/feedback, integración Seismic, reranking.
