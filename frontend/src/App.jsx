import { useState, useEffect, useRef } from 'react'
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
  IconButton,
  InlineLoading,
  Tile,
  Tag,
  Dropdown,
  Accordion,
  AccordionItem,
  FileUploaderDropContainer,
  InlineNotification,
  ProgressBar,
  Modal,
} from '@carbon/react'
import {
  Send,
  WatsonHealthTextAnnotationToggle,
  ThumbsUp,
  ThumbsDown,
  Copy,
  Checkmark,
  Add,
  Login,
  Logout,
  UserAvatar,
  TrashCan,
} from '@carbon/icons-react'
import { initAuth, login, logout } from './auth'
import SlideDeck from './SlideDeck'
import ConceptMap from './ConceptMap'
import './App.css'

const API_BASE = (
  (typeof window !== 'undefined' && window.__API_URL__) ||
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000'
).replace(/\/+$/, '')

const STORAGE_KEY = 'ika-chat'      // conversación persistida en este navegador
const HISTORY_TURNS = 6             // turnos previos enviados al backend
const VISIBLE_TURNS = 2             // intercambios visibles; el resto va al desplegable

const PRODUCTS = [
  { id: 'all', label: null },
  { id: 'watsonx', label: 'watsonx.ai' },
  { id: 'vpc', label: 'VPC' },
  { id: 'messages-for-rabbitmq', label: 'RabbitMQ' },
  { id: 'containers', label: 'Kubernetes' },
  { id: 'codeengine', label: 'Code Engine' },
  { id: 'cloud-object-storage', label: 'Object Storage' },
  { id: 'databases-for-postgresql', label: 'Databases (PostgreSQL)' },
]

const T = {
  es: {
    product: 'Knowledge Agent',
    langPrefix: 'Idioma',
    title: 'Pregunta a la documentación de IBM',
    subtitle: 'RAG sobre IBM Docs con watsonx.ai · con memoria de conversación',
    questionLabel: 'Tu pregunta',
    placeholder: 'Ej: ¿Cómo elijo un foundation model en watsonx.ai?',
    ask: 'Preguntar',
    asking: 'Consultando watsonx...',
    tryLabel: 'Prueba:',
    productLabel: 'Producto',
    allProducts: 'Todos los productos',
    newChat: 'Nueva conversación',
    you: 'Tú',
    signIn: 'Iniciar sesión',
    signOut: 'Salir',
    greeting: 'Hola',
    landingHeadline: 'Tu asistente de la documentación de IBM',
    landingDesc:
      'Pregunta en lenguaje natural y obtén respuestas confiables, siempre citando la fuente, sobre los productos de IBM Cloud.',
    landingB1: 'Respuestas con la fuente, sin alucinar',
    landingB2: 'Cubre watsonx, VPC, Kubernetes, Code Engine y más',
    landingB3: 'Con memoria de conversación y subida de tus PDFs',
    landingSignIn: 'Iniciar sesión con IBMid',
    landingGuest: 'Explorar sin iniciar sesión',
    authErrorTitle: 'No se pudo completar el inicio de sesión',
    history: 'Historial de la conversación',
    olderMsgs: 'intercambios anteriores',
    myConversations: 'Mis conversaciones',
    noConversations: 'Aún no tienes conversaciones guardadas.',
    deleteConversation: 'Borrar conversación',
    untitledConversation: 'Conversación sin título',
    errorTitle: 'Error',
    connectError: 'No se pudo conectar con el backend',
    serverError: (s) => `El servidor respondió ${s}`,
    noMatchTitle: 'Sin coincidencias relevantes',
    noMatchSub:
      'La base de conocimiento no contiene información que supere el umbral de relevancia para esta pregunta.',
    answer: 'Respuesta',
    sources: 'Fuentes',
    similarity: 'similitud',
    low: 'baja',
    copy: 'Copiar',
    copied: '¡Copiado!',
    helpful: '¿Te sirvió?',
    thanks: '¡Gracias!',
    ingestTitle: 'Indexar un PDF',
    ingestHelp: 'Sube un documento técnico para ampliar la base de conocimiento.',
    uploadLabel: 'Arrastra un PDF aquí o haz clic para subir',
    uploadingPhase: (n) => `Subiendo ${n}…`,
    uploadingBytes: (loaded, total) => `Subiendo… ${formatMB(loaded)} MB / ${formatMB(total)} MB`,
    receivedPhase: (n) => `Recibido ${n}, guardando…`,
    phaseStoring: 'Guardando en Object Storage…',
    phaseExtracting: 'Extrayendo texto…',
    processingPhase: (n) => `Procesando ${n}…`,
    chunksIndexed: 'fragmentos indexados',
    indexError: 'Error al indexar',
    statusDone: 'Listo',
    statusProcessing: 'Procesando',
    cancel: 'Cancelar',
    cancelTitle: '¿Cancelar la indexación?',
    cancelBody:
      'Se detendrá el proceso y los fragmentos ya cargados de este archivo se descartarán. Puedes volver a subirlo cuando quieras.',
    cancelConfirm: 'Sí, cancelar',
    cancelKeep: 'Seguir indexando',
    cancelled: 'Indexación cancelada',
    phrases: [
      'Extrayendo el contenido…',
      'Aprendiendo del archivo…',
      'Buscando cómo enseñarlo…',
      'Organizando el conocimiento…',
      'Generando los vectores…',
    ],
    samples: [
      '¿Cómo elijo un foundation model en watsonx.ai?',
      '¿Qué es el patrón RAG y cómo funciona?',
      '¿Qué es una VPC en IBM Cloud?',
    ],
    orgDemoTitle: 'Datos de demostración',
    orgDemoSubtitle:
      'Este organigrama usa datos de ejemplo. Se conectará al directorio real próximamente.',
    orgYou: 'Tú',
    modeLabel: 'Modo',
    modeStandard: 'Estándar',
    modeEmail: 'Correo',
    modeCampaign: 'Campaña',
    modePresentation: 'Presentación',
    modeConceptMap: 'Mapa conceptual',
    conceptmapFallback: 'No se pudo renderizar el mapa conceptual',
    audienceLabel: 'Audiencia',
    audienceExecutive: 'Ejecutiva',
    audienceTechnical: 'Técnica',
    audienceSales: 'Comercial',
    slidesLabel: 'Slides',
    downloadPptx: 'Descargar .pptx',
    downloadingPptx: 'Descargando…',
    downloadPptxError: 'No se pudo generar el .pptx',
    themeLabel: 'Tema',
    themeDark: 'Oscuro',
    themeLight: 'Claro',
  },
  en: {
    product: 'Knowledge Agent',
    langPrefix: 'Language',
    title: 'Ask the IBM documentation',
    subtitle: 'RAG over IBM Docs with watsonx.ai · with conversation memory',
    questionLabel: 'Your question',
    placeholder: 'E.g.: How do I choose a foundation model in watsonx.ai?',
    ask: 'Ask',
    asking: 'Querying watsonx...',
    tryLabel: 'Try:',
    productLabel: 'Product',
    allProducts: 'All products',
    newChat: 'New conversation',
    you: 'You',
    signIn: 'Sign in',
    signOut: 'Sign out',
    greeting: 'Hi',
    landingHeadline: 'Your IBM documentation assistant',
    landingDesc:
      'Ask in plain language and get trustworthy answers, always citing the source, about IBM Cloud products.',
    landingB1: 'Answers with sources, no hallucinations',
    landingB2: 'Covers watsonx, VPC, Kubernetes, Code Engine and more',
    landingB3: 'Conversation memory and your own PDF uploads',
    landingSignIn: 'Sign in with IBMid',
    landingGuest: 'Explore without signing in',
    authErrorTitle: 'Could not complete sign-in',
    history: 'Conversation history',
    olderMsgs: 'earlier exchanges',
    myConversations: 'My conversations',
    noConversations: "You don't have any saved conversations yet.",
    deleteConversation: 'Delete conversation',
    untitledConversation: 'Untitled conversation',
    errorTitle: 'Error',
    connectError: 'Could not connect to the backend',
    serverError: (s) => `The server responded ${s}`,
    noMatchTitle: 'No relevant matches',
    noMatchSub:
      'The knowledge base has no information above the relevance threshold for this question.',
    answer: 'Answer',
    sources: 'Sources',
    similarity: 'similarity',
    low: 'low',
    copy: 'Copy',
    copied: 'Copied!',
    helpful: 'Helpful?',
    thanks: 'Thanks!',
    ingestTitle: 'Index a PDF',
    ingestHelp: 'Upload a technical document to expand the knowledge base.',
    uploadLabel: 'Drag a PDF here or click to upload',
    uploadingPhase: (n) => `Uploading ${n}…`,
    uploadingBytes: (loaded, total) => `Uploading… ${formatMB(loaded)} MB / ${formatMB(total)} MB`,
    receivedPhase: (n) => `Received ${n}, saving…`,
    phaseStoring: 'Saving to Object Storage…',
    phaseExtracting: 'Extracting text…',
    processingPhase: (n) => `Processing ${n}…`,
    chunksIndexed: 'chunks indexed',
    indexError: 'Indexing error',
    statusDone: 'Done',
    statusProcessing: 'Processing',
    cancel: 'Cancel',
    cancelTitle: 'Cancel indexing?',
    cancelBody:
      'The process will stop and the chunks already loaded for this file will be discarded. You can upload it again anytime.',
    cancelConfirm: 'Yes, cancel',
    cancelKeep: 'Keep indexing',
    cancelled: 'Indexing cancelled',
    phrases: [
      'Extracting the content…',
      'Learning from the file…',
      'Figuring out how to teach it…',
      'Organizing the knowledge…',
      'Generating the vectors…',
    ],
    samples: [
      'How do I choose a foundation model in watsonx.ai?',
      'What is the RAG pattern and how does it work?',
      'What is a VPC in IBM Cloud?',
    ],
    orgDemoTitle: 'Demo data',
    orgDemoSubtitle:
      'This org chart uses sample data. It will connect to the real directory soon.',
    orgYou: 'You',
    modeLabel: 'Mode',
    modeStandard: 'Standard',
    modeEmail: 'Email',
    modeCampaign: 'Campaign',
    modePresentation: 'Presentation',
    modeConceptMap: 'Concept map',
    conceptmapFallback: 'Could not render the concept map',
    audienceLabel: 'Audience',
    audienceExecutive: 'Executive',
    audienceTechnical: 'Technical',
    audienceSales: 'Sales',
    slidesLabel: 'Slides',
    downloadPptx: 'Download .pptx',
    downloadingPptx: 'Downloading…',
    downloadPptxError: 'Could not generate the .pptx',
    themeLabel: 'Theme',
    themeDark: 'Dark',
    themeLight: 'Light',
  },
}

const uid = () => Math.random().toString(36).slice(2)

function similarityColor(source) {
  if (!source.relevant) return 'gray'
  if (source.similarity >= 0.85) return 'green'
  return 'teal'
}

function sourceLabel(source) {
  const gh = source.match(/ibm-cloud-docs\/([^/]+)\/blob\/[^/]+\/(.+)\.md$/)
  if (gh) return `${gh[1]} · ${gh[2]}`
  if (source.includes('topic=')) {
    const topic = source.split('topic=').pop()
    return source.includes('/watsonx/') ? `watsonx · ${topic}` : topic
  }
  return source
}

function formatMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(1)
}

function browserLang() {
  return typeof navigator !== 'undefined' && navigator.language?.startsWith('en')
    ? 'en'
    : 'es'
}

function loadMessages() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY))
    if (Array.isArray(saved)) return saved.map((m) => ({ ...m, streaming: false }))
  } catch {
    /* ignore */
  }
  return []
}

function App() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState(loadMessages)
  const [language, setLanguage] = useState('auto')
  const [mode, setMode] = useState('standard') // standard|email|campaign|presentation|conceptmap
  // Opciones de presentación (solo aplican cuando mode === 'presentation')
  const [presAudience, setPresAudience] = useState('executive')
  const [presSlides, setPresSlides] = useState(6)
  const [presTheme, setPresTheme] = useState('dark') // 'dark' | 'light'
  const [product, setProduct] = useState('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [copiedId, setCopiedId] = useState(null)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [authUser, setAuthUser] = useState(null) // {name, email, token} | null
  const [authError, setAuthError] = useState(null)
  const [guest, setGuest] = useState(false) // "explorar sin iniciar sesión"

  // Memoria persistente en BD (solo usuarios autenticados). Anónimos: null siempre,
  // no se manda `conversation_id` y no se toca esta lista.
  const [conversationId, setConversationId] = useState(null)
  const [conversations, setConversations] = useState([])

  // Ingesta de PDF
  const [ingestMsg, setIngestMsg] = useState(null)
  const [ingestPhase, setIngestPhase] = useState(null)
  // Progreso real de bytes en la fase 'uploading'. `pct` queda topado a 95 mientras
  // el servidor no confirme (línea NDJSON "start") que ya tiene el archivo completo:
  // el evento nativo `xhr.upload.onprogress` solo mide bytes entregados al buffer
  // de red del SO, no bytes recibidos/procesados por el backend.
  const [uploadStats, setUploadStats] = useState({ loaded: 0, total: 0, pct: 0, indeterminate: false })
  const [ingestProgress, setIngestProgress] = useState({ done: 0, total: 0 })
  const [ingestFile, setIngestFile] = useState('')
  const [cancelModal, setCancelModal] = useState(false)
  const [phraseIdx, setPhraseIdx] = useState(0)
  const abortRef = useRef(null)

  const uiLang = language === 'auto' ? browserLang() : language
  const t = T[uiLang]

  const langItems = [
    { id: 'auto', label: `${t.langPrefix}: Auto` },
    { id: 'es', label: `${t.langPrefix}: Español` },
    { id: 'en', label: `${t.langPrefix}: English` },
  ]
  const modeItems = [
    { id: 'standard', label: `${t.modeLabel}: ${t.modeStandard}` },
    { id: 'email', label: `${t.modeLabel}: ${t.modeEmail}` },
    { id: 'campaign', label: `${t.modeLabel}: ${t.modeCampaign}` },
    { id: 'presentation', label: `${t.modeLabel}: ${t.modePresentation}` },
    { id: 'conceptmap', label: `${t.modeLabel}: ${t.modeConceptMap}` },
  ]
  const audienceItems = [
    { id: 'executive', label: `${t.audienceLabel}: ${t.audienceExecutive}` },
    { id: 'technical', label: `${t.audienceLabel}: ${t.audienceTechnical}` },
    { id: 'sales', label: `${t.audienceLabel}: ${t.audienceSales}` },
  ]
  const slidesItems = [4, 6, 8, 10].map((n) => ({
    id: String(n),
    label: `${t.slidesLabel}: ${n}`,
  }))
  const themeItems = [
    { id: 'dark',  label: `${t.themeLabel}: ${t.themeDark}` },
    { id: 'light', label: `${t.themeLabel}: ${t.themeLight}` },
  ]
  const productItems = PRODUCTS.map((p) => ({
    id: p.id,
    label: p.id === 'all' ? t.allProducts : p.label,
  }))

  // Inicializa la autenticación (lee /auth/config, procesa el callback del login).
  useEffect(() => {
    initAuth()
      .then(({ enabled, user, error }) => {
        setAuthEnabled(enabled)
        setAuthUser(user || null)
        if (error) setAuthError(error)
      })
      .catch(() => setAuthEnabled(false))
  }, [])

  // Cabeceras con el token (si hay sesión) para las llamadas a la API.
  function authHeader() {
    return authUser?.token ? { Authorization: `Bearer ${authUser.token}` } : {}
  }

  // Lista de conversaciones guardadas del usuario (solo con sesión iniciada).
  async function refreshConversations() {
    if (!authUser) return
    try {
      const res = await fetch(`${API_BASE}/conversations`, { headers: { ...authHeader() } })
      if (!res.ok) return
      setConversations(await res.json())
    } catch {
      /* best-effort: la lista de conversaciones no es crítica */
    }
  }

  // Al iniciar sesión, carga la lista de conversaciones guardadas en la BD.
  useEffect(() => {
    if (authUser) refreshConversations()
    else setConversations([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authUser])

  // Carga una conversación guardada (reconstruye los turnos user/assistant).
  async function loadConversation(id) {
    if (loading) return
    try {
      const res = await fetch(`${API_BASE}/conversations/${id}`, { headers: { ...authHeader() } })
      if (!res.ok) return
      const data = await res.json()
      let lastQuestion = ''
      const loaded = (data.messages || []).map((m) => {
        if (m.role === 'user') {
          lastQuestion = m.content
          return { id: uid(), role: 'user', content: m.content }
        }
        return {
          id: uid(),
          role: 'assistant',
          q: lastQuestion,
          content: m.content,
          sources: m.sources || [],
          // Restaura el estado de relevancia real: sin fuentes = chitchat/ok;
          // con fuentes, relevante solo si alguna superó el umbral.
          relevant: (m.sources || []).length === 0 || (m.sources || []).some((s) => s.relevant),
          streaming: false,
          mode: m.mode || 'standard',
          // Metadata persistida ({theme, presentation_opts} en modo presentación):
          // permite re-exportar el PPTX con el tema original, no el del dropdown actual.
          meta: m.meta || null,
        }
      })
      setMessages(loaded)
      setConversationId(data.id)
      setError(null)
    } catch {
      setError(t.connectError)
    }
  }

  // Borra una conversación guardada; si era la activa, limpia el chat actual.
  async function deleteConversation(id, e) {
    e?.stopPropagation()
    try {
      await fetch(`${API_BASE}/conversations/${id}`, {
        method: 'DELETE',
        headers: { ...authHeader() },
      })
    } catch {
      /* best-effort */
    }
    setConversations((prev) => prev.filter((c) => c.id !== id))
    if (conversationId === id) {
      setMessages([])
      setConversationId(null)
    }
  }

  // Persistencia: guarda la conversación en el navegador (sobrevive recargas).
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages))
  }, [messages])

  // Frases rotativas durante el procesamiento del PDF.
  useEffect(() => {
    if (ingestPhase !== 'processing') return undefined
    const id = setInterval(() => setPhraseIdx((i) => (i + 1) % t.phrases.length), 2200)
    return () => clearInterval(id)
  }, [ingestPhase, t.phrases.length])

  function patchMessage(id, patch) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    )
  }

  async function ask(q) {
    const query = (q ?? question).trim()
    if (!query || loading) return
    setQuestion('')
    setError(null)

    // Historial = últimos turnos previos, en el formato del backend.
    const history = messages
      .filter((m) => m.content)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.content }))

    const aId = uid()
    const currentMode = mode
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: 'user', content: query },
      {
        id: aId, role: 'assistant', q: query, content: '', sources: [], relevant: true, streaming: true, mode: currentMode,
        // Igual que al recargar de BD: el deck queda ligado al theme con que se generó.
        ...(currentMode === 'presentation'
          ? { meta: { theme: presTheme, presentation_opts: { audience: presAudience, slides: presSlides } } }
          : {}),
      },
    ])
    setLoading(true)
    // El intercambio nuevo se renderiza arriba: llevamos la vista al tope.
    setTimeout(() => window.scrollTo({ top: 0, behavior: 'smooth' }), 60)
    try {
      const res = await fetch(`${API_BASE}/query_stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({
          question: query,
          language,
          mode: currentMode,
          products: product === 'all' ? null : [product],
          history,
          // Solo con sesión iniciada: liga el turno a una conversación persistida en BD.
          ...(authUser ? { conversation_id: conversationId } : {}),
          // Opciones de presentación: solo se envían en modo presentación.
          // `theme` viaja aparte porque no afecta la generación (solo el PPTX);
          // el backend lo persiste en messages.meta para re-exportar con el tema original.
          ...(currentMode === 'presentation'
            ? { presentation_opts: { audience: presAudience, slides: presSlides }, theme: presTheme }
            : {}),
        }),
      })
      if (!res.ok || !res.body) throw new Error(t.serverError(res.status))
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let acc = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let nl
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, nl).trim()
          buffer = buffer.slice(nl + 1)
          if (!line) continue
          const m = JSON.parse(line)
          if (m.type === 'conversation') {
            setConversationId(m.conversation_id)
          } else if (m.type === 'meta') {
            patchMessage(aId, {
              sources: m.sources || [],
              relevant: m.relevant !== false,
              // mode confirmado por el backend (por si cambia entre el envío y la respuesta)
              ...(m.mode ? { mode: m.mode } : {}),
            })
          } else if (m.type === 'token') {
            acc += m.text
            patchMessage(aId, { content: acc })
          }
        }
      }
      patchMessage(aId, { streaming: false })
      // Refresca la lista (título/orden) ahora que el backend guardó el turno.
      if (authUser) refreshConversations()
    } catch (e) {
      patchMessage(aId, { streaming: false, content: '', error: e.message || t.connectError })
      setError(e.message || t.connectError)
    } finally {
      setLoading(false)
    }
  }

  function newChat() {
    if (loading) return
    setMessages([])
    setError(null)
    localStorage.removeItem(STORAGE_KEY)
    if (authUser) setConversationId(null)
  }

  async function sendFeedback(msg, rating) {
    patchMessage(msg.id, { rating })
    try {
      await fetch(`${API_BASE}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({ question: msg.q, answer: msg.content, rating, language: uiLang }),
      })
    } catch {
      /* best-effort */
    }
  }

  async function copyAnswer(msg) {
    try {
      await navigator.clipboard.writeText(msg.content)
      setCopiedId(msg.id)
      setTimeout(() => setCopiedId(null), 1500)
    } catch {
      /* ignore */
    }
  }

  function handleUpload(files) {
    const file = files?.[0]
    if (!file) return
    setIngestMsg(null)
    setIngestFile(file.name)
    setPhraseIdx(0)
    setUploadStats({ loaded: 0, total: file.size || 0, pct: 0, indeterminate: false })
    setIngestProgress({ done: 0, total: 0 })
    setIngestPhase('uploading')

    const xhr = new XMLHttpRequest()
    abortRef.current = xhr
    xhr.open('POST', `${API_BASE}/ingest_stream`)
    if (authUser?.token) xhr.setRequestHeader('Authorization', `Bearer ${authUser.token}`)
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) {
        setUploadStats((prev) => ({ ...prev, indeterminate: true }))
        return
      }
      // Topado a 95%: el 100% real solo llega cuando el servidor confirma
      // (línea NDJSON "start"), no cuando el SO terminó de entregar bytes.
      const rawPct = e.total > 0 ? (e.loaded / e.total) * 100 : 0
      setUploadStats({ loaded: e.loaded, total: e.total, pct: Math.min(95, Math.round(rawPct)), indeterminate: false })
    }
    let lastIdx = 0
    xhr.onprogress = () => {
      const text = xhr.responseText
      const fresh = text.slice(lastIdx)
      const nlPos = fresh.lastIndexOf('\n')
      if (nlPos === -1) return
      lastIdx += nlPos + 1
      for (const line of fresh.slice(0, nlPos).split('\n')) {
        const trimmed = line.trim()
        if (!trimmed) continue
        let m
        try {
          m = JSON.parse(trimmed)
        } catch {
          continue
        }
        if (m.type === 'received') {
          // El servidor confirma que ya tiene TODOS los bytes: recién aquí la
          // fase de subida es honestamente 100%. Lo que sigue (guardar/extraer)
          // no tiene progreso medible en bytes, así que pasamos a fases con
          // nombre e indeterminadas hasta que llegue "start".
          setUploadStats((prev) => ({ ...prev, pct: 100, indeterminate: false }))
          setIngestPhase('received')
        } else if (m.type === 'phase') {
          if (m.name === 'storing') setIngestPhase('storing')
          else if (m.name === 'extracting') setIngestPhase('extracting')
          // Fases futuras desconocidas: se ignoran (no rompen el flujo).
        } else if (m.type === 'start') {
          setIngestPhase('processing')
          setIngestProgress({ done: 0, total: m.total || 0 })
        } else if (m.type === 'progress') {
          setIngestProgress({ done: m.done || 0, total: m.total || 0 })
        } else if (m.type === 'done') {
          setIngestPhase(null)
          setIngestMsg({ kind: 'success', text: `${m.count} ${t.chunksIndexed}` })
        }
        // Cualquier otro `type` no reconocido se ignora silenciosamente (ver
        // contrato NDJSON de /ingest_stream en docs/GOVERNANCE.md).
      }
    }
    xhr.onerror = () => {
      setIngestPhase(null)
      setIngestMsg({ kind: 'error', text: t.indexError })
    }
    xhr.onloadend = () => {
      abortRef.current = null
    }
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  }

  async function cancelIngest() {
    abortRef.current?.abort()
    setCancelModal(false)
    setIngestPhase(null)
    try {
      await fetch(`${API_BASE}/ingest_cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: ingestFile }),
      })
    } catch {
      /* best-effort */
    }
    setIngestMsg({ kind: 'warning', text: t.cancelled })
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      ask()
    }
  }

  function renderAssistantBody(m) {
    const msgMode = m.mode || 'standard'

    // Durante el streaming: todos los modos muestran el texto acumulado tal cual.
    // Solo conceptmap muestra un cursor diferente (el JSON parcial no es legible).
    if (m.streaming) {
      if (msgMode === 'conceptmap') {
        return (
          <p className="answer-text answer-text--muted">
            <span className="cursor">▋</span>
          </p>
        )
      }
      return (
        <p className="answer-text">
          {m.content}
          <span className="cursor">▋</span>
        </p>
      )
    }

    // Terminó el streaming — renderizado según modo
    if (msgMode === 'presentation') {
      return (
        <SlideDeck
          content={m.content}
          copyLabel={t.copy}
          copiedLabel={t.copied}
          downloadPptxLabel={t.downloadPptx}
          downloadingPptxLabel={t.downloadingPptx}
          downloadPptxErrorLabel={t.downloadPptxError}
          apiBase={API_BASE}
          theme={m.meta?.theme || presTheme}
          presenter={authUser ? { name: authUser.name, role: 'IBM Cloud', email: authUser.email } : undefined}
        />
      )
    }
    if (msgMode === 'conceptmap') {
      return (
        <ConceptMap
          content={m.content}
          fallbackNote={t.conceptmapFallback}
        />
      )
    }
    // standard | email | campaign — texto tal cual (pre-wrap)
    return <p className="answer-text">{m.content}</p>
  }

  function renderAssistant(m) {
    return (
      <>
        {!m.streaming && !m.relevant && m.content && (
          <InlineNotification
            kind="warning"
            title={t.noMatchTitle}
            subtitle={t.noMatchSub}
            lowContrast
            hideCloseButton
          />
        )}
        <Tile className="answer-tile">
          <div className="answer-header">
            <WatsonHealthTextAnnotationToggle size={20} />
            <span>{t.answer}</span>
          </div>
          {renderAssistantBody(m)}
          {!m.streaming && m.content && (m.mode || 'standard') !== 'presentation' && (
            <div className="answer-footer">
              <IconButton
                label={copiedId === m.id ? t.copied : t.copy}
                kind="ghost"
                size="sm"
                onClick={() => copyAnswer(m)}
              >
                {copiedId === m.id ? <Checkmark /> : <Copy />}
              </IconButton>
              {m.rating ? (
                <span className="feedback-thanks">{t.thanks}</span>
              ) : (
                <>
                  <span className="feedback-label">{t.helpful}</span>
                  <IconButton label="👍" kind="ghost" size="sm" onClick={() => sendFeedback(m, 'up')}>
                    <ThumbsUp />
                  </IconButton>
                  <IconButton label="👎" kind="ghost" size="sm" onClick={() => sendFeedback(m, 'down')}>
                    <ThumbsDown />
                  </IconButton>
                </>
              )}
            </div>
          )}
        </Tile>
        {m.sources?.length > 0 && (
          <div className="sources">
            <h3>{t.sources}</h3>
            <Accordion>
              {m.sources.map((s, i) => (
                <AccordionItem
                  key={i}
                  title={
                    <span className="source-title">
                      {sourceLabel(s.source)}
                      <Tag type={similarityColor(s)} size="sm">
                        {(s.similarity * 100).toFixed(1)}% {t.similarity}
                        {!s.relevant && ` · ${t.low}`}
                      </Tag>
                    </span>
                  }
                >
                  <p className="source-content">{s.content}</p>
                  <a
                    href={
                      s.source.startsWith('http')
                        ? s.source
                        : `${API_BASE}/files/${encodeURIComponent(s.source)}`
                    }
                    target="_blank"
                    rel="noreferrer"
                  >
                    {s.source}
                  </a>
                </AccordionItem>
              ))}
            </Accordion>
          </div>
        )}
      </>
    )
  }

  function renderTurn(turn, key) {
    return (
      <div className="turn" key={turn.user?.id || turn.assistant?.id || key}>
        {turn.user && (
          <div className="msg msg-user">
            <span className="msg-role">{t.you}</span>
            <p>{turn.user.content}</p>
          </div>
        )}
        {turn.assistant && renderAssistant(turn.assistant)}
      </div>
    )
  }

  // Agrupa los mensajes en intercambios (pregunta + respuesta) y los ordena con el
  // más reciente primero, para que el último quede pegado al input.
  const turns = []
  for (const m of messages) {
    if (m.role === 'user') turns.push({ user: m, assistant: null })
    else if (turns.length) turns[turns.length - 1].assistant = m
    else turns.push({ user: null, assistant: m })
  }
  const reversedTurns = [...turns].reverse()
  const recentTurns = reversedTurns.slice(0, VISIBLE_TURNS)
  const olderTurns = reversedTurns.slice(VISIBLE_TURNS)
  const olderCount = olderTurns.length

  // Landing: si el login está activo y aún no entraste (ni como invitado).
  const showLanding = authEnabled && !authUser && !guest

  return (
    <Theme theme="g100">
      <Header aria-label="IBM Knowledge Agent">
        <HeaderName href="/" prefix="IBM">
          {t.product}
        </HeaderName>
        <HeaderGlobalBar>
          <div className="lang-dropdown">
            <Dropdown
              id="language-select"
              size="sm"
              type="inline"
              label={t.langPrefix}
              titleText=""
              hideLabel
              items={langItems}
              itemToString={(i) => (i ? i.label : '')}
              selectedItem={langItems.find((l) => l.id === language)}
              onChange={({ selectedItem }) => setLanguage(selectedItem.id)}
            />
          </div>
          <div className="lang-dropdown">
            <Dropdown
              id="mode-select"
              size="sm"
              type="inline"
              label={t.modeLabel}
              titleText=""
              hideLabel
              items={modeItems}
              itemToString={(i) => (i ? i.label : '')}
              selectedItem={modeItems.find((m) => m.id === mode)}
              onChange={({ selectedItem }) => setMode(selectedItem.id)}
            />
          </div>
          {mode === 'presentation' && (
            <>
              <div className="lang-dropdown">
                <Dropdown
                  id="audience-select"
                  size="sm"
                  type="inline"
                  label={t.audienceLabel}
                  titleText=""
                  hideLabel
                  items={audienceItems}
                  itemToString={(i) => (i ? i.label : '')}
                  selectedItem={audienceItems.find((a) => a.id === presAudience)}
                  onChange={({ selectedItem }) => setPresAudience(selectedItem.id)}
                />
              </div>
              <div className="lang-dropdown">
                <Dropdown
                  id="slides-select"
                  size="sm"
                  type="inline"
                  label={t.slidesLabel}
                  titleText=""
                  hideLabel
                  items={slidesItems}
                  itemToString={(i) => (i ? i.label : '')}
                  selectedItem={slidesItems.find((s) => s.id === String(presSlides))}
                  onChange={({ selectedItem }) => setPresSlides(Number(selectedItem.id))}
                />
              </div>
              <div className="lang-dropdown">
                <Dropdown
                  id="pptx-theme-select"
                  size="sm"
                  type="inline"
                  label={t.themeLabel}
                  titleText=""
                  hideLabel
                  items={themeItems}
                  itemToString={(i) => (i ? i.label : '')}
                  selectedItem={themeItems.find((th) => th.id === presTheme)}
                  onChange={({ selectedItem }) => setPresTheme(selectedItem.id)}
                />
              </div>
            </>
          )}
          {authEnabled && (
            <div className="auth-controls">
              {authUser ? (
                <>
                  <span className="auth-user">
                    <UserAvatar size={16} />
                    {t.greeting}, {authUser.name}
                  </span>
                  <Button kind="ghost" size="sm" renderIcon={Logout} onClick={logout}>
                    {t.signOut}
                  </Button>
                </>
              ) : (
                <Button kind="ghost" size="sm" renderIcon={Login} onClick={login}>
                  {t.signIn}
                </Button>
              )}
            </div>
          )}
        </HeaderGlobalBar>
      </Header>

      {showLanding ? (
        <Content className="landing-content">
          <div className="landing">
            <h1 className="landing-headline">{t.landingHeadline}</h1>
            <p className="landing-desc">{t.landingDesc}</p>
            <ul className="landing-bullets">
              <li>{t.landingB1}</li>
              <li>{t.landingB2}</li>
              <li>{t.landingB3}</li>
            </ul>
            {authError && (
              <InlineNotification
                kind="error"
                title={t.authErrorTitle}
                subtitle={authError}
                lowContrast
                onCloseButtonClick={() => setAuthError(null)}
              />
            )}
            <div className="landing-actions">
              <Button renderIcon={Login} onClick={login}>
                {t.landingSignIn}
              </Button>
              <Button kind="ghost" onClick={() => setGuest(true)}>
                {t.landingGuest}
              </Button>
            </div>
          </div>
        </Content>
      ) : (
      <Content className="app-content">
        <Grid>
          <Column lg={10} md={6} sm={4}>
            <div className="title-row">
              <div>
                <h1 className="app-title">{t.title}</h1>
                <p className="app-subtitle">{t.subtitle}</p>
              </div>
              {messages.length > 0 && (
                <Button kind="ghost" size="sm" renderIcon={Add} onClick={newChat} disabled={loading}>
                  {t.newChat}
                </Button>
              )}
            </div>

            {authUser && (
              <Accordion className="my-conversations-accordion">
                <AccordionItem title={`${t.myConversations} · ${conversations.length}`}>
                  {conversations.length === 0 ? (
                    <p className="no-conversations">{t.noConversations}</p>
                  ) : (
                    <ul className="conversation-list">
                      {conversations.map((c) => (
                        <li
                          key={c.id}
                          className={`conversation-item${c.id === conversationId ? ' active' : ''}`}
                        >
                          <button
                            type="button"
                            className="conversation-title"
                            onClick={() => loadConversation(c.id)}
                          >
                            {c.title || t.untitledConversation}
                          </button>
                          <IconButton
                            label={t.deleteConversation}
                            kind="ghost"
                            size="sm"
                            onClick={(e) => deleteConversation(c.id, e)}
                          >
                            <TrashCan />
                          </IconButton>
                        </li>
                      ))}
                    </ul>
                  )}
                </AccordionItem>
              </Accordion>
            )}

            <TextArea
              labelText={t.questionLabel}
              placeholder={t.placeholder}
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
            />

            <div className="ask-row">
              <Button renderIcon={Send} onClick={() => ask()} disabled={loading || !question.trim()}>
                {t.ask}
              </Button>
              <div className="product-filter">
                <Dropdown
                  id="product-select"
                  size="sm"
                  type="inline"
                  label={t.productLabel}
                  titleText=""
                  hideLabel
                  items={productItems}
                  itemToString={(i) => (i ? i.label : '')}
                  selectedItem={productItems.find((p) => p.id === product)}
                  onChange={({ selectedItem }) => setProduct(selectedItem.id)}
                />
              </div>
            </div>

            {messages.length === 0 && (
              <div className="samples">
                <span className="samples-label">{t.tryLabel}</span>
                {t.samples.map((s) => (
                  <Button
                    key={s}
                    kind="ghost"
                    size="sm"
                    className="sample-btn"
                    disabled={loading}
                    onClick={() => ask(s)}
                  >
                    {s}
                  </Button>
                ))}
              </div>
            )}

            {error && (
              <InlineNotification
                kind="error"
                title={t.errorTitle}
                subtitle={error}
                lowContrast
                onCloseButtonClick={() => setError(null)}
              />
            )}

            <div className="chat-thread">
              {loading && <InlineLoading description={t.asking} className="thread-loading" />}

              {/* Intercambios recientes: el más nuevo arriba, pegado al input */}
              {recentTurns.map((tn, i) => renderTurn(tn, `recent-${i}`))}

              {/* El resto del historial, colapsado para no saturar la vista activa */}
              {olderCount > 0 && (
                <Accordion className="history-accordion">
                  <AccordionItem title={`${t.history} · ${olderCount} ${t.olderMsgs}`}>
                    {olderTurns.map((tn, i) => renderTurn(tn, `old-${i}`))}
                  </AccordionItem>
                </Accordion>
              )}
            </div>
          </Column>

          <Column lg={6} md={2} sm={4}>
            <Tile className="ingest-tile">
              <h3>{t.ingestTitle}</h3>
              <p className="ingest-help">{t.ingestHelp}</p>
              <FileUploaderDropContainer
                accept={['application/pdf']}
                labelText={t.uploadLabel}
                onAddFiles={(_, { addedFiles }) => handleUpload(addedFiles)}
              />

              {ingestPhase === 'uploading' && (
                <div className="ingest-status">
                  <ProgressBar
                    label={t.uploadingPhase(ingestFile)}
                    helperText={
                      uploadStats.indeterminate
                        ? undefined
                        : t.uploadingBytes(uploadStats.loaded, uploadStats.total)
                    }
                    value={uploadStats.indeterminate ? undefined : uploadStats.pct}
                    max={100}
                  />
                </div>
              )}

              {(ingestPhase === 'received' || ingestPhase === 'storing' || ingestPhase === 'extracting') && (
                <div className="ingest-status">
                  <ProgressBar
                    label={
                      ingestPhase === 'storing'
                        ? t.phaseStoring
                        : ingestPhase === 'extracting'
                          ? t.phaseExtracting
                          : t.receivedPhase(ingestFile)
                    }
                    helperText={undefined}
                    value={undefined}
                    max={100}
                  />
                  <Button kind="danger" size="sm" onClick={() => setCancelModal(true)}>
                    {t.cancel}
                  </Button>
                </div>
              )}

              {ingestPhase === 'processing' && (
                <div className="ingest-status">
                  <ProgressBar
                    label={t.processingPhase(ingestFile)}
                    helperText={
                      ingestProgress.total > 0
                        ? `${ingestProgress.done}/${ingestProgress.total}`
                        : '…'
                    }
                    value={ingestProgress.total > 0 ? ingestProgress.done : undefined}
                    max={ingestProgress.total || undefined}
                  />
                  <p className="ingest-phrase" key={phraseIdx}>
                    {t.phrases[phraseIdx]}
                  </p>
                  <Button kind="danger" size="sm" onClick={() => setCancelModal(true)}>
                    {t.cancel}
                  </Button>
                </div>
              )}

              {ingestMsg && (
                <InlineNotification
                  kind={ingestMsg.kind}
                  title={
                    ingestMsg.kind === 'success'
                      ? t.statusDone
                      : ingestMsg.kind === 'error'
                      ? t.errorTitle
                      : ingestMsg.kind === 'warning'
                      ? t.cancelled
                      : t.statusProcessing
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
      )}

      <Modal
        open={cancelModal}
        danger
        modalHeading={t.cancelTitle}
        primaryButtonText={t.cancelConfirm}
        secondaryButtonText={t.cancelKeep}
        onRequestSubmit={cancelIngest}
        onRequestClose={() => setCancelModal(false)}
      >
        <p>{t.cancelBody}</p>
      </Modal>
    </Theme>
  )
}

export default App
