const API = '/api'
const TIMEOUT_MS = 90000

const $ = (id) => document.getElementById(id)
const els = {
  messages: $('messages'),
  form: $('form'),
  input: $('input'),
  send: $('send'),
  registered: $('registered'),
  registeredLabel: $('registeredLabel'),
  clientType: $('clientType'),
  language: $('language'),
  sessionId: $('sessionId'),
  newChat: $('newChat'),
  health: $('health'),
  examples: $('examples'),
}

const EXAMPLES = [
  { text: '¿Qué color de paño uso para los inodoros?', registered: true, client: 'hotel' },
  { text: '¿Qué beneficios tiene ECO360 para mi hotel?', registered: false, client: 'hotel' },
  { text: '¿Qué dosis de X5 recomiendan?', registered: false, client: 'hotel' },
  { text: '¿Puedo mezclar el producto con lejía?', registered: true, client: 'hotel' },
]

const FALLBACKS = {
  no_documents: 'No se encontró información relevante',
  ungrounded: 'Sin respuesta respaldada por documentos',
  answer_mismatch: 'La respuesta no contestaba la pregunta',
  safety: 'Respuesta de seguridad (enlatada, sin LLM)',
}

let sessionId = crypto.randomUUID()
let busy = false

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

// Markdown mínimo (negritas, cursiva, código, listas, enlaces) sobre texto escapado.
function renderMarkdown(src) {
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
  for (const line of src.split('\n')) {
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

function append(node) {
  els.messages.appendChild(node)
  els.messages.scrollTop = els.messages.scrollHeight
}

function el(className, html) {
  const d = document.createElement('div')
  d.className = className
  d.innerHTML = html
  return d
}

const AVATAR = '<div class="avatar" aria-hidden="true">e</div>'

function addUser(text) {
  append(el('msg user', `<div class="bubble">${esc(text).replace(/\n/g, '<br>')}</div>`))
}

function addSystem(text) {
  const d = el('system', '')
  d.textContent = text
  append(d)
}

function addTyping() {
  const d = el('msg bot', `${AVATAR}<div class="bubble typing"><span></span><span></span><span></span><em>escribiendo…</em></div>`)
  append(d)
  const t0 = Date.now()
  const timer = setInterval(() => {
    d.querySelector('em').textContent = `escribiendo… ${Math.floor((Date.now() - t0) / 1000)} s`
  }, 1000)
  return () => {
    clearInterval(timer)
    d.remove()
  }
}

function addBot(data, ms) {
  const fb = data.fallback_reason
  const log = data.audit_log || []
  const cls = fb === 'safety' ? 'safety' : fb ? 'fallback' : ''
  const badges = [
    `<span class="badge flow-${esc(data.flow)}">${esc(data.flow)}</span>`,
    fb ? `<span class="badge fb-${esc(fb)}" title="${esc(FALLBACKS[fb] || fb)}">${esc(fb)}</span>` : '',
    `<span class="badge muted">${(ms / 1000).toFixed(1)} s</span>`,
  ].join('')
  const byTitle = new Map()
  for (const s of data.sources || []) {
    if (!byTitle.has(s.title)) byTitle.set(s.title, [])
    if (s.section) byTitle.get(s.title).push(s.section)
  }
  const sources = byTitle.size
    ? `<details class="sources"><summary>📄 Fuentes (${byTitle.size})</summary><ul>${[...byTitle]
        .map(([t, secs]) => `<li>${esc(t)}${secs.length ? `<span>${secs.map(esc).join(' · ')}</span>` : ''}</li>`)
        .join('')}</ul></details>`
    : ''
  const cta = data.cta_url
    ? `<a class="cta" href="${esc(data.cta_url)}" target="_blank" rel="noopener noreferrer" title="${esc(data.cta_url)}">Hablar con el equipo comercial ↗</a>`
    : ''
  append(
    el(
      'msg bot',
      `${AVATAR}<div class="bubble ${cls}">
        ${fb === 'safety' ? '<div class="warn">⚠ Aviso de seguridad</div>' : ''}
        <div class="meta">${badges}</div>
        <div class="answer">${renderMarkdown(data.cta_url ? (data.answer || '').replace(data.cta_url, '').trim() : data.answer || '')}</div>
        ${fb && fb !== 'safety' ? `<div class="fbnote">${esc(FALLBACKS[fb] || fb)}</div>` : ''}
        ${log.some((e) => e.event === 'security_event') ? '<div class="warn danger">🚨 security_event en audit_log: incidencia real</div>' : ''}
        ${sources}
        ${cta}
        <details class="debug"><summary>Depuración (audit_log · ${log.length} eventos)</summary>
          <pre>${esc(JSON.stringify(log, null, 2))}</pre>
        </details>
      </div>`,
    ),
  )
}

function addError(text, retryText) {
  const d = el('msg bot', `${AVATAR}<div class="bubble error"><b>Error</b><div></div></div>`)
  d.querySelector('.bubble div').textContent = text
  if (retryText) {
    const b = document.createElement('button')
    b.className = 'ghost'
    b.textContent = 'Reenviar mensaje'
    b.onclick = () => {
      d.remove()
      send(retryText, false)
    }
    d.querySelector('.bubble').appendChild(b)
  }
  append(d)
}

const formatDetail = (detail) =>
  Array.isArray(detail) ? detail.map((x) => `${(x.loc || []).join('.')}: ${x.msg}`).join('\n') : String(detail ?? '')

async function send(text, showUser = true) {
  if (busy || !text.trim()) return
  busy = true
  els.send.disabled = true
  if (showUser) addUser(text)
  const stop = addTyping()
  const ctrl = new AbortController()
  const to = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  const t0 = Date.now()
  try {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        is_registered: els.registered.checked,
        client_type: els.clientType.value,
        language: els.language.value.trim() || 'es',
        message: text,
      }),
      signal: ctrl.signal,
    })
    stop()
    if (res.ok) addBot(await res.json(), Date.now() - t0)
    else if (res.status === 422) {
      const body = await res.json().catch(() => ({}))
      addError(`Petición inválida (422):\n${formatDetail(body.detail)}`)
    } else addError(`Fallo interno del servicio (HTTP ${res.status}). Puede ser el proveedor de LLM.`, text)
  } catch (e) {
    stop()
    addError(
      e.name === 'AbortError'
        ? `Timeout tras ${TIMEOUT_MS / 1000} s sin respuesta.`
        : `No se pudo conectar con la API (${e.message}). ¿Está corriendo en localhost:8000?`,
      text,
    )
  } finally {
    clearTimeout(to)
    busy = false
    els.send.disabled = false
    els.input.focus()
  }
}

function updateLabel() {
  els.registeredLabel.textContent = els.registered.checked ? 'Cliente (dar soporte)' : 'No cliente (ventas)'
}

function resetChat(reason) {
  sessionId = crypto.randomUUID()
  els.sessionId.textContent = sessionId.slice(0, 8)
  els.sessionId.title = sessionId
  els.messages.innerHTML = ''
  const flow = els.registered.checked ? 'support' : 'sales'
  addSystem(`${reason ? reason + '. ' : ''}Sesión nueva · flujo ${flow} · ${els.clientType.value}`)
}

async function checkHealth() {
  try {
    const r = await fetch(`${API}/health`)
    els.health.className = `health ${r.ok ? 'ok' : 'bad'}`
    els.health.querySelector('span').textContent = r.ok ? 'API conectada' : `API con problemas (${r.status})`
  } catch {
    els.health.className = 'health bad'
    els.health.querySelector('span').textContent = 'API sin conexión'
  }
}

els.registered.addEventListener('change', () => {
  updateLabel()
  resetChat('Cambió el modo')
})
els.clientType.addEventListener('change', () => resetChat('Cambió el tipo de cliente'))
els.newChat.addEventListener('click', () => resetChat())
els.form.addEventListener('submit', (e) => {
  e.preventDefault()
  const t = els.input.value
  if (!t.trim()) return
  els.input.value = ''
  els.input.style.height = ''
  send(t)
})
els.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    els.form.requestSubmit()
  }
})
els.input.addEventListener('input', () => {
  els.input.style.height = 'auto'
  els.input.style.height = Math.min(els.input.scrollHeight, 140) + 'px'
})

for (const ex of EXAMPLES) {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = 'chip'
  b.textContent = ex.text
  b.title = `${ex.registered ? 'cliente' : 'no cliente'} · ${ex.client}`
  b.onclick = () => {
    if (busy) return
    const changed = els.registered.checked !== ex.registered || els.clientType.value !== ex.client
    els.registered.checked = ex.registered
    els.clientType.value = ex.client
    updateLabel()
    if (changed) resetChat()
    send(ex.text)
  }
  els.examples.appendChild(b)
}

updateLabel()
resetChat()
checkHealth()
setInterval(checkHealth, 15000)
