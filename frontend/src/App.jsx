import { useState } from 'react'
import {
  Theme,
  Header,
  HeaderName,
  HeaderGlobalBar,
  Content,
  Grid,
  Column,
  TextArea,
  Button,
  InlineLoading,
  Tile,
  Tag,
  Dropdown,
  Accordion,
  AccordionItem,
  FileUploaderDropContainer,
  InlineNotification,
} from '@carbon/react'
import { Send, WatsonHealthTextAnnotationToggle } from '@carbon/icons-react'
import './App.css'

// Prioridad: config inyectada en runtime (contenedor) > variable de build de Vite > localhost.
// Se quita cualquier barra final para evitar URLs con doble slash (...cloud//query → 404).
const API_BASE = (
  (typeof window !== 'undefined' && window.__API_URL__) ||
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000'
).replace(/\/+$/, '')

const SAMPLE_QUESTIONS = [
  'How do I choose a foundation model in watsonx.ai?',
  '¿Qué es el patrón RAG y cómo funciona?',
  'What can I do with the Prompt Lab?',
]

const LANGUAGES = [
  { id: 'auto', label: 'Idioma: Auto' },
  { id: 'es', label: 'Idioma: Español' },
  { id: 'en', label: 'Idioma: English' },
]

function similarityColor(source) {
  // El color refleja la relevancia real (según el umbral del backend), no el % crudo.
  if (!source.relevant) return 'gray'
  if (source.similarity >= 0.85) return 'green'
  return 'teal'
}

function sourceLabel(source) {
  // Deriva "producto · tema" desde la URL de IBM Docs para mostrarlo limpio.
  // watsonx:  www.ibm.com/docs/en/watsonx/saas?topic=...   -> producto "watsonx"
  // IBM Cloud: cloud.ibm.com/docs/<producto>?topic=...      -> producto del path
  if (!source.includes('topic=')) return source
  const topic = source.split('topic=').pop()
  let product = ''
  const m = source.match(/\/docs\/(?:en\/)?([^/?]+)/)
  if (m) product = m[1] === 'en' ? 'watsonx' : m[1]
  if (source.includes('/watsonx/')) product = 'watsonx'
  return product ? `${product} · ${topic}` : topic
}

function App() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [sources, setSources] = useState([])
  const [relevant, setRelevant] = useState(true)
  const [language, setLanguage] = useState('auto')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [ingestMsg, setIngestMsg] = useState(null)

  async function ask(q) {
    const query = (q ?? question).trim()
    if (!query) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    setSources([])
    setRelevant(true)
    try {
      const res = await fetch(`${API_BASE}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: query, language }),
      })
      if (!res.ok) throw new Error(`El servidor respondió ${res.status}`)
      const data = await res.json()
      setAnswer(data.answer)
      setSources(data.sources || [])
      setRelevant(data.relevant !== false)
    } catch (e) {
      setError(e.message || 'No se pudo conectar con el backend')
    } finally {
      setLoading(false)
    }
  }

  async function handleUpload(files) {
    const file = files?.[0]
    if (!file) return
    setIngestMsg({ kind: 'info', text: `Indexando ${file.name}...` })
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${API_BASE}/ingest`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(`El servidor respondió ${res.status}`)
      const data = await res.json()
      setIngestMsg({ kind: 'success', text: data.message || 'Documento indexado' })
    } catch (e) {
      setIngestMsg({ kind: 'error', text: e.message || 'Error al indexar' })
    }
  }

  function onKeyDown(e) {
    // Enter envía; Shift+Enter agrega salto de línea.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      ask()
    }
  }

  return (
    <Theme theme="g100">
      <Header aria-label="IBM Knowledge Agent">
        <HeaderName href="/" prefix="IBM">
          Knowledge Agent
        </HeaderName>
        <HeaderGlobalBar>
          <div className="lang-dropdown">
            <Dropdown
              id="language-select"
              size="sm"
              type="inline"
              label="Idioma"
              titleText=""
              hideLabel
              items={LANGUAGES}
              itemToString={(i) => (i ? i.label : '')}
              selectedItem={LANGUAGES.find((l) => l.id === language)}
              onChange={({ selectedItem }) => setLanguage(selectedItem.id)}
            />
          </div>
        </HeaderGlobalBar>
      </Header>

      <Content className="app-content">
        <Grid>
          <Column lg={10} md={6} sm={4}>
            <h1 className="app-title">Pregunta a la documentación de IBM</h1>
            <p className="app-subtitle">
              RAG sobre IBM Docs con watsonx.ai &middot; respuestas citando la fuente
            </p>

            <TextArea
              labelText="Tu pregunta"
              placeholder="Ej: How do I choose a foundation model in watsonx.ai?"
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
            />

            <div className="ask-row">
              <Button
                renderIcon={Send}
                onClick={() => ask()}
                disabled={loading || !question.trim()}
              >
                Preguntar
              </Button>
              {loading && <InlineLoading description="Consultando watsonx..." />}
            </div>

            <div className="samples">
              <span className="samples-label">Prueba:</span>
              {SAMPLE_QUESTIONS.map((s) => (
                <Button
                  key={s}
                  kind="ghost"
                  size="sm"
                  className="sample-btn"
                  disabled={loading}
                  onClick={() => {
                    setQuestion(s)
                    ask(s)
                  }}
                >
                  {s}
                </Button>
              ))}
            </div>

            {error && (
              <InlineNotification
                kind="error"
                title="Error"
                subtitle={error}
                lowContrast
                onCloseButtonClick={() => setError(null)}
              />
            )}

            {answer && !relevant && (
              <InlineNotification
                kind="warning"
                title="Sin coincidencias relevantes"
                subtitle="La base de conocimiento no contiene información que supere el umbral de relevancia para esta pregunta."
                lowContrast
                hideCloseButton
              />
            )}

            {answer && (
              <Tile className="answer-tile">
                <div className="answer-header">
                  <WatsonHealthTextAnnotationToggle size={20} />
                  <span>Respuesta</span>
                </div>
                <p className="answer-text">{answer}</p>
              </Tile>
            )}

            {sources.length > 0 && (
              <div className="sources">
                <h3>Fuentes</h3>
                <Accordion>
                  {sources.map((s, i) => (
                    <AccordionItem
                      key={i}
                      title={
                        <span className="source-title">
                          {sourceLabel(s.source)}
                          <Tag type={similarityColor(s)} size="sm">
                            {(s.similarity * 100).toFixed(1)}% similitud
                            {!s.relevant && ' · baja'}
                          </Tag>
                        </span>
                      }
                    >
                      <p className="source-content">{s.content}</p>
                      <a href={s.source} target="_blank" rel="noreferrer">
                        {s.source}
                      </a>
                    </AccordionItem>
                  ))}
                </Accordion>
              </div>
            )}
          </Column>

          <Column lg={6} md={2} sm={4}>
            <Tile className="ingest-tile">
              <h3>Indexar un PDF</h3>
              <p className="ingest-help">
                Sube un documento técnico para ampliar la base de conocimiento.
              </p>
              <FileUploaderDropContainer
                accept={['application/pdf']}
                labelText="Arrastra un PDF aquí o haz clic para subir"
                onAddFiles={(_, { addedFiles }) => handleUpload(addedFiles)}
              />
              {ingestMsg && (
                <InlineNotification
                  kind={ingestMsg.kind}
                  title={
                    ingestMsg.kind === 'success'
                      ? 'Listo'
                      : ingestMsg.kind === 'error'
                      ? 'Error'
                      : 'Procesando'
                  }
                  subtitle={ingestMsg.text}
                  lowContrast
                  onCloseButtonClick={() => setIngestMsg(null)}
                />
              )}
            </Tile>
          </Column>
        </Grid>
      </Content>
    </Theme>
  )
}

export default App
