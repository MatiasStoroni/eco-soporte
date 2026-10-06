import { FALLBACKS, answerText, esc, groupSources, renderMarkdown, sourcesList } from './shared.js'

const API = '/api/admin'
const REFRESH_MS = 5000
const UNANSWERED = ['no_documents', 'ungrounded', 'answer_mismatch']

const STATUS = {
  bot: { label: 'Bot', cls: 'st-bot' },
  pending: { label: 'Derivación pendiente', cls: 'st-pending' },
  human: { label: 'Con persona', cls: 'st-human' },
}
const HANDOFF_REASONS = {
  user_request: 'El usuario pidió hablar con una persona',
  purchase: 'Quiere comprar o pidió una cotización',
  repeated_fallback: 'El bot no pudo responder varias veces seguidas',
}
const FLOWS = { support: 'Soporte', sales: 'Ventas' }
const CLIENTS = { hotel: 'Hotel', bodega: 'Bodega', restaurante: 'Restaurante', generic: 'Genérico' }

const $ = (id) => document.getElementById(id)
const store = {
  get: (k, s = sessionStorage) => {
    try {
      return s.getItem(k) || ''
    } catch {
      return ''
    }
  },
  set: (k, v, s = sessionStorage) => {
    try {
      v ? s.setItem(k, v) : s.removeItem(k)
    } catch {
      /* almacenamiento bloqueado: solo dura esta pestaña */
    }
  },
}

let token = store.get('eco_admin_token')
let author = store.get('eco_admin_author', localStorage)
let current = null // conversación abierta
let currentSig = ''
let listSig = ''
let timer = null
const filters = { status: '', flow: '', client_type: '', q: '', issues: false }
let view = 'conv' // conv | files
let files = null // {client_types: [{id, label}], documents: [...]} de /documents
const drafts = new Map() // archivo -> client_types tildados sin guardar
const fileFilters = { q: '', see: '' }
const FILES_HASH = 'archivos'

// --- API -------------------------------------------------------------------------------------------------

class AuthError extends Error {}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) throw new AuthError('Contraseña incorrecta.')
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return res.json()
}

// --- formato ---------------------------------------------------------------------------------------------

const ago = (iso) => {
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000)
  if (s < 60) return 'recién'
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`
  return new Date(iso).toLocaleDateString('es-AR', { day: 'numeric', month: 'short' })
}
const time = (iso) => new Date(iso).toLocaleString('es-AR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : '—')
const badge = (text, cls = '', title = '') => `<span class="badge ${cls}" ${title ? `title="${esc(title)}"` : ''}>${esc(text)}</span>`

// Resumen legible del audit_log: qué hizo el pipeline en el turno.
function trace(log) {
  const steps = []
  for (const e of log || []) {
    switch (e.event) {
      case 'safety_gate':
        if (e.flagged) steps.push(['Seguridad: coincidió', 'bad'])
        break
      case 'handoff_gate':
        if (e.requested) steps.push(['Pidió una persona', 'warn'])
        break
      case 'classify_intent':
        steps.push([`Intención: ${e.intent}`, e.reason === 'classifier_error' ? 'bad' : ''])
        break
      case 'rewrite_query':
        steps.push([`${e.retry ? 'Reescritura (reintento)' : 'Reescritura'}: ${(e.queries || []).length} consultas`, e.retry ? 'warn' : ''])
        break
      case 'retrieve':
        steps.push([`Recuperó ${(e.chunk_ids || []).length}`, ''])
        break
      case 'grade_documents':
        steps.push([`Relevantes: ${(e.relevant_ids || []).length}`, (e.relevant_ids || []).length ? '' : 'bad'])
        break
      case 'generate':
        steps.push(e.error ? [`Falló el LLM #${e.attempt ?? '?'}`, 'bad'] : [`Generó #${e.attempt ?? '?'}`, ''])
        break
      case 'check_grounding':
        steps.push([e.ok ? 'Respaldada ✓' : `Sin respaldo: ${e.reason || '?'}`, e.ok ? 'ok' : 'bad'])
        break
      case 'check_answer':
        steps.push([e.ok ? 'Contesta ✓' : 'No contesta', e.ok ? 'ok' : 'bad'])
        break
      case 'no_answer':
        steps.push([`Sin respuesta: ${e.reason}`, 'bad'])
        break
      case 'converse':
        steps.push(['Charla', ''])
        break
      case 'purchase_response':
        steps.push(['Compra → equipo comercial', 'warn'])
        break
      case 'sales_technical_response':
        steps.push(['Técnica en ventas → comercial', ''])
        break
      case 'security_event':
        steps.push(['🚨 security_event', 'bad'])
        break
    }
  }
  return steps.map(([t, c]) => `<span class="step ${c}">${esc(t)}</span>`).join('<i class="arrow">›</i>')
}

const interpreted = (log) => (log || []).find((e) => e.event === 'rewrite_query' && e.intent)?.intent

// Caso para evals/questions.yaml a partir de un turno (pregunta del usuario + respuesta del bot).
function evalCase(conv, botMsg, userMsg) {
  const q = JSON.stringify(userMsg?.content || '')
  const ct = conv.client_type !== 'hotel' ? `, client_type: ${conv.client_type}` : ''
  let tail
  if (UNANSWERED.includes(botMsg.fallback_reason)) tail = 'fallback: true}  # ¿debía negarse? si no, cambiar por expect: [...]'
  else if (botMsg.sources?.length) tail = `expect: ${JSON.stringify([...new Set(botMsg.sources.map((s) => s.section || s.title))])}}`
  else tail = 'expect: ["<sección esperada>"]}'
  return `# ${conv.flow}: (agregar bajo "${conv.flow}:" en evals/questions.yaml)\n  - {q: ${q}${ct}, ${tail}`
}

// --- render: estadísticas y lista ------------------------------------------------------------------------

function renderStats(s) {
  const cards = [
    ['Conversaciones', s.total, `${s.last_24h} en 24 h`, ''],
    ['Derivaciones', s.pending, `${s.human} con persona`, s.pending ? 'hot' : ''],
    ['Sin respuesta', pct(s.unanswered, s.answers), `${s.unanswered} de ${s.answers} respuestas`, ''],
    ['Revisión', `${s.good_reviews} 👍 · ${s.bad_reviews} 👎`, 'calificadas por el equipo', ''],
    ['Latencia', s.avg_latency_ms ? `${(s.avg_latency_ms / 1000).toFixed(1)} s` : '—', 'promedio por respuesta', ''],
  ]
  $('stats').innerHTML = cards
    .map(([k, v, sub, cls]) => `<div class="stat ${cls}"><b>${esc(v)}</b><span>${esc(k)}</span><small>${esc(sub)}</small></div>`)
    .join('')
  $('pendingCount').textContent = s.pending || ''
}

function renderList(rows) {
  const sig = JSON.stringify(rows.map((r) => [r.session_id, r.updated_at, r.status, r.bad_reviews]))
  if (sig === listSig) return
  listSig = sig
  if (!rows.length) {
    $('list').innerHTML = `<div class="list-empty">No hay conversaciones con estos filtros.<br><a href="/">Probá el chat</a> para generar algunas.</div>`
    return
  }
  $('list').innerHTML = rows
    .map((c) => {
      const st = STATUS[c.status]
      const flags = [
        c.unanswered ? `<span class="flag bad" title="Respuestas sin información">⚠ ${c.unanswered}</span>` : '',
        c.bad_reviews ? `<span class="flag bad" title="Calificadas como malas">👎 ${c.bad_reviews}</span>` : '',
        c.safety ? `<span class="flag warn" title="Respuestas de seguridad">⛑ ${c.safety}</span>` : '',
      ].join('')
      return `<button type="button" class="item ${current?.session_id === c.session_id ? 'on' : ''} ${st.cls}" data-id="${esc(c.session_id)}">
        <div class="item-top">
          <span class="dot" title="${esc(st.label)}"></span>
          <span class="item-title">${esc(CLIENTS[c.client_type] || c.client_type)} · ${esc(FLOWS[c.flow] || c.flow)}</span>
          <span class="item-time">${esc(ago(c.updated_at))}</span>
        </div>
        <div class="item-msg">${c.last_role === 'human' ? '<b>Equipo:</b> ' : ''}${esc(c.last_message || '—')}</div>
        <div class="item-foot">
          ${c.status !== 'bot' ? `<span class="pill ${st.cls}">${esc(st.label)}</span>` : ''}
          <span class="muted">${c.turns} ${c.turns === 1 ? 'mensaje' : 'mensajes'}</span>${flags}
        </div>
      </button>`
    })
    .join('')
}

// --- render: detalle -------------------------------------------------------------------------------------

function messageHtml(conv, m, i) {
  if (m.role === 'system') return `<div class="sys">${esc(m.content)} <time>${esc(time(m.created_at))}</time></div>`
  if (m.role === 'user')
    return `<div class="m user"><div class="who">Cliente · ${esc(time(m.created_at))}</div><div class="bubble">${renderMarkdown(m.content)}</div></div>`
  if (m.role === 'human')
    return `<div class="m team"><div class="who">${esc(m.author || 'Equipo')} · equipo · ${esc(time(m.created_at))}</div><div class="bubble">${renderMarkdown(m.content)}</div></div>`

  // bot
  const fb = m.fallback_reason
  const groups = groupSources(m.sources)
  const prevUser = conv.messages.slice(0, i).reverse().find((x) => x.role === 'user')
  const intent = interpreted(m.audit_log)
  const badges = [
    badge(m.intent || '—', 'intent', 'Intención detectada'),
    fb ? badge(fb, `fb fb-${fb}`, FALLBACKS[fb] || fb) : '',
    m.latency_ms != null ? badge(`${(m.latency_ms / 1000).toFixed(1)} s`, 'muted', 'Latencia') : '',
    badge(FLOWS[m.flow] || m.flow || '', `flow-${m.flow}`),
  ].join('')
  return `<div class="m bot ${fb === 'safety' ? 'safety' : fb ? 'fallback' : ''} ${m.rating ? `rated-${m.rating}` : ''}" data-mid="${m.id}">
    <div class="who">Asistente · ${esc(time(m.created_at))}</div>
    <div class="bubble">
      <div class="meta">${badges}</div>
      ${intent ? `<div class="interp" title="Cómo interpretó la pregunta (rewrite_query)">Interpretó: “${esc(intent)}”</div>` : ''}
      <div class="answer">${renderMarkdown(answerText(m.content, m.cta_url))}</div>
      ${m.cta_url ? `<div class="cta-line">CTA → <a href="${esc(m.cta_url)}" target="_blank" rel="noopener noreferrer">${esc(m.cta_url)}</a></div>` : ''}
      ${groups.length ? `<div class="srcs"><b>Fuentes</b>${sourcesList(groups)}</div>` : ''}
      ${m.audit_log?.length ? `<div class="trace">${trace(m.audit_log)}</div>` : ''}
      <div class="review">
        <button type="button" class="rv ${m.rating === 'good' ? 'on' : ''}" data-rate="good" title="Buena respuesta">👍</button>
        <button type="button" class="rv ${m.rating === 'bad' ? 'on' : ''}" data-rate="bad" title="Mala respuesta">👎</button>
        <button type="button" class="link" data-act="note">${m.review_note ? 'Editar nota' : 'Nota'}</button>
        <button type="button" class="link" data-act="eval" data-case="${esc(evalCase(conv, m, prevUser))}">Copiar caso de eval</button>
        ${m.reviewed_by && m.rating ? `<span class="muted">revisó ${esc(m.reviewed_by)}</span>` : ''}
      </div>
      ${m.review_note ? `<div class="note-view">📝 ${esc(m.review_note)}</div>` : ''}
      <div class="note-edit" hidden>
        <textarea rows="2" placeholder="¿Qué estuvo mal? (ej.: inventó una dilución, no citó la ficha de X4…)">${esc(m.review_note || '')}</textarea>
        <div><button type="button" data-act="save-note">Guardar nota</button> <button type="button" class="ghost" data-act="cancel-note">Cancelar</button></div>
      </div>
      ${m.audit_log?.length ? `<details class="raw"><summary>audit_log (${m.audit_log.length} eventos)</summary><pre>${esc(JSON.stringify(m.audit_log, null, 2))}</pre></details>` : ''}
    </div>
  </div>`
}

function handoffBar(c) {
  if (c.status === 'pending')
    return `<div class="hbar pending">
      <div><b>⚑ Derivación pendiente</b><span>${esc(HANDOFF_REASONS[c.handoff_reason] || c.handoff_reason || '')}${c.handoff_at ? ` · ${esc(ago(c.handoff_at))}` : ''}. El bot sigue respondiendo hasta que alguien la tome.</span></div>
      <div class="hbar-actions"><button type="button" data-status="human">Tomar conversación</button><button type="button" class="ghost" data-status="bot">Descartar</button></div>
    </div>`
  if (c.status === 'human')
    return `<div class="hbar human">
      <div><b>👤 Atendida por ${esc(c.assignee || 'el equipo')}</b><span>El bot no responde mientras tanto. El cliente ve tus mensajes en el chat.</span></div>
      <div class="hbar-actions"><button type="button" class="ghost" data-status="bot">Devolver al bot</button></div>
    </div>`
  return ''
}

function renderDetail(c, { force = false } = {}) {
  const sig = JSON.stringify([c.session_id, c.status, c.assignee, c.messages.map((m) => [m.id, m.rating, m.review_note])])
  if (!force && sig === currentSig) return
  // No pisar una nota que se está escribiendo.
  if (!force && document.activeElement?.closest?.('.note-edit')) return
  currentSig = sig
  const d = $('detail')
  const transcript = d.querySelector('.transcript')
  const atBottom = !transcript || transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80
  const prevScroll = transcript?.scrollTop
  const draft = d.querySelector('#reply')?.value || ''
  const bots = c.messages.filter((m) => m.role === 'bot')
  const st = STATUS[c.status]

  d.innerHTML = `
    <div class="dhead">
      <button type="button" class="ghost back-btn" id="back">← Conversaciones</button>
      <div class="dtitle">
        <h2>${esc(CLIENTS[c.client_type] || c.client_type)} · ${esc(FLOWS[c.flow] || c.flow)} <span class="pill ${st.cls}">${esc(st.label)}</span></h2>
        <div class="muted">Sesión <code title="${esc(c.session_id)}">${esc(c.session_id.slice(0, 8))}</code> · iniciada ${esc(time(c.created_at))} ·
          ${bots.length} respuestas · ${bots.filter((m) => UNANSWERED.includes(m.fallback_reason)).length} sin información · idioma ${esc(c.language)}</div>
      </div>
    </div>
    ${handoffBar(c)}
    <div class="transcript">${c.messages.map((m, i) => messageHtml(c, m, i)).join('')}</div>
    <form class="reply" id="replyForm">
      <textarea id="reply" rows="1" placeholder="Responder como ${esc(author)}…"></textarea>
      <button type="submit">Enviar</button>
      ${c.status !== 'human' ? '<small>Al enviar, tomás la conversación y el bot deja de responder.</small>' : ''}
    </form>`
  d.querySelector('#reply').value = draft
  const t = d.querySelector('.transcript')
  t.scrollTop = atBottom ? t.scrollHeight : prevScroll
}

// --- carga -----------------------------------------------------------------------------------------------

async function loadList() {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v === true ? 'true' : v)
  const [stats, rows] = await Promise.all([api('/stats'), api(`/conversations?${p}`)])
  renderStats(stats)
  renderList(rows)
}

async function openConversation(id, opts = {}) {
  try {
    const c = await api(`/conversations/${encodeURIComponent(id)}`)
    if (current?.session_id !== id) currentSig = ''
    current = c
    renderDetail(c, opts)
    document.body.classList.add('show-detail')
    for (const b of document.querySelectorAll('.item')) b.classList.toggle('on', b.dataset.id === id)
    if (location.hash.slice(1) !== id) history.replaceState(null, '', `#${id}`)
  } catch (e) {
    handleError(e)
  }
}

async function refresh() {
  try {
    await loadList()
    if (current) await openConversation(current.session_id)
  } catch (e) {
    handleError(e)
  }
}

function handleError(e) {
  if (e instanceof AuthError) return logout('La sesión expiró o cambió la contraseña.')
  toast(e.message || String(e), 'bad')
}

function toast(text, cls = '') {
  const t = document.createElement('div')
  t.className = `toast ${cls}`
  t.textContent = text
  document.body.appendChild(t)
  setTimeout(() => t.remove(), 3500)
}

// --- acciones --------------------------------------------------------------------------------------------

async function setStatus(status) {
  try {
    await api(`/conversations/${encodeURIComponent(current.session_id)}/status`, { method: 'POST', body: { status, author } })
    toast(status === 'human' ? 'Tomaste la conversación' : 'La conversación vuelve al bot', 'ok')
    listSig = ''
    await refresh()
  } catch (e) {
    handleError(e)
  }
}

async function sendReply(text) {
  try {
    await api(`/conversations/${encodeURIComponent(current.session_id)}/messages`, {
      method: 'POST',
      body: { content: text, author },
    })
    $('reply').value = ''
    listSig = ''
    await refresh()
  } catch (e) {
    handleError(e)
  }
}

async function review(mid, patch) {
  const m = current.messages.find((x) => x.id === mid)
  const body = { rating: m.rating, note: m.review_note, author, ...patch }
  try {
    const out = await api(`/messages/${mid}/review`, { method: 'PUT', body })
    Object.assign(m, { rating: out.rating, review_note: out.review_note, reviewed_by: out.reviewed_by })
    renderDetail(current, { force: true })
    listSig = ''
    loadList().catch(handleError)
  } catch (e) {
    handleError(e)
  }
}

$('detail').addEventListener('click', async (e) => {
  const btn = e.target.closest('button')
  if (!btn || !current) return
  const box = btn.closest('[data-mid]')
  const mid = box ? Number(box.dataset.mid) : null
  const m = mid && current.messages.find((x) => x.id === mid)
  if (btn.id === 'back') {
    document.body.classList.remove('show-detail')
    return
  }
  if (btn.dataset.status) return setStatus(btn.dataset.status)
  if (btn.dataset.rate) {
    const rating = m.rating === btn.dataset.rate ? null : btn.dataset.rate
    await review(mid, { rating })
    if (rating === 'bad' && !m.review_note) $('detail').querySelector(`[data-mid="${mid}"] [data-act="note"]`)?.click()
    return
  }
  const act = btn.dataset.act
  if (act === 'note') {
    const ed = box.querySelector('.note-edit')
    ed.hidden = false
    ed.querySelector('textarea').focus()
  } else if (act === 'cancel-note') {
    box.querySelector('.note-edit').hidden = true
  } else if (act === 'save-note') {
    const note = box.querySelector('.note-edit textarea').value.trim()
    document.activeElement?.blur()
    await review(mid, { note })
  } else if (act === 'eval') {
    try {
      await navigator.clipboard.writeText(btn.dataset.case)
      toast('Caso copiado: pegalo en evals/questions.yaml', 'ok')
    } catch {
      prompt('Copiá el caso para evals/questions.yaml:', btn.dataset.case)
    }
  }
})

$('detail').addEventListener('submit', (e) => {
  e.preventDefault()
  const text = $('reply').value.trim()
  if (text) sendReply(text)
})

$('detail').addEventListener('keydown', (e) => {
  if (e.target.id === 'reply' && e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    $('replyForm').requestSubmit()
  }
})

$('detail').addEventListener('input', (e) => {
  if (e.target.id !== 'reply') return
  e.target.style.height = 'auto'
  e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
})

$('list').addEventListener('click', (e) => {
  const item = e.target.closest('.item')
  if (item) openConversation(item.dataset.id, { force: true })
})

let qTimer
$('q').addEventListener('input', (e) => {
  clearTimeout(qTimer)
  qTimer = setTimeout(() => {
    filters.q = e.target.value.trim()
    refresh()
  }, 300)
})
$('statusTabs').addEventListener('click', (e) => {
  const b = e.target.closest('button')
  if (!b) return
  filters.status = b.dataset.status
  for (const x of $('statusTabs').children) x.classList.toggle('on', x === b)
  refresh()
})
for (const id of ['flow', 'clientType']) {
  $(id).addEventListener('change', (e) => {
    filters[id === 'flow' ? 'flow' : 'client_type'] = e.target.value
    refresh()
  })
}
$('issues').addEventListener('change', (e) => {
  filters.issues = e.target.checked
  refresh()
})

// --- base de conocimiento: archivos, visibilidad, subida y revisión ----------------------------------------

const COMMON = 'common'
const MAX_UPLOAD = 15 * 1024 * 1024
const UPLOAD_EXT = ['.pdf', '.docx', '.md']
const POLL_PROCESSING_MS = 3000
const docKey = (d) => `${d.audience}|${d.source_id}`
const findDoc = (key) => files?.documents.find((x) => docKey(x) === key)
// Igual que el backend: "common" incluye a todos; el resto, sin duplicados y en orden estable.
const normTypes = (types) => (types.includes(COMMON) ? [COMMON] : [...new Set(types)].sort())
const sameTypes = (a, b) => normTypes(a).join() === normTypes(b).join()
const typeLabel = (id) => files?.client_types.find((t) => t.id === id)?.label || id
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

function visibleFor(d, type) {
  return d.client_types.includes(type) || d.client_types.includes(COMMON)
}

// Casillas de tipos de cliente ("Todos (común)" deshabilita las individuales). Las usan las filas y la subida.
function typeChips(selected) {
  const all = selected.includes(COMMON)
  return files.client_types
    .map((t) => {
      const isAll = t.id === COMMON
      const checked = isAll ? all : all || selected.includes(t.id)
      const disabled = !isAll && all
      return `<label class="chip ${isAll ? 'all' : ''} ${checked ? 'on' : ''} ${disabled ? 'dis' : ''}">
        <input type="checkbox" value="${esc(t.id)}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''} />${esc(isAll ? 'Todos (común)' : t.label)}</label>`
    })
    .join('')
}

// En qué está el documento: procesando, error, borrador para revisar o publicado.
function docStatus(d) {
  if (d.state === 'processing') return badge('Procesando…', 'proc', 'Se está leyendo el archivo (con IA si hace falta)')
  if (d.state === 'error') return badge('Error', 'off', d.error || '')
  const out = []
  if (d.has_draft) {
    const alerts = d.alerts ? ` · ${plural(d.alerts, 'alerta', 'alertas')}` : ''
    out.push(badge(`Borrador para revisar${alerts}`, 'draft', 'El bot no lo usa hasta que se publique'))
  }
  if (d.chunks) out.push(badge(d.has_draft ? 'Publicada la versión anterior' : 'Publicado', 'pub'))
  return out.join('')
}

function docRow(d) {
  const key = docKey(d)
  const draft = drafts.get(key) ?? d.client_types
  const dirty = !sameTypes(draft, d.client_types)
  const off = !draft.length
  const busy = d.state === 'processing'
  const who = d.updated_by
    ? `Actualizado por ${esc(d.updated_by)} · ${esc(ago(d.updated_at))}`
    : d.origin === 'files' ? 'Cargado desde el repositorio' : ''
  return `<div class="doc ${off ? 'off' : ''} ${dirty ? 'dirty' : ''} ${d.has_draft ? 'has-draft' : ''}" data-key="${esc(key)}">
    <div class="doc-info">
      <div class="doc-title">${esc(d.title)} ${docStatus(d)}
        ${off && d.chunks ? badge('Sin habilitar', 'off', 'Ningún cliente lo consulta: el bot no lo usa') : ''}
        ${dirty ? badge('Sin guardar', 'dirty') : ''}</div>
      <div class="doc-path" title="${esc(d.source_id)}">${esc(d.original_name || d.source_id)}</div>
      ${d.state === 'error' ? `<div class="doc-error">${esc(d.error)}</div>` : ''}
      <div class="doc-meta">${d.product ? `${esc(d.product)} · ` : ''}${plural(d.chunks, 'fragmento', 'fragmentos')}${who ? ` · ${who}` : ''}</div>
    </div>
    <div class="doc-vis" role="group" aria-label="Tipos de cliente que consultan ${esc(d.title)}">${typeChips(draft)}</div>
    <button type="button" class="save" ${dirty ? '' : 'disabled'}>Guardar</button>
    <div class="doc-actions">
      <button type="button" class="link" data-act="edit" ${busy || d.state === 'error' ? 'disabled' : ''}>${d.has_draft ? 'Revisar borrador' : 'Ver y editar'}</button>
      <button type="button" class="link" data-act="replace" ${busy ? 'disabled' : ''}>Reemplazar archivo</button>
      ${d.has_original ? '<button type="button" class="link" data-act="original">Descargar original</button>' : ''}
      <button type="button" class="link danger" data-act="delete" ${busy ? 'disabled' : ''}>Eliminar</button>
    </div>
  </div>`
}

function renderFiles() {
  if (!files) return
  const q = fileFilters.q.toLowerCase()
  const see = fileFilters.see
  const docs = files.documents.filter(
    (d) =>
      (!q || `${d.title} ${d.source_id} ${d.original_name || ''} ${d.product || ''}`.toLowerCase().includes(q)) &&
      (!see || visibleFor(d, see)),
  )
  const html = Object.entries(FLOWS)
    .map(([aud, label]) => {
      const ds = docs.filter((d) => d.audience === aud)
      if (!ds.length) return ''
      return `<section class="fgroup"><h3>${esc(label)} <span class="muted">${plural(ds.length, 'archivo', 'archivos')}</span></h3>${ds.map(docRow).join('')}</section>`
    })
    .join('')
  const empty = files.documents.length
    ? 'No hay archivos con estos filtros.'
    : 'Todavía no hay archivos. Subí el primero desde el recuadro de arriba.'
  $('fileList').innerHTML = html || `<div class="list-empty">${empty}</div>`
}

const rowEl = (key) => $('fileList').querySelector(`.doc[data-key="${CSS.escape(key)}"]`)

// Redibuja una sola fila, conservando el foco del teclado en la casilla que se tocó.
function updateRow(key, focusValue) {
  const d = findDoc(key)
  const el = rowEl(key)
  if (!d || !el) return
  el.outerHTML = docRow(d)
  if (focusValue) rowEl(key)?.querySelector(`input[value="${CSS.escape(focusValue)}"]`)?.focus()
}

// Mientras haya archivos procesándose, se consulta cada pocos segundos y se avisa cuando terminan.
let filesTimer = null
function announceChanges(before, after) {
  for (const d of after) {
    const prev = before.get(docKey(d))
    if (prev?.state !== 'processing' || d.state === 'processing') continue
    if (d.state === 'error') toast(`${d.title}: no se pudo procesar`, 'bad')
    else if (d.has_draft) toast(`${d.title}: la IA armó un borrador, revisalo antes de publicarlo`, 'ok')
    else toast(`${d.title}: publicado sin IA (ya tenía buen formato)`, 'ok')
  }
}

async function loadFiles() {
  clearTimeout(filesTimer)
  try {
    const before = new Map((files?.documents || []).map((d) => [docKey(d), d]))
    files = await api('/documents')
    for (const k of drafts.keys()) if (!findDoc(k)) drafts.delete(k)
    const sel = $('fsee')
    const keep = sel.value
    sel.innerHTML =
      '<option value="">Cualquier tipo</option>' +
      files.client_types
        .filter((t) => t.id !== COMMON)
        .map((t) => `<option value="${esc(t.id)}">${esc(t.label)}</option>`)
        .join('')
    sel.value = keep
    renderUploadTypes()
    $('upNote').textContent = files.ai
      ? 'Si ya tiene buen formato se publica al instante. Si no, la IA arma un borrador que alguien revisa antes de publicarlo.'
      : 'Sin IA configurada (falta GOOGLE_API_KEY): solo se aceptan archivos que ya tengan buen formato.'
    announceChanges(before, files.documents)
    renderFiles()
    if (view === 'files' && files.documents.some((d) => d.state === 'processing'))
      filesTimer = setTimeout(() => document.hidden || loadFiles(), POLL_PROCESSING_MS)
  } catch (e) {
    handleError(e)
  }
}

async function saveDoc(key) {
  const d = findDoc(key)
  const types = normTypes(drafts.get(key) ?? d.client_types)
  rowEl(key).querySelector('.save').disabled = true
  try {
    const out = await api('/documents/visibility', {
      method: 'PUT',
      body: { audience: d.audience, source_id: d.source_id, client_types: types, author },
    })
    Object.assign(d, out)
    drafts.delete(key)
    updateRow(key)
    const who = types.includes(COMMON) ? 'todos los tipos' : types.map(typeLabel).join(', ')
    toast(types.length ? `${d.title}: lo consultan ${who}` : `${d.title}: quedó sin habilitar`, 'ok')
  } catch (e) {
    updateRow(key)
    handleError(e)
  }
}

const docRef = (d) => ({ audience: d.audience, source_id: d.source_id, author })

async function downloadOriginal(d) {
  try {
    const q = new URLSearchParams({ audience: d.audience, source_id: d.source_id })
    const res = await fetch(`${API}/documents/original?${q}`, { headers: { Authorization: `Bearer ${token}` } })
    if (res.status === 401) throw new AuthError('Contraseña incorrecta.')
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`)
    const url = URL.createObjectURL(await res.blob())
    const a = Object.assign(document.createElement('a'), { href: url, download: d.original_name || 'original' })
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) {
    handleError(e)
  }
}

async function deleteDoc(d) {
  if (!confirm(`¿Eliminar «${d.title}»?\n\nEl asistente deja de consultarlo al instante. No se puede deshacer.`)) return
  try {
    await api('/documents/delete', { method: 'POST', body: docRef(d) })
    toast(`${d.title}: eliminado`, 'ok')
    loadFiles()
  } catch (e) {
    handleError(e)
  }
}

$('fileList').addEventListener('change', (e) => {
  const input = e.target.closest('input[type="checkbox"]')
  const row = e.target.closest('.doc')
  if (!input || !row) return
  const key = row.dataset.key
  const d = findDoc(key)
  const draft = new Set(drafts.get(key) ?? d.client_types)
  input.checked ? draft.add(input.value) : draft.delete(input.value)
  const next = [...draft]
  sameTypes(next, d.client_types) ? drafts.delete(key) : drafts.set(key, next)
  updateRow(key, input.value)
})

$('fileList').addEventListener('click', (e) => {
  const btn = e.target.closest('button')
  const row = btn?.closest('.doc')
  if (!btn || !row || btn.disabled) return
  const d = findDoc(row.dataset.key)
  if (btn.classList.contains('save')) return saveDoc(row.dataset.key)
  const act = btn.dataset.act
  if (act === 'edit') openEditor(d)
  else if (act === 'original') downloadOriginal(d)
  else if (act === 'delete') deleteDoc(d)
  else if (act === 'replace') {
    replaceTarget = d
    $('replaceFile').click()
  }
})

let fqTimer
$('fq').addEventListener('input', (e) => {
  clearTimeout(fqTimer)
  fqTimer = setTimeout(() => {
    fileFilters.q = e.target.value.trim()
    renderFiles()
  }, 150)
})
$('fsee').addEventListener('change', (e) => {
  fileFilters.see = e.target.value
  renderFiles()
})

// --- subir archivos ------------------------------------------------------------------------------------------

let upFile = null
let upTypes = []
let replaceTarget = null

function renderUploadTypes() {
  if (files) $('upTypes').innerHTML = typeChips(upTypes)
}

function checkFile(file) {
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!UPLOAD_EXT.includes(ext)) return `Formato no soportado: se aceptan ${UPLOAD_EXT.join(', ')}`
  if (file.size > MAX_UPLOAD) return 'El archivo supera los 15 MB'
  if (!file.size) return 'El archivo está vacío'
  return ''
}

function pickFile(file) {
  const err = file && checkFile(file)
  if (err) {
    toast(err, 'bad')
    file = null
  }
  upFile = file || null
  $('upName').textContent = upFile ? upFile.name : 'Elegí un archivo o arrastralo acá'
  $('drop').classList.toggle('has-file', !!upFile)
  $('upBtn').disabled = !upFile
}

const toBase64 = (file) =>
  new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',', 2)[1] || '')
    r.onerror = () => reject(new Error('No se pudo leer el archivo'))
    r.readAsDataURL(file)
  })

async function uploadFile(file, { audience, client_types = [], source_id = null }) {
  const body = { audience, filename: file.name, content_base64: await toBase64(file), client_types, source_id, author }
  const doc = await api('/documents/upload', { method: 'POST', body })
  toast(`${file.name}: subido, procesando…`, 'ok')
  await loadFiles()
  return doc
}

$('upFile').addEventListener('change', (e) => pickFile(e.target.files[0]))
$('drop').addEventListener('dragover', (e) => {
  e.preventDefault()
  $('drop').classList.add('over')
})
$('drop').addEventListener('dragleave', () => $('drop').classList.remove('over'))
$('drop').addEventListener('drop', (e) => {
  e.preventDefault()
  $('drop').classList.remove('over')
  pickFile(e.dataTransfer.files[0])
})
$('upTypes').addEventListener('change', (e) => {
  const input = e.target.closest('input[type="checkbox"]')
  if (!input) return
  const s = new Set(upTypes)
  input.checked ? s.add(input.value) : s.delete(input.value)
  upTypes = [...s]
  renderUploadTypes()
})
$('upForm').addEventListener('submit', async (e) => {
  e.preventDefault()
  if (!upFile) return
  $('upBtn').disabled = true
  $('upBtn').textContent = 'Subiendo…'
  try {
    await uploadFile(upFile, { audience: $('upAud').value, client_types: normTypes(upTypes) })
    $('upFile').value = ''
    upTypes = []
    renderUploadTypes()
    pickFile(null)
  } catch (err) {
    handleError(err)
    $('upBtn').disabled = false
  } finally {
    $('upBtn').textContent = 'Subir'
  }
})
$('replaceFile').addEventListener('change', async (e) => {
  const file = e.target.files[0]
  const d = replaceTarget
  e.target.value = ''
  if (!file || !d) return
  const err = checkFile(file)
  if (err) return toast(err, 'bad')
  if (!confirm(`¿Reemplazar «${d.title}» con «${file.name}»?\n\nSe conserva quién lo consulta. Si el archivo nuevo necesita IA, la versión actual sigue publicada hasta que publiques el borrador.`)) return
  try {
    await uploadFile(file, { audience: d.audience, source_id: d.source_id })
  } catch (err2) {
    handleError(err2)
  }
})

// --- editor: revisar un borrador, editar y publicar ---------------------------------------------------------

let editing = null // documento abierto en el editor (con markdown, borrador y revisión)
let editorBase = '' // texto al abrir o al último guardado, para detectar cambios

const editorDirty = () => editing && $('edText').value !== editorBase
const splitFront = (md) => {
  const m = /^---\n[\s\S]*?\n---\n?/.exec(md)
  return m ? [m[0], md.slice(m[0].length)] : ['', md]
}

// Vista previa: secciones ## como títulos y tablas markdown como tablas; el resto, el markdown mínimo del chat.
function docPreview(md) {
  const [, body] = splitFront(md.replace(/\r\n/g, '\n'))
  const out = []
  let buf = []
  let table = []
  const flush = () => {
    if (buf.length) out.push(renderMarkdown(buf.join('\n')))
    buf = []
  }
  const flushTable = () => {
    if (!table.length) return
    const rows = table.filter((r) => !/^\|?[\s:|-]+\|?$/.test(r))
    const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => esc(c.trim()))
    const [head, ...rest] = rows
    out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rest
      .map((r) => `<tr>${cells(r).map((c) => `<td>${c}</td>`).join('')}</tr>`)
      .join('')}</tbody></table>`)
    table = []
  }
  for (const line of body.split('\n')) {
    const h = /^(#{1,3})\s+(.*)/.exec(line)
    if (line.trim().startsWith('|')) {
      flush()
      table.push(line.trim())
    } else if (h) {
      flush()
      flushTable()
      out.push(`<h${h[1].length + 2}>${esc(h[2])}</h${h[1].length + 2}>`)
    } else {
      flushTable()
      buf.push(line)
    }
  }
  flush()
  flushTable()
  return out.join('')
}

function reviewHtml(review) {
  if (!review?.blocks?.length) return ''
  const head = review.used_ai
    ? review.alerts
      ? `<b>La IA reorganizó el archivo. Hay ${plural(review.alerts, 'alerta', 'alertas')} para revisar.</b>`
      : '<b>La IA reorganizó el archivo. No hay alertas automáticas, pero igual compará con el original.</b>'
    : '<b>Avisos</b>'
  return `<div class="rv-box ${review.alerts ? 'has-alerts' : ''}">${head}${review.blocks
    .map((b) => `<div class="rv-block ${b.alert ? 'alert' : ''}"><div class="rv-title">${esc(b.title)}</div><ul>${b.items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul></div>`)
    .join('')}</div>`
}

function renderEditor() {
  const d = editing
  $('edTitle').textContent = d.title
  const state = d.has_draft
    ? 'Borrador: el asistente todavía no lo usa.'
    : d.chunks
      ? 'Versión publicada: al guardar se crea un borrador; el asistente sigue con esta hasta que publiques.'
      : 'Sin publicar.'
  $('edMeta').textContent = `${FLOWS[d.audience]} · ${d.original_name || d.source_id} · ${state}`
  $('edReview').innerHTML = reviewHtml(d.has_draft ? d.review : null)
  $('editor').querySelector('[data-ed="discard"]').hidden = !d.has_draft
  $('editor').querySelector('[data-ed="original"]').hidden = !d.has_original
  updateEditorButtons()
}

function updateEditorButtons() {
  const dirty = editorDirty()
  $('editor').querySelector('[data-ed="save"]').disabled = !dirty
  $('editor').querySelector('[data-ed="publish"]').disabled = !dirty && !editing.has_draft
}

function setEditorTab(tab) {
  for (const b of $('editor').querySelectorAll('[data-tab]')) b.classList.toggle('on', b.dataset.tab === tab)
  $('edText').hidden = tab !== 'edit'
  $('edPreview').hidden = tab !== 'preview'
  if (tab === 'preview') $('edPreview').innerHTML = docPreview($('edText').value)
}

async function openEditor(d) {
  try {
    editing = await api(`/documents/detail?${new URLSearchParams({ audience: d.audience, source_id: d.source_id })}`)
    const text = editing.draft_markdown ?? editing.markdown
    if (text == null) return toast('Este documento todavía no tiene el texto guardado: volvé a ingerirlo o reemplazá el archivo.', 'bad')
    editorBase = text.replace(/\r\n/g, '\n')
    $('edText').value = editorBase
    $('edCheck').innerHTML = ''
    setEditorTab('edit')
    renderEditor()
    $('editor').showModal()
  } catch (e) {
    handleError(e)
  }
}

function closeEditor(force = false) {
  if (!force && editorDirty() && !confirm('Hay cambios sin guardar. ¿Cerrar igual?')) return
  editing = null
  $('editor').close()
}

function showCheck(check) {
  const items = [...(check?.problems || []).map((p) => ['bad', p]), ...(check?.warnings || []).map((w) => ['', w])]
  $('edCheck').innerHTML = items.length
    ? `<ul>${items.map(([c, t]) => `<li class="${c}">${esc(t)}</li>`).join('')}</ul>`
    : '<span class="ok">Formato correcto.</span>'
}

async function saveEditor() {
  const out = await api('/documents/draft', { method: 'PUT', body: { ...docRef(editing), markdown: $('edText').value } })
  editing = { ...editing, ...out.document }
  editorBase = $('edText').value
  showCheck(out.format)
  renderEditor()
  return out
}

$('editor').addEventListener('click', async (e) => {
  const btn = e.target.closest('button')
  if (!btn || !editing) return
  if (btn.dataset.tab) return setEditorTab(btn.dataset.tab)
  const act = btn.dataset.ed
  try {
    if (act === 'close') closeEditor()
    else if (act === 'original') downloadOriginal(editing)
    else if (act === 'save') {
      await saveEditor()
      toast('Borrador guardado: el asistente sigue usando la versión publicada', 'ok')
      loadFiles()
    } else if (act === 'publish') {
      if (editorDirty()) await saveEditor()
      const alerts = editing.review?.alerts || 0
      if (alerts && !confirm(`El borrador tiene ${plural(alerts, 'alerta', 'alertas')} de la revisión automática. ¿Ya lo comparaste con el original y querés publicarlo?`)) return
      btn.disabled = true
      const out = await api('/documents/publish', { method: 'POST', body: docRef(editing) })
      const vis = out.client_types.length ? '' : ' Todavía no lo consulta ningún cliente: tildalos en la lista.'
      toast(`${out.title}: publicado (${plural(out.chunks, 'fragmento', 'fragmentos')}).${vis}`, 'ok')
      closeEditor(true)
      loadFiles()
    } else if (act === 'discard') {
      const never = !editing.chunks
      if (!confirm(never ? '¿Descartar el borrador? Como nunca se publicó, se elimina el documento.' : '¿Descartar el borrador? Se mantiene la versión publicada.')) return
      await api('/documents/discard-draft', { method: 'POST', body: docRef(editing) })
      toast(never ? 'Borrador descartado y documento eliminado' : 'Borrador descartado', 'ok')
      closeEditor(true)
      loadFiles()
    }
  } catch (err) {
    handleError(err)
    if (editing) updateEditorButtons()
  }
})
$('edText').addEventListener('input', updateEditorButtons)
$('editor').addEventListener('cancel', (e) => {
  e.preventDefault() // Escape: pasa por el aviso de cambios sin guardar
  closeEditor()
})

// --- vistas: conversaciones | archivos (#archivos) -------------------------------------------------------

function showView(v) {
  view = v
  for (const b of $('views').children) b.classList.toggle('on', b.dataset.view === v)
  document.querySelector('.body').hidden = v !== 'conv'
  $('stats').hidden = v !== 'conv'
  $('files').hidden = v !== 'files'
  if (v === 'files') loadFiles()
  else refresh() // al volver, ponerse al día (en Archivos el refresco de conversaciones queda en pausa)
}

$('views').addEventListener('click', (e) => {
  const b = e.target.closest('button')
  if (!b || b.dataset.view === view) return
  // El hash manda: hashchange cambia la vista (y el botón Atrás del navegador funciona).
  location.hash = b.dataset.view === 'files' ? FILES_HASH : current?.session_id || ''
})

// --- acceso ----------------------------------------------------------------------------------------------

function logout(msg) {
  clearInterval(timer)
  token = ''
  store.set('eco_admin_token', '')
  $('app').hidden = true
  $('login').hidden = false
  $('loginError').textContent = msg || ''
  $('author').value = author
}

async function start() {
  $('login').hidden = true
  $('app').hidden = false
  $('me').textContent = author
  current = null
  listSig = currentSig = ''
  const id = decodeURIComponent(location.hash.slice(1))
  if (id === FILES_HASH) showView('files')
  else {
    showView('conv')
    if (id) openConversation(id, { force: true })
  }
  clearInterval(timer)
  timer = setInterval(() => document.hidden || view !== 'conv' || refresh(), REFRESH_MS)
}

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault()
  token = $('token').value.trim()
  author = $('author').value.trim()
  try {
    await api('/ping')
    store.set('eco_admin_token', token)
    store.set('eco_admin_author', author, localStorage)
    $('token').value = ''
    start()
  } catch (err) {
    $('loginError').textContent = err.message
  }
})

$('logout').addEventListener('click', () => logout())

// Enlaces directos (#<session_id> o #archivos), también si cambia solo el hash con la página ya abierta.
window.addEventListener('hashchange', () => {
  if (!token) return
  const id = decodeURIComponent(location.hash.slice(1))
  if (id === FILES_HASH) return view === 'files' || showView('files')
  if (view !== 'conv') showView('conv')
  if (id && id !== current?.session_id) openConversation(id, { force: true })
})

if (token && author) api('/ping').then(start).catch(() => logout())
else logout()
