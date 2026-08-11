'use strict'
/*
 * ZAQ · Serviço de WhatsApp via QR Code (Baileys), multi-tenant por conta_id.
 *
 * Fala HTTP com o app Python (web). Autenticação simples por segredo compartilhado
 * (header x-wa-secret == WA_QR_SHARED_SECRET). Estado da sessão no Postgres.
 *
 * Rotas (todas exigem o header do segredo):
 *   POST /session/:conta/iniciar   -> garante o socket; {status, qr?} (qr = data URL)
 *   GET  /session/:conta/status    -> {status, qr?}
 *   POST /session/:conta/enviar    -> body {numero, texto} -> {ok, id?} | {ok:false,erro}
 *   POST /session/:conta/sair      -> logout + limpa estado -> {ok}
 *   GET  /saude                    -> {ok:true} (sem segredo; healthcheck do Render)
 *
 * Ao chegar mensagem de texto, faz POST em ${APP_URL}/webhooks/wa-qr com o mesmo
 * segredo e {conta_id, sender, texto, nome, id}.
 *
 * Histórico: logo após conectar/parear, o Baileys manda o histórico de conversas
 * (messaging-history.set). Mensagens de ANTES de conectar (últimos 30 dias só) vão
 * pra ${APP_URL}/webhooks/wa-qr/historico — viram conversa órfã no Zaq, sem gerar
 * lead sozinho (o vendedor decide se vale virar lead pra um número antigo).
 *
 * Env: DATABASE_URL, WA_QR_SHARED_SECRET, APP_URL, PORT (default 3000).
 */
const http = require('node:http')
const { Pool } = require('pg')
const pino = require('pino')
const QRCode = require('qrcode')
const makeWASocket = require('@whiskeysockets/baileys').default
const { DisconnectReason, makeCacheableSignalKeyStore, fetchLatestBaileysVersion } =
  require('@whiskeysockets/baileys')
const { useDbAuthState } = require('./auth-db')

const PORT = parseInt(process.env.PORT || '3000', 10)
const SEGREDO = process.env.WA_QR_SHARED_SECRET || ''
const APP_URL = (process.env.APP_URL || '').replace(/\/+$/, '')
const log = pino({ level: process.env.LOG_LEVEL || 'info' })

if (!process.env.DATABASE_URL) { log.error('Falta DATABASE_URL'); process.exit(1) }
if (!SEGREDO) { log.error('Falta WA_QR_SHARED_SECRET'); process.exit(1) }

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: /localhost|127\.0\.0\.1/.test(process.env.DATABASE_URL) ? false : { rejectUnauthorized: false },
  max: 4
})

// contaId -> { sock, status, qr, iniciando }
const sessoes = new Map()

function jidDe (numero) {
  const d = String(numero || '').replace(/\D/g, '')
  if (!d) return null
  const comDDI = (!d.startsWith('55') && d.length <= 11) ? '55' + d : d
  return comDDI + '@s.whatsapp.net'
}

function textoDaMsg (m) {
  const msg = m.message || {}
  return (msg.conversation ||
          (msg.extendedTextMessage && msg.extendedTextMessage.text) ||
          (msg.imageMessage && msg.imageMessage.caption) ||
          (msg.videoMessage && msg.videoMessage.caption) || '').trim()
}

async function repassarEntrada (contaId, m) {
  if (!APP_URL) { log.warn({ contaId }, 'APP_URL vazio — não repassa entrada'); return }
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto || !jid || jid.endsWith('@g.us') || jid === 'status@broadcast') {
    log.info({ contaId, temTexto: !!texto, jid }, 'entrada ignorada (sem texto, grupo ou status)')
    return
  }
  const sender = jid.split('@')[0]
  const corpo = JSON.stringify({
    conta_id: contaId, sender, texto,
    nome: m.pushName || '', id: (m.key && m.key.id) || ''
  })
  try {
    const r = await fetch(APP_URL + '/webhooks/wa-qr', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: corpo
    })
    if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr respondeu não-ok')
    else log.info({ contaId, sender: sender.slice(0, 6) + '…' }, 'entrada repassada ao webhook ✓')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar entrada') }
}

const HISTORICO_JANELA_SEGUNDOS = 30 * 24 * 3600 // só os últimos 30 dias — ver README (risco QR)

// Histórico importado (evento messaging-history.set, só dispara logo após conectar/parear).
// Vira conversa ÓRFÃ do lado Python (nunca gera lead sozinho) — ver /webhooks/wa-qr/historico.
async function repassarHistorico (contaId, m) {
  if (!APP_URL) return
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto || !jid || jid.endsWith('@g.us') || jid === 'status@broadcast') return
  if (m.key && m.key.fromMe) return
  const ts = Number(m.messageTimestamp) || 0
  const corteSegundos = Math.floor(Date.now() / 1000) - HISTORICO_JANELA_SEGUNDOS
  if (!ts || ts < corteSegundos) return
  const sender = jid.split('@')[0]
  const corpo = JSON.stringify({
    conta_id: contaId, sender, texto, quando: ts,
    id: (m.key && m.key.id) || ''
  })
  try {
    const r = await fetch(APP_URL + '/webhooks/wa-qr/historico', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: corpo
    })
    if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr/historico respondeu não-ok')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar histórico') }
}

async function iniciarSessao (contaId) {
  let s = sessoes.get(contaId)
  if (s && (s.status === 'conectado' || s.iniciando)) return s
  s = s || { status: 'desconectado', qr: null }
  s.iniciando = true
  sessoes.set(contaId, s)

  const { state, saveCreds, limparTudo } = await useDbAuthState(pool, contaId)
  s._limparTudo = limparTudo
  let version
  try { ({ version } = await fetchLatestBaileysVersion()) } catch (_) { /* usa o default */ }

  const sock = makeWASocket({
    version,
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, log) },
    printQRInTerminal: false,
    browser: ['ZAQ', 'Chrome', '1.0.0'],
    logger: log,
    // liga a sincronização de histórico (só chega logo após conectar/parear); o
    // filtro de janela fica em repassarHistorico — só os últimos 30 dias sobem pro Zaq.
    syncFullHistory: true,
    markOnlineOnConnect: false
  })
  s.sock = sock
  s.iniciando = false

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (u) => {
    const { connection, lastDisconnect, qr } = u
    if (qr) {
      try { s.qr = await QRCode.toDataURL(qr) } catch (_) { s.qr = null }
      s.status = 'aguardando_qr'
      log.info({ contaId }, 'QR gerado')
    }
    if (connection === 'open') {
      s.status = 'conectado'; s.qr = null
      log.info({ contaId }, 'WhatsApp conectado')
    }
    if (connection === 'close') {
      const code = (lastDisconnect && lastDisconnect.error &&
        lastDisconnect.error.output && lastDisconnect.error.output.statusCode) || 0
      const deslogado = code === DisconnectReason.loggedOut
      s.status = deslogado ? 'desconectado' : 'reconectando'
      s.qr = null
      log.warn({ contaId, code, deslogado }, 'conexão fechou')
      if (deslogado) {
        try { await limparTudo() } catch (_) {}
        sessoes.delete(contaId)
      } else {
        setTimeout(() => { iniciarSessao(contaId).catch(() => {}) }, 2500)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // log sempre que o evento disparar, mesmo filtrado — sem isso não dava pra saber
    // se o socket estava recebendo mensagem nenhuma ou só descartando pelo filtro.
    log.info({ contaId, type, n: messages.length }, 'messages.upsert recebido')
    if (type !== 'notify') return
    for (const m of messages) {
      if (m.key && m.key.fromMe) { log.info({ contaId }, 'ignorada: fromMe'); continue }
      await repassarEntrada(contaId, m)
    }
  })

  // Histórico (só dispara logo após conectar/parear, isLatest marca o último lote).
  // Mensagens de ANTES de conectar viram conversa ÓRFÃ (nunca lead sozinho) — importa
  // só os últimos 30 dias, ver HISTORICO_JANELA_SEGUNDOS.
  sock.ev.on('messaging-history.set', async ({ messages, isLatest }) => {
    log.info({ contaId, n: messages.length, isLatest }, 'messaging-history.set recebido')
    for (const m of messages) { await repassarHistorico(contaId, m) }
  })

  return s
}

// --------------------------------------------------------------- HTTP

function json (res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, { 'content-type': 'application/json' })
  res.end(body)
}

function lerBody (req) {
  return new Promise((resolve) => {
    let d = ''
    req.on('data', (c) => { d += c; if (d.length > 1e6) req.destroy() })
    req.on('end', () => { try { resolve(d ? JSON.parse(d) : {}) } catch (_) { resolve({}) } })
    req.on('error', () => resolve({}))
  })
}

const servidor = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://x')
    const partes = url.pathname.split('/').filter(Boolean)

    if (req.method === 'GET' && url.pathname === '/saude') return json(res, 200, { ok: true })

    if (req.headers['x-wa-secret'] !== SEGREDO) return json(res, 403, { ok: false, erro: 'segredo' })

    // /session/:conta/<acao>
    if (partes[0] === 'session' && partes[1]) {
      const contaId = parseInt(partes[1], 10)
      const acao = partes[2] || ''
      if (!contaId) return json(res, 400, { ok: false, erro: 'conta' })

      if (req.method === 'POST' && acao === 'iniciar') {
        const s = await iniciarSessao(contaId)
        return json(res, 200, { ok: true, status: s.status, qr: s.qr })
      }
      if (req.method === 'GET' && acao === 'status') {
        const s = sessoes.get(contaId) || { status: 'desconectado', qr: null }
        return json(res, 200, { ok: true, status: s.status, qr: s.qr || null })
      }
      if (req.method === 'POST' && acao === 'enviar') {
        const body = await lerBody(req)
        const jid = jidDe(body.numero)
        const s = sessoes.get(contaId)
        if (!s || s.status !== 'conectado' || !s.sock) return json(res, 200, { ok: false, erro: 'desconectado' })
        if (!jid) return json(res, 200, { ok: false, erro: 'numero_invalido' })
        try {
          const r = await s.sock.sendMessage(jid, { text: String(body.texto || '').slice(0, 4000) })
          return json(res, 200, { ok: true, id: (r && r.key && r.key.id) || '' })
        } catch (e) { return json(res, 200, { ok: false, erro: String(e).slice(0, 180) }) }
      }
      if (req.method === 'POST' && acao === 'sair') {
        const s = sessoes.get(contaId)
        try { if (s && s.sock) await s.sock.logout() } catch (_) {}
        try { if (s && s._limparTudo) await s._limparTudo() } catch (_) {}
        sessoes.delete(contaId)
        return json(res, 200, { ok: true })
      }
    }
    return json(res, 404, { ok: false, erro: 'rota' })
  } catch (e) {
    log.error({ e: String(e) }, 'erro na requisição')
    return json(res, 500, { ok: false, erro: 'interno' })
  }
})

servidor.listen(PORT, () => log.info({ PORT }, 'wa-qr no ar'))
