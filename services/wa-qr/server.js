'use strict'
/*
 * ZAQ · Serviço de WhatsApp via QR Code (Baileys), multi-tenant por conta_id.
 *
 * Fala HTTP com o app Python (web). Autenticação simples por segredo compartilhado
 * (header x-wa-secret == WA_QR_SHARED_SECRET). Estado da sessão no Postgres.
 *
 * Rotas (todas exigem o header do segredo):
 *   POST /session/:conta/iniciar   -> garante o socket; {status, qr?} (qr = data URL)
 *                                     body {forcar:true} derruba o socket atual antes
 *                                     (pra ressuscitar sessão "conectada" mas muda)
 *   GET  /session/:conta/status    -> {status, qr?, mudoMs} (mudoMs = há quanto tempo
 *                                     aquele socket não entrega evento nenhum)
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
 * Socket que morre sem avisar (fica "conectado" e não entrega mais nada) é religado
 * sozinho pelo vigia — ver vigiarSessoes(): silêncio longo + ping sem resposta.
 *
 * O log vai pro stdout E pro Postgres (wa_qr_log), e o estado de cada sessão é
 * carimbado em wa_qr_sessao_estado de minuto em minuto — o log do Render não é legível de
 * fora, e sem isso todo diagnóstico dependia de alguém abrir o dashboard. Ver o
 * bloco "log no banco". WA_QR_LOG_DB=0 desliga.
 *
 * Env: DATABASE_URL, WA_QR_SHARED_SECRET, APP_URL, PORT (default 3000).
 */
const http = require('node:http')
const { Pool } = require('pg')
const pino = require('pino')
const QRCode = require('qrcode')
const makeWASocket = require('@whiskeysockets/baileys').default
const { DisconnectReason, makeCacheableSignalKeyStore, fetchLatestBaileysVersion, proto, BufferJSON,
  normalizeMessageContent, downloadMediaMessage } = require('@whiskeysockets/baileys')
const TIPO_HIST = proto.Message.HistorySyncNotification.HistorySyncType
const { useDbAuthState } = require('./auth-db')
const { criarTrava } = require('./sessao-lock')

const PORT = parseInt(process.env.PORT || '3000', 10)
const SEGREDO = process.env.WA_QR_SHARED_SECRET || ''
const APP_URL = (process.env.APP_URL || '').replace(/\/+$/, '')
const logBase = pino({ level: process.env.LOG_LEVEL || 'info' })
// Quanto o processo fica ocioso depois de abrir a porta, antes de religar as
// sessões — ver o comentário no servidor.listen. Regulável por env caso o
// arranque fique mais pesado (mais contas pareadas no mesmo serviço).
const ESPERA_RESTAURAR_MS = parseInt(process.env.WA_QR_ESPERA_RESTAURAR_MS || '10000', 10)
// Quanto esperar ENTRE uma conta e a próxima ao religar. Eram 3s, e isso era pouco: o
// que cada conta faz depois de conectar (backlog offline que o WhatsApp guardou,
// sincronização de agenda) dura minutos, então três contas abertas com 3s de intervalo
// sincronizam praticamente juntas. Medido no log de um estouro: as contas 3, 34 e 35
// pedindo a agenda dentro de 11 segundos uma da outra. Numa instância pequena isso é o
// amplificador do laço — estoura, reinicia, as três sincronizam juntas, estoura de novo.
// O custo de espaçar é a última conta demorar mais pra voltar a RECEBER; o envio não
// espera, porque /enviar religa a sessão sob demanda.
const ESPACO_CONTAS_MS = parseInt(process.env.WA_QR_ESPACO_CONTAS_MS || '30000', 10)
// Vigia de sessão MUDA — ver vigiarSessoes(). Quanto tempo sem UM evento do socket
// (mensagem, recibo, contato, histórico) até desconfiar, e de quanto em quanto tempo
// conferir. 10min é folgado de propósito: conta parada meia hora é rotina.
const MUDO_LIMITE_MS = parseInt(process.env.WA_QR_MUDO_LIMITE_MS || '600000', 10)
const VIGIA_INTERVALO_MS = parseInt(process.env.WA_QR_VIGIA_MS || '60000', 10)
// Timeout do ping. O keep-alive do próprio Baileys usa 30s; aqui é menor porque a
// sessão já está sob suspeita e o vigia roda de novo daqui a um minuto.
const PING_TIMEOUT_MS = parseInt(process.env.WA_QR_PING_TIMEOUT_MS || '15000', 10)
// Espaço entre pings da MESMA sessão: enquanto ela estiver calada o vigia passa de
// minuto em minuto, e não é pra pingar em toda passada.
const PING_ESPACO_MS = parseInt(process.env.WA_QR_PING_ESPACO_MS || '300000', 10)
// TETO do silêncio: aqui religa mesmo com o ping respondendo. O ping prova que o
// cano está aberto, NÃO que o WhatsApp ainda está entregando — e foi exatamente esse
// o estado da conta que motivou tudo isto (ver vigiarSessoes): o keep-alive do Baileys
// ia e voltava de 30 em 30s, por isso ele nunca deu a conexão por perdida, e mesmo
// assim não entrou uma mensagem por horas. Só o religamento completo traz de volta.
const MUDO_TETO_MS = parseInt(process.env.WA_QR_MUDO_TETO_MS || '2700000', 10)   // 45min
// Espera antes de tentar retomar uma conta que levou 440 e ficou sem dono. Dobra a
// cada tentativa (5min, 10, 20, 40, 80) — ver esperaPos440.
const ESPERA_POS_440_MS = parseInt(process.env.WA_QR_ESPERA_POS_440_MS || '300000', 10)
// Quanto tempo DE PÉ uma sessão precisa ficar pra a retomada valer como resolvida —
// ver sessaoFirme. Existe porque a dobra do esperaPos440 não estava dobrando: quem
// zerava o contador era o marcarVivo, e uma sessão que sobe, entrega dois recibos e
// leva 440 de novo já tinha zerado. Resultado medido em produção na noite de 15/08:
// a conta 23 foi substituída às 20:42, 20:48, 20:54, 21:00, 21:06, 21:12, 21:18,
// 21:24 e 21:30 — de 6 em 6 minutos, no relógio, a noite inteira. 15min é mais que o
// dobro da volta desse laço: quem passa disso está de pé de verdade.
const SESSAO_FIRME_MS = parseInt(process.env.WA_QR_SESSAO_FIRME_MS || '900000', 10)

if (!process.env.DATABASE_URL) { logBase.error('Falta DATABASE_URL'); process.exit(1) }
if (!SEGREDO) { logBase.error('Falta WA_QR_SHARED_SECRET'); process.exit(1) }

// Dois erros do Baileys apareciam em VERMELHO sem representar perda nenhuma, e
// vermelho que não pede ação treina a gente a ignorar o log inteiro — foi assim
// que erro de verdade passou batido nesta sessão. Estes dois são rebaixados pra
// debug: não somem (voltam com LOG_LEVEL=debug), só saem do vermelho.
function ehRuidoConhecido (msg, obj) {
  const err = obj && obj.err
  if (!err) return false
  // 'init queries' pede as propriedades da conta assim que a conexão abre e leva
  // 408 quando a sessão ainda não está pronta (ou nem pareada — acontece em todo
  // ciclo de QR). Não afeta enviar nem receber.
  if (msg === "unexpected error in 'init queries'" &&
      err.output && err.output.statusCode === 408) return true
  // Mensagem NOSSA reentregue depois de um restart: o contador do Signal recusa a
  // repetição ("Key used already"). O conteúdo já está no banco, perder a 2ª cópia
  // é o comportamento certo. Só vale pra fromMe — falha ao decifrar mensagem
  // RECEBIDA é perda de verdade e continua vermelha.
  if (msg === 'failed to decrypt message' && err.name === 'MessageCounterError' &&
      obj.key && obj.key.fromMe) return true
  return false
}

function comFiltroDeRuido (base) {
  const nivel = (n) => (a, b) => {
    const obj = (a && typeof a === 'object') ? a : null
    const msg = typeof a === 'string' ? a : b
    if (obj && typeof msg === 'string' && ehRuidoConhecido(msg, obj)) return base.debug(a, b)
    return base[n](a, b)
  }
  const filtrado = {
    fatal: nivel('fatal'),
    error: nivel('error'),
    warn: nivel('warn'),
    info: nivel('info'),
    debug: nivel('debug'),
    trace: nivel('trace'),
    // o Baileys chama child({class:'ns'}) e lê .level (Socket/socket.js)
    child: (bindings) => comFiltroDeRuido(base.child(bindings || {}))
  }
  Object.defineProperty(filtrado, 'level', {
    get: () => base.level,
    set: (v) => { base.level = v }
  })
  return filtrado
}

// ---------------------------------------------------------------- log no banco
//
// O log do Render não se lê de fora: o dashboard exige sessão de navegador e
// `api.render.com` cai em 403 na política de egresso do ambiente do agente. Num
// chamado real isso custou horas — dava pra provar pelo banco QUE a sessão de uma
// conta tinha emudecido (mensagens, contatos, ecos e mapa de @lid param todos no
// mesmo minuto, com as contas vizinhas gravando normal), e não dava pra saber POR
// QUÊ, porque o motivo estava só no log. Cada rodada virava "abra o dashboard,
// filtre pela conta e me diga o que aparece".
//
// Então o log vai também pro Postgres (tabela wa_qr_log, migração 158), que é
// onde todo mundo já consegue ler. Regras pra isto não virar um problema maior
// que o que resolve:
//
//   - só o log da NOSSA aplicação vai inteiro; do Baileys só error/fatal, senão
//     o firehose dele entope a tabela (e o que interessa dele é erro mesmo, tipo
//     'failed to decrypt message');
//   - debug/trace nunca vão;
//   - em LOTE, de 2 em 2s, com fila limitada: fila cheia DESCARTA e depois conta
//     quantas perdeu, porque log atrasando o serviço é pior que log faltando;
//   - retenção de 48h (isto é ferramenta de diagnóstico, não arquivo);
//   - falha ao gravar nunca sobe: o serviço não pode cair por causa do log.
//
// WA_QR_LOG_DB=0 desliga tudo isso.
const LOG_DB = (process.env.WA_QR_LOG_DB || '1') !== '0'
const LOG_DB_FILA_MAX = parseInt(process.env.WA_QR_LOG_FILA_MAX || '500', 10)
const LOG_DB_LOTE = 200
const LOG_DB_FLUSH_MS = parseInt(process.env.WA_QR_LOG_FLUSH_MS || '2000', 10)
const LOG_DB_RETENCAO_H = parseInt(process.env.WA_QR_LOG_RETENCAO_H || '48', 10)
const _logFila = []
let _logDescartadas = 0
let _logAvisouFalha = false

// O objeto logado vira `dados` jsonb, menos o contaId, que vira coluna (é por ele
// que todo diagnóstico filtra). Cap de tamanho: um payload gigante logado por
// engano não pode virar uma linha de megabytes.
function _dadosDoLog (obj) {
  if (!obj) return null
  const resto = {}
  for (const k of Object.keys(obj)) if (k !== 'contaId') resto[k] = obj[k]
  if (!Object.keys(resto).length) return null
  let s
  try {
    s = JSON.stringify(resto, (_k, v) => (typeof v === 'bigint' ? String(v) : v))
  } catch (_) {
    return null                      // ciclo, getter que explode: melhor sem dados
  }
  if (!s) return null
  // truncar JSON deixaria um texto que não é jsonb válido — embrulha o pedaço
  return s.length > 4000 ? JSON.stringify({ _truncado: s.slice(0, 3900) }) : s
}

function enfileirarLog (nivel, a, b) {
  if (!LOG_DB) return
  // a garantia "debug/trace não vão pro banco" mora AQUI, não só no embrulho do
  // logger: assim ela vale pra qualquer chamada, inclusive uma futura que esqueça
  // disso. Com LOG_LEVEL=debug ligado num apuro, o volume é de outra ordem.
  if (nivel === 'debug' || nivel === 'trace') return
  const obj = (a && typeof a === 'object') ? a : null
  const msg = typeof a === 'string' ? a : (typeof b === 'string' ? b : '')
  if (_logFila.length >= LOG_DB_FILA_MAX) { _logDescartadas++; return }
  _logFila.push([_contaDoLog(obj), nivel, String(msg).slice(0, 500), _dadosDoLog(obj),
    // a hora do EVENTO, não a da gravação. O `default now()` da coluna carimbava a
    // hora do INSERT — e como as linhas vão em lote de 2 em 2s, uma leva inteira
    // saía com o mesmo instante. Pra ler "o que veio antes do quê" num incidente,
    // 2s de granularidade é justamente o que se perde.
    new Date()])
}

// contaId virando coluna: aceita número OU string de dígitos. O driver do Postgres
// devolve bigint como STRING (é o padrão do node-postgres pra int8), então o
// `contaId` que sai de uma consulta — restaurarSessoes, por exemplo — é '35', não 35.
// Com o Number.isFinite cru, TODA linha dessas nascia sem conta: o log gravava certo
// e ficava inútil pra exatamente o uso que motivou a tabela, filtrar por conta.
// Visto no primeiro minuto em produção, com o log novo se denunciando sozinho.
function _contaDoLog (obj) {
  if (!obj) return null
  const bruto = obj.contaId
  if (bruto === null || bruto === undefined || bruto === '') return null
  const n = typeof bruto === 'number' ? bruto : Number(bruto)
  return Number.isFinite(n) ? n : null
}

async function gravarLogsPendentes () {
  if (!_logFila.length && !_logDescartadas) return
  const lote = _logFila.splice(0, LOG_DB_LOTE)
  const perdidas = _logDescartadas
  _logDescartadas = 0
  const partes = []
  const params = []
  lote.forEach((l, i) => {
    const b = i * 5
    partes.push(`($${b + 1},$${b + 2},$${b + 3},$${b + 4}::jsonb,$${b + 5})`)
    params.push(l[0], l[1], l[2], l[3], l[4])
  })
  try {
    if (partes.length) {
      await pool.query(
        'insert into wa_qr_log (conta_id, nivel, msg, dados, criado_em) values ' + partes.join(','),
        params)
    }
    // a perda tem que aparecer NA PRÓPRIA tabela: um diagnóstico com buraco
    // silencioso é pior que um diagnóstico que avisa onde está o buraco
    if (perdidas) {
      await pool.query(
        `insert into wa_qr_log (nivel, msg, dados) values
         ('warn','log: fila cheia, linhas descartadas',$1::jsonb)`,
        [JSON.stringify({ perdidas })])
    }
    _logAvisouFalha = false
  } catch (e) {
    // uma vez por episódio, e no stdout (aqui não dá pra logar no banco)
    if (!_logAvisouFalha) {
      _logAvisouFalha = true
      logBase.warn({ e: String(e) }, 'espelho do log no banco falhou — seguindo só no stdout')
    }
  }
}

async function limparLogsAntigos () {
  try {
    await pool.query(
      `delete from wa_qr_log where criado_em < now() - ($1 || ' hours')::interval`,
      [String(LOG_DB_RETENCAO_H)])
  } catch (_) {}
}

function comEspelhoNoBanco (base, opcoes) {
  const soErro = !!(opcoes && opcoes.soErro)
  const nivel = (n) => (a, b) => {
    if (!soErro || n === 'error' || n === 'fatal') enfileirarLog(n, a, b)
    return base[n](a, b)
  }
  const espelhado = {
    fatal: nivel('fatal'),
    error: nivel('error'),
    warn: nivel('warn'),
    info: nivel('info'),
    debug: (a, b) => base.debug(a, b),      // debug/trace nunca vão pro banco
    trace: (a, b) => base.trace(a, b),
    child: (bindings) => comEspelhoNoBanco(base.child(bindings || {}), opcoes)
  }
  Object.defineProperty(espelhado, 'level', {
    get: () => base.level,
    set: (v) => { base.level = v }
  })
  return espelhado
}

const log = comEspelhoNoBanco(logBase)
// Do Baileys, só erro. O filtro de ruído fica POR FORA: o que ele rebaixa pra
// debug (408 de init queries, retry de mensagem nossa) não chega ao espelho.
const logBaileys = comFiltroDeRuido(comEspelhoNoBanco(logBase, { soErro: true }))

// Sem isso, um erro assíncrono que escapa de um try/catch (ex.: dentro de um
// listener de evento do Baileys) só aparecia no Render como um stack trace cru
// do Node, sem contexto nenhum — ou o processo caía silencioso e reiniciava sem
// deixar rastro de PORQUE. Loga estruturado antes de qualquer coisa.
process.on('unhandledRejection', (err) => log.error({ err: String(err && err.stack || err) }, 'unhandledRejection'))
process.on('uncaughtException', (err) => log.error({ err: String(err && err.stack || err) }, 'uncaughtException'))

// Detector de travamento do event loop. O "HTTP health check failed (timed out
// after 5 seconds)" do Render não diz NADA sobre a causa: /saude responde na
// hora e nem toca no banco, então falha ali significa que o processo estava
// preso numa operação síncrona longa e a rota nem chegou a ser chamada. Este
// tique mede o atraso e denuncia o momento exato — sem ele, a única pista era o
// aviso do Render, que não dá pra correlacionar com nada.
//
// O MESMO tique também publica a memória de minuto em minuto. O serviço morreu no
// Render com "Ran out of memory (used over 512MB)" sem deixar UMA linha de log: quem
// matou foi o kernel (cgroup), não o Node, então não há stack trace nenhum. Sem série
// histórica de memória não dá pra distinguir "pico legítimo de sincronização" de
// "vazamento que sobe em rampa" — que exigem consertos opostos. Vai junto o tamanho de
// cada estrutura em memória e a fila do pool do Postgres (waitingCount): consulta
// enfileirada é memória retida esperando uma das 4 conexões, e cresce sem teto.
const MB = (b) => Math.round((b || 0) / 1048576)
let _ultimoTique = Date.now()
let _tiques = 0
setInterval(() => {
  const agora = Date.now()
  const atraso = agora - _ultimoTique - 1000
  _ultimoTique = agora
  if (atraso > 1000) {
    log.warn({ atrasoMs: atraso },
      'event loop travou — nesse intervalo /saude não respondia (risco de health check falhar)')
  }
  if (++_tiques % 60) return
  const m = process.memoryUsage()
  let lids = 0
  for (const mapa of lidMaps.values()) lids += mapa.size
  log.info({
    rssMB: MB(m.rss), heapMB: MB(m.heapUsed), heapTotalMB: MB(m.heapTotal),
    externalMB: MB(m.external), buffersMB: MB(m.arrayBuffers),
    sessoes: sessoes.size, enviadas: enviadas.size, jids: jidsResolvidos.size, lids,
    pgFila: pool.waitingCount, pgConexoes: pool.totalCount
  }, 'memória')
}, 1000).unref()

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: /localhost|127\.0\.0\.1/.test(process.env.DATABASE_URL) ? false : { rejectUnauthorized: false },
  max: 4
})

// contaId -> { sock, status, qr, iniciando }
const sessoes = new Map()

// Quanto esperar antes de tentar pegar a trava de novo. Precisa ser MENOR que o
// TTL do aluguel: assim, quando a instância dona sai, esta assume no próximo
// ciclo em vez de esperar o prazo inteiro vencer.
const RETENTAR_TRAVA_MS = parseInt(process.env.WA_QR_RETENTAR_TRAVA_MS || '15000', 10)
const tentativasDeTrava = new Map()   // contaId -> timer

// Reagenda UMA tentativa por conta (o timer anterior é cancelado). Sem isso, cada
// caminho que falha em pegar a trava — /iniciar, /enviar, restaurarSessoes, o
// aoPerder — armava o seu, e uma conta disputada acumulava um timer por chamada.
function agendarTentativaDeTrava (contaId) {
  clearTimeout(tentativasDeTrava.get(contaId))
  const t = setTimeout(() => {
    tentativasDeTrava.delete(contaId)
    iniciarSessao(contaId).catch((e) =>
      log.error({ contaId, e: String(e) }, 'nova tentativa de pegar a trava falhou'))
  }, RETENTAR_TRAVA_MS)
  if (t.unref) t.unref()
  tentativasDeTrava.set(contaId, t)
}

// Trava de sessão única por conta (ver sessao-lock.js). Sem ela, a instância nova
// do deploy abre socket com a mesma credencial da velha e o WhatsApp derruba uma
// das duas com 440 — em loop, a cada deploy.
//
// aoPerder é o lado que quase ninguém lembra de escrever: se o aluguel não
// renovar, não somos mais donos, e continuar com o socket aberto recria
// exatamente a disputa que a trava veio impedir. Então larga tudo e tenta pegar
// de novo mais tarde — se a outra instância ainda estiver lá, a tentativa falha
// de novo, sem barulho.
const trava = criarTrava(pool, log, {
  aoPerder: (contaId) => {
    const s = sessoes.get(contaId)
    if (!s) return
    descartarSocket(s.sock, contaId, 'trava_perdida')
    pararTimersDaAgenda(s)
    s.sock = null
    s.qr = null
    s.status = 'reconectando'
    agendarTentativaDeTrava(contaId)
  }
})

// Encerra um socket PRA VALER: marca `_descartado` (pro 'close' que o próprio end()
// emite não reentrar nos nossos handlers) e fecha. O que importa pra memória é ser
// chamado NA HORA — é o end() que derruba o WebSocket e mata o keep-alive do Baileys,
// e enquanto isso não acontece o socket morto continua inteiro, com os caches dele
// (signal store, devices, retry) e as conexões abertas.
//
// De propósito NÃO mexe nos listeners: o ciclo sock -> ev -> closure -> sock é coletado
// pelo GC sozinho, então removê-los não devolveria memória nenhuma — e removeria junto
// o `creds.update`, com risco de perder uma gravação de credencial pendente. O próprio
// Baileys já solta os listeners de connection.update dentro do end().
function descartarSocket (sock, contaId, motivo) {
  if (!sock) return
  try {
    sock._descartado = true
    sock.end(undefined)
    log.info({ contaId, motivo }, 'socket descartado')
  } catch (e) { log.warn({ contaId, motivo, e: String(e) }, 'falha ao fechar socket') }
}

// Todos os temporizadores da agenda de UMA encarnação da sessão. esperarAcalmarEResync
// fica até 15min batendo no banco de 5 em 5s, e cada reconexão armava mais um por cima
// sem desarmar o anterior.
function pararTimersDaAgenda (s) {
  if (!s) return
  clearTimeout(s._agendaT1); clearTimeout(s._agendaT2); clearInterval(s._agendaT3)
  s._agendaT3 = null
}

// Carimbo de VIDA da sessão: a última vez que este socket entregou alguma coisa
// (mensagem, recibo, contato, onda de histórico, conexão abrindo). É o único jeito de
// distinguir "conta parada" de "socket morto que ninguém percebeu" — ver vigiarSessoes.
function marcarVivo (contaId) {
  const s = sessoes.get(contaId)
  if (!s) return
  s.ultimoEvento = Date.now()
  // entregou de verdade = a desconfiança do vigia zera junto (ver tetoMudo)
  s.reconexoesMudas = 0
  // ...e a conta deixa de ser órfã de 440: quem entrega está vivo e é nosso
  s.substituidaEm = null
  // Mas o contador de retomadas NÃO se apaga aqui. Entregar um evento prova que o
  // socket subiu, não que ele vai FICAR — e era essa confusão que desarmava a dobra
  // do esperaPos440: numa conta movimentada sempre chega um recibo entre subir e
  // levar 440 de novo, então o contador voltava a zero toda volta e a espera ficava
  // presa nos 5 minutos iniciais pra sempre. Quem zera é o tempo de pé (sessaoFirme).
  if (sessaoFirme(s, Date.now(), SESSAO_FIRME_MS)) s.tentativasPos440 = 0
}

// A sessão está de pé há tempo suficiente pra a retomada ter dado certo?
//
// `abertoEm` é carimbado no 'open' e limpo quando o socket é descartado, então isto
// mede a ENCARNAÇÃO atual — não a conta. Sem `abertoEm` (sessão que nunca abriu, ou
// restaurada por um deploy antes deste campo existir) a resposta é não: na dúvida a
// espera continua crescendo, que é o lado seguro. Religar de menos atrasa uma conta;
// religar de mais é o que faz o WhatsApp achar que é abuso.
function sessaoFirme (s, agora, firme) {
  if (!s || !s.abertoEm) return false
  return (agora - s.abertoEm) >= firme
}

// Este evento vem do socket que a sessão está usando AGORA?
//
// Duas formas de um evento chegar velho, e as duas já custaram caro:
//
// * o socket foi descartado (trocamos de encarnação) e o end() dele ainda emite um
//   último 'close' — ver descartarSocket;
// * o socket levou 440, a gente soltou (s.sock = null) e o Baileys emite um 'open'
//   ATRASADO desse mesmo socket. Medido na conta 34: 440 às 22:16:15.890 e 'open' às
//   22:16:16.402. Esse 'open' punha status='conectado' numa sessão sem socket, e daí
//   pra frente ela mentia — chip verde no painel, vigia do silêncio pulando a conta
//   (sessaoMuda exige s.sock) e só o /enviar descobrindo, na hora de mandar a
//   mensagem do cliente, que não havia socket nenhum.
function socketAtual (s, sock) {
  return !!sock && !sock._descartado && !!s && s.sock === sock
}

// Quanto esperar antes de tentar retomar uma conta que levou 440, dobrando a cada
// tentativa até 16× (5min, 10, 20, 40, 80). A espera existe porque quem substituiu
// pode ser uma sessão LEGÍTIMA de fora — o WhatsApp Web que a cliente abriu no
// computador dela. Retomar na hora contra uma dessas é a guerra de sessões que
// derruba as duas em revezamento; esperar e ir espaçando deixa a conta voltar quando
// a outra sair, sem brigar enquanto ela está lá.
function esperaPos440 (s, base) {
  const n = Math.min((s && s.tentativasPos440) || 0, 4)
  return base * Math.pow(2, n)
}

// A conta ficou ÓRFÃ depois de um 440?
//
// O caminho do 440 solta a trava e retorna — e estava certo em não reconectar na
// hora. O que faltava era o depois: ninguém reagendava nada, então a conta ficava
// sem sessão em lugar nenhum até o próximo deploy. Aconteceu com a conta 35 em
// 15/08: levou 440 às 22:46:07, soltou a trava, e às 22:58 a caixa da cliente ainda
// estava morta — com o serviço saudável, atendendo as vizinhas normalmente.
//
// E o vigia, que era quem podia resgatar, se recusava: `!trava.segura(contaId)` foi
// escrito presumindo "não é minha = tem outra instância cuidando". Só que "não é
// minha" também cobre "não é de NINGUÉM", que é exatamente este caso.
//
// Quem decide se pode assumir não é esta função — é o `trava.pegar` lá dentro do
// iniciarSessao, que é atômico. Se outra instância estiver com a conta, ele recusa e
// agenda a tentativa dele; aqui a gente só resolve QUANDO vale tentar.
function sessaoOrfa (s, agora, base) {
  if (!s || s.sock || s.iniciando || !s.substituidaEm) return false
  return (agora - s.substituidaEm) >= esperaPos440(s, base)
}

// A sessão está MUDA a ponto de merecer um ping? Separado numa função pura porque é
// a regra que decide religar uma sessão de produção — e religar à toa é justamente o
// que faz o WhatsApp achar que é abuso.
//
// Só entra sessão que se DIZ conectada: 'reconectando' já tem quem cuide dela, e
// 'aguardando_qr' está esperando gente, não rede. Sem `ultimoEvento` (sessão restaurada
// por um deploy antes deste campo existir) conta como agora — na dúvida, não mexe.
function sessaoMuda (s, agora, limite) {
  if (!s || s.iniciando || !s.sock || s.status !== 'conectado') return false
  return (agora - (s.ultimoEvento || agora)) >= limite
}

// Teto de silêncio DESTA sessão: a partir daqui religa mesmo com o ping respondendo.
// Dobra a cada religamento que não trouxe evento nenhum de volta, até 16×. Sem isso
// uma conta legitimamente parada (loja fechada, fim de semana) seria religada a cada
// 45min a noite inteira — e reconexão em série é justamente o que faz o WhatsApp achar
// que é abuso. Com a dobra são 45min, 1h30, 3h, 6h e 12h; o primeiro evento de verdade
// zera a conta e o teto volta pro começo (marcarVivo).
function tetoMudo (s, teto) {
  const n = Math.min((s && s.reconexoesMudas) || 0, 4)
  return teto * Math.pow(2, n)
}

// PING de verdade no socket — o mesmo IQ que o keep-alive do Baileys manda sozinho a
// cada 30s. Se voltar, a sessão está viva e o silêncio era só falta de assunto.
async function pingSessao (sock) {
  if (!sock || typeof sock.query !== 'function') throw new Error('socket sem query')
  await sock.query({
    tag: 'iq',
    attrs: { to: '@s.whatsapp.net', type: 'get', xmlns: 'w:p' },
    content: [{ tag: 'ping', attrs: {} }]
  }, PING_TIMEOUT_MS)
}

// O socket que morre SEM avisar. Aconteceu em produção com a conta 35 (Confeitaria
// Doce Mell): às 13:28 o serviço parou de receber qualquer evento daquela sessão —
// nem mensagem, nem eco do celular, nem contato — enquanto as outras contas do mesmo
// processo seguiam normais. Nada no banco mudou pra ela dali em diante (mensagens,
// wa_contatos, wa_qr_enviadas e o mapa de @lid param todos no mesmo minuto), e o
// painel continuou mostrando o chip como CONECTADO, porque o status é o que ficou na
// memória do último 'open'. Três horas depois a cliente ainda via a caixa parada no
// mesmo minuto, sem uma linha de erro em lugar nenhum.
//
// O keep-alive do Baileys não pegou: ele derruba a conexão quando o servidor fica >35s
// sem responder, e o ping continuava indo e voltando. Ou seja, o cano estava aberto e
// o WhatsApp simplesmente não entregava mais nada naquela sessão. Por isso o vigia não
// pode decidir SÓ pelo ping — ele é a prova rápida de socket morto, não de sessão viva.
//
// E o pior não era a queda: era não ter volta. Com `status === 'conectado'` e um `sock`
// na mão, o iniciarSessao devolve a sessão existente na primeira linha — então nem o
// botão de reconectar do painel ressuscitava a conta. Só um deploy resolvia.
//
// Regra, então, em dois degraus (status/sock são zerados antes de religar, senão o
// iniciarSessao volta a dizer "já está conectada"):
//   10min calada  -> pinga (no máximo de 5 em 5min). Ping sem resposta = socket morto,
//                    religa na hora.
//   45min calada  -> religa mesmo com o ping respondendo; é o caso da Doce Mell.
//                    O teto dobra a cada religamento que não trouxe nada — ver tetoMudo.
// O que cada sessão diz de si mesma, gravado onde dá pra ler (wa_qr_sessao_estado).
// Uma linha por conta, sobrescrita. Foi a pergunta que ficou sem resposta no
// chamado: "o serviço acha que está conectado?" — o painel mostrava o estado,
// mas ele mora na memória do processo e some no deploy seguinte, junto com a
// prova do que aconteceu.
async function registrarSessoes () {
  const agora = Date.now()
  for (const [contaId, s] of sessoes) {
    const mudoS = s.ultimoEvento ? Math.round((agora - s.ultimoEvento) / 1000) : null
    try {
      await pool.query(
        `insert into wa_qr_sessao_estado (conta_id, status, ultimo_evento, mudo_s, religamentos, detalhe, atualizado)
         values ($1,$2,$3,$4,$5,$6::jsonb,now())
         on conflict (conta_id) do update set
           status=excluded.status, ultimo_evento=excluded.ultimo_evento, mudo_s=excluded.mudo_s,
           religamentos=excluded.religamentos, detalhe=excluded.detalhe, atualizado=now()`,
        [contaId, s.status || '', s.ultimoEvento ? new Date(s.ultimoEvento) : null, mudoS,
          s.reconexoesMudas || 0,
          JSON.stringify({ temSock: !!s.sock, iniciando: !!s.iniciando,
            sincronizando: !!s.sincronizando, syncProgress: s.syncProgress || 0,
            // quem está com a conta: sem isso, "calada" numa instância que nem
            // segura a trava se lê como defeito, quando é a outra trabalhando
            trava: trava.segura(contaId) })])
    } catch (_) {}   // diagnóstico não pode atrapalhar quem está trabalhando
  }
}

// Costura só pro teste: o resgate da órfã chama iniciarSessao, que abre socket de
// verdade e fala com o WhatsApp — coisa que teste nenhum pode fazer. Passando por
// aqui, o teste troca a função e confere QUANDO o vigia decide retomar, que é a
// regra que interessa. Em produção é o iniciarSessao de sempre.
const _ganchos = { iniciarSessao }

async function vigiarSessoes () {
  const agora = Date.now()
  for (const [contaId, s] of sessoes) {
    // ÓRFÃ DE 440 primeiro: ela não tem socket, então nem chegaria no teste de
    // silêncio abaixo (que exige um). É a conta que soltou a trava depois de ser
    // substituída e ficou sem ninguém — ver sessaoOrfa.
    if (sessaoOrfa(s, agora, ESPERA_POS_440_MS)) {
      s.tentativasPos440 = (s.tentativasPos440 || 0) + 1
      s.substituidaEm = agora        // reinicia a espera, tenha dado certo ou não
      log.warn({ contaId, tentativa: s.tentativasPos440,
        proximaEsperaMin: Math.round(esperaPos440(s, ESPERA_POS_440_MS) / 60000) },
      'vigia: conta órfã desde a substituição — tentando retomar')
      try {
        // o trava.pegar lá dentro é quem decide: se outra instância estiver com a
        // conta, ele recusa e agenda a tentativa dela, sem socket nenhum aqui
        await _ganchos.iniciarSessao(contaId)
      } catch (e) {
        log.error({ contaId, e: String(e) }, 'vigia: retomar a órfã falhou — tenta na próxima')
      }
      continue
    }
    if (!sessaoMuda(s, agora, MUDO_LIMITE_MS)) continue
    // Sessão que não é NOSSA não se religa. Com a trava por conta (sessao-lock.js),
    // silêncio aqui pode ser simplesmente outra instância trabalhando com aquela
    // credencial — e insistir seria recriar a guerra de sessões que a trava veio
    // acabar. Sem a trava, o iniciarSessao recusaria de qualquer jeito; parar antes
    // evita derrubar um socket bom por nada.
    if (!trava.segura(contaId)) {
      log.info({ contaId }, 'vigia: conta calada, mas a trava é de outra instância — não mexo')
      continue
    }
    const mudoMs = agora - (s.ultimoEvento || agora)
    const mudoMin = Math.round(mudoMs / 60000)
    let motivo = ''
    if (mudoMs >= tetoMudo(s, MUDO_TETO_MS)) {
      motivo = 'mudo_alem_do_teto'
      log.warn({ contaId, mudoMin, religamentos: s.reconexoesMudas || 0 },
        'vigia: sessão sem entregar nada por tempo demais — religando (o ping não basta)')
    } else if (agora - (s.ultimoPing || 0) >= PING_ESPACO_MS) {
      s.ultimoPing = agora
      try {
        await pingSessao(s.sock)
        log.info({ contaId, mudoMin }, 'vigia: sessão calada mas o ping voltou — de olho')
        continue
      } catch (e) {
        motivo = 'ping_sem_resposta'
        log.warn({ contaId, mudoMin, e: String(e) },
          'vigia: sessão calada e o ping não voltou — socket morto, religando')
      }
    } else {
      continue   // calada, mas o ping desta rodada já foi
    }
    s.reconexoesMudas = (s.reconexoesMudas || 0) + 1
    s.status = 'reconectando'
    s.qr = null
    s.ultimoPing = 0
    descartarSocket(s.sock, contaId, 'vigia_' + motivo)
    s.sock = null
    pararTimersDaAgenda(s)
    try {
      await iniciarSessao(contaId)
    } catch (e) {
      log.error({ contaId, e: String(e) }, 'vigia: religar falhou — tenta de novo na próxima volta')
    }
  }
}

// contaId -> Map(jid @lid -> jid real @s.whatsapp.net). Usado pra traduzir o ID
// interno de privacidade do WhatsApp pro telefone de verdade, nos três fluxos
// (entrada, saída e histórico). Alimentado por DUAS fontes: a lista de contatos
// que vem no histórico E cada mensagem ao vivo recebida (que traz lid + senderPn
// juntos). Persistido no Postgres (arquivo 'lidmap-...' na wa_qr_auth) porque a
// memória zera a cada deploy — e era justamente após deploy que o eco de mensagem
// mandada pelo celular pra um chat @lid se perdia por falta do mapa.
const lidMaps = new Map()

// Teto por conta. O mapa era alimentado pela agenda INTEIRA (milhares de contatos) e
// ainda por todo frame que chega no CB:message, sem nunca encolher — só sumia no
// logout. Map preserva ordem de inserção, então despejar o primeiro é despejar o mais
// antigo (mesmo padrão do cache `enviadas`). Despejo aqui não perde nada: a linha
// continua no Postgres e volta no próximo religamento.
const MAX_LIDS_POR_CONTA = 20000

// Escrita dos pares @lid->telefone em LOTE, não uma query por par.
// Antes cada par novo disparava um pool.query() sem await. Com `max: 4` no pool, uma
// sincronização de agenda com milhares de contatos empilhava milhares de consultas na
// fila INTERNA do pg — cada uma segurando params e promise na memória, sem teto — e
// ainda represava atrás delas as consultas que importam (creds, chaves de app-state).
// Agora acumula e grava de LID_FLUSH_MS em LID_FLUSH_MS num insert multi-linha só.
// A chave do buffer é conta+lid, então o mesmo par nunca aparece duas vezes no mesmo
// statement — o que também evita o "ON CONFLICT DO UPDATE cannot affect row a second
// time" que um lote com repetição levaria do Postgres.
const lidsPendentes = new Map()
const LID_FLUSH_MS = 2000
const MAX_LIDS_PENDENTES = 2000
const LID_LOTE = 500
let _lidFlushT = null

async function gravarLidsPendentes () {
  _lidFlushT = null
  if (!lidsPendentes.size) return
  const pares = [...lidsPendentes]
  lidsPendentes.clear()
  for (let i = 0; i < pares.length; i += LID_LOTE) {
    const lote = pares.slice(i, i + LID_LOTE)
    const valores = []
    const params = []
    for (const [chave, pnJid] of lote) {
      const corte = chave.indexOf(' ')
      params.push(parseInt(chave.slice(0, corte), 10), 'lidmap-' + chave.slice(corte + 1),
        JSON.stringify(pnJid))
      valores.push(`($${params.length - 2},$${params.length - 1},$${params.length}, now())`)
    }
    try {
      await pool.query(
        `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
         values ${valores.join(',')}
         on conflict (conta_id, arquivo)
         do update set conteudo=excluded.conteudo, atualizado=now()`, params)
    } catch (e) {
      log.warn({ n: lote.length, e: String(e) }, 'gravarLidsPendentes: falha ao persistir')
    }
  }
}

function aprenderLid (contaId, lidJid, pnJid) {
  if (!lidJid || !pnJid || !lidJid.endsWith('@lid') || !pnJid.endsWith('@s.whatsapp.net')) return
  let mapa = lidMaps.get(contaId)
  if (!mapa) { mapa = new Map(); lidMaps.set(contaId, mapa) }
  if (mapa.get(lidJid) === pnJid) return
  mapa.set(lidJid, pnJid)
  while (mapa.size > MAX_LIDS_POR_CONTA) mapa.delete(mapa.keys().next().value)
  lidsPendentes.set(contaId + ' ' + lidJid, pnJid)
  // buffer cheio: grava JÁ, senão o teto seria só outro jeito de acumular memória
  if (lidsPendentes.size >= MAX_LIDS_PENDENTES) {
    clearTimeout(_lidFlushT)
    _lidFlushT = null
    gravarLidsPendentes().catch((e) => log.warn({ e: String(e) }, 'flush de lids falhou'))
    return
  }
  if (!_lidFlushT) {
    _lidFlushT = setTimeout(() => {
      gravarLidsPendentes().catch((e) => log.warn({ e: String(e) }, 'flush de lids falhou'))
    }, LID_FLUSH_MS)
  }
}

function atualizarLidMap (contaId, contacts) {
  if (!Array.isArray(contacts) || !contacts.length) return
  for (const ct of contacts) {
    if (ct && ct.lid && ct.jid) aprenderLid(contaId, ct.lid, ct.jid)
  }
}

async function carregarLidMap (contaId) {
  try {
    // com limite, pelos mais recentes: sem ele o religamento de uma conta com agenda
    // grande carregava a tabela inteira de uma vez, e ainda por cima furava o teto de
    // MAX_LIDS_POR_CONTA logo no arranque. O que ficar de fora volta sozinho quando o
    // contato aparecer numa mensagem (o senderPn traz o par de graça).
    const r = await pool.query(
      `select arquivo, conteudo from wa_qr_auth
        where conta_id=$1 and arquivo like 'lidmap-%'
        order by atualizado desc limit $2`,
      [contaId, MAX_LIDS_POR_CONTA])
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
// ...e o cache PRECISA sobreviver a restart. Memória sozinha não bastava: numa
// mensagem enviada 07:48, com deploy do serviço logo em seguida, o pedido de
// reenvio chegou no processo novo com o cache vazio — o Baileys não teve o
// conteúdo pra re-encriptar e a mensagem ficou eternamente "Aguardando
// mensagem" no celular do cliente (no banco: status 'enviado' que nunca virou
// 'entregue'). Memória continua como caminho rápido; o Postgres é a rede de
// segurança, igual ao que já se faz com o mapa de @lid.
const MAX_ENVIADAS = 400
// ...e o teto precisa ser de BYTES também, não só de contagem. 400 mensagens de texto
// dão ~100 KB; 400 ECOS de imagem mandada pelo celular (todo fromMe passa por aqui, ver
// messages.upsert) carregam o `jpegThumbnail` embutido e viram dezenas de MB presos até
// serem despejados. Acima deste tamanho a mensagem vai só pro Postgres: buscarEnviada
// já lê de lá quando não acha na memória, então o reenvio continua funcionando igual —
// só paga uma consulta, num evento que é raro por natureza.
const MAX_BYTES_ENVIADA = 8192
const enviadas = new Map()

function guardarEnviada (contaId, m) {
  if (!m || !m.key || !m.key.id || !m.message) return
  // serializa UMA vez: o mesmo texto decide o cache e vai pro banco
  const corpo = JSON.stringify(m.message, BufferJSON.replacer)
  if (corpo && corpo.length <= MAX_BYTES_ENVIADA) {
    const k = contaId + ':' + m.key.id
    if (enviadas.has(k)) enviadas.delete(k)
    enviadas.set(k, m.message)
    // Map preserva ordem de inserção: o primeiro é sempre o mais antigo.
    while (enviadas.size > MAX_ENVIADAS) enviadas.delete(enviadas.keys().next().value)
  }
  // não dá await: guardar é best-effort e não pode atrasar o envio
  pool.query(
    `insert into wa_qr_enviadas (conta_id, msg_id, conteudo, criado_em)
     values ($1,$2,$3, now())
     on conflict (conta_id, msg_id) do nothing`,
    [contaId, m.key.id, corpo]
  ).catch((e) => log.warn({ contaId, e: String(e) }, 'guardarEnviada: falha ao persistir'))
}

async function buscarEnviada (contaId, id) {
  const emMemoria = enviadas.get(contaId + ':' + id)
  if (emMemoria) return emMemoria
  try {
    const r = await pool.query(
      'select conteudo from wa_qr_enviadas where conta_id=$1 and msg_id=$2', [contaId, id])
    if (!r.rows[0]) return undefined
    return JSON.parse(r.rows[0].conteudo, BufferJSON.reviver)
  } catch (e) {
    log.warn({ contaId, id, e: String(e) }, 'buscarEnviada: falha ao ler do banco')
    return undefined
  }
}

// Pedido de reenvio chega em minutos/horas, não em semanas — guardar mais que
// isso só engorda a tabela. Roda uma vez por restauração de sessão.
async function limparEnviadasAntigas () {
  try {
    const r = await pool.query(
      "delete from wa_qr_enviadas where criado_em < now() - interval '3 days'")
    if (r.rowCount) log.info({ n: r.rowCount }, 'cache de enviadas: linhas antigas apagadas')
  } catch (e) { log.warn({ e: String(e) }, 'limparEnviadasAntigas falhou') }
}

function jidDe (numero) {
  const d = String(numero || '').replace(/\D/g, '')
  if (!d) return null
  const comDDI = (!d.startsWith('55') && d.length <= 11) ? '55' + d : d
  return comDDI + '@s.whatsapp.net'
}

// Número do jeito que está guardado NEM SEMPRE é o JID de verdade da pessoa, e o
// WhatsApp não reclama: ele aceita a mensagem (vira ✓, SERVER_ACK) e nunca
// entrega. Fica o traço único pra sempre, sem erro nenhum em lugar nenhum.
// Visto em produção: contato salvo como 558694867388 (12 dígitos, sem o 9 do
// celular) — tudo que saía pelo Zaq parava no ✓, enquanto o que o vendedor
// mandava pelo celular chegava e era lido, porque o aparelho usa o JID certo da
// conversa.
//
// onWhatsApp() pergunta ao próprio WhatsApp qual é o JID canônico daquele
// telefone e ainda diz se a conta existe. Resolve de vez o 9º dígito brasileiro
// e transforma "sumiu sem avisar" em erro claro na tela.
const jidsResolvidos = new Map()
const PRAZO_ONWHATSAPP_MS = 4000
// Teto: uma entrada por número já contatado, por conta, e nada nunca saía daqui — nem
// no Desconectar. Numa conta que dispara pra muita gente isso só cresce. Despejo pelo
// mais antigo (Map preserva ordem de inserção); o pior efeito de despejar é uma
// consulta onWhatsApp a mais no próximo envio pra aquele número.
const MAX_JIDS_RESOLVIDOS = 2000

function lembrarJid (chave, valor) {
  jidsResolvidos.set(chave, valor)
  while (jidsResolvidos.size > MAX_JIDS_RESOLVIDOS) {
    jidsResolvidos.delete(jidsResolvidos.keys().next().value)
  }
}

// Tudo que uma conta ocupa em memória, num lugar só. Antes o logout limpava `sessoes` e
// `lidMaps` e esquecia os outros três caches — que ficavam ali segurando memória de uma
// conta que nem existe mais no serviço. As chaves são prefixadas por conta_id, e o
// separador (':' / ' ') impede que a conta 2 case com as chaves da conta 23.
function esquecerConta (contaId) {
  lidMaps.delete(contaId)
  const prefixo = contaId + ':'
  for (const k of jidsResolvidos.keys()) if (k.startsWith(prefixo)) jidsResolvidos.delete(k)
  for (const k of enviadas.keys()) if (k.startsWith(prefixo)) enviadas.delete(k)
  const prefixoLid = contaId + ' '
  for (const k of lidsPendentes.keys()) if (k.startsWith(prefixoLid)) lidsPendentes.delete(k)
}

async function jidRealDe (sock, contaId, numero) {
  const base = jidDe(numero)
  if (!base) return { jid: null, erro: 'numero_invalido' }
  const chave = contaId + ':' + base
  if (jidsResolvidos.has(chave)) {
    const guardado = jidsResolvidos.get(chave)
    return guardado ? { jid: guardado } : { jid: null, erro: 'sem_whatsapp' }
  }
  try {
    // PRAZO CURTO. O onWhatsApp cai no query() do Baileys, que usa o
    // defaultQueryTimeoutMs: 60 SEGUNDOS. Sem esse limite, uma consulta que o
    // WhatsApp demora a responder segurava o envio por até um minuto — e o lado
    // Python desiste em 20s (whatsapp_qr._TIMEOUT), então o vendedor levava erro
    // na tela com a mensagem ainda a caminho. Conferir o número é um conforto;
    // entregar a mensagem é a obrigação. Se não responder rápido, manda como veio.
    const consulta = sock.onWhatsApp(base.split('@')[0])
    consulta.catch(() => {})   // se estourar DEPOIS do prazo, ninguém está ouvindo
    const r = await Promise.race([
      consulta,
      new Promise((r2) => setTimeout(() => r2(null), PRAZO_ONWHATSAPP_MS))
    ])
    if (!r) {
      log.warn({ contaId, numero: base }, 'onWhatsApp demorou — manda pro número como veio')
      return { jid: base }
    }
    const achado = Array.isArray(r) ? r[0] : null
    if (achado && achado.exists && achado.jid) {
      lembrarJid(chave, achado.jid)
      if (achado.jid !== base) {
        log.info({ contaId, pedido: base, real: achado.jid },
          'número corrigido pelo WhatsApp antes de enviar')
      }
      return { jid: achado.jid }
    }
    if (achado && !achado.exists) {
      lembrarJid(chave, null)
      log.warn({ contaId, numero: base }, 'esse número não tem WhatsApp')
      return { jid: null, erro: 'sem_whatsapp' }
    }
  } catch (e) {
    log.warn({ contaId, e: String(e) }, 'onWhatsApp falhou — manda pro número como veio')
  }
  // consulta indisponível: segue com o número original (comportamento antigo),
  // melhor tentar entregar do que travar o envio por causa da checagem
  return { jid: base }
}

function duracao (segundos) {
  const s = Math.max(0, Math.round(Number(segundos) || 0))
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')
}

// Áudio não tem texto NENHUM, então caía no filtro de "sem texto" e a mensagem
// sumia do painel: o vendedor via a conversa com um buraco no meio, sem saber
// que tinha chegado alguma coisa. Vira uma marca legível com a duração. O áudio
// em si continua só no celular — aqui é pra ninguém perder o fio da conversa.
// normalizeMessageContent desembrulha efêmera/viewOnce, senão o áudio mandado
// em conversa com mensagens temporárias continuaria invisível.
function textoDaMsg (m) {
  const msg = normalizeMessageContent(m.message) || {}
  const texto = (msg.conversation ||
          (msg.extendedTextMessage && msg.extendedTextMessage.text) ||
          (msg.imageMessage && msg.imageMessage.caption) ||
          (msg.videoMessage && msg.videoMessage.caption) || '').trim()
  if (texto) return texto
  const audio = msg.audioMessage
  if (audio) {
    // ptt = gravado na hora (o "áudio do WhatsApp"); sem ptt é arquivo de música
    return (audio.ptt ? '🎤 Áudio' : '🎵 Áudio') + ' (' + duracao(audio.seconds) + ')'
  }
  return ''
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
//
// Só os caminhos AO VIVO (entrada/saída) passam por aqui: lá é uma linha por mensagem
// de verdade, e é sinal. O histórico faz o mesmo teste dentro do prepararHistorico, sem
// logar — uma onda de pareamento despejava centenas destas linhas iguais de uma vez, e
// hoje ela sai resumida numa linha só por onda (motivos + lidsPerdidos).
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

// Áudio RECEBIDO vira texto, pro vendedor responder digitando sem precisar ouvir.
// Só entrada ao vivo: histórico não passa por aqui de propósito — um pareamento
// novo despejaria centenas de áudios de uma vez e viraria uma conta inesperada.
const LIMITE_AUDIO_SEG = 120
// ...e um teto de BYTES, não só de duração. `seconds` vem do metadado que o remetente
// manda — não é medida nossa, e nada garante que bate com o arquivo. Um áudio de 120s
// em taxa alta são vários MB, e aqui ele existe em QUATRO cópias ao mesmo tempo:
// o Buffer do download, a string base64 (+33%), o corpo do JSON.stringify e a
// serialização do fetch.
const LIMITE_AUDIO_BYTES = 2 * 1024 * 1024

// E uma FILA, concorrência 1: a chamada sai sem await (de propósito — a marca
// "🎤 Áudio (0:18)" tem que aparecer no painel antes da transcrição ficar pronta), então
// N áudios chegando juntos multiplicavam aquelas quatro cópias por N, sem limite nenhum.
// Enfileirar troca latência de transcrição, que já é assíncrona pro vendedor, por pico
// de memória, que derruba o serviço inteiro.
let _filaAudio = Promise.resolve()

function enfileirarAudio (fn) {
  const proximo = _filaAudio.then(fn, fn)
  _filaAudio = proximo.catch(() => {})   // um áudio que falha não pode travar a fila
  return proximo
}

async function transcreverAudio (contaId, m, sender) {
  const msg = normalizeMessageContent(m.message) || {}
  const audio = msg.audioMessage
  if (!audio || !APP_URL) return
  const seg = Math.round(Number(audio.seconds) || 0)
  if (seg > LIMITE_AUDIO_SEG) {
    log.info({ contaId, seg }, 'áudio longo demais — não transcreve')
    return
  }
  try {
    const bytes = await downloadMediaMessage(m, 'buffer', {})
    if (!bytes || !bytes.length) return
    if (bytes.length > LIMITE_AUDIO_BYTES) {
      log.info({ contaId, seg, kb: Math.round(bytes.length / 1024) },
        'áudio pesado demais — não transcreve')
      return
    }
    const r = await fetch(APP_URL + '/webhooks/wa-qr/audio', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: JSON.stringify({
        conta_id: contaId, sender, segundos: seg,
        id: (m.key && m.key.id) || '',
        audio_b64: bytes.toString('base64')
      })
    })
    if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr/audio respondeu não-ok')
    else log.info({ contaId, seg, kb: Math.round(bytes.length / 1024) }, 'áudio mandado pra transcrição ✓')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao transcrever áudio') }
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
  // depois de a mensagem já estar no painel: assim ela aparece na hora com a
  // marca "🎤 Áudio (0:18)" e o texto entra por cima quando ficar pronto, em vez
  // de o vendedor esperar a transcrição pra ver que chegou alguma coisa
  enfileirarAudio(() => transcreverAudio(contaId, m, sender)).catch((e) =>
    log.warn({ contaId, e: String(e) }, 'transcreverAudio falhou'))
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
  // O áudio que o VENDEDOR grava pelo celular também vira texto. Faltava: a
  // transcrição só era disparada aqui na entrada (repassarEntrada), então metade
  // da conversa ficava legível e a outra metade era um "🎤 Áudio (0:09)" mudo —
  // quem abrisse a conversa depois via a pergunta do cliente e não via a resposta.
  // Mesma fila de concorrência 1 da entrada, e pelo mesmo motivo: a marca precisa
  // aparecer no painel antes, e N áudios juntos não podem multiplicar o pico de
  // memória. O webhook do outro lado casa por provider_sid e não olha direção, então
  // não precisou de rota nova.
  enfileirarAudio(() => transcreverAudio(contaId, m, destinatario)).catch((e) =>
    log.warn({ contaId, e: String(e) }, 'transcreverAudio (saída) falhou'))
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
  // por que cada contato foi descartado — sem isso não dá pra distinguir "o
  // WhatsApp mandou pouco" de "mandou muito e a gente jogou fora no filtro"
  let semNome = 0; let lidSemMapa = 0; let outroJid = 0
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
      else { lidSemMapa++; continue }
    }
    if (!jid.endsWith('@s.whatsapp.net')) { outroJid++; continue }
    const nomeAgenda = daAgenda ? String(ct.name || '').trim() : ''
    const nome = (nomeAgenda || String(ct.notify || ct.name || '').trim()).slice(0, 120)
    if (!nome) { semNome++; continue }
    ;(nomeAgenda ? agenda : reserva).push({ numero: jid.split('@')[0], nome })
  }
  if (contatos.length > 20 || lidSemMapa || outroJid) {
    log.info({ contaId, recebidos: contatos.length, agenda: agenda.length,
      reserva: reserva.length, semNome, lidSemMapa, outroJid },
    'repassarContatos: peneira')
  }
  await enviarContatos(contaId, agenda, true)
  await enviarContatos(contaId, reserva, false)
}

// A AGENDA do celular chega pela sincronização de "app state" (coleção
// critical_unblock_low -> contactAction.fullName -> evento contacts.upsert). Só
// que o Baileys dispara essa sincronização na PRIMEIRA notificação de histórico
// (Socket/chats.js: `if (shouldProcessHistoryMsg) await doAppStateSync()`), que
// costuma chegar ANTES das chaves de decodificação (appStateSyncKeyShare). Sem a
// chave, decodePatches estoura, o catch faz
// `keys.set({'app-state-sync-version': {[name]: null}})` — apaga a versão — e o
// estado vira Online. A partir daí a chave até chega, mas o retry embutido só
// roda `if (syncState === SyncState.Syncing)`: nunca mais. Resultado medido na
// conta 23: 31 chaves app-state-sync-key guardadas, ZERO app-state-sync-version,
// zero contato de agenda.
//
// Como a versão foi apagada, ela volta como 0 — e o resync com versão 0 pede
// `return_snapshot`, ou seja, a coleção INTEIRA de uma vez. Então basta refazer o
// pedido mais tarde, com as chaves já no banco. Duas tentativas: 30s e 2min
// depois de conectar (a segunda cobre o pareamento novo, onde as chaves demoram
// mais). É idempotente: se a agenda já veio, o WhatsApp devolve nada novo.
//
// ATENÇÃO ao `return_snapshot: (!state.version).toString()`: quem manda é a
// VERSÃO GUARDADA, não o parâmetro isInitialSync. Com versão > 0 o WhatsApp
// devolve só os patches DEPOIS daquela versão — e num pareamento em que a
// sincronização inicial funcionou pela metade (medido: versão 31 com apenas 93
// dos ~2870 contatos) o resto da agenda não vinha nunca, porque a versão parada
// no meio calava o pedido de snapshot. Por isso a 2ª tentativa APAGA a versão
// antes, forçando o snapshot inteiro. Roda só uma vez por credencial (marca
// 'agenda-completa' na própria wa_qr_auth): baixar a agenda toda a cada deploy
// seria desperdício, e a marca some junto com as credenciais no Desconectar.
const INTERVALO_AGENDA_MS = 20 * 60 * 1000
// ...com teto: 6 × 20min ≈ 2h de insistência por conexão — ver insistirNaAgenda.
const MAX_INSISTENCIAS_AGENDA = 6

function agendarResyncAgenda (contaId, s) {
  pararTimersDaAgenda(s)
  s._agendaTentativas = 0
  s._agendaT1 = setTimeout(() => resyncAgenda(contaId, 1, false), 30000)
  // a passada COMPLETA só depois que a sincronização inicial acalmar — ver
  // esperarAcalmarEResync
  s._agendaT2 = setTimeout(() => {
    esperarAcalmarEResync(contaId).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'esperarAcalmarEResync falhou'))
  }, 120000)
  // TEIMOSIA. As chaves de app-state podem simplesmente não chegar no primeiro
  // fôlego — medido num pareamento: 26 pre-keys e 8 sessões Signal gravadas, e
  // ZERO chave de app-state depois de 10 minutos. Sem essa repetição, a agenda
  // só teria outra chance no próximo religamento do serviço, e o vendedor ficava
  // com nome novo só de quem mandasse mensagem. Agora insiste a cada 20min
  // enquanto a conta estiver conectada e a agenda não tiver vindo; assim que
  // vier (marca 'agenda-completa'), para sozinho.
  s._agendaT3 = setInterval(() => {
    insistirNaAgenda(contaId).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'insistirNaAgenda falhou'))
  }, INTERVALO_AGENDA_MS)
}

async function insistirNaAgenda (contaId) {
  const s = sessoes.get(contaId)
  if (!s || s.status !== 'conectado') return           // sem sessão: nada a fazer
  const marca = await pool.query(
    `select 1 from wa_qr_auth where conta_id=$1 and arquivo='agenda-completa'`, [contaId])
  if (marca.rowCount) {
    log.info({ contaId }, 'agenda: já veio inteira, parando de insistir')
    clearInterval(s._agendaT3)
    s._agendaT3 = null
    return
  }
  // A teimosia precisa de fim. O próprio comentário acima descreve contas em que as
  // chaves de app-state nunca chegam — nessas, a marca 'agenda-completa' nunca aparece
  // e este intervalo repetia PRA SEMPRE, de 20 em 20 minutos, um pedido de snapshot da
  // coleção INTEIRA (o Baileys decodifica os ~4 mil contatos de uma vez). Era um pico
  // de memória recorrente e eterno numa instância de 512 MB, tentando de novo uma coisa
  // que já falhou dezenas de vezes pelo mesmo motivo. Depois do teto, para e registra;
  // o próximo religamento do serviço recomeça a contagem e tenta de novo.
  if (++s._agendaTentativas > MAX_INSISTENCIAS_AGENDA) {
    log.warn({ contaId, tentativas: MAX_INSISTENCIAS_AGENDA },
      'agenda: não veio depois de todas as tentativas — desistindo até o próximo religamento')
    clearInterval(s._agendaT3)
    s._agendaT3 = null
    return
  }
  log.info({ contaId, tentativa: s._agendaTentativas }, 'agenda: ainda não veio, tentando de novo')
  await esperarAcalmarEResync(contaId)
}

// Pedir a agenda inteira NO MEIO da sincronização inicial corrompe as duas.
// O motivo está no addTransactionCapability do Baileys (Utils/auth-utils.js):
// transactionCache e mutations são compartilhados por TODAS as transações em
// aberto, com um contador simples — não é um isolamento de verdade. Quando a
// nossa passada completa zera a versão da coleção, esse null cai no mesmo cache
// que a sincronização em andamento está lendo, e ela passa a calcular o estado
// a partir do zero com patches já aplicados. Resultado medido num pareamento
// novo: a agenda parou em exatos 1000 contatos (5 lotes) e nunca mais voltou,
// enquanto o mesmo código numa sessão ociosa trouxe 4264.
//
// Então espera o histórico parar de chegar (s.sincronizando cai sozinho 5s
// depois da última onda) antes de pedir. Teto de 15min pra nunca ficar preso.
// Espera DUAS condições, não uma:
//  1. o histórico parar de chegar (senão as transações se atropelam — ver acima);
//  2. as chaves de app-state existirem. Sem elas não há como decifrar a agenda, e
//     o resyncAgenda desiste na hora. Elas chegam DEPOIS do pareamento, num
//     appStateSyncKeyShare do celular — medido num pareamento novo: 26 pre-keys e
//     8 sessões Signal gravadas, e ZERO chave de app-state quase 6 minutos
//     depois. Antes disso a tentativa era uma só: se as chaves demorassem mais
//     que ela, a agenda ficava vazia até o próximo religamento do serviço.
async function esperarAcalmarEResync (contaId) {
  const limite = Date.now() + 15 * 60 * 1000
  let acalmou = false
  let temChaves = false
  while (Date.now() < limite) {
    const s = sessoes.get(contaId)
    if (!s || s.status !== 'conectado') return          // caiu: nada a fazer
    if (!acalmou && !s.sincronizando) acalmou = true
    if (!temChaves) {
      try {
        const r = await pool.query(
          `select 1 from wa_qr_auth where conta_id=$1 and arquivo like 'app-state-sync-key-%' limit 1`,
          [contaId])
        temChaves = !!r.rowCount
      } catch (e) { log.warn({ contaId, e: String(e) }, 'esperarAcalmarEResync: falha ao checar chaves') }
    }
    if (acalmou && temChaves) break
    await new Promise((r2) => setTimeout(r2, 5000))
  }
  if (!temChaves) {
    log.warn({ contaId, acalmou },
      'agenda: as chaves de app-state não chegaram no prazo — sem elas o WhatsApp não entrega a agenda')
    return
  }
  await resyncAgenda(contaId, 2, true)
}

// Só a coleção da AGENDA. Pedir as outras a partir do zero não traz contato
// nenhum e ainda estoura — medido em produção:
//
//   name: "regular_low"
//   error: "Error: tried remove, but no previous op" (Utils/chat-utils.js:55)
//   msg: "failed to sync state from version"
//
// Versão 0 PEDE o snapshot, mas o WhatsApp responde o que ele quiser — e pra
// regular_low ele mandou só patches. Os patches vêm com operações REMOVE, e sem
// snapshot a gente não tem o estado base de onde remover: o mix() acha
// prevOp indefinido e joga a exceção. Não é erro nosso nem dele, é pedir
// "apague o item X" pra quem nunca teve o item X. O catch do Baileys é por
// coleção, então isso nunca atrapalhou a agenda — só sujava o log.
const COLECOES = ['critical_unblock_low']
const VERSAO_AGENDA = 'app-state-sync-version-critical_unblock_low'

async function resyncAgenda (contaId, tentativa, completa) {
  const s = sessoes.get(contaId)
  if (!s || !s.sock || s.status !== 'conectado') return
  try {
    const chaves = await pool.query(
      `select count(*)::int as n from wa_qr_auth
        where conta_id=$1 and arquivo like 'app-state-sync-key-%'`, [contaId])
    if (!chaves.rows[0] || !chaves.rows[0].n) {
      log.info({ contaId, tentativa }, 'resyncAgenda: sem chaves de app-state ainda — pula')
      return
    }
    if (completa) {
      // a marca é só um controle nosso; existe = a agenda inteira já veio uma vez
      const marca = await pool.query(
        `select 1 from wa_qr_auth where conta_id=$1 and arquivo='agenda-completa'`,
        [contaId])
      if (marca.rowCount) {
        log.info({ contaId, tentativa }, 'resyncAgenda: agenda completa já foi baixada — só patches')
        completa = false
      } else {
        // Zerar a versão pelo KEY STORE, não por SQL. O Baileys lê a versão através
        // do makeCacheableSignalKeyStore, que guarda o valor em memória por 5min
        // (Utils/auth-utils.js) — apagar a linha no Postgres era invisível pra ele.
        // Medido no log: 'resyncAgenda: pedindo a agenda de novo (completa: true)'
        // seguido de 'resyncing critical_unblock_low from v31', com a linha já
        // apagada do banco. Pelo store o set(null) grava null no cache E chama o
        // nosso apagar(); aí o resyncAppState acha state falsy, cai em
        // newLTHashState() (versão 0) e finalmente pede o snapshot inteiro.
        const zeradas = {}
        for (const c of COLECOES) zeradas[c] = null
        await s.sock.authState.keys.set({ 'app-state-sync-version': zeradas })
        await pool.query('delete from wa_qr_auth where conta_id=$1 and arquivo=$2',
          [contaId, VERSAO_AGENDA])
      }
    }
    log.info({ contaId, tentativa, completa: !!completa, chaves: chaves.rows[0].n },
      'resyncAgenda: pedindo a agenda de novo')
    await s.sock.resyncAppState(COLECOES, true)
    if (completa) {
      // Só marca DEPOIS de dar certo, e o critério é a versão ter voltado: o
      // Baileys grava a versão nova quando o snapshot decodifica, e o catch dele
      // APAGA a versão quando falha (Socket/chats.js). Marcar antes de tentar
      // queimava a única chance — foi o que aconteceu no pedido que saiu com a
      // versão errada: falhou, mas a marca ficou lá dizendo que a agenda já viera.
      const v = await pool.query(
        'select 1 from wa_qr_auth where conta_id=$1 and arquivo=$2', [contaId, VERSAO_AGENDA])
      if (v.rowCount) {
        await pool.query(
          `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
                values ($1, 'agenda-completa', '1', now())
           on conflict (conta_id, arquivo) do nothing`, [contaId])
      } else {
        log.warn({ contaId, tentativa },
          'resyncAgenda: snapshot não gravou versão — falhou, deixando pra tentar de novo')
      }
    }
    log.info({ contaId, tentativa }, 'resyncAgenda: pedido concluído')
  } catch (e) {
    log.warn({ contaId, tentativa, e: String(e) }, 'resyncAgenda falhou')
  }
}

// Recibo de entrega/leitura. O Baileys emite messages.update com o status novo
// (proto.WebMessageInfo.Status: 0 ERROR, 1 PENDING, 2 SERVER_ACK, 3 DELIVERY_ACK,
// 4 READ, 5 PLAYED). PENDING fica de fora: é "ainda nem saiu", não diz nada pra
// quem está olhando o chat.
const STATUS_RECIBO = { 0: 'erro', 2: 'enviado', 3: 'entregue', 4: 'lido', 5: 'lido' }

async function repassarStatus (contaId, itens) {
  if (!APP_URL || !itens.length) return
  try {
    const r = await fetch(APP_URL + '/webhooks/wa-qr/status', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': SEGREDO },
      body: JSON.stringify({ conta_id: contaId, itens })
    })
    if (!r.ok) log.warn({ contaId, status: r.status }, 'webhook wa-qr/status respondeu não-ok')
    else log.info({ contaId, n: itens.length }, 'recibos repassados ✓')
  } catch (e) { log.warn({ contaId, e: String(e) }, 'falha ao repassar recibos') }
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

// QUAIS ondas de histórico processar. Esta função decide se um BLOB inteiro vai ser
// baixado da rede e descompactado na memória — não é um filtro de "o que fazer com o
// que já chegou". Confira na fonte do Baileys:
//
//   Socket/chats.js:779    shouldProcessHistoryMsg = shouldSyncHistoryMessage(...)
//   Utils/process-message.js:158-167
//       if (process) { await downloadAndProcessHistorySyncNotification(...) }
//
// Ou seja: o gate roda ANTES do download. Recusar aqui faz o blob nunca existir.
//
// E é por isso que o INITIAL_BOOTSTRAP saiu. Utils/history.js:10-20 mostra o custo:
//
//   for await (const chunk of stream) bufferArray.push(chunk)  // pedaços
//   let buffer = Buffer.concat(bufferArray)                    // cópia inteira
//   buffer = await inflatePromise(buffer)                      // descompactado, vários× maior
//   const syncData = proto.HistorySync.decode(buffer)          // objetos no heap
//
// Os três primeiros são Buffer, ou seja MEMÓRIA EXTERNA — que o --max-old-space-size
// NÃO limita —, e os quatro coexistem no pico. Foi exatamente isso que matou a
// instância de 512MB no Render, reproduzido: apagar o dispositivo no celular, parear
// de novo, e estourar na hora. A morte veio com a mensagem do Render
// ("Ran out of memory (used over 512MB)"), não com a do Node ("JavaScript heap out of
// memory") — a prova de que o teto de heap segurou o heap e o problema estava fora dele.
//
// O que fica:
//   RECENT     — a janela recente que o WhatsApp manda sozinho com syncFullHistory:false.
//                É a importação de conversa que sobrou.
//   PUSH_NAME  — só os nomes (Utils/history.js: `contacts.push({ id, notify: c.pushname })`,
//                nenhuma mensagem). É a fonte mais completa de nome que o WhatsApp manda,
//                e é barata justamente por não ter mensagem pra processar.
//
// O que fica de fora:
//   INITIAL_BOOTSTRAP — o blob do pareamento, o que estourava.
//   FULL              — o backfill da conta inteira (meses/anos), que já represava o
//                       tempo real antes.
//   ON_DEMAND         — pedido sob demanda; a gente não pede.
//
// CUSTO ACEITO: o pareamento não importa mais o histórico do BOOTSTRAP. Se um dia a
// conversa importada nascer vazia, o caminho é devolver o INITIAL_BOOTSTRAP e subir o
// serviço pra um plano com mais memória — nesta ordem, porque nenhum ajuste nosso
// encolhe o blob: quem baixa e descompacta é o Baileys por dentro.
function deveSincronizarHistorico (syncType) {
  return syncType === TIPO_HIST.RECENT || syncType === TIPO_HIST.PUSH_NAME
}

const HISTORICO_JANELA_SEGUNDOS = 30 * 24 * 3600 // só os últimos 30 dias — ver README (risco QR)

// Histórico importado (evento messaging-history.set, só dispara logo após conectar/parear).
// Vira conversa ÓRFÃ do lado Python (nunca gera lead sozinho) — ver /webhooks/wa-qr/historico.
//
// Separado em DUAS etapas de propósito. A peneira e a montagem do corpo são síncronas e
// baratas; o POST é que demora. Fazendo a peneira ANTES de montar a fila, o que fica
// retido enquanto os POSTs saem é uma string de algumas centenas de bytes por mensagem
// — não o proto do Baileys, que carrega Buffers (jpegThumbnail, mídia) junto. Numa onda
// de milhares de mensagens é a diferença entre segurar centenas de MB e segurar poucos.
// E o descarte (sem texto, grupo, canal, fora dos 30 dias) acontece de graça, antes de
// entrar na fila, em vez de gastar um passo da fila pra cada mensagem que ia ser jogada
// fora mesmo.
//
// Devolve `{ chat, corpo }` quando aceita e `{ motivo }` quando descarta — NUNCA null.
// O motivo existe porque a linha de log da onda dizia só `descartadas: 5000` num
// pareamento real, e 5000 pode ser tudo certo (grupo, status, mensagem velha) ou pode ser
// perda de conversa (@lid que o mapa não resolveu). Sem separar, qualquer decisão sobre
// @lid seria chute. Quem soma os motivos é o handler do messaging-history.set.
function prepararHistorico (contaId, m) {
  if (!APP_URL) return { motivo: 'sem_app_url' }
  const texto = textoDaMsg(m)
  const jid = (m.key && m.key.remoteJid) || ''
  if (!texto) return { motivo: 'sem_texto' }
  // Mesmo teste do ehConversaValida (usado nos caminhos ao vivo), aberto por sufixo:
  // aqui a gente CONTA, e "grupo" e "status" são coisas diferentes na hora de decidir
  // se o descarte foi legítimo.
  if (jid.endsWith('@g.us')) return { motivo: 'grupo' }
  if (jid.endsWith('@newsletter')) return { motivo: 'canal' }
  if (!jid || jid === 'status@broadcast') return { motivo: 'status' }
  const ts = Number(m.messageTimestamp) || 0
  if (!ts) return { motivo: 'sem_data' }
  const corteSegundos = Math.floor(Date.now() / 1000) - HISTORICO_JANELA_SEGUNDOS
  if (ts < corteSegundos) return { motivo: 'fora_da_janela' }
  // Mensagem ENVIADA (fromMe) entra também — antes era pulada e o histórico
  // importado ficava só com o lado do cliente, conversa pela metade. Nos dois
  // casos quem identifica a conversa é o CHAT (o outro lado), nunca o autor.
  const deMim = !!(m.key && m.key.fromMe)
  const resolvido = numeroDoChat(m, contaId)
  // Não passa pelo semNumeroReal de propósito: ele loga uma linha POR MENSAGEM, e numa
  // onda de histórico isso são centenas de linhas iguais no minuto mais movimentado do
  // serviço. Devolve o jid junto pra a onda contar CONTATOS distintos perdidos — 1400
  // mensagens de 8 pessoas é um problema, de 300 pessoas é outro.
  if (resolvido.endsWith('@lid')) return { motivo: 'lid_sem_mapa', jid: resolvido }
  const sender = resolvido.split('@')[0]
  return {
    // agrupa pelo CHAT já resolvido: duas mensagens do mesmo contato que chegaram
    // com jid diferente (@lid e @s.whatsapp.net) precisam cair na MESMA fila, senão
    // saem em paralelo e recriam a corrida de conversa duplicada que o comentário
    // do messaging-history.set descreve.
    chat: resolvido,
    corpo: JSON.stringify({
      conta_id: contaId, sender, texto, quando: ts, de_mim: deMim,
      // pushName só existe nas RECEBIDAS (numa fromMe o nome seria o do próprio
      // vendedor) — o Python só sobrescreve quando vem preenchido.
      nome: (!deMim && m.pushName) || '',
      id: (m.key && m.key.id) || ''
    })
  }
}

async function enviarHistorico (contaId, corpo) {
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
    // TRAVA ANTES DE QUALQUER COISA. Abrir socket com uma credencial que outra
    // instância já está usando é o que o WhatsApp responde com 440, derrubando
    // as duas em revezamento. Enquanto a outra não sair, esta conta fica
    // 'reconectando' — que é a verdade, e o painel já sabe mostrar isso — e a
    // gente volta a tentar sozinho. Nada é apagado: a credencial continua no
    // banco, quem está com ela segue trabalhando.
    if (!(await trava.pegar(contaId))) {
      s.status = 'reconectando'
      s.qr = null
      agendarTentativaDeTrava(contaId)
      return s
    }

    const { state, saveCreds, limparTudo } = await useDbAuthState(pool, contaId, log)
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
    descartarSocket(s.sock, contaId, 'substituido')

    const sock = makeWASocket({
      version,
      auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, log) },
      printQRInTerminal: false,
      browser: ['ZAQ', 'Chrome', '1.0.0'],
      logger: logBaileys,
      // syncFullHistory:true PEDE pro WhatsApp o histórico INTEIRO da conta (meses/
      // anos) — numa conta movimentada são dezenas de milhares de mensagens que
      // represam o tempo real e fazem parecer que "parou de funcionar do nada".
      // Deixando false, o WhatsApp manda sozinho só a janela RECENTE ao parear,
      // que é justamente o que a gente quer importar.
      syncFullHistory: false,
      // ...e aqui a gente escolhe QUAIS ondas processar — ver deveSincronizarHistorico
      shouldSyncHistoryMessage: (msg) => {
        const t = msg && msg.syncType
        const aceita = deveSincronizarHistorico(t)
        if (!aceita) {
          log.info({ contaId, syncType: t },
            'histórico recusado pelo gate — o blob não vai ser baixado')
        }
        return aceita
      },
      markOnlineOnConnect: false,
      // ver comentário do cache `enviadas`: é isto que permite reenviar quando o
      // aparelho do vendedor (ou do cliente) não consegue decifrar e pede retry.
      getMessage: async (key) => {
        const id = key && key.id
        const m = id ? await buscarEnviada(contaId, id) : undefined
        log.info({ contaId, id, achou: !!m, memoria: !!enviadas.get(contaId + ':' + id) },
          'retry: pediram reenvio de mensagem nossa')
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
          // frame chegando é o sinal de vida mais cru que existe — vale até quando a
          // mensagem não decodifica e nenhum evento de alto nível é emitido
          marcarVivo(contaId)
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
    //
    // `s.sock !== sock` fecha o outro lado da mesma porta, e é o que faltava: o 440
    // solta o socket (s.sock = null) e o Baileys ainda emite um 'open' ATRASADO
    // desse mesmo socket. Medido na conta 34 em 15/08:
    //
    //     22:16:15.890  conexão substituída  → s.sock = null
    //     22:16:16.402  WhatsApp conectado   ← meio segundo DEPOIS
    //
    // Esse 'open' tardio ressuscitava o estado: status voltava a 'conectado' com
    // socket nenhum. Daí em diante a sessão mentia pra todo mundo — o painel
    // mostrava o chip verde, o vigia do silêncio pulava a conta (sessaoMuda exige
    // s.sock), o carimbo de sessão firme era refeito por uma encarnação que já tinha
    // morrido, e só o /enviar descobria a verdade, com "temSock: false" na hora de
    // mandar a mensagem do cliente. É o mesmo "conectado e mudo" que abriu tudo isto.
    if (!socketAtual(s, sock)) return
    const { connection, lastDisconnect, qr } = u
    if (qr) {
      try { s.qr = await QRCode.toDataURL(qr) } catch (_) { s.qr = null }
      s.status = 'aguardando_qr'
      log.info({ contaId }, 'QR gerado')
    }
    if (connection === 'open') {
      s.status = 'conectado'; s.qr = null
      s.ultimoEvento = Date.now()   // ponto de partida do vigia — ver vigiarSessoes
      // quando ESTA encarnação subiu — é o relógio do sessaoFirme, que decide se uma
      // retomada pós-440 pegou ou se a conta está no meio de uma guerra de sessões
      s.abertoEm = Date.now()
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
      agendarResyncAgenda(contaId, s)
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
        // "sem apagar nada" vale pro que está no BANCO (credencial, histórico). O que
        // está na memória tem que sair: este socket não volta mais, e os laços da
        // agenda continuavam batendo no Postgres de 20 em 20min por uma sessão morta.
        descartarSocket(sock, contaId, 'substituida_por_outra_sessao')
        pararTimersDaAgenda(s)
        s.sock = null
        // esta encarnação acabou: quanto ela durou já foi contabilizado (sessaoFirme),
        // e deixar o carimbo velho faria a PRÓXIMA parecer firme antes de subir
        s.abertoEm = null
        // Levar 440 SEGURANDO a trava significa que quem assumiu não é outra
        // instância nossa — é o celular do vendedor abrindo o WhatsApp Web em
        // outro lugar, ou uma instância rodando sem a tabela da trava. A
        // distinção importa: a primeira é comportamento normal do usuário, a
        // segunda é a trava não estar valendo e merece investigação.
        log.warn({ contaId, seguravaATrava: trava.segura(contaId) },
          'conexão substituída por outra sessão — parando sem apagar nada')
        // Marca a hora pro vigia poder retomar depois (ver sessaoOrfa). Sem isto a
        // conta ficava sem sessão em lugar nenhum até o próximo deploy: soltava o
        // aluguel, retornava, e ninguém reagendava coisa alguma.
        s.substituidaEm = Date.now()
        // Solta: esta sessão não volta AGORA (reconectar aqui vira guerra de sessões),
        // e segurar o aluguel sem usar só impediria outra instância de assumir.
        await trava.soltar(contaId)
        return
      }
      const deslogado = code === DisconnectReason.loggedOut
      s.status = deslogado ? 'desconectado' : 'reconectando'
      s.qr = null
      s.abertoEm = null              // ver sessaoFirme: o relógio é por encarnação
      log.warn({ contaId, code, deslogado }, 'conexão fechou')
      if (deslogado) {
        // Só conta como logout DE VERDADE (o que autoriza apagar o histórico de
        // conversa) se esta sessão chegou a ABRIR neste processo. Um 401 logo na
        // tentativa de login é credencial velha/podre sendo rejeitada — limpa a
        // credencial (pro próximo /iniciar parear do zero) mas PRESERVA o chat.
        // Não dá pra usar creds.registered aqui: no fluxo QR ele nunca vira true.
        const foiLogoutReal = !!s.jaConectou
        // Fecha o socket ANTES de limpar. Sem isso ele seguia vivo com os
        // listeners escutando: o serviço já tinha apagado a credencial e tirado
        // a sessão do mapa, mas o socket zumbi continuava despejando contatos e
        // histórico no webhook com este contaId. Visto em produção — wa_qr_auth
        // ZERADA e wa_contatos crescendo centenas por minuto ao mesmo tempo, e
        // nenhuma conversa entrando. Marca _descartado primeiro pro 'close' que
        // o próprio end() emite não reentrar neste handler (mesmo padrão de
        // quando trocamos de socket em iniciarSessao).
        descartarSocket(sock, contaId, 'logout')
        pararTimersDaAgenda(s)
        try { await limparTudo() } catch (e) { log.warn({ contaId, e: String(e) }, 'limparTudo falhou') }
        sessoes.delete(contaId)
        esquecerConta(contaId)
        // Sem credencial não há sessão pra proteger; segurar o aluguel só
        // atrasaria o próximo pareamento (que pode cair noutra instância).
        clearTimeout(tentativasDeTrava.get(contaId)); tentativasDeTrava.delete(contaId)
        await trava.soltar(contaId)
        log.warn({ contaId, foiLogoutReal }, 'deslogado — credenciais limpas')
        if (foiLogoutReal) {
          avisarDeslogado(contaId).catch((e) => log.warn({ contaId, e: String(e) }, 'avisarDeslogado falhou'))
        }
      } else {
        // Descarta ESTE socket agora, não daqui a 2,5s no começo do iniciarSessao.
        // Nesse intervalo ele seguia vivo com todos os listeners presos ao closure —
        // e num flapping (o WhatsApp fechando em loop) cada ciclo deixava mais um pra
        // trás. Os timers da agenda também são desta encarnação: o agendarResyncAgenda
        // do próximo 'open' arma os dele.
        descartarSocket(sock, contaId, 'reconectando')
        pararTimersDaAgenda(s)
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
    marcarVivo(contaId)
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
    marcarVivo(contaId)
    repassarContatos(contaId, contatos, true).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'repassarContatos falhou'))
  })
  // ✓ enviado · ✓✓ entregue · 👀 lido. Só das mensagens que saíram daqui
  // (fromMe); recibo de mensagem recebida é o NOSSO aparelho confirmando, não
  // interessa pro chat. O lado Python casa pelo id e nunca deixa o status
  // regredir, porque esses eventos chegam fora de ordem com frequência.
  sock.ev.on('messages.update', (atualizacoes) => {
    marcarVivo(contaId)
    const itens = []
    for (const u of atualizacoes || []) {
      const k = u && u.key
      const st = u && u.update && u.update.status
      if (!k || !k.fromMe || !k.id) continue
      const nome = STATUS_RECIBO[st]
      if (nome) itens.push({ id: k.id, status: nome })
    }
    if (itens.length) {
      repassarStatus(contaId, itens).catch((e) =>
        log.warn({ contaId, e: String(e) }, 'repassarStatus falhou'))
    }
  })

  // Nome do PERFIL (pushName) — reserva, só preenche quando não há nome ainda.
  sock.ev.on('contacts.update', (contatos) => {
    marcarVivo(contaId)
    repassarContatos(contaId, contatos, false).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'repassarContatos (update) falhou'))
  })

  sock.ev.on('messaging-history.set', async ({ messages, contacts, isLatest, progress, syncType }) => {
    // Memória na ENTRADA e na SAÍDA desta onda. O log de minuto em minuto não serve
    // aqui: a tempestade de um pareamento inteiro coube em 28 segundos e passou entre
    // duas medições. `external`/`buffers` são a parte que interessa — é onde o blob de
    // histórico vive (Buffer, fora do heap), e foi o que estourou o RSS da instância.
    marcarVivo(contaId)
    const _m0 = process.memoryUsage()
    log.info({ contaId, n: messages.length, contatos: (contacts || []).length,
      isLatest, progress, syncType,
      rssMB: MB(_m0.rss), heapMB: MB(_m0.heapUsed),
      externalMB: MB(_m0.external), buffersMB: MB(_m0.arrayBuffers) },
    'messaging-history.set recebido')
    // Cada onda de histórico traz também os contatos daquele bloco — usa pra
    // resolver @lid -> número real (ver comentário em numeroReal). Faz isso ANTES
    // de repassar as mensagens da mesma onda, senão o mapa fica sempre um bloco
    // atrasado.
    atualizarLidMap(contaId, contacts)
    // O histórico também traz nome de contato: `name` no bloco de conversas (agenda)
    // e `notify` na onda PUSH_NAME (perfil). repassarContatos separa os dois.
    // Com await: sem ele, cada onda soltava uma cadeia concorrente que segurava um
    // array de milhares de contatos e ainda montava lotes de 200 em JSON — e as ondas
    // chegam em sequência rápida, então elas se acumulavam vivas ao mesmo tempo.
    // Esperar aqui não atrasa mensagem ao vivo (isto é um listener à parte).
    await repassarContatos(contaId, contacts, true).catch((e) =>
      log.warn({ contaId, e: String(e) }, 'repassarContatos (histórico) falhou'))
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
    // Peneira e monta o corpo AQUI (ver prepararHistorico): o que entra na fila é
    // string pronta, e a referência ao proto do Baileys morre neste laço em vez de
    // ficar viva até o último POST sair.
    const porChat = new Map()
    let descartadas = 0
    // Descarte ABERTO por motivo. `descartadas` sozinho não responde a pergunta que
    // importa: a maior parte de uma onda é jogada fora, e isso pode estar certo (grupo,
    // canal, status, mídia sem legenda, mais velha que 30 dias) ou pode ser conversa
    // perdida (@lid sem número no mapa). `lidsPerdidos` conta CONTATOS distintos, não
    // mensagens, porque é o número de contatos que diz se vale construir a resolução
    // atrasada de @lid; os exemplos são pra conferir no celular se é gente de verdade.
    const motivos = Object.create(null)
    const lidsPerdidos = new Set()
    for (const m of messages) {
      const pronta = prepararHistorico(contaId, m)
      if (pronta.motivo) {
        descartadas++
        motivos[pronta.motivo] = (motivos[pronta.motivo] || 0) + 1
        if (pronta.jid) lidsPerdidos.add(pronta.jid)
        continue
      }
      let grupo = porChat.get(pronta.chat)
      if (!grupo) { grupo = []; porChat.set(pronta.chat, grupo) }
      grupo.push(pronta.corpo)
    }
    log.info({ contaId, syncType, chats: porChat.size, descartadas, motivos,
      lidsPerdidos: lidsPerdidos.size, exemplosLid: [...lidsPerdidos].slice(0, 5) },
    'histórico peneirado, repassando')
    await comLimiteDeConcorrencia([...porChat.values()], 8, async (grupo) => {
      for (let i = 0; i < grupo.length; i++) {
        await enviarHistorico(contaId, grupo[i])
        grupo[i] = null   // solta o corpo assim que ele virou POST
      }
    })
    const _m1 = process.memoryUsage()
    log.info({ contaId, syncType,
      rssMB: MB(_m1.rss), heapMB: MB(_m1.heapUsed),
      externalMB: MB(_m1.external), buffersMB: MB(_m1.arrayBuffers),
      rssDeltaMB: MB(_m1.rss) - MB(_m0.rss) },
    'messaging-history.set concluído')
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
  limparEnviadasAntigas().catch(() => {})
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
    log.info({ n: contas.length, contas, espacoMs: ESPACO_CONTAS_MS },
      'restaurarSessoes: religando contas já pareadas')
    for (const contaId of contas) {
      try {
        await iniciarSessao(contaId)
      } catch (e) {
        log.error({ contaId, e: String(e && e.stack || e) }, 'restaurarSessoes: falhou nessa conta')
      }
      // Espaça as reconexões por TRÊS motivos: várias contas abrindo socket no
      // mesmo instante é um bom jeito de o WhatsApp achar que é abuso e
      // derrubar/bloquear todas; o intervalo devolve o event loop pro servidor
      // HTTP entre uma conta e outra, pro /saude continuar respondendo durante o
      // arranque; e — o que motivou subir de 3s pra 30s — cada conta continua
      // trabalhando pesado por MINUTOS depois de conectar (backlog offline,
      // agenda), então 3s fazia as sincronizações se empilharem em vez de se
      // sucederem. Ver ESPACO_CONTAS_MS.
      await new Promise((r2) => setTimeout(r2, ESPACO_CONTAS_MS))
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
        // {forcar:true} = derruba o socket atual ANTES de abrir outro. Sem isso o
        // iniciarSessao devolve a sessão que existe já na primeira linha, e uma sessão
        // que se diz conectada mas está muda (ver vigiarSessoes) não tinha como ser
        // ressuscitada por ninguém — nem pelo botão de reconectar do painel.
        const body = await lerBody(req)
        if (body && body.forcar) {
          const atual = sessoes.get(contaId)
          if (atual) {
            log.warn({ contaId, status: atual.status }, 'iniciar: reconexão FORÇADA pedida')
            descartarSocket(atual.sock, contaId, 'reconexao_forcada')
            pararTimersDaAgenda(atual)
            atual.sock = null
            atual.status = 'reconectando'
            atual.iniciando = false
          }
        }
        const s = await iniciarSessao(contaId)
        return json(res, 200, { ok: true, status: s.status, qr: s.qr,
          sincronizando: !!s.sincronizando, syncProgress: s.syncProgress || 0 })
      }
      if (req.method === 'GET' && acao === 'status') {
        const s = sessoes.get(contaId) || { status: 'desconectado', qr: null }
        // `mudoMs` é o que separa "conectado" de "conectado no papel": quem consome
        // consegue ver há quanto tempo aquele socket não entrega nada.
        return json(res, 200, { ok: true, status: s.status, qr: s.qr || null,
          sincronizando: !!s.sincronizando, syncProgress: s.syncProgress || 0,
          mudoMs: s.ultimoEvento ? (Date.now() - s.ultimoEvento) : null })
      }
      // Refaz o pedido da AGENDA sem precisar de novo QR (ver agendarResyncAgenda).
      // Serve pra consertar uma sessão que já está de pé com os nomes faltando.
      if (req.method === 'POST' && acao === 'agenda') {
        const s = sessoes.get(contaId)
        if (!s || s.status !== 'conectado' || !s.sock) {
          return json(res, 200, { ok: false, erro: 'desconectado' })
        }
        // na mão é sempre a agenda INTEIRA: quem chama aqui está justamente
        // dizendo que o que veio sozinho não bastou, então a marca sai da frente
        await pool.query(`delete from wa_qr_auth where conta_id=$1 and arquivo='agenda-completa'`,
          [contaId])
        await resyncAgenda(contaId, 0, true)
        return json(res, 200, { ok: true })
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
        // pergunta ao WhatsApp o JID de verdade antes de mandar — ver jidRealDe
        const alvo = await jidRealDe(s.sock, contaId, body.numero)
        if (!alvo.jid) return json(res, 200, { ok: false, erro: alvo.erro || 'numero_invalido' })
        try {
          const r = await s.sock.sendMessage(alvo.jid, { text: String(body.texto || '').slice(0, 4000) })
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
        // sem isso a teimosia da agenda continuaria batendo no banco a cada 20min
        // pra uma conta que acabou de ser desconectada
        pararTimersDaAgenda(s)
        // logout() com PRAZO: ele escreve um stanza no socket e espera
        // (Socket/socket.js: `await sendNode({... remove-companion-device ...})`).
        // Se o socket estiver num estado ruim isso pendura, e TODA a limpeza abaixo
        // — apagar credencial, apagar histórico — ficava esperando junto: o
        // "Desconectar" não desconectava nada e o painel dizia que sim. Avisar o
        // celular é o passo mais dispensável dos três; se não sair em 5s, segue.
        if (s && s.sock) {
          await Promise.race([
            s.sock.logout().catch(() => {}),
            new Promise((r2) => setTimeout(r2, 5000))
          ])
          // ...e fecha de fato. Quando o logout estoura o prazo acima, ele não chegou
          // a fechar nada: o socket ficava aberto pra sempre, com os caches do Baileys
          // inteiros, numa conta que o painel já mostra como desconectada.
          descartarSocket(s.sock, contaId, 'sair')
        }
        // Se o serviço reiniciou desde a última vez que essa conta ficou conectada em
        // memória (deploy, crash, etc.), `s` não existe mais aqui — mas as credenciais
        // continuam salvas no Postgres. Sem isso, "Desconectar" virava um no-op: as
        // creds antigas ficavam presas no banco e o próximo QR tentava retomar uma
        // sessão inválida em vez de parear do zero, causando "Não foi possível
        // conectar o dispositivo" repetido no celular.
        try {
          if (s && s._limparTudo) { await s._limparTudo() } else {
            const { limparTudo } = await useDbAuthState(pool, contaId, log)
            await limparTudo()
          }
        } catch (_) {}
        sessoes.delete(contaId)
        esquecerConta(contaId)
        // Desconectou de propósito: solta o aluguel e cancela a tentativa de
        // retomada, senão o timer religaria a conta que o vendedor acabou de
        // mandar desligar.
        clearTimeout(tentativasDeTrava.get(contaId)); tentativasDeTrava.delete(contaId)
        await trava.soltar(contaId)
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

// Só sobe o servidor quando o arquivo é EXECUTADO (npm start). Quando ele é apenas
// require()-ado — o teste-lidmap.js faz isso pra exercitar o lote de gravação contra um
// Postgres de verdade — não abre porta nem religa sessão nenhuma. Idioma padrão do Node;
// em produção `node server.js` cai no ramo de sempre.
// Saída limpa. Sem isto, o deploy do Render era assim: a instância nova subia,
// religava as sessões e roubava a credencial da velha (440 em todo mundo),
// enquanto a velha seguia de pé até levar SIGKILL. Agora o SIGTERM fecha os
// sockets PRIMEIRO — a ordem importa, um socket vivo com a trava já solta é
// exatamente a disputa que a trava veio impedir — e só depois solta os aluguéis,
// pra instância nova assumir em segundos em vez de esperar o prazo vencer.
let saindo = false
async function encerrar (sinal) {
  if (saindo) return
  saindo = true
  log.info({ sinal, contas: trava.contas() }, 'encerrando: fechando sockets e soltando travas')
  // Rede lenta na saída não pode virar processo pendurado: o Render manda
  // SIGKILL depois de alguns segundos de qualquer jeito, e sair sem soltar é
  // recuperável (o aluguel vence sozinho) — ficar preso não é.
  const forca = setTimeout(() => {
    log.warn({ sinal }, 'encerrando: demorou demais — saindo à força')
    process.exit(0)
  }, 5000)
  if (forca.unref) forca.unref()
  for (const [contaId, s] of sessoes) {
    try { descartarSocket(s.sock, contaId, 'encerrando'); pararTimersDaAgenda(s) } catch (_) {}
  }
  for (const t of tentativasDeTrava.values()) clearTimeout(t)
  tentativasDeTrava.clear()
  try { await trava.soltarTudo() } catch (e) { log.warn({ e: String(e) }, 'encerrando: soltarTudo falhou') }
  try { servidor.close() } catch (_) {}
  try { await pool.end() } catch (_) {}
  clearTimeout(forca)
  log.info({ sinal }, 'encerrando: pronto')
  process.exit(0)
}

if (require.main === module) {
process.on('SIGTERM', () => { encerrar('SIGTERM').catch(() => process.exit(0)) })
process.on('SIGINT', () => { encerrar('SIGINT').catch(() => process.exit(0)) })
servidor.listen(PORT, () => {
  log.info({ PORT, esperaRestaurarMs: ESPERA_RESTAURAR_MS }, 'wa-qr no ar')
  // Abrir a porta não basta: o health check do Render bate em /saude e desiste
  // em 5s, e /saude só responde se o event loop estiver livre. Religar sessão é
  // a parte mais pesada do arranque (handshake e criptografia do Signal, que têm
  // trechos SÍNCRONOS), então começar isso no mesmo instante em que a porta abre
  // fazia o primeiro health check cair no meio do bloqueio — deploy marcado como
  // falho com o serviço na verdade são. Agora o processo fica ocioso alguns
  // segundos primeiro: o health check passa, e só então as sessões voltam.
  //
  // O custo é atrasar em alguns segundos a volta das mensagens de ENTRADA depois
  // de um deploy. Envio não espera: /enviar religa a sessão sob demanda.
  setTimeout(() => {
    restaurarSessoes().catch((e) => log.error({ e: String(e) }, 'restaurarSessoes: erro solto'))
  }, ESPERA_RESTAURAR_MS)
  // Vigia das sessões mudas + retrato delas no banco. Uma volta de cada vez (o
  // await dentro do laço já serializa): duas rodadas em paralelo poderiam religar
  // a mesma conta duas vezes.
  let vigiando = false
  setInterval(() => {
    if (vigiando) return
    vigiando = true
    vigiarSessoes()
      .catch((e) => log.error({ e: String(e) }, 'vigiarSessoes: erro solto'))
      .then(registrarSessoes)
      .catch(() => {})
      .finally(() => { vigiando = false })
  }, VIGIA_INTERVALO_MS).unref()
  // Espelho do log no Postgres: descarrega o lote e limpa o que passou da idade.
  if (LOG_DB) {
    setInterval(() => {
      gravarLogsPendentes().catch(() => {})
    }, LOG_DB_FLUSH_MS).unref()
    limparLogsAntigos().catch(() => {})
    setInterval(() => { limparLogsAntigos().catch(() => {}) }, 60 * 60 * 1000).unref()
  }
})
}

// exposto só pro teste — ver o bloco acima
module.exports = { aprenderLid, gravarLidsPendentes, esquecerConta, guardarEnviada, buscarEnviada, deveSincronizarHistorico, prepararHistorico, sessaoMuda, tetoMudo, sessaoOrfa, esperaPos440, sessaoFirme, socketAtual, marcarVivo, vigiarSessoes, _ganchos, enfileirarLog, gravarLogsPendentes, registrarSessoes, TIPO_HIST, lidMaps, lidsPendentes, enviadas, jidsResolvidos, pool, iniciarSessao, trava, sessoes, tentativasDeTrava, encerrar, _logFila }
