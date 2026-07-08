import { InlineNotification } from '@carbon/react'
import './ConceptMap.css'

/**
 * ConceptMap — renderiza un mapa conceptual como diagrama SVG hecho a mano
 * (sin dependencias nuevas). Layout radial: nodo central con `title`, nodos
 * satélite alrededor en círculo, aristas como líneas con etiqueta opcional.
 *
 * Si el JSON no parsea, muestra el texto crudo con una notificación de fallback.
 *
 * Props:
 *   content      — string JSON crudo devuelto por el LLM
 *   fallbackNote — texto i18n de la notificación de error de parseo
 */
export default function ConceptMap({ content, fallbackNote }) {
  let data
  try {
    // El modelo a veces rodea el JSON con ```json ... ``` — los quitamos.
    const cleaned = content.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim()
    data = JSON.parse(cleaned)
  } catch {
    // Fallback elegante: muestra el texto tal cual con notificación
    return (
      <div className="conceptmap-fallback">
        <InlineNotification
          kind="warning"
          title={fallbackNote}
          lowContrast
          hideCloseButton
        />
        <pre className="conceptmap-raw">{content}</pre>
      </div>
    )
  }

  const { title = '', nodes = [], edges = [] } = data

  // Dimensiones del SVG
  const W = 640
  const H = 420
  const CX = W / 2
  const CY = H / 2
  const RADIUS = 155         // radio del círculo de nodos satélite
  const NODE_R = 38          // radio del nodo satélite
  const CENTER_RX = 72       // semieje X del nodo central (elipse)
  const CENTER_RY = 30       // semieje Y del nodo central

  const n = nodes.length
  // Posiciones de los nodos satélite en círculo
  const positions = nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2
    return {
      id: node.id,
      label: node.label,
      x: CX + RADIUS * Math.cos(angle),
      y: CY + RADIUS * Math.sin(angle),
    }
  })

  const posMap = Object.fromEntries(positions.map((p) => [p.id, p]))
  // El nodo central se representa con el id "__center__"
  posMap['__center__'] = { x: CX, y: CY, label: title }

  function resolvePos(id) {
    // Coincidencia exacta primero; luego busca el nodo cuyo label o id contiene el texto
    if (posMap[id]) return posMap[id]
    const match = positions.find(
      (p) => p.label?.toLowerCase() === id?.toLowerCase() || p.id?.toLowerCase() === id?.toLowerCase()
    )
    return match || posMap['__center__']
  }

  // Wrap de texto en múltiples líneas (SVG no tiene text-wrap nativo)
  function wrapText(text, maxChars = 14) {
    const words = (text || '').split(' ')
    const lines = []
    let line = ''
    for (const w of words) {
      if ((line + ' ' + w).trim().length > maxChars) {
        if (line) lines.push(line)
        line = w
      } else {
        line = line ? line + ' ' + w : w
      }
    }
    if (line) lines.push(line)
    return lines
  }

  return (
    <div className="conceptmap-container">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="conceptmap-svg"
        aria-label={`Mapa conceptual: ${title}`}
      >
        {/* Aristas */}
        {edges.map((edge, i) => {
          const from = resolvePos(edge.from)
          const to = resolvePos(edge.to)
          if (!from || !to) return null
          const mx = (from.x + to.x) / 2
          const my = (from.y + to.y) / 2
          return (
            <g key={`edge-${i}`}>
              <line
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                className="conceptmap-edge"
              />
              {edge.label && (
                <text
                  x={mx}
                  y={my - 5}
                  className="conceptmap-edge-label"
                  textAnchor="middle"
                >
                  {edge.label}
                </text>
              )}
            </g>
          )
        })}

        {/* Nodo central */}
        <ellipse
          cx={CX}
          cy={CY}
          rx={CENTER_RX}
          ry={CENTER_RY}
          className="conceptmap-center-node"
        />
        {wrapText(title, 16).map((line, i, arr) => (
          <text
            key={i}
            x={CX}
            y={CY + (i - (arr.length - 1) / 2) * 16}
            className="conceptmap-center-label"
            textAnchor="middle"
            dominantBaseline="middle"
          >
            {line}
          </text>
        ))}

        {/* Nodos satélite */}
        {positions.map((p) => (
          <g key={p.id}>
            <circle cx={p.x} cy={p.y} r={NODE_R} className="conceptmap-node" />
            {wrapText(p.label, 12).map((line, i, arr) => (
              <text
                key={i}
                x={p.x}
                y={p.y + (i - (arr.length - 1) / 2) * 14}
                className="conceptmap-node-label"
                textAnchor="middle"
                dominantBaseline="middle"
              >
                {line}
              </text>
            ))}
          </g>
        ))}
      </svg>
    </div>
  )
}
