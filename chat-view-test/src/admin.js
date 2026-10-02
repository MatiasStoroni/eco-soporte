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

// --- API -------------------------------------------------------------------------------------------------

class AuthError extends Error {}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) throw new AuthError('Token inválido.')
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
        steps.push([`Generó #${e.attempt ?? '?'}`, ''])
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
  if (e instanceof AuthError) return logout('La sesión expiró o el token cambió.')
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
  await refresh()
  const id = decodeURIComponent(location.hash.slice(1))
  if (id) openConversation(id, { force: true })
  clearInterval(timer)
  timer = setInterval(() => document.hidden || refresh(), REFRESH_MS)
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

// Enlaces directos (#<session_id>), también si cambia solo el hash con la página ya abierta.
window.addEventListener('hashchange', () => {
  const id = decodeURIComponent(location.hash.slice(1))
  if (token && id && id !== current?.session_id) openConversation(id, { force: true })
})

if (token && author) api('/ping').then(start).catch(() => logout())
else logout()
