// Utilidades compartidas entre la vista de chat (main.js) y el panel de administración (admin.js).

export const FALLBACKS = {
  no_documents: 'No se encontró información relevante',
  ungrounded: 'Sin respuesta respaldada por documentos',
  answer_mismatch: 'La respuesta no contestaba la pregunta',
  safety: 'Respuesta de seguridad (enlatada, sin LLM)',
  off_topic: 'Fuera de tema',
}

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

// Markdown mínimo (negritas, cursiva, código, listas, enlaces) sobre texto escapado.
export function renderMarkdown(src) {
  const inline = (t) =>
    esc(t)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*(?!\s)(.+?)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
  const out = []
  let list = null
  const close = () => {
    if (list) out.push(`</${list}>`)
    list = null
  }
  for (const line of String(src ?? '').split('\n')) {
    const ul = line.match(/^\s*[-*•]\s+(.*)/)
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/)
    const h = line.match(/^#{1,6}\s+(.*)/)
    if (ul || ol) {
      const tag = ul ? 'ul' : 'ol'
      if (list !== tag) {
        close()
        out.push(`<${tag}>`)
        list = tag
      }
      out.push(`<li>${inline((ul || ol)[1])}</li>`)
    } else {
      close()
      if (h) out.push(`<p><strong>${inline(h[1])}</strong></p>`)
      else if (line.trim()) out.push(`<p>${inline(line)}</p>`)
    }
  }
  close()
  return out.join('')
}

// Texto de la respuesta sin la URL del CTA (la vista la muestra como botón).
export const answerText = (answer, ctaUrl) => (ctaUrl ? (answer || '').replace(ctaUrl, '').trim() : answer || '')

// Fuentes agrupadas por documento: [[título, [secciones]]]
export function groupSources(sources) {
  const byTitle = new Map()
  for (const s of sources || []) {
    if (!byTitle.has(s.title)) byTitle.set(s.title, [])
    if (s.section) byTitle.get(s.title).push(s.section)
  }
  return [...byTitle]
}

export const sourcesList = (groups) =>
  `<ul>${groups
    .map(([t, secs]) => `<li>${esc(t)}${secs.length ? `<span>${secs.map(esc).join(' · ')}</span>` : ''}</li>`)
    .join('')}</ul>`
