import { useState } from 'react'
import { Button, IconButton } from '@carbon/react'
import { Copy, Checkmark, ChevronLeft, ChevronRight, Download } from '@carbon/icons-react'
import './SlideDeck.css'

/**
 * SlideDeck — renderiza la respuesta en modo "presentation" como un carrusel de
 * slides navegable. El markdown llega separado por líneas `---`; cada slide empieza
 * con `# Título`.
 *
 * Props:
 *   content               — string con el markdown completo (ya terminó de hacer stream)
 *   copyLabel / copiedLabel — etiquetas i18n para el botón de copia
 *   downloadPptxLabel       — label i18n para el botón de descarga PPTX
 *   downloadingPptxLabel    — label mientras se descarga
 *   downloadPptxErrorLabel  — texto de error si falla la descarga
 *   apiBase                 — base URL del API (para POST /export/pptx)
 *   theme                   — 'dark' | 'light' — tema del PPTX exportado (default 'dark')
 *   presenter               — {name, role, email} para el bloque de presenter en portada
 */
export default function SlideDeck({
  content,
  copyLabel,
  copiedLabel,
  downloadPptxLabel,
  downloadingPptxLabel,
  downloadPptxErrorLabel,
  apiBase,
  theme,
  presenter,
}) {
  const [current, setCurrent] = useState(0)
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState(null)

  // Separa los slides por la línea divisoria ---
  const slides = content
    .split(/^---\s*$/m)
    .map((s) => s.trim())
    .filter(Boolean)

  const total = slides.length
  if (total === 0) return <pre className="slide-raw">{content}</pre>

  const slide = slides[current]

  // Parsea inline bold **texto** a array de spans
  function parseBold(text) {
    const parts = []
    let rest = text
    let key = 0
    const re = /\*\*(.+?)\*\*/g
    let last = 0
    let m
    while ((m = re.exec(rest)) !== null) {
      if (m.index > last) parts.push(<span key={key++}>{rest.slice(last, m.index)}</span>)
      parts.push(<strong key={key++}>{m[1]}</strong>)
      last = m.index + m[0].length
    }
    if (last < rest.length) parts.push(<span key={key++}>{rest.slice(last)}</span>)
    return parts.length > 0 ? parts : [text]
  }

  // Detecta si una slide es de tipo específico para renderizarla diferente en la UI.
  // Orden de precedencia (debe reflejar backend/main.py::_parse_slides):
  //   divider > stats > impact > steps > defs > cards > normal
  function detectLayout(lines) {
    const nonEmpty = lines.filter((l) => l.trim())
    // Impact: solo una línea blockquote
    if (nonEmpty.length === 1 && nonEmpty[0].trim().startsWith('>')) return 'impact'

    const bullets = nonEmpty.filter((l) => /^[-*]\s+/.test(l))
    const ordered = nonEmpty.filter((l) => /^\d+[.)]\s+/.test(l))

    // Divider: solo H1 sin contenido
    const hasH1 = nonEmpty.some((l) => /^#\s+/.test(l))
    const hasContent = bullets.length > 0 || ordered.length > 0 || nonEmpty.some((l) => /^>/.test(l))
    if (hasH1 && !hasContent) return 'divider'

    // Stats: 2-4 bullets con **cifra** desc
    const statBullets = bullets.filter((l) => /^[-*]\s+\*\*[^*]+\*\*\s+.+/.test(l))
    if (statBullets.length >= 2 && statBullets.length <= 4 && statBullets.length === bullets.length) {
      const allStats = statBullets.every((l) => {
        const m = l.match(/^[-*]\s+\*\*([^*]+)\*\*/)
        return m && /\d/.test(m[1]) && m[1].length <= 14
      })
      if (allStats) return 'stats'
    }

    // Steps: lista ordenada (1. 2. ...) con 2-6 items, sin bullets sueltos
    if (ordered.length >= 2 && ordered.length <= 6 && bullets.length === 0) return 'steps'

    // Defs: bullets con **Término:** desc (colon DENTRO o inmediatamente tras el bold)
    const defBullets = bullets.filter((l) => /^[-*]\s+\*\*[^*]+?:?\*\*:\s*.+/.test(l) || /^[-*]\s+\*\*[^*]+:\*\*\s*.+/.test(l))
    if (defBullets.length >= 2 && defBullets.length === bullets.length) return 'defs'

    // Cards: 3-4 bullets, todos **Keyword** desc (sin dos puntos)
    const kwBullets = bullets.filter((l) => {
      const m = l.match(/^[-*]\s+\*\*([^*]+)\*\*\s+.+/)
      return m && !m[1].trim().endsWith(':') && m[1].trim().length <= 28
    })
    if (bullets.length >= 3 && bullets.length <= 4 && kwBullets.length === bullets.length) return 'cards'

    return 'normal'
  }

  // Renderiza el markdown de una slide con layouts variados
  function renderSlide(md) {
    const lines = md.split('\n')
    const layout = detectLayout(lines)
    const nonEmpty = lines.filter((l) => l.trim())

    // IMPACT: frase de alto impacto
    if (layout === 'impact') {
      const quoteText = nonEmpty[0].replace(/^>\s*/, '')
      return (
        <div className="slide-impact">
          <div className="slide-impact-bar" />
          <blockquote className="slide-impact-quote">{parseBold(quoteText)}</blockquote>
        </div>
      )
    }

    // DIVIDER: slide de sección
    if (layout === 'divider') {
      const titleLine = nonEmpty.find((l) => /^#\s+/.test(l)) || ''
      const divTitle = titleLine.replace(/^#+\s+/, '')
      return (
        <div className="slide-divider">
          <div className="slide-divider-accent" />
          <h2 className="slide-divider-title">{divTitle}</h2>
        </div>
      )
    }

    // STATS: tarjetas de estadística
    if (layout === 'stats') {
      const titleLine = nonEmpty.find((l) => /^#\s+/.test(l)) || ''
      const statsTitle = titleLine.replace(/^#+\s+/, '')
      const statLines = nonEmpty.filter((l) => /^[-*]\s+/.test(l))
      const stats = statLines.map((l) => {
        const m = l.match(/^[-*]\s+\*\*([^*]+)\*\*\s+(.+)$/)
        return m ? { figure: m[1], desc: m[2] } : { figure: '', desc: l }
      })
      return (
        <div className="slide-stats">
          {statsTitle && <h2 className="slide-title">{statsTitle}</h2>}
          <div className="slide-stats-cards">
            {stats.map((s, i) => (
              <div key={i} className="slide-stat-card">
                <span className="slide-stat-figure">{s.figure}</span>
                <span className="slide-stat-desc">{s.desc}</span>
              </div>
            ))}
          </div>
        </div>
      )
    }

    // DEFS: lista de definiciones
    if (layout === 'defs') {
      const titleLine = nonEmpty.find((l) => /^#\s+/.test(l)) || ''
      const defsTitle = titleLine.replace(/^#+\s+/, '')
      const defLines = nonEmpty.filter((l) => /^[-*]\s+/.test(l))
      const defs = defLines.map((l) => {
        const m = l.match(/^[-*]\s+\*\*([^*]+:?)\*\*:?\s+(.+)$/)
        return m ? { term: m[1].replace(/:$/, ''), desc: m[2] } : { term: '', desc: l }
      })
      return (
        <div className="slide-defs">
          {defsTitle && <h2 className="slide-title">{defsTitle}</h2>}
          <dl className="slide-defs-list">
            {defs.map((d, i) => (
              <div key={i} className="slide-def-item">
                <dt className="slide-def-term">{d.term}</dt>
                <dd className="slide-def-desc">{d.desc}</dd>
              </div>
            ))}
          </dl>
        </div>
      )
    }

    // STEPS: pasos numerados estilo Carbon
    if (layout === 'steps') {
      const titleLine = nonEmpty.find((l) => /^#\s+/.test(l)) || ''
      const stepsTitle = titleLine.replace(/^#+\s+/, '')
      const stepLines = nonEmpty.filter((l) => /^\d+[.)]\s+/.test(l))
      const steps = stepLines.map((l) => {
        const rest = l.replace(/^\d+[.)]\s+/, '')
        const m = rest.match(/^\*\*([^*]+)\*\*\s+(.+)$/)
        return m ? { keyword: m[1].replace(/:$/, ''), desc: m[2] } : { keyword: '', desc: rest }
      })
      return (
        <div className="slide-steps">
          {stepsTitle && <h2 className="slide-title">{stepsTitle}</h2>}
          <ol className="slide-steps-list">
            {steps.map((s, i) => (
              <li key={i} className="slide-step-item">
                <span className="slide-step-number">{String(i + 1).padStart(2, '0')}</span>
                <span className="slide-step-text">
                  {s.keyword && <strong className="slide-step-keyword">{s.keyword}</strong>}
                  {s.keyword && <span className="slide-step-sep">{'—'}</span>}
                  <span className="slide-step-desc">{parseBold(s.desc)}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )
    }

    // CARDS: grid de tarjetas estilo Carbon
    if (layout === 'cards') {
      const titleLine = nonEmpty.find((l) => /^#\s+/.test(l)) || ''
      const cardsTitle = titleLine.replace(/^#+\s+/, '')
      const cardLines = nonEmpty.filter((l) => /^[-*]\s+/.test(l))
      const cards = cardLines.map((l) => {
        const m = l.match(/^[-*]\s+\*\*([^*]+)\*\*\s+(.+)$/)
        return m ? { keyword: m[1], desc: m[2] } : { keyword: '', desc: l }
      })
      return (
        <div className="slide-cards">
          {cardsTitle && <h2 className="slide-title">{cardsTitle}</h2>}
          <div className="slide-cards-grid">
            {cards.map((c, i) => (
              <div key={i} className="slide-card-item">
                <span className="slide-card-keyword">{c.keyword}</span>
                <span className="slide-card-desc">{c.desc}</span>
              </div>
            ))}
          </div>
        </div>
      )
    }

    // NORMAL: título + bullets con parseo de bold
    const elements = []
    let bulletGroup = []
    function flushBullets() {
      if (bulletGroup.length > 0) {
        elements.push(
          <ul key={`ul-${elements.length}`} className="slide-bullets">
            {bulletGroup.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        )
        bulletGroup = []
      }
    }
    for (const line of lines) {
      const h1 = line.match(/^#\s+(.+)/)
      const h2 = line.match(/^##\s+(.+)/)
      const bullet = line.match(/^[-*]\s+(.+)/)
      if (h1) {
        flushBullets()
        elements.push(<h2 key={`h-${elements.length}`} className="slide-title">{h1[1]}</h2>)
      } else if (h2) {
        flushBullets()
        elements.push(<h3 key={`h2-${elements.length}`} className="slide-subtitle">{h2[1]}</h3>)
      } else if (bullet) {
        bulletGroup.push(parseBold(bullet[1]))
      } else if (line.trim() && !line.trim().startsWith('>')) {
        flushBullets()
        elements.push(
          <p key={`p-${elements.length}`} className="slide-para">{parseBold(line.trim())}</p>
        )
      }
    }
    flushBullets()
    return elements
  }

  async function copyAll() {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* ignore */
    }
  }

  async function downloadPptx() {
    if (downloading) return
    setDownloadError(null)
    setDownloading(true)
    // Título = primer H1 del markdown
    const titleMatch = content.match(/^#\s+(.+)/m)
    const title = titleMatch ? titleMatch[1].trim() : ''
    try {
      const base = (apiBase || '').replace(/\/+$/, '')
      const body = {
        markdown: content,
        title,
        theme: theme || 'dark',
        ...(presenter ? { presenter } : {}),
      }
      const res = await fetch(`${base}/export/pptx`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      // Usa el filename del header si está disponible; fallback slug del título.
      const disposition = res.headers.get('Content-Disposition') || ''
      const nameMatch = disposition.match(/filename="([^"]+)"/)
      a.download = nameMatch ? nameMatch[1] : `${title.toLowerCase().replace(/\s+/g, '-') || 'presentation'}.pptx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      setDownloadError(downloadPptxErrorLabel || 'Error generating .pptx')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="slide-deck">
      <div className="slide-card">
        <div className="slide-body">{renderSlide(slide)}</div>
      </div>

      <div className="slide-controls">
        <IconButton
          label="Previous"
          kind="ghost"
          size="sm"
          disabled={current === 0}
          onClick={() => setCurrent((c) => c - 1)}
        >
          <ChevronLeft />
        </IconButton>
        <span className="slide-counter">
          {current + 1} / {total}
        </span>
        <IconButton
          label="Next"
          kind="ghost"
          size="sm"
          disabled={current === total - 1}
          onClick={() => setCurrent((c) => c + 1)}
        >
          <ChevronRight />
        </IconButton>

        <Button
          kind="ghost"
          size="sm"
          renderIcon={copied ? Checkmark : Copy}
          onClick={copyAll}
          className="slide-copy-btn"
        >
          {copied ? copiedLabel : copyLabel}
        </Button>

        {downloadPptxLabel && (
          <Button
            kind="ghost"
            size="sm"
            renderIcon={Download}
            onClick={downloadPptx}
            disabled={downloading}
            className="slide-download-btn"
          >
            {downloading ? downloadingPptxLabel : downloadPptxLabel}
          </Button>
        )}
      </div>

      {downloadError && (
        <p className="slide-download-error">{downloadError}</p>
      )}
    </div>
  )
}
