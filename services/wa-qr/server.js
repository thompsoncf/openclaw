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
 * Deslogar avisa ${APP_URL}/webhooks/wa-qr/deslogado — apaga o histórico de conversa
 * daquele canal (não apaga o lead em si). Vale tanto pro botão "Desconectar" quanto
 * pro logout feito pelo celular; queda temporária de conexão NUNCA apaga nada.
 *
 * Ao subir (deploy/restart/crash) o serviço religa sozinho todas as contas que já
 * estavam pareadas, lendo as credenciais do Postgres — ver restaurarSessoes().
 * Ninguém precisa abrir tela nem escanear QR de novo por causa de um deploy.
 *
 * Env: DATABASE_URL, WA_QR_SHARED_SECRET, APP_URL, PORT (default 3000).
 */
const http = require('node:http')
const { Pool } = require('pg')
const pino = require('pino')
const QRCode = require('qrcode')
const makeWASocket = require('@whiskeysockets/baileys').default
const { DisconnectReason, makeCacheableSignalKeyStore, fetchLatestBaileysVersion, proto } =
  require('@whiskeysockets/baileys')
const TIPO_HIST = proto.Message.HistorySyncNotification.HistorySyncType
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

// contaId -> Map(jid @lid -> jid real @s.whatsapp.net). Usado pra traduzir o ID
// interno de privacidade do WhatsApp pro telefone de verdade, nos três fluxos
// (entrada, saída e histórico). Alimentado por DUAS fontes: a lista de contatos
// que vem no histórico E cada mensagem ao vivo recebida (que traz lid + senderPn
// juntos). Persistido no Postgres (arquivo 'lidmap-...' na wa_qr_auth) porque a
// memória zera a cada deploy — e era justamente após deploy que o eco de mensagem
// mandada pelo celular pra um chat @lid se perdia por falta do mapa.
const lidMaps = new Map()

function aprenderLid (contaId, lidJid, pnJid) {
  if (!lidJid || !pnJid || !lidJid.endsWith('@lid') || !pnJid.endsWith('@s.whatsapp.net')) return
  let mapa = lidMaps.get(contaId)
  if (!mapa) { mapa = new Map(); lidMaps.set(contaId, mapa) }
  if (mapa.get(lidJid) === pnJid) return
  mapa.set(lidJid, pnJid)
  pool.query(
    `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
     values ($1,$2,$3, now())
     on conflict (conta_id, arquivo)
     do update set conteudo=excluded.conteudo, atualizado=now()`,
    [contaId, 'lidmap-' + lidJid, JSON.stringify(pnJid)]
  ).catch((e) => log.warn({ contaId, e: String(e) }, 'aprenderLid: falha ao persistir'))
}

function atualizarLidMap (contaId, contacts) {
  if (!Array.isArray(contacts) || !contacts.length) return
  for (const ct of contacts) {
    if (ct && ct.lid && ct.jid) aprenderLid(contaId, ct.lid, ct.jid)
  }
}

async function carregarLidMap (contaId) {
  try {
    const r = await pool.query(
      `select arquivo, conteudo from wa_qr_auth where conta_id=$1 and arquivo like 'lidmap-%'`,
      [contaId])
    if (!r.rows.length) return
    let mapa = lidMaps.get(contaId)
    if (!mapa) { mapa = new Map(); lidMaps.set(contaId, mapa) }
    for (const l of r.rows) {
      try { mapa.set(l.arquivo.slice('lidmap-'.length), JSON.parse(l.conteudo)) } catch (_) {}
    }
    log.info({ contaId, n: r.rows.length }, 'mapa lid->numero carregado do banco')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao carregar mapa lid') }
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

// Cache das mensagens que ENVIAMOS. Quando um aparelho não consegue decifrar uma
// mensagem nossa, ele pede reenvio (retry receipt) e o Baileys chama getMessage
// pra pegar o conteúdo original e re-encriptar. O default do Baileys devolve
// undefined (Defaults/index.js: `getMessage: async () => undefined`), e aí ele
// desiste com "recv retry request, but message not available" — a mensagem fica
// eternamente como "Aguardando mensagem" no celular, mesmo tendo saído do Zaq.
// O próprio Baileys deixa esse cache a cargo da aplicação (tem um TODO no fonte).
const MAX_ENVIADAS = 400
const enviadas = new Map()

function guardarEnviada (contaId, m) {
  if (!m || !m.key || !m.key.id || !m.message) return
  const k = contaId + ':' + m.key.id
  if (enviadas.has(k)) enviadas.delete(k)
  enviadas.set(k, m.message)
  // Map preserva ordem de inserção: o primeiro é sempre o mais antigo.
  while (enviadas.size > MAX_ENVIADAS) enviadas.delete(enviadas.keys().next().value)
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
  const remoteJid = (m.key && m.key.remoteJid) || ''
  if (m.key && m.key.senderPn) {
    // chat @lid + senderPn na mesma mensagem = par código->número de graça.
    // Aprende aqui pra que o ECO de uma resposta mandada pelo celular pra esse
    // mesmo chat (que só traz o @lid) consiga ser resolvido depois.
    aprenderLid(contaId, remoteJid, m.key.senderPn)
    return m.key.senderPn
  }
  if (remoteJid.endsWith('@lid')) {
    const real = lidMaps.get(contaId) && lidMaps.get(contaId).get(remoteJid)
    if (real) return real
  }
  return remoteJid
}

// Número do CHAT (o outro lado da conversa), resolvendo @lid pelo mapa. Numa
// mensagem de SAÍDA (fromMe) o "sender" somos NÓS — m.key.senderPn traz o número
// do próprio vendedor, não o do destinatário — então quem identifica a conversa
// aqui é sempre o remoteJid, nunca o senderPn (ver repassarSaida).
function numeroDoChat (m, contaId) {
  const remoteJid = (m.key && m.key.remoteJid) || ''
  if (remoteJid.endsWith('@lid')) {
    const real = lidMaps.get(contaId) && lidMaps.get(contaId).get(remoteJid)
    if (real) return real
  }
  return remoteJid
}

// @lid que o mapa não resolveu NÃO pode virar conversa: o ID interno não é
// telefone — a conversa nasce com um "código" no lugar do número e nem responder
// dá (o envio montaria um jid inválido). Melhor pular e logar; quando o contato
// mandar mensagem ao vivo, o senderPn traz o número real e a conversa nasce certa.
function semNumeroReal (resolvido, contaId, origem) {
  if (!resolvido.endsWith('@lid')) return false
  log.info({ contaId, jid: resolvido, origem }, 'ignorado: @lid sem número real no mapa')
  return true
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
  const resolvido = numeroReal(m, contaId)
  if (semNumeroReal(resolvido, contaId, 'entrada')) return
  const sender = resolvido.split('@')[0]
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
  if (!texto || !ehConversaValida(jid)) {
    log.info({ contaId, temTexto: !!texto, jid }, 'saída ignorada (sem texto, grupo, canal ou status)')
    return
  }
  // numeroDoChat, NÃO numeroReal: aqui quem manda é o vendedor, então senderPn
  // seria o número dele mesmo — o Python procuraria a conversa pelo número
  // errado, não acharia nenhuma e descartaria a mensagem em silêncio (era
  // exatamente por isso que mensagem mandada pelo celular não aparecia no Zaq).
  const chatResolvido = numeroDoChat(m, contaId)
  if (semNumeroReal(chatResolvido, contaId, 'saida')) return
  const destinatario = chatResolvido.split('@')[0]
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
    else log.info({ contaId, destinatario: destinatario.slice(0, 6) + '…' }, 'saída repassada ao webhook ✓')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar saída') }
}

// Deslogou de vez (não é queda temporária) — avisa o Python pra limpar o
// histórico de conversa daquele canal. Best-effort: se falhar, o histórico
// só fica desatualizado, não trava o logout.
// Nomes de contato. O WhatsApp manda o nome em DOIS campos diferentes, e o peso
// de cada um é diferente — quem decide não é o evento, é o CAMPO que veio:
//  - `name`   -> nome da AGENDA DO CELULAR (o que o vendedor salvou). Vem do
//    contacts.upsert (fullName do app-state) e do bloco de conversas do histórico
//    (Utils/history.js: `name: chat.name`). É o melhor nome que existe.
//  - `notify` -> pushName (o nome que a PRÓPRIA pessoa pôs no perfil dela). Vem do
//    contacts.update e da onda PUSH_NAME do histórico. Serve de reserva.
// Antes isto era decidido pelo evento (`daAgenda` valia pro lote inteiro): quando
// vinha marcado como agenda, só `name` era lido — e a onda PUSH_NAME, que tem
// SOMENTE `notify`, era descartada inteira. Era a maior fonte de nomes do
// WhatsApp indo pro lixo. Agora cada contato é classificado pelo campo que trouxe.
async function repassarContatos (contaId, contatos, daAgenda) {
  if (!APP_URL || !Array.isArray(contatos) || !contatos.length) return
  const agenda = []
  const reserva = []
  for (const ct of contatos) {
    if (!ct) continue
    // bônus: o contactAction traz lidJid junto do jid — mais um par pro mapa
    aprenderLid(contaId, ct.lid, ct.jid || ct.id)
    let jid = String(ct.jid || ct.id || '')
    // contato que só vem como @lid (privacidade) tem número real no mapa — sem
    // isso o nome da agenda desses contatos era descartado aqui mesmo
    if (jid.endsWith('@lid')) {
      const real = lidMaps.get(contaId) && lidMaps.get(contaId).get(jid)
      if (real) jid = real
    }
    if (!jid.endsWith('@s.whatsapp.net')) continue
    const nomeAgenda = daAgenda ? String(ct.name || '').trim() : ''
    const nome = (nomeAgenda || String(ct.notify || ct.name || '').trim()).slice(0, 120)
    if (!nome) continue
    ;(nomeAgenda ? agenda : reserva).push({ numero: jid.split('@')[0], nome })
  }
  await enviarContatos(contaId, agenda, true)
  await enviarContatos(contaId, reserva, false)
}

async function enviarContatos (contaId, lista, daAgenda) {
  if (!lista.length) return
  // a sincronização inicial pode despejar milhares de contatos de uma vez
  for (let i = 0; i < lista.length; i += 200) {
    const lote = lista.slice(i, i + 200)
    try {
      const r = await fetch(APP_URL + '/webhooks/wa-qr/contatos', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
        body: JSON.stringify({ conta_id: contaId, da_agenda: !!daAgenda, contatos: lote })
      })
      if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr/contatos respondeu não-ok')
      else log.info({ contaId, n: lote.length, daAgenda: !!daAgenda }, 'contatos repassados ✓')
    } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar contatos') }
  }
}

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
  const ts = Number(m.messageTimestamp) || 0
  const corteSegundos = Math.floor(Date.now() / 1000) - HISTORICO_JANELA_SEGUNDOS
  if (!ts || ts < corteSegundos) return
  // Mensagem ENVIADA (fromMe) entra também — antes era pulada e o histórico
  // importado ficava só com o lado do cliente, conversa pela metade. Nos dois
  // casos quem identifica a conversa é o CHAT (o outro lado), nunca o autor.
  const deMim = !!(m.key && m.key.fromMe)
  const resolvido = numeroDoChat(m, contaId)
  if (semNumeroReal(resolvido, contaId, 'historico')) return
  const sender = resolvido.split('@')[0]
  const corpo = JSON.stringify({
    conta_id: contaId, sender, texto, quando: ts, de_mim: deMim,
    // pushName só existe nas RECEBIDAS (numa fromMe o nome seria o do próprio
    // vendedor) — o Python só sobrescreve quando vem preenchido.
    nome: (!deMim && m.pushName) || '',
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
    const { state, saveCreds, limparTudo } = await useDbAuthState(pool, contaId)
    log.info({ contaId, pareada: !!(state.creds && state.creds.me) },
      'iniciarSessao: creds carregadas do banco')
    // o mapa código->número vive em memória e zera a cada deploy; recarrega do
    // banco pra o eco de mensagem do celular em chat @lid não se perder.
    await carregarLidMap(contaId)
    // NUNCA limpar credencial preventivamente aqui. Já existiu uma "detecção de
    // pareamento travado" que apagava as creds quando registered=false + account
    // preenchido — só que, CONFERIDO NA FONTE do Baileys (messages-recv.js), o
    // registered=true só existe no fluxo de pareamento por CÓDIGO; no fluxo por QR
    // ele fica false pra sempre, mesmo com a sessão perfeitamente válida. Ou seja:
    // aquela "detecção" batia em TODA credencial boa a cada início a frio, apagava
    // tudo e forçava QR novo depois de cada deploy — era ESSE o bug recorrente.
    // Credencial realmente podre se resolve sozinha: o login falha com 401, o
    // handler de close limpa e o próximo /iniciar pareia do zero com QR.
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
      // syncFullHistory:true PEDE pro WhatsApp o histórico INTEIRO da conta (meses/
      // anos) — numa conta movimentada são dezenas de milhares de mensagens que
      // represam o tempo real e fazem parecer que "parou de funcionar do nada".
      // Deixando false, o WhatsApp manda sozinho só a janela RECENTE ao parear,
      // que é justamente o que a gente quer importar.
      syncFullHistory: false,
      // ...e aqui a gente processa essa janela recente (por padrão o Baileys
      // descartaria tudo junto com o FULL, já que o gate default é só o
      // syncFullHistory). FULL fica de fora de propósito: é o backfill da conta
      // inteira, o mesmo que travou as mensagens ao vivo antes.
      // O corte de 30 dias continua em repassarHistorico, ANTES do POST — mensagem
      // antiga é descartada sem custo de rede.
      // PUSH_NAME entra junto: é a onda que traz SÓ os nomes (Utils/history.js:
      // `contacts.push({ id, notify: c.pushname })`, nenhuma mensagem), e é de longe
      // a fonte mais completa de nome que o WhatsApp manda. Estava sendo barrada
      // aqui — por isso as conversas importadas ficavam com o número cru mesmo com
      // o contato salvo na agenda. É barata: não tem mensagem pra processar.
      shouldSyncHistoryMessage: (msg) => {
        const t = msg && msg.syncType
        return t === TIPO_HIST.RECENT || t === TIPO_HIST.INITIAL_BOOTSTRAP ||
               t === TIPO_HIST.PUSH_NAME
      },
      markOnlineOnConnect: false,
      // ver comentário do cache `enviadas`: é isto que permite reenviar quando o
      // aparelho do vendedor (ou do cliente) não consegue decifrar e pede retry.
      getMessage: async (key) => {
        const m = enviadas.get(contaId + ':' + (key && key.id))
        log.info({ contaId, id: key && key.id, achou: !!m }, 'retry: pediram reenvio de mensagem nossa')
        return m
      }
    })
    s.sock = sock
    log.info({ contaId }, 'iniciarSessao: socket criado, registrando listeners')

    sock.ev.on('creds.update', saveCreds)

    // O par código->telefone vem no NÓ CRU de toda mensagem, mas o Baileys não
    // repassa esses campos no evento já decodificado (decode-wa-message.js monta
    // a key só com senderPn/senderLid/participant*). Resultado: mensagem que o
    // vendedor mandava do celular pra um chat @lid chegava sem número nenhum e
    // era descartada — e o aprendizado por senderPn só cobria o caso do contato
    // ter escrito primeiro. Aqui a gente lê o nó cru e aprende nos dois sentidos:
    //   recebida : from=<lid>      + sender_pn=<telefone>
    //   enviada  : recipient=<lid> + peer_recipient_pn=<telefone>
    // Roda antes do messages.upsert (o CB é emitido de forma síncrona assim que o
    // frame chega, a decodificação é que é assíncrona), então o mapa já está
    // pronto quando o eco da mensagem for repassado.
    try {
      sock.ws.on('CB:message', (node) => {
        try {
          const a = (node && node.attrs) || {}
          aprenderLid(contaId, a.from, a.sender_pn)
          aprenderLid(contaId, a.recipient, a.peer_recipient_pn)
          aprenderLid(contaId, a.participant, a.participant_pn)
        } catch (_) {}
      })
    } catch (e) { log.warn({ contaId, e: String(e) }, 'não deu pra escutar o nó cru das mensagens') }

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
      // marca que ESTA sessão chegou a abrir de verdade — é isso (e não o
      // creds.registered, que nunca vira true no fluxo QR) que separa um logout
      // real de uma credencial podre rejeitada logo na primeira tentativa.
      s.jaConectou = true
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
        // conversa) se esta sessão chegou a ABRIR neste processo. Um 401 logo na
        // tentativa de login é credencial velha/podre sendo rejeitada — limpa a
        // credencial (pro próximo /iniciar parear do zero) mas PRESERVA o chat.
        // Não dá pra usar creds.registered aqui: no fluxo QR ele nunca vira true.
        const foiLogoutReal = !!s.jaConectou
        try { await limparTudo() } catch (e) { log.warn({ contaId, e: String(e) }, 'limparTudo falhou') }
        sessoes.delete(contaId)
        lidMaps.delete(contaId)
        log.warn({ contaId, foiLogoutReal }, 'deslogado — credenciais limpas')
        if (foiLogoutReal) {
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
      if (m.key && m.key.fromMe) { guardarEnviada(contaId, m); await repassarSaida(contaId, m); continue }
      await repassarEntrada(contaId, m)
    }
  })

  // Histórico (só dispara logo após conectar/parear). Mensagens de ANTES de
  // conectar viram conversa ÓRFÃ (nunca lead sozinho) — importa só os últimos
  // 30 dias, ver HISTORICO_JANELA_SEGUNDOS. `progress` (0-100) vem do próprio
  // Baileys por bloco sincronizado — usa pra alimentar a barra de carregamento
  // do painel; sem evento novo por 5s, dá como concluído (ver timeout acima).
  // Nome da AGENDA do celular: chega aqui, via sincronização de app-state
  // (chat-utils.js -> contactAction.fullName). É o nome que o vendedor salvou.
  sock.ev.on('contacts.upsert', (contatos) => {
    log.info({ contaId, n: (contatos || []).length }, 'contacts.upsert recebido')
    repassarContatos(contaId, contatos, true).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'repassarContatos falhou'))
  })
  // Nome do PERFIL (pushName) — reserva, só preenche quando não há nome ainda.
  sock.ev.on('contacts.update', (contatos) => {
    repassarContatos(contaId, contatos, false).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'repassarContatos (update) falhou'))
  })

  sock.ev.on('messaging-history.set', async ({ messages, contacts, isLatest, progress, syncType }) => {
    log.info({ contaId, n: messages.length, contatos: (contacts || []).length,
      isLatest, progress, syncType }, 'messaging-history.set recebido')
    // Cada onda de histórico traz também os contatos daquele bloco — usa pra
    // resolver @lid -> número real (ver comentário em numeroReal). Faz isso ANTES
    // de repassar as mensagens da mesma onda, senão o mapa fica sempre um bloco
    // atrasado.
    atualizarLidMap(contaId, contacts)
    // O histórico também traz nome de contato: `name` no bloco de conversas (agenda)
    // e `notify` na onda PUSH_NAME (perfil). repassarContatos separa os dois.
    repassarContatos(contaId, contacts, true).catch(() => {})
    // O histórico chega em ondas (ex.: recentes primeiro, depois o histórico
    // completo) e cada onda reinicia o progress do zero — sem o Math.max a barra
    // sobe até 100%, "volta" pra perto de 0% quando a próxima onda começa, e
    // parece quebrada na tela mesmo sem ter dado erro nenhum.
    if (typeof progress === 'number') s.syncProgress = Math.max(s.syncProgress || 0, progress)
    clearTimeout(s._syncTimeout)
    s._syncTimeout = setTimeout(() => { s.sincronizando = false }, 5000)
    // Paraleliza entre CHATS diferentes, mas mensagem do MESMO chat vai em ordem,
    // uma de cada vez. Dois motivos, os dois vistos em produção no primeiro sync:
    // (1) dois POSTs simultâneos do mesmo contato novo criavam DUAS conversas —
    //     o lado Python faz select-then-insert sem lock, e a corrida venceu;
    // (2) preserva a ordem das mensagens dentro da conversa.
    const porChat = new Map()
    for (const m of messages) {
      const jid = (m.key && m.key.remoteJid) || ''
      if (!porChat.has(jid)) porChat.set(jid, [])
      porChat.get(jid).push(m)
    }
    await comLimiteDeConcorrencia([...porChat.values()], 8, async (grupo) => {
      for (const m of grupo) { await repassarHistorico(contaId, m) }
    })
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

// Toda vez que o serviço sobe (deploy, restart, crash) a memória vem vazia: as
// credenciais continuam salvas no Postgres, mas nenhum socket existe até alguém
// mandar abrir. Na prática o vendedor via "o WhatsApp caiu, reconecte" no meio do
// trabalho e não conseguia enviar nada. Aqui a gente religa sozinho todas as
// contas que já estavam pareadas — sem QR novo, sem ninguém precisar abrir tela
// nenhuma. Best-effort: se falhar, o fluxo manual pela aba Canais continua igual.
async function restaurarSessoes () {
  try {
    // Pareada = creds.me preenchido — é ISSO que o Baileys usa pra decidir entre
    // "logging in..." (retoma a sessão) e "attempting registration..." (QR novo).
    // O registered NÃO serve de filtro: no fluxo QR ele fica false pra sempre (só
    // o pareamento por código seta true), então filtrar por ele fazia esta função
    // nunca achar conta nenhuma — e todo deploy "derrubava" o WhatsApp.
    const r = await pool.query(
      `select conta_id from wa_qr_auth
        where arquivo = 'creds' and conteudo::json->'me'->>'id' is not null
        order by conta_id`)
    const contas = r.rows.map((l) => l.conta_id)
    if (!contas.length) {
      log.info('restaurarSessoes: nenhuma conta pareada pra religar')
      return
    }
    log.info({ n: contas.length, contas }, 'restaurarSessoes: religando contas já pareadas')
    for (const contaId of contas) {
      try {
        await iniciarSessao(contaId)
      } catch (e) {
        log.error({ contaId, e: String(e && e.stack || e) }, 'restaurarSessoes: falhou nessa conta')
      }
      // espaça as reconexões: várias contas abrindo socket no mesmo instante é
      // um bom jeito de o WhatsApp achar que é abuso e derrubar/bloquear todas.
      await new Promise((r2) => setTimeout(r2, 1500))
    }
  } catch (e) {
    log.error({ e: String(e && e.stack || e) }, 'restaurarSessoes: falhou')
  }
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
        let s = sessoes.get(contaId)
        // log sempre, mesmo quando falha — sem isso não dava pra saber SE o
        // status realmente não era 'conectado' na hora do envio ou se o
        // sendMessage em si que deu erro (dois motivos bem diferentes).
        log.info({ contaId, status: s && s.status, temSock: !!(s && s.sock) }, 'enviar: tentativa')
        // Sem sessão viva (serviço acabou de subir e ainda não religou essa conta,
        // ou ela caiu agora): tenta religar na hora com a credencial salva em vez
        // de já responder "desconectado" — que era o que o vendedor via bem no meio
        // de uma conversa. Se mesmo assim não subir, aí sim devolve o erro.
        if (!s || s.status !== 'conectado' || !s.sock) {
          log.info({ contaId }, 'enviar: sem sessão viva — religando e AGUARDANDO conectar')
          try { await iniciarSessao(contaId) } catch (e) {
            log.warn({ contaId, e: String(e) }, 'enviar: religar falhou')
          }
          // iniciarSessao retorna assim que o socket é criado, ANTES de a conexão
          // abrir (leva ~2–5s). Sem esta espera, um envio feito logo depois de um
          // deploy respondia "desconectado" com a reconexão em pleno andamento —
          // e o vendedor levava o alerta de queda na cara à toa. O lado Python
          // espera até 20s (finance/whatsapp_qr.py: _TIMEOUT), então 12s cabem.
          const limite = Date.now() + 12000
          while (Date.now() < limite) {
            s = sessoes.get(contaId)
            if (s && s.status === 'conectado' && s.sock) break
            // pedindo QR = não tem credencial salva; esperar não vai conectar nada
            if (s && s.status === 'aguardando_qr') break
            await new Promise((r2) => setTimeout(r2, 400))
          }
          s = sessoes.get(contaId)
          log.info({ contaId, status: s && s.status }, 'enviar: situação após aguardar reconexão')
        }
        if (!s || s.status !== 'conectado' || !s.sock) return json(res, 200, { ok: false, erro: 'desconectado' })
        if (!jid) return json(res, 200, { ok: false, erro: 'numero_invalido' })
        try {
          const r = await s.sock.sendMessage(jid, { text: String(body.texto || '').slice(0, 4000) })
          guardarEnviada(contaId, r)
          log.info({ contaId, id: r && r.key && r.key.id }, 'enviar: sucesso ✓')
          return json(res, 200, { ok: true, id: (r && r.key && r.key.id) || '' })
        } catch (e) {
          log.warn({ contaId, e: String(e) }, 'enviar: sendMessage falhou')
          return json(res, 200, { ok: false, erro: String(e).slice(0, 180) })
        }
      }
      if (req.method === 'POST' && acao === 'sair') {
        const s = sessoes.get(contaId)
        // marca como descartado ANTES do logout: o 'close' que o logout provoca
        // cairia no branch de deslogado e refaria essa mesma limpeza por outro
        // caminho. Aqui embaixo ela é feita explicitamente, igual nos dois casos
        // (com ou sem sessão viva) — que era exatamente o que faltava.
        if (s && s.sock) s.sock._descartado = true
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
        // Desconectar apaga o histórico de chat desse canal SEMPRE. Antes isso só
        // acontecia quando havia socket vivo na memória (o logout gerava um 401 que
        // disparava a limpeza por tabela); depois de um restart do serviço o MESMO
        // botão não apagava nada — o resultado dependia de o serviço ter reiniciado
        // ou não. O LEAD em si continua existindo: só some a aba de conversa.
        await avisarDeslogado(contaId)
        log.info({ contaId }, 'sair: sessão encerrada e histórico de conversa limpo')
        return json(res, 200, { ok: true })
      }
    }
    return json(res, 404, { ok: false, erro: 'rota' })
  } catch (e) {
    log.error({ e: String(e) }, 'erro na requisição')
    return json(res, 500, { ok: false, erro: 'interno' })
  }
})

servidor.listen(PORT, () => {
  log.info({ PORT }, 'wa-qr no ar')
  // não dá await: o healthcheck do Render precisa da porta respondendo já, e
  // religar as contas pode levar alguns segundos por conta.
  restaurarSessoes().catch((e) => log.error({ e: String(e) }, 'restaurarSessoes: erro solto'))
})
