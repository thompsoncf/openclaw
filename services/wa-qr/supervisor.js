'use strict'
/*
 * ZAQ · Supervisor do wa-qr — o processo que atende a porta e NUNCA congela.
 *
 * POR QUE ISTO EXISTE
 *
 * Até hoje o wa-qr era um processo só: HTTP, Baileys, criptografia do Signal,
 * agenda, histórico — tudo no mesmo event loop. O Signal tem trechos SÍNCRONOS, e
 * enquanto eles rodam o loop não atende mais ninguém: nem o `/enviar` do painel,
 * nem o `/saude` que o Render bate de 5 em 5s. O Render desiste e MATA a
 * instância — derrubando os três chips de uma vez por causa de um só.
 *
 * Medido em produção em 11/09/2026, com o carimbo da Fase 1 (`subindo`/`emCurso`):
 *
 *   16:36:30  event loop travou  atrasoMs: 43.472  subindo: [36, 34, 23] (~50s cada)
 *   16:37:23  event loop travou  atrasoMs: 45.575  decifragem: { 34: 30 }
 *   16:37:33  wa-qr no ar        ← o Render matou e subiu de novo
 *
 * Quarenta e cinco segundos de loop parado, com as três contas subindo juntas. A
 * memória estava em 118 MB o tempo todo: não faltava RAM, faltava LOOP.
 *
 * O CONSERTO, EM DUAS CAMADAS
 *
 * Aqui (Fase 2) o serviço passa a ser dois processos:
 *
 *   supervisor (este arquivo)  — abre a PORT, responde /saude, repassa o resto.
 *                                NÃO carrega o Baileys. O loop dele fica livre.
 *   worker     (server.js)     — o serviço de sempre, inteiro, numa porta interna.
 *
 * Um congelamento de 45s no worker deixa de ser visto pelo Render, porque quem
 * responde /saude é este processo. O worker sai do congelamento sozinho — foi o
 * que ele fez às 16:37, logo depois de ter sido morto à toa.
 *
 * O QUE ISTO **NÃO** RESOLVE, e é honesto dizer: com UM worker as três contas
 * continuam no mesmo loop, e o congelamento continua existindo lá dentro — só não
 * custa mais um reinício. Quem acaba com ele é a Fase 3, um worker por conta.
 * Esta fase é o encanamento sem o qual a Fase 3 não existe.
 *
 * SEM MEXER NO RENDER
 *
 * O Start Command continua o mesmo. Quem decide é o `server.js`, no topo: rodando
 * direto e sem `WA_QR_WORKER`, ele delega pra cá; com `WA_QR_WORKER=1` ele é o
 * serviço de sempre. Pra voltar ao comportamento antigo basta a variável de
 * ambiente `WA_QR_SUPERVISOR=0` — não precisa tocar no Start Command.
 *
 * Env daqui: PORT (a de fora), WA_QR_PORTA_WORKER (interna, default PORT+1),
 *            WA_QR_SUPERVISOR=0 (desliga), WA_QR_SAUDE_TOLERANCIA_MS,
 *            WA_QR_WORKER_ESPERA_MS, WA_QR_WORKER_ESPERA_MAX_MS.
 */
const http = require('node:http')
const { fork } = require('node:child_process')
const path = require('node:path')
const pino = require('pino')
const { Pool } = require('pg')

const PORT = parseInt(process.env.PORT || '3000', 10)
// A porta interna nunca sai da máquina: o worker escuta em 127.0.0.1 e só o
// supervisor fala com ele. PORT+1 evita ter que configurar mais uma variável.
const PORTA_WORKER = parseInt(process.env.WA_QR_PORTA_WORKER || String(PORT + 1), 10)

// Quanto tempo o /saude continua dizendo "ok" com o worker FORA do ar.
//
// Não é generosidade: é a diferença entre absorver um reinício de worker (que leva
// segundos) e ESCONDER um serviço quebrado. Worker congelado segue vivo como
// processo e o /saude responde ok — esse é o ponto de tudo isto. Worker que morreu
// e não volta em dois minutos é outra coisa, e aí o Render TEM que reiniciar a
// instância: mentir pra ele seria pior que o problema que viemos resolver.
const SAUDE_TOLERANCIA_MS = parseInt(process.env.WA_QR_SAUDE_TOLERANCIA_MS || '120000', 10)

// Espera antes de subir o worker de novo, dobrando. Worker que morre no arranque
// (erro de sintaxe, banco fora, variável faltando) reiniciaria em laço apertado e
// só encheria o log; a espera dá tempo de o motivo aparecer.
const ESPERA_MS = parseInt(process.env.WA_QR_WORKER_ESPERA_MS || '1000', 10)
const ESPERA_MAX_MS = parseInt(process.env.WA_QR_WORKER_ESPERA_MAX_MS || '30000', 10)
// Tempo de pé a partir do qual o worker "provou que fica", e a espera volta ao início.
const FIRME_MS = parseInt(process.env.WA_QR_WORKER_FIRME_MS || '300000', 10)

// Quanto o supervisor espera o worker fechar sozinho depois do SIGTERM.
//
// É o número mais importante deste arquivo. Quem solta as travas (`wa_qr_sessao_lock`)
// é o `encerrar()` do worker; se o supervisor morrer antes dele, as travas ficam
// presas até o prazo vencer — 180s — e a instância nova do deploy fica esperando,
// com os chips no chão. O `encerrar()` do worker já tem teto próprio; aqui a gente
// dá folga em cima dele.
const SIGTERM_ESPERA_MS = parseInt(process.env.WA_QR_SIGTERM_ESPERA_MS || '20000', 10)

// O QUE ACONTECE AQUI TEM QUE CHEGAR NO wa_qr_log.
//
// Todo diagnóstico desta base começa com SQL em `wa_qr_log` — o log do Render não
// é legível de fora, e é por isso que o worker espelha tudo no Postgres. Se a morte
// e o reinício do worker ficassem só no stdout, a pergunta "o worker caiu?" não
// teria resposta por consulta, justo agora que ela passa a ser a primeira pergunta.
//
// Sem lote, ao contrário do worker: aqui são poucas linhas por deploy (subiu,
// morreu, encerrando), e uma fila em memória só criaria a chance de perdê-las
// exatamente no momento em que elas importam — o processo indo embora.
//
// Falha de banco NUNCA derruba o supervisor: quem segura a porta não pode cair
// porque o Postgres piscou. O stdout continua tendo a linha de qualquer jeito.
function criarLogDoBanco (log) {
  if (process.env.WA_QR_LOG_DB === '0' || !process.env.DATABASE_URL) return () => {}
  let pool = null
  return function gravar (nivel, msg, dados) {
    try {
      if (!pool) pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 1 })
      pool.query(
        "insert into wa_qr_log (nivel, msg, dados) values ($1,$2,$3::jsonb)",
        [nivel, msg, JSON.stringify(dados || {})]
      ).catch((e) => log.warn({ e: String(e) }, 'supervisor: não consegui espelhar o log no banco'))
    } catch (e) {
      log.warn({ e: String(e) }, 'supervisor: não consegui espelhar o log no banco')
    }
  }
}

function iniciarSupervisor (opcoes) {
  const op = opcoes || {}
  const log = op.log || pino({
    level: process.env.LOG_LEVEL || 'info',
    base: { svc: 'wa-qr-sup' }
  })
  const forkar = op.fork || fork
  const noBanco = op.noBanco || criarLogDoBanco(log)
  const porta = op.porta || PORT
  const portaWorker = op.portaWorker || PORTA_WORKER
  const caminhoWorker = op.caminhoWorker || path.join(__dirname, 'server.js')

  const estado = {
    worker: null,
    // Desde quando não há worker vivo. `null` = tem worker. É o relógio do /saude.
    semWorkerDesde: Date.now(),
    quedas: 0,
    subiuEm: null,
    espera: op.esperaMs || ESPERA_MS,
    encerrando: false,
    timerSubida: null
  }

  // ---------------------------------------------------------------- o worker

  function subirWorker () {
    if (estado.encerrando) return
    estado.timerSubida = null
    let filho
    try {
      filho = forkar(caminhoWorker, [], {
        env: Object.assign({}, process.env, {
          // é isto que faz o server.js ser o serviço, e não outro supervisor
          WA_QR_WORKER: '1',
          PORT: String(portaWorker)
        }),
        stdio: 'inherit'
      })
    } catch (e) {
      // fork falhando (sem descritor, sem memória) não pode derrubar quem segura a
      // porta: tenta de novo depois da espera, como se o worker tivesse morrido.
      log.error({ e: String(e), esperaMs: estado.espera }, 'supervisor: não consegui dar fork no worker')
      const espera = estado.espera
      estado.espera = Math.min(espera * 2, op.esperaMaxMs || ESPERA_MAX_MS)
      estado.timerSubida = setTimeout(subirWorker, espera)
      if (estado.timerSubida.unref) estado.timerSubida.unref()
      return null
    }
    estado.worker = filho
    estado.subiuEm = Date.now()
    estado.semWorkerDesde = null
    log.info({ pid: filho.pid, portaWorker }, 'supervisor: worker no ar')
    noBanco('info', 'supervisor: worker no ar', { pid: filho.pid, quedas: estado.quedas })

    filho.on('exit', (codigo, sinal) => {
      // Só reage à morte do worker ATUAL. Um worker antigo terminando tarde (o que
      // acontece no SIGTERM, que espera o encerrar()) não pode ressuscitar nada nem
      // mexer no relógio do /saude do worker novo.
      if (estado.worker !== filho) return
      estado.worker = null
      estado.semWorkerDesde = Date.now()
      if (estado.encerrando) {
        log.info({ pid: filho.pid, codigo, sinal }, 'supervisor: worker fechou no encerramento')
        return
      }
      estado.quedas++
      // A ESPERA SÓ VOLTA A ZERO DEPOIS DE TEMPO DE PÉ, não a cada worker que sobe.
      //
      // Mesmo raciocínio do `sessaoFirme` lá no worker, e pelo mesmo motivo: zerar
      // no arranque desarma a dobra. Worker que morre a cada 40s reiniciaria pra
      // sempre com a espera inicial, e o log encheria de reinícios iguais sem nunca
      // dizer "isto não para de acontecer". Já um worker que ficou horas de pé e
      // caiu uma vez merece voltar rápido — por isso zerar, e não só não dobrar.
      const firme = estado.subiuEm && (Date.now() - estado.subiuEm) >= (op.firmeMs || FIRME_MS)
      if (firme) estado.espera = op.esperaMs || ESPERA_MS
      const espera = estado.espera
      log.error({ pid: filho.pid, codigo, sinal, quedas: estado.quedas, esperaMs: espera,
        dePeMs: estado.subiuEm ? Date.now() - estado.subiuEm : null },
      'supervisor: o worker morreu — subindo outro')
      // `dePeMs` vai junto: é ele que separa "caiu uma vez depois de horas" de "não
      // para de cair", e sem ele a linha do banco diria menos que a do stdout.
      noBanco('error', 'supervisor: o worker morreu — subindo outro',
        { pid: filho.pid, codigo, sinal, quedas: estado.quedas, esperaMs: espera,
          dePeMs: estado.subiuEm ? Date.now() - estado.subiuEm : null })
      estado.espera = Math.min(espera * 2, op.esperaMaxMs || ESPERA_MAX_MS)
      estado.timerSubida = setTimeout(subirWorker, espera)
      if (estado.timerSubida.unref) estado.timerSubida.unref()
    })

    filho.on('error', (e) => {
      log.error({ pid: filho.pid, e: String(e) }, 'supervisor: erro no worker')
    })
    return filho
  }

  // ------------------------------------------------------------------ /saude

  // O worker está vivo, ou morreu faz pouco o bastante pra não valer um reinício?
  //
  // Função pura de propósito: é a regra que decide se o Render mata a instância, e
  // regra dessas tem que poder ser lida e testada sem subir processo nenhum.
  function saudavel (est, agora, toleranciaMs) {
    if (est.worker) return true
    if (!est.semWorkerDesde) return true
    return (agora - est.semWorkerDesde) < toleranciaMs
  }

  // ------------------------------------------------------------- roteamento

  // Repassa a requisição inteira pro worker SEM juntar o corpo na memória.
  //
  // `enviar-midia` manda arquivo; bufferizar aqui traria de volta, no supervisor, o
  // problema de memória que a gente veio tirar do caminho. Cano de cano: o corpo
  // que entra é despejado direto no worker, e a resposta dele direto no cliente.
  function repassar (req, res) {
    const upstream = http.request({
      host: '127.0.0.1',
      port: portaWorker,
      method: req.method,
      path: req.url,
      headers: req.headers
    }, (resp) => {
      res.writeHead(resp.statusCode || 502, resp.headers)
      resp.pipe(res)
    })
    upstream.on('error', (e) => {
      // Worker fora do ar ou no meio de um reinício. Responder na hora — com o
      // mesmo formato que o worker usaria — é o que impede o painel de pendurar
      // esperando um processo que não vai responder.
      log.warn({ url: req.url, e: String(e) }, 'supervisor: o worker não atendeu')
      if (res.headersSent) return res.destroy()
      res.writeHead(503, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: false, erro: 'worker fora do ar' }))
    })
    // Se o cliente desistir no meio, o cano pro worker morre junto — senão fica
    // requisição órfã consumindo o worker que a gente está tentando aliviar.
    req.on('aborted', () => upstream.destroy())
    req.pipe(upstream)
  }

  const servidor = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/saude') {
      const ok = saudavel(estado, Date.now(), op.toleranciaMs || SAUDE_TOLERANCIA_MS)
      res.writeHead(ok ? 200 : 503, { 'content-type': 'application/json' })
      // `worker: false` num 200 é o sinal de "reiniciando agora" — o Render aceita,
      // e quem for depurar vê a diferença sem precisar do log.
      return res.end(JSON.stringify({ ok, worker: !!estado.worker, quedas: estado.quedas }))
    }
    return repassar(req, res)
  })

  // ------------------------------------------------------------- encerramento

  // SIGTERM é como o Render pede pra sair — no deploy e também quando mata por
  // health check. Repassar e ESPERAR não é educação: é o `encerrar()` do worker que
  // fecha os sockets e solta `wa_qr_sessao_lock`. Morrer antes dele deixaria as
  // travas presas até o prazo vencer (180s), com a instância nova esperando de
  // braços cruzados e os chips no chão — exatamente o deploy ruim que a trava veio
  // impedir.
  function encerrar (sinal) {
    if (estado.encerrando) return
    estado.encerrando = true
    clearTimeout(estado.timerSubida)
    const filho = estado.worker
    log.info({ sinal, pid: filho && filho.pid }, 'supervisor: encerrando')
    noBanco('info', 'supervisor: encerrando', { sinal, quedas: estado.quedas })
    try { servidor.close() } catch (_) {}
    if (!filho) return process.exit(0)
    const forca = setTimeout(() => {
      log.warn({ pid: filho.pid }, 'supervisor: o worker não fechou a tempo — matando')
      try { filho.kill('SIGKILL') } catch (_) {}
      process.exit(0)
    }, op.sigtermEsperaMs || SIGTERM_ESPERA_MS)
    if (forca.unref) forca.unref()
    filho.on('exit', () => { clearTimeout(forca); process.exit(0) })
    try { filho.kill('SIGTERM') } catch (_) { clearTimeout(forca); process.exit(0) }
  }

  return { servidor, estado, subirWorker, saudavel, repassar, encerrar, log, porta, portaWorker, noBanco }
}

// Abre a porta, sobe o worker e passa a atender. Separado de `iniciarSupervisor`
// porque quem chama isto na produção é o `server.js` — e um `require` sozinho não
// sobe nada, o que o teste de fumaça de 11/09 mostrou na primeira tentativa: o
// arquivo era importado, o `require.main === module` daqui dava falso porque o
// processo principal é o server.js, e o processo saía calado sem porta e sem
// worker. Função explícita não deixa esse buraco existir.
function rodar (opcoes) {
  const sup = iniciarSupervisor(opcoes)
  process.on('SIGTERM', () => sup.encerrar('SIGTERM'))
  process.on('SIGINT', () => sup.encerrar('SIGINT'))
  sup.servidor.listen(sup.porta, () => {
    sup.log.info({ PORT: sup.porta, portaWorker: sup.portaWorker }, 'supervisor no ar')
    sup.subirWorker()
  })
  return sup
}

// Rodar `node supervisor.js` direto também funciona — útil pra depurar sem passar
// pelo server.js. O teste require()-a e não cai aqui: exercita as peças sem abrir
// porta nem dar fork em nada.
if (require.main === module) rodar()

module.exports = { iniciarSupervisor, rodar, criarLogDoBanco }
