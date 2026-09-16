// Renderizador de markdown minimalista para las respuestas del asistente.
//
// Por qué no una librería (react-markdown, marked, etc.): el backend solo genera
// un subconjunto muy acotado de markdown (negrita, `código inline`, bloques ```
// ```, listas numeradas/con viñetas — ver SYSTEM_PROMPT/MODE_INSTRUCTIONS en
// main.py). Un parser de 60 líneas cubre ese subconjunto exacto, sin agregar una
// dependencia nueva ni su bundle la noche antes de una demo.
//
// Bug real reportado por César (2026-09-16): las respuestas con comandos de CLI
// mostraban los `**` y ``` literales en pantalla — el texto nunca se parseaba,
// solo se mostraba tal cual (`<p>{m.content}</p>`).

function parseInline(text, keyPrefix) {
  const parts = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let m
  let key = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) {
      parts.push(<strong key={`${keyPrefix}-${key++}`}>{tok.slice(2, -2)}</strong>)
    } else {
      parts.push(<code key={`${keyPrefix}-${key++}`}>{tok.slice(1, -1)}</code>)
    }
    last = re.lastIndex
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

// Bloque de texto sin fences de código: párrafos + listas numeradas/con viñetas.
function renderTextBlock(block, keyPrefix) {
  const lines = block.split('\n')
  const elements = []
  let i = 0
  let key = 0
  const isOl = (l) => /^\s*\d+[.)]\s+/.test(l)
  const isUl = (l) => /^\s*[-*]\s+/.test(l)

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    if (isOl(line)) {
      const items = []
      while (i < lines.length && isOl(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''))
        i++
      }
      elements.push(
        <ol key={`${keyPrefix}-ol-${key++}`}>
          {items.map((it, idx) => (
            <li key={idx}>{parseInline(it, `${keyPrefix}-ol-${key}-${idx}`)}</li>
          ))}
        </ol>
      )
      continue
    }
    if (isUl(line)) {
      const items = []
      while (i < lines.length && isUl(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
        i++
      }
      elements.push(
        <ul key={`${keyPrefix}-ul-${key++}`}>
          {items.map((it, idx) => (
            <li key={idx}>{parseInline(it, `${keyPrefix}-ul-${key}-${idx}`)}</li>
          ))}
        </ul>
      )
      continue
    }
    const paraLines = []
    while (i < lines.length && lines[i].trim() && !isOl(lines[i]) && !isUl(lines[i])) {
      paraLines.push(lines[i])
      i++
    }
    elements.push(
      <p key={`${keyPrefix}-p-${key++}`}>{parseInline(paraLines.join(' '), `${keyPrefix}-p-${key}`)}</p>
    )
  }
  return elements
}

/** Renderiza el texto de una respuesta (markdown acotado) como JSX. */
export default function MarkdownText({ text }) {
  if (!text) return null
  const fenceRe = /```(\w*)\n?([\s\S]*?)```/g
  const elements = []
  let last = 0
  let m
  let key = 0
  while ((m = fenceRe.exec(text))) {
    if (m.index > last) {
      elements.push(...renderTextBlock(text.slice(last, m.index), `t${key++}`))
    }
    const code = m[2].replace(/\n$/, '')
    elements.push(
      <pre key={`code-${key++}`}>
        <code>{code}</code>
      </pre>
    )
    last = fenceRe.lastIndex
  }
  if (last < text.length) {
    elements.push(...renderTextBlock(text.slice(last), `t${key++}`))
  }
  return <div className="markdown-body">{elements}</div>
}
