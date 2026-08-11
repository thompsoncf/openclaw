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
 * Saída direto do celular: mensagem que o vendedor manda fora do Zaq (Baileys ecoa
 * como fromMe) vai pra ${APP_URL}/webhooks/wa-qr/saida — só registra em conversa
 * que já existe, nunca cria lead novo.
 *
 * Deslogar de vez (não queda temporária) avisa ${APP_URL}/webhooks/wa-qr/deslogado
 * — apaga o histórico de conversa daquele canal (não apaga o lead em si).
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

// Sem isso, um erro assíncrono que escapa de um try/catch (ex.: dentro de um
// listener de evento do Baileys) só aparecia no Render como um stack trace cru
// do Node, sem contexto nenhum — ou o processo caía silencioso e reiniciava sem
// deixar rastro de PORQUE. Loga estruturado antes de qualquer coisa.
process.on('unhandledRejection', (err) => log.error({ err: String(err && err.stack || err) }, 'unhandledRejection'))
process.on('uncaughtException', (err) => log.error({ err: String(err && err.stack || err) }, 'uncaughtException'))

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: /localhost|127\.0\.0\.1/.test(process.env.DATABASE_URL) ? false : { rejectUnauthorized: false },
  max: 4
})

// contaId -> { sock, status, qr, iniciando }
const sessoes = new Map()

// contaId -> Map(jid @lid -> jid real @s.whatsapp.net). Só existe pra reconstruir o
// número real de mensagens de HISTÓRICO (ver comentário em numeroReal mais abaixo).
const lidMaps = new Map()

function atualizarLidMap (contaId, contacts) {
  if (!Array.isArray(contacts) || !contacts.length) return
  let mapa = lidMaps.get(contaId)
  if (!mapa) { mapa = new Map(); lidMaps.set(contaId, mapa) }
  for (const ct of contacts) {
    if (ct && ct.lid && ct.jid) mapa.set(ct.lid, ct.jid)
  }
}

// Roda fn(item) pra cada item com no máximo `limite` em paralelo — usado pra não
// processar histórico um por um (um POST HTTP síncrono de cada vez pra 5000
// mensagens represa mensagem AO VIVO atrás dessa fila enorme).
async function comLimiteDeConcorrencia (itens, limite, fn) {
  let i = 0
  async function trabalhador () {
    while (i < itens.length) {
      const item = itens[i++]
      await fn(item)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limite, itens.length) }, trabalhador))
}

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

// O WhatsApp esconde o número de telefone real de alguns contatos atrás de um ID
// interno opaco (@lid, "linked ID" — privacidade). Em mensagem AO VIVO o Baileys
// manda o número real em m.key.senderPn (formato <numero>@s.whatsapp.net); sem
// isso, sender = jid.split('@')[0] vira um ID sem sentido, não um telefone.
// Mensagem de HISTÓRICO (messaging-history.set) não tem senderPn — esse campo só
// existe no decode de mensagem ao vivo — então cai pro mapa lid->jid construído a
// partir da lista de contatos que vem junto no próprio histórico (atualizarLidMap).
function numeroReal (m, contaId) {
  if (m.key && m.key.senderPn) return m.key.senderPn
  const remoteJid = (m.key && m.key.remoteJid) || ''
  if (remoteJid.endsWith('@lid')) {
    const real = lidMaps.get(contaId) && lidMaps.get(contaId).get(remoteJid)
    if (real) return real
  }
  return remoteJid
}

// Canais/newsletters do WhatsApp usam o MESMO formato numérico de ID que grupos
// (ex.: 120363...), só que com sufixo @newsletter em vez de @g.us — sem filtrar
// isso, conteúdo de canal (notícia, propaganda) vira "mensagem de lead".
function ehConversaValida (jid) {
  return !!jid && !jid.endsWith('@g.us') && !jid.endsWith('@newsletter') && jid !== 'status@broadcast'
}

async function repassarEntrada (contaId, m) {
  if (!APP_URL) { log.warn({ contaId }, 'APP_URL vazio — não repassa entrada'); return }
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto || !ehConversaValida(jid)) {
    log.info({ contaId, temTexto: !!texto, jid }, 'entrada ignorada (sem texto, grupo, canal ou status)')
    return
  }
  const sender = numeroReal(m, contaId).split('@')[0]
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

// Mensagem que o VENDEDOR mandou direto pelo WhatsApp do celular (fora do Zaq) —
// o Baileys ecoa de volta como fromMe (todo aparelho ligado à mesma conta vê a
// saída dos outros). Sem repassar isso, a conversa no Zaq ficava capenga: só
// aparecia o que o lead mandou, nunca a resposta se o vendedor respondeu pelo
// próprio celular. Dedup por provider_sid do lado Python evita duplicar quando
// a mensagem já saiu PELO Zaq (que já grava na hora do envio).
async function repassarSaida (contaId, m) {
  if (!APP_URL) return
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto || !ehConversaValida(jid)) return
  const destinatario = numeroReal(m, contaId).split('@')[0]
  const corpo = JSON.stringify({
    conta_id: contaId, sender: destinatario, texto,
    id: (m.key && m.key.id) || ''
  })
  try {
    const r = await fetch(APP_URL + '/webhooks/wa-qr/saida', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: corpo
    })
    if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr/saida respondeu não-ok')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar saída') }
}

// Deslogou de vez (não é queda temporária) — avisa o Python pra limpar o
// histórico de conversa daquele canal. Best-effort: se falhar, o histórico
// só fica desatualizado, não trava o logout.
async function avisarDeslogado (contaId) {
  if (!APP_URL) return
  try {
    await fetch(APP_URL + '/webhooks/wa-qr/deslogado', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: JSON.stringify({ conta_id: contaId })
    })
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao avisar deslogado') }
}

const HISTORICO_JANELA_SEGUNDOS = 30 * 24 * 3600 // só os últimos 30 dias — ver README (risco QR)

// Histórico importado (evento messaging-history.set, só dispara logo após conectar/parear).
// Vira conversa ÓRFÃ do lado Python (nunca gera lead sozinho) — ver /webhooks/wa-qr/historico.
async function repassarHistorico (contaId, m) {
  if (!APP_URL) return
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto || !ehConversaValida(jid)) return
  if (m.key && m.key.fromMe) return
  const ts = Number(m.messageTimestamp) || 0
  const corteSegundos = Math.floor(Date.now() / 1000) - HISTORICO_JANELA_SEGUNDOS
  if (!ts || ts < corteSegundos) return
  const sender = numeroReal(m, contaId).split('@')[0]
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
  // Já tem sessão viva? Devolve a que existe em vez de abrir outra. 'aguardando_qr'
  // entra na lista porque a página chama /iniciar sozinha ao carregar: sem isso,
  // recarregar a página (ou abrir numa segunda aba) no meio de um pareamento
  // derrubava o socket do QR que a pessoa estava escaneando e gerava outro QR.
  if (s && (s.iniciando || ((s.status === 'conectado' || s.status === 'aguardando_qr') && s.sock))) return s
  // Reconexão automática (setTimeout logo após o close handler) x início a frio
  // (primeiro "Gerar QR" depois do processo subir, sem sessão em memória): só o
  // início a frio pode estar herdando credenciais de um processo anterior que
  // caiu no meio do pareamento. Uma reconexão automática dentro do MESMO processo
  // é exatamente o passo normal do Baileys logo após "pairing configured
  // successfully" (ele sempre fecha com stream:error 515 e reconecta sozinho pra
  // terminar o registro) — tratar isso como "travado" apaga o pareamento que
  // acabou de dar certo, e é por isso que nenhum QR terminava de conectar.
  const reconexaoAutomatica = !!(s && s.status === 'reconectando')
  s = s || { status: 'desconectado', qr: null }
  s.iniciando = true
  sessoes.set(contaId, s)
  log.info({ contaId }, 'iniciarSessao: começando')

  // Trava de segurança: se por qualquer motivo isso nunca terminar (nem sucesso
  // nem erro — ex.: uma chamada de rede que trava sem nunca rejeitar), sem isso
  // `iniciando` ficava travado em true PRA SEMPRE, e todo clique em "Gerar QR"
  // dali em diante virava um no-op silencioso, sem log nenhum explicando por quê.
  const destravar = setTimeout(() => {
    if (s.iniciando) {
      log.error({ contaId }, 'iniciarSessao: travou mais de 30s sem terminar — destravando')
      s.iniciando = false
    }
  }, 30000)

  try {
    let { state, saveCreds, limparTudo } = await useDbAuthState(pool, contaId)
    log.info({ contaId }, 'iniciarSessao: creds carregadas do banco')
    // Pareamento travado pela metade: o celular escaneou e o WhatsApp já assinou a
    // conta (creds.account preenchido), mas o processo caiu/reiniciou antes de
    // terminar o handshake (registered nunca virou true). Reaproveitar essas
    // chaves meio-consumidas faz TODO QR novo falhar com "não foi possível
    // conectar o dispositivo" — já aconteceu mais de uma vez, sempre coincidindo
    // com um deploy no meio de um pareamento em andamento. Detecta e reseta
    // sozinho pra um pareamento limpo, sem precisar mexer no banco na mão. NUNCA
    // roda numa reconexão automática (ver comentário acima) — só num início a frio.
    if (!reconexaoAutomatica && state.creds && state.creds.registered === false && state.creds.account) {
      log.warn({ contaId }, 'pareamento travado pela metade detectado — limpando pra recomeçar do zero')
      await limparTudo()
      ;({ state, saveCreds, limparTudo } = await useDbAuthState(pool, contaId))
    }
    s._limparTudo = limparTudo
    let version
    try {
      // fetchLatestBaileysVersion não tem timeout próprio — se a rede travar (não
      // dar erro, só nunca responder), o await ficava pendurado pra sempre e nada
      // disso aqui embaixo rodava. 8s é de sobra pra um GET numa API pública.
      version = (await Promise.race([
        fetchLatestBaileysVersion(),
        new Promise((_r, rej) => setTimeout(() => rej(new Error('timeout ao buscar versão')), 8000))
      ])).version
    } catch (e) {
      log.warn({ contaId, e: String(e) }, 'fetchLatestBaileysVersion falhou/travou — usando versão default')
    }
    log.info({ contaId, version }, 'iniciarSessao: versão resolvida, abrindo socket')

    // Fecha o socket ANTERIOR antes de abrir outro. Sem isso só a referência
    // (s.sock) era sobrescrita — o socket velho continuava vivo, conectado e com
    // os listeners escutando. Dois sockets com a MESMA credencial ao mesmo tempo
    // fazem o WhatsApp derrubar um deles (401/440), e o handler do socket velho
    // tratava isso como "deslogou de vez": apagava a credencial inteira da conta
    // (wa_qr_auth zerada) E mandava apagar todo o histórico de conversa — matando
    // junto a sessão nova, que estava perfeitamente válida.
    if (s.sock) {
      try {
        s.sock._descartado = true
        s.sock.end(undefined)
        log.info({ contaId }, 'socket anterior fechado antes de abrir um novo')
      } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao fechar socket anterior') }
    }

    const sock = makeWASocket({
      version,
      auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, log) },
      printQRInTerminal: false,
      browser: ['ZAQ', 'Chrome', '1.0.0'],
      logger: log,
      // syncFullHistory:true pede pro WhatsApp o histórico INTEIRO da conta (meses/
      // anos, não só os últimos 30 dias que a gente já filtra em repassarHistorico)
      // — numa conta movimentada isso é uma fila de dezenas de milhares de
      // mensagens, cada uma com um POST síncrono pro webhook; enquanto essa fila não
      // esvazia, mensagem NOVA (ao vivo) fica represada atrás dela e parece que
      // "parou de funcionar do nada". false é o default do Baileys: sincroniza só o
      // recente, que já cobre bem os últimos 30 dias numa conta ativa.
      syncFullHistory: false,
      markOnlineOnConnect: false
    })
    s.sock = sock
    log.info({ contaId }, 'iniciarSessao: socket criado, registrando listeners')

    sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (u) => {
    // Socket já descartado (substituído por um novo): o end() dele ainda emite um
    // último 'close'. Sem ignorar isso, esse evento tardio mexia no estado da
    // sessão NOVA — marcava desconectado, agendava reconexão e, no pior caso,
    // disparava a limpeza de credencial/histórico da sessão que estava ok.
    if (sock._descartado) return
    const { connection, lastDisconnect, qr } = u
    if (qr) {
      try { s.qr = await QRCode.toDataURL(qr) } catch (_) { s.qr = null }
      s.status = 'aguardando_qr'
      log.info({ contaId }, 'QR gerado')
    }
    if (connection === 'open') {
      s.status = 'conectado'; s.qr = null
      // Sincronizando o histórico recente que o WhatsApp manda sozinho ao parear
      // (filtrado aos últimos 30 dias em repassarHistorico) — some sozinho quando
      // parar de chegar evento novo por 5s, ou no máximo em 25s
      // (nem todo pareamento dispara messaging-history.set; sem isso a barra
      // ficava carregando pra sempre quando não tinha histórico pra sincronizar).
      s.sincronizando = true
      s.syncProgress = 0
      clearTimeout(s._syncTimeout)
      s._syncTimeout = setTimeout(() => { s.sincronizando = false }, 25000)
      log.info({ contaId }, 'WhatsApp conectado')
    }
    if (connection === 'close') {
      const code = (lastDisconnect && lastDisconnect.error &&
        lastDisconnect.error.output && lastDisconnect.error.output.statusCode) || 0
      // 440 = connectionReplaced: OUTRA sessão assumiu esta credencial (outro
      // aparelho, outra instância do serviço). Não é logout do usuário, então não
      // pode apagar nada — e reconectar aqui vira uma guerra de sessões, cada uma
      // derrubando a outra em loop até o WhatsApp bloquear a conta.
      if (code === DisconnectReason.connectionReplaced) {
        s.status = 'desconectado'
        s.qr = null
        log.warn({ contaId }, 'conexão substituída por outra sessão — parando sem apagar nada')
        return
      }
      const deslogado = code === DisconnectReason.loggedOut
      s.status = deslogado ? 'desconectado' : 'reconectando'
      s.qr = null
      log.warn({ contaId, code, deslogado }, 'conexão fechou')
      if (deslogado) {
        // Só conta como logout DE VERDADE (o que autoriza apagar o histórico de
        // conversa) se a sessão chegou a ficar registrada. Um 401 no meio de um
        // pareamento que nunca completou não é "o usuário desconectou" — apagar o
        // histórico aí destrói conversa à toa, sem nada em troca.
        const eraRegistrada = !!(state.creds && state.creds.registered)
        try { await limparTudo() } catch (e) { log.warn({ contaId, e: String(e) }, 'limparTudo falhou') }
        sessoes.delete(contaId)
        lidMaps.delete(contaId)
        log.warn({ contaId, eraRegistrada }, 'deslogado — credenciais limpas')
        if (eraRegistrada) {
          avisarDeslogado(contaId).catch((e) => log.warn({ contaId, e: String(e) }, 'avisarDeslogado falhou'))
        }
      } else {
        setTimeout(() => {
          iniciarSessao(contaId).catch((e) => log.error({ contaId, e: String(e) }, 'reconexão automática falhou'))
        }, 2500)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // log sempre que o evento disparar, mesmo filtrado — sem isso não dava pra saber
    // se o socket estava recebendo mensagem nenhuma ou só descartando pelo filtro.
    log.info({ contaId, type, n: messages.length }, 'messages.upsert recebido')
    // MessageUpsertType só tem 'notify' (ao vivo) e 'append' (mensagem legítima que
    // chegou enquanto a conexão teve uma variação breve — "estava offline, aqui está
    // o que você perdeu", node.attrs.offline no Baileys). NÃO é o histórico em massa
    // (isso é o messaging-history.set, evento separado) — descartar 'append' jogava
    // fora mensagem de verdade da conversa, tanto entrada quanto o eco de mensagem
    // que o vendedor manda direto pelo celular (fromMe).
    for (const m of messages) {
      if (m.key && m.key.fromMe) { await repassarSaida(contaId, m); continue }
      await repassarEntrada(contaId, m)
    }
  })

  // Histórico (só dispara logo após conectar/parear). Mensagens de ANTES de
  // conectar viram conversa ÓRFÃ (nunca lead sozinho) — importa só os últimos
  // 30 dias, ver HISTORICO_JANELA_SEGUNDOS. `progress` (0-100) vem do próprio
  // Baileys por bloco sincronizado — usa pra alimentar a barra de carregamento
  // do painel; sem evento novo por 5s, dá como concluído (ver timeout acima).
  sock.ev.on('messaging-history.set', async ({ messages, contacts, isLatest, progress, syncType }) => {
    log.info({ contaId, n: messages.length, isLatest, progress, syncType }, 'messaging-history.set recebido')
    // Cada onda de histórico traz também os contatos daquele bloco — usa pra
    // resolver @lid -> número real (ver comentário em numeroReal). Faz isso ANTES
    // de repassar as mensagens da mesma onda, senão o mapa fica sempre um bloco
    // atrasado.
    atualizarLidMap(contaId, contacts)
    // O histórico chega em ondas (ex.: recentes primeiro, depois o histórico
    // completo) e cada onda reinicia o progress do zero — sem o Math.max a barra
    // sobe até 100%, "volta" pra perto de 0% quando a próxima onda começa, e
    // parece quebrada na tela mesmo sem ter dado erro nenhum.
    if (typeof progress === 'number') s.syncProgress = Math.max(s.syncProgress || 0, progress)
    clearTimeout(s._syncTimeout)
    s._syncTimeout = setTimeout(() => { s.sincronizando = false }, 5000)
    await comLimiteDeConcorrencia(messages, 8, (m) => repassarHistorico(contaId, m))
  })
  } catch (e) {
    log.error({ contaId, e: String(e && e.stack || e) }, 'iniciarSessao: falhou')
    s.status = 'desconectado'
  } finally {
    clearTimeout(destravar)
    s.iniciando = false
  }

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
        return json(res, 200, { ok: true, status: s.status, qr: s.qr,
          sincronizando: !!s.sincronizando, syncProgress: s.syncProgress || 0 })
      }
      if (req.method === 'GET' && acao === 'status') {
        const s = sessoes.get(contaId) || { status: 'desconectado', qr: null }
        return json(res, 200, { ok: true, status: s.status, qr: s.qr || null,
          sincronizando: !!s.sincronizando, syncProgress: s.syncProgress || 0 })
      }
      if (req.method === 'POST' && acao === 'enviar') {
        const body = await lerBody(req)
        const jid = jidDe(body.numero)
        const s = sessoes.get(contaId)
        // log sempre, mesmo quando falha — sem isso não dava pra saber SE o
        // status realmente não era 'conectado' na hora do envio ou se o
        // sendMessage em si que deu erro (dois motivos bem diferentes).
        log.info({ contaId, status: s && s.status, temSock: !!(s && s.sock) }, 'enviar: tentativa')
        if (!s || s.status !== 'conectado' || !s.sock) return json(res, 200, { ok: false, erro: 'desconectado' })
        if (!jid) return json(res, 200, { ok: false, erro: 'numero_invalido' })
        try {
          const r = await s.sock.sendMessage(jid, { text: String(body.texto || '').slice(0, 4000) })
          log.info({ contaId, id: r && r.key && r.key.id }, 'enviar: sucesso ✓')
          return json(res, 200, { ok: true, id: (r && r.key && r.key.id) || '' })
        } catch (e) {
          log.warn({ contaId, e: String(e) }, 'enviar: sendMessage falhou')
          return json(res, 200, { ok: false, erro: String(e).slice(0, 180) })
        }
      }
      if (req.method === 'POST' && acao === 'sair') {
        const s = sessoes.get(contaId)
        try { if (s && s.sock) await s.sock.logout() } catch (_) {}
        // Se o serviço reiniciou desde a última vez que essa conta ficou conectada em
        // memória (deploy, crash, etc.), `s` não existe mais aqui — mas as credenciais
        // continuam salvas no Postgres. Sem isso, "Desconectar" virava um no-op: as
        // creds antigas ficavam presas no banco e o próximo QR tentava retomar uma
        // sessão inválida em vez de parear do zero, causando "Não foi possível
        // conectar o dispositivo" repetido no celular.
        try {
          if (s && s._limparTudo) { await s._limparTudo() } else {
            const { limparTudo } = await useDbAuthState(pool, contaId)
            await limparTudo()
          }
        } catch (_) {}
        sessoes.delete(contaId)
        lidMaps.delete(contaId)
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
