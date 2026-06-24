# Asset Hub — Taxonomía por área (v0, para validar con cada equipo)

Documento de trabajo para definir **cómo se clasifican los assets** en el Asset Hub.
Piloto con 2 áreas: **Client Engineering (CE)** y **Customer Success (CSM)**.
Objetivo: llevarlo a cada equipo, validar/ajustar, y usarlo como esquema de metadata.

---

## 1. Metadata común (todos los assets, todas las áreas)

Campos que SIEMPRE existen, independientes del área. Son la base del esquema de datos.

| Campo | Tipo | Ejemplo | Notas |
|---|---|---|---|
| `title` | texto | "RAG MVP para banca con watsonx" | obligatorio |
| `description` | texto | resumen de 1-2 frases | alimenta la búsqueda semántica |
| `area` | enum | `client-engineering` / `csm` | faceta principal |
| `asset_type` | enum | depende del área (ver §2 y §3) | obligatorio |
| `industry` | enum[] | `banking`, `retail`, `public-sector` | multi-valor |
| `ibm_products` | enum[] | `watsonx`, `openshift`, `vpc` | multi-valor |
| `tags` | texto[] | libre, ej. `rag`, `multicloud` | búsqueda/filtro |
| `owner` | texto | nombre / equipo autor | atribución ("quién lo hizo") |
| `maturity` | enum | `proven` / `draft` / `experimental` | calidad/curación |
| `confidentiality` | enum | `public` / `internal` / `client-nda` | control de acceso |
| `source_url` | url | link al asset real (Seismic/GitHub/Box) | NO es un silo nuevo |
| `language` | enum | `es` / `en` | |
| `updated_at` | fecha | | frescura |

### Vocabularios controlados (compartidos)
- **industry:** banking, insurance, retail, healthcare, public-sector, telco, manufacturing, energy, education, cross-industry
- **ibm_products:** watsonx.ai, watsonx.data, watsonx.governance, openshift, cloud-paks, vpc, kubernetes, code-engine, databases, cloud-object-storage, security-qradar, storage, consulting
- **maturity:** `proven` (usado en cliente real, reutilizable), `draft` (incompleto), `experimental` (prueba)
- **confidentiality:** `public`, `internal` (solo IBMers), `client-nda` (requiere permiso/anonimización)

---

## 2. Client Engineering (CE)

> CE construye lo técnico para ganar oportunidades. Dolor: rehacer demos/arquitecturas desde 0.

### Tipos de asset (`asset_type`)
| asset_type | Qué es | "Clonar" produce |
|---|---|---|
| `demo-mvp` | Repo de demo o MVP funcional | base de código para personalizar |
| `reference-architecture` | Diagrama + doc de arquitectura | arquitectura base |
| `poc-report` | Reporte/deck de un POC | plantilla de POC |
| `solution-pattern` | Patrón/acelerador reutilizable | esqueleto de solución |
| `notebook` | Jupyter / data science | notebook base |
| `integration` | Conector/integración | integración adaptable |
| `technical-sow` | SOW/propuesta técnica | borrador de SOW |
| `demo-script` | Guion / talk track de demo | guion adaptable |

### Facetas que más importan en CE
`industry` · `ibm_products` · `solution-pattern` (rag, agents, data-fabric, app-modernization, observability, security) · `tech-stack` (python, react, terraform, ansible…)

### Preguntas para validar con el equipo CE
1. ¿Qué tipos de asset faltan o sobran en la lista de arriba?
2. ¿Cuáles son los 5 patrones de solución más repetidos en sus oportunidades?
3. ¿Dónde viven hoy esos assets (GitHub, Box, drives personales)?
4. ¿Qué hace que un asset sea "reutilizable" vs "de un solo uso"?

---

## 3. Customer Success (CSM)

> CSM mantiene y hace crecer al cliente post-venta. Dolor: rehacer playbooks/planes por cliente.

### Tipos de asset (`asset_type`)
| asset_type | Qué es | "Clonar" produce |
|---|---|---|
| `onboarding-playbook` | Guía de onboarding | playbook adaptable al cliente |
| `success-plan` | Plan de éxito/adopción | plan base |
| `qbr-deck` | Plantilla de QBR/EBR | deck de revisión |
| `health-check` | Framework/scorecard de salud | scorecard reutilizable |
| `renewal-playbook` | Guía de renovación/expansión | playbook de renovación |
| `escalation-runbook` | Runbook de escalación | runbook adaptable |
| `journey-map` | Mapa de viaje del cliente | mapa base |
| `value-story` | Caso de realización de valor | referencia/plantilla |
| `enablement-guide` | Material de capacitación | guía adaptable |

### Facetas que más importan en CSM
`industry` · `customer-stage` (onboarding, adoption, renewal, expansion, at-risk) · `motion` (proactive, reactive, tech-touch, high-touch) · `ibm_products`

### Preguntas para validar con el equipo CSM
1. ¿Qué etapas del ciclo de cliente usan más assets?
2. ¿Qué plantillas reusan SIEMPRE (candidatas estrella para el hub)?
3. ¿Qué tan sensibles son (datos de cliente) → nivel de `confidentiality`?
4. ¿Qué define un playbook "ganador" reutilizable?

---

## 4. Cómo mapea al sistema actual (RAG existente)

Mínimos cambios sobre lo ya construido:
- Tabla `documents` gana columnas: `area`, `asset_type`, `title`, `industry[]`, `ibm_products[]`, `maturity`, `confidentiality`, `source_url`.
- Búsqueda semántica actual (watsonx + pgvector) se mantiene; se le suman **filtros por faceta**.
- Frontend Carbon: **tabs/filtro por área** + badges de `area` y `asset_type` en cada resultado + botón "Clonar y personalizar".

## 5. Ejemplos de registro (mezcla real + sample)

```json
{
  "title": "RAG MVP banca con watsonx + pgvector",
  "area": "client-engineering", "asset_type": "demo-mvp",
  "industry": ["banking"], "ibm_products": ["watsonx.ai"],
  "tags": ["rag","pgvector","carbon"], "maturity": "proven",
  "confidentiality": "internal", "owner": "César C.",
  "source_url": "https://github.ibm.com/.../rag-ibm-docs"
}
```
```json
{
  "title": "Plantilla QBR cliente enterprise",
  "area": "csm", "asset_type": "qbr-deck",
  "industry": ["cross-industry"], "ibm_products": ["watsonx.governance"],
  "tags": ["qbr","renewal"], "maturity": "proven",
  "confidentiality": "internal", "owner": "Equipo CSM"
}
```
