'use strict'
/*
 * ZAQ · Supervisor do wa-qr — o processo que atende a porta e NUNCA congela,
 * e que sobe UM WORKER POR CONTA.
 *
 * POR QUE ISTO EXISTE
 *
 * Até 11/09 o wa-qr era um processo só: HTTP, Baileys, criptografia do Signal,
 * agenda, histórico — tudo no mesmo event loop, pra todas as contas. O Signal tem
 * trechos SÍNCRONOS, e enquanto eles rodam o loop não atende mais ninguém: nem o
 * `/enviar` do painel, nem o `/saude` que o Render bate de 5 em 5s. O Render
 * desiste e MATA a instância — derrubando os três chips por causa de um só.
 *
 * Medido em produção em 11/09/2026, com o carimbo da Fase 1 (`subindo`/`emCurso`):
 *
 *   16:36:30  event loop travou  atrasoMs: 43.472  subindo: [36, 34, 23] (~50s cada)
 *   16:37:23  event loop travou  atrasoMs: 45.575  decifragem: { 34: 30 }
 *   16:37:33  wa-qr no ar        ← o Render matou e subiu de novo
 *
 * Quarenta e cinco segundos de loop parado, com as três contas subindo juntas. A
 * memória estava em 118 MB o tempo todo: não faltava RAM, faltava LOOP. E o CP
 * Thiago (conta 36), que não tinha mensagem presa nenhuma, levou 14 quedas 408 em
 * três dias — todas pagas pelo travamento das vizinhas.
 *
 * O CONSERTO, EM DUAS FASES
 *
 *   Fase 2  o serviço vira dois processos: este SUPERVISOR abre a PORT, responde
 *           /saude e repassa o resto; o WORKER (server.js) é o serviço de sempre
 *           numa porta interna. Congelamento no worker deixa de custar reinício,
 *           porque quem responde /saude é este processo, e ele não carrega o
 *           Baileys.
 *
 *   Fase 3  UM WORKER POR CONTA. Cada conta ganha processo, event loop e teto de
 *           heap próprios (server.js com WA_QR_CONTA=<id>). O travamento de uma
 *           conta passa a ser só dela: o loop das outras segue livre. É o que
 *           acaba com o problema — a Fase 2 só tirava o reinício do caminho.
 *
 * COMO O SUPERVISOR SABE QUAIS CONTAS EXISTEM
 *
 * Lê do banco, com a MESMA consulta que o restaurarSessoes do worker sempre usou
 * (creds com `me.id` = conta pareada), de tempos em tempos: sobe worker que falta,
 * para worker de conta que deixou de existir. E conta NOVA — o primeiro
 * pareamento, quando ainda não há credencial — sobe sob demanda quando o painel
 * chama `/session/:conta/iniciar`. Com uma cautela: o segredo é conferido AQUI,
 * antes de abrir processo, senão qualquer requisição de fora faria o serviço
 * criar worker (ver `repassar`).
 *
 * SEM MEXER NO RENDER
 *
 * O Start Command continua o mesmo. Quem decide é o `server.js`, no topo: rodando
 * direto e sem `WA_QR_WORKER`, ele delega pra cá; com `WA_QR_WORKER=1` ele é o
 * serviço. Pra voltar ao comportamento antigo (um processo, todas as contas):
 * `WA_QR_SUPERVISOR=0` no ambiente.
 *
 * Env daqui: PORT (a de fora), WA_QR_PORTA_BASE (primeira porta interna, default
 *            PORT+1), WA_QR_SUPERVISOR=0 (desliga), WA_QR_SAUDE_TOLERANCIA_MS,
 *            WA_QR_WORKER_ESPERA_MS, WA_QR_WORKER_ESPERA_MAX_MS, WA_QR_WORKER_FIRME_MS,
 *            WA_QR_ESPACO_WORKERS_MS, WA_QR_RECONCILIA_MS, WA_QR_REAPER_GRACE_MS,
 *            WA_QR_HEAP_TOTAL_MB, WA_QR_HEAP_MIN_MB, WA_QR_MAX_WORKERS.
 */
const http = require('node:http')
const net = require('node:net')
const { fork } = require('node:child_process')
const path = require('node:path')
const pino = require('pino')
const { Pool } = require('pg')

const PORT = parseInt(process.env.PORT || '3000', 10)
// As portas internas nunca saem da máquina: cada worker escuta em 127.0.0.1 numa
// porta a partir desta, e só o supervisor fala com ele.
const PORTA_BASE = parseInt(process.env.WA_QR_PORTA_BASE || String(PORT + 1), 10)
const SEGREDO = process.env.WA_QR_SHARED_SECRET || ''

// Quanto tempo o /saude continua dizendo "ok" sem NENHUM worker vivo pra contas
// que deveriam ter um.
//
// Com um worker por conta a regra muda de figura: UM worker morto não pode
// derrubar a instância — seria devolver ao Render exatamente o poder de matar os
// três chips por causa de um. O /saude só fica vermelho quando TODAS as contas
// esperadas estão sem worker há mais que isto. Aí o serviço está quebrado de
// verdade e o Render tem que reiniciar; mentir pra ele seria pior.
const SAUDE_TOLERANCIA_MS = parseInt(process.env.WA_QR_SAUDE_TOLERANCIA_MS || '120000', 10)

// Espera antes de subir um worker de novo, dobrando por conta. Worker que morre no
// arranque reiniciaria em laço apertado e só encheria o log.
const ESPERA_MS = parseInt(process.env.WA_QR_WORKER_ESPERA_MS || '1000', 10)
const ESPERA_MAX_MS = parseInt(process.env.WA_QR_WORKER_ESPERA_MAX_MS || '30000', 10)
// Tempo de pé a partir do qual o worker "provou que fica", e a espera volta ao início.
const FIRME_MS = parseInt(process.env.WA_QR_WORKER_FIRME_MS || '300000', 10)

// Espaço entre a subida de um worker e a do próximo, no arranque e na reconciliação.
// Bem menor que os 30s que o processo único usava entre contas: agora cada uma tem o
// próprio loop, e o espaço aqui só evita que N handshakes disputem a CPU no mesmo
// segundo.
const ESPACO_WORKERS_MS = parseInt(process.env.WA_QR_ESPACO_WORKERS_MS || '5000', 10)

// De quanto em quanto tempo o supervisor relê as contas pareadas do banco.
const RECONCILIA_MS = parseInt(process.env.WA_QR_RECONCILIA_MS || '60000', 10)
// Um worker cuja conta NÃO está pareada só é parado depois disto de pé. É a folga
// do primeiro pareamento: o painel chama /iniciar, o worker sobe, mostra o QR e a
// pessoa leva um tempo pra escanear — nesse meio tempo não existe credencial no
// banco, e parar o worker aqui devolveria "desconectado" pra quem está com o
// celular na mão.
const REAPER_GRACE_MS = parseInt(process.env.WA_QR_REAPER_GRACE_MS || '600000', 10)

// TETO DE HEAP POR WORKER — o detalhe que separa "isolamento" de "OOM".
//
// O Start Command do Render é `node --max-old-space-size=1024 server.js`, e o fork
// herda os argumentos do Node. Com um worker (Fase 2) isso é o certo. Com N
// workers, cada um herdaria 1 GB de teto: três workers = 3 GB autorizados num
// plano de 2 GB, e o container inteiro cairia por OOM no dia de uma onda de
// histórico. O total é repartido entre os workers, com um piso: em 20/08 uma
// onda levou o heap de UMA conta a 314 MB, e o piso tem que caber isso.
const HEAP_TOTAL_MB = parseInt(process.env.WA_QR_HEAP_TOTAL_MB || '1536', 10)
const HEAP_MIN_MB = parseInt(process.env.WA_QR_HEAP_MIN_MB || '384', 10)

// Teto de workers. Não é limite de clientes — é o freio de mão contra uma
// enxurrada de /iniciar pra contas inventadas (o segredo já barra isso, mas
// segredo vazado não pode virar fork-bomb).
const MAX_WORKERS = parseInt(process.env.WA_QR_MAX_WORKERS || '40', 10)

// ------------------------------------------------------- Fase 4: o Baileys 7
//
// Quais contas rodam no 7.0.0-rc14 em vez do 6.7.24. Lista de ids separada por
// vírgula; VAZIA por padrão, então nada muda pra ninguém sem alguém escrever isto
// no ambiente do Render. É o chip de teste da Fase 4 — uma semana contando quedas
// por código antes de sequer pensar em migrar conta de cliente.
const BAILEYS7_CONTAS = String(process.env.WA_QR_BAILEYS7_CONTAS || '')

// Quantas vezes um worker v7 pode morrer ANTES de o supervisor desistir do v7
// naquela conta e subi-la de volta no 6.7.24.
//
// A regra existe porque a Fase 4 não pode custar um chip. Duas causas de morte
// precoce são certas e diagnosticáveis: Node velho demais pro require() de ESM
// (o rc14 é ESM puro) e o engine-requirements do próprio pacote. As duas saem com
// código 3 — e código 3 volta pro v6 na PRIMEIRA vez, sem gastar tentativa.
// Qualquer outra morte gasta uma tentativa: três seguidas e a conta volta pro v6.
const BAILEYS7_QUEDAS_MAX = parseInt(process.env.WA_QR_BAILEYS7_QUEDAS_MAX || '3', 10)

// Quanto o supervisor espera os workers fecharem depois do SIGTERM.
//
// É o número mais importante deste arquivo. Quem solta a trava de cada conta
// (`wa_qr_sessao_lock`) é o `encerrar()` do worker dela; se o supervisor morrer
// antes, as travas ficam presas até o prazo vencer — 180s — e a instância nova
// do deploy fica esperando, com os chips no chão.
const SIGTERM_ESPERA_MS = parseInt(process.env.WA_QR_SIGTERM_ESPERA_MS || '20000', 10)

// ---------------------------------------------------------------- funções puras

// Argumentos do Node pra um worker, com o teto de heap repartido.
//
// Pura de propósito: é a conta que decide se N workers cabem no plano, e uma
// conta dessas tem que poder ser lida e testada sem dar fork em nada.
// Esta conta roda em qual Baileys? Pura, porque é a chave da Fase 4: decide num
// lugar só, dá pra testar sem subir processo, e o teste fixa os dois sentidos.
//
// `lista` vem do ambiente como texto ('23' ou '23,36'). Comparação por String
// pelo mesmo motivo do contasDesteWorker no server.js: o id vem como número aqui
// e como texto do banco, e um `===` entre os dois já derrubou os três chips hoje.
function baileysDaConta (contaId, lista) {
  const ids = String(lista || '').split(',').map((x) => x.trim()).filter(Boolean)
  return ids.some((id) => String(id) === String(contaId)) ? 7 : 6
}

function execArgvDoWorker (execArgvDoPai, nWorkers, totalMb, minMb) {
  const n = Math.max(1, nWorkers || 1)
  const porWorker = Math.max(minMb, Math.floor(totalMb / n))
  const semTeto = (execArgvDoPai || []).filter((a) => !/^--max-old-space-size(=|$)/.test(a))
  return semTeto.concat(['--max-old-space-size=' + porWorker])
}

// O serviço está saudável? Só fica vermelho quando TODAS as contas esperadas
// estão sem worker há mais que a tolerância. Sem conta esperada (arranque, ou
// serviço vazio), está de pé.
function saudavel (est, agora, toleranciaMs) {
  const esperadas = [...est.contasEsperadas]
  if (!esperadas.length) return true
  let algumVivo = false
  let semWorkerHaMenos = false
  for (const contaId of esperadas) {
    const w = est.workers.get(contaId)
    if (w && w.filho) { algumVivo = true; break }
    const desde = (w && w.semWorkerDesde) || est.desde
    if ((agora - desde) < toleranciaMs) semWorkerHaMenos = true
  }
  return algumVivo || semWorkerHaMenos
}

// Menor porta livre a partir da base. Determinística: quem sobe primeiro fica com
// a primeira, e uma conta que morre e volta pega a mesma porta enquanto ninguém a
// ocupou. Não espera o worker "avisar" a porta — o supervisor manda, o worker
// obedece, e o roteamento já sabe pra onde ir antes mesmo de o processo existir.
function portaLivre (est, base) {
  const usadas = new Set([...est.workers.values()].map((w) => w.porta))
  let p = base
  while (usadas.has(p)) p++
  return p
}

// A leitura das contas pareadas é a MESMA do restaurarSessoes do worker, de
// propósito: é a definição de "conta pareada" desta base, e ter duas definições
// seria ter duas verdades.
const SQL_CONTAS_PAREADAS = `select conta_id from wa_qr_auth
  where arquivo = 'creds' and conteudo::json->'me'->>'id' is not null
  order by conta_id`

// O que acontece aqui tem que chegar no wa_qr_log: todo diagnóstico desta base
// começa com SQL nessa tabela. Sem lote — são poucas linhas, e uma fila em
// memória só criaria a chance de perdê-las no momento em que importam. Falha de
// banco NUNCA derruba o supervisor.
function criarLogDoBanco (log, pool) {
  if (!pool) return () => {}
  return function gravar (nivel, msg, dados, contaId) {
    try {
      pool.query(
        'insert into wa_qr_log (conta_id, nivel, msg, dados) values ($1,$2,$3,$4::jsonb)',
        [contaId || null, nivel, msg, JSON.stringify(dados || {})]
      ).catch((e) => log.warn({ e: String(e) }, 'supervisor: não consegui espelhar o log no banco'))
    } catch (e) {
      log.warn({ e: String(e) }, 'supervisor: não consegui espelhar o log no banco')
    }
  }
}

function iniciarSupervisor (opcoes) {
  const op = opcoes || {}
  const log = op.log || pino({ level: process.env.LOG_LEVEL || 'info', base: { svc: 'wa-qr-sup' } })
  const forkar = op.fork || fork
  const porta = op.porta || PORT
  const portaBase = op.portaBase || PORTA_BASE
  const caminhoWorker = op.caminhoWorker || path.join(__dirname, 'server.js')
  const segredo = op.segredo !== undefined ? op.segredo : SEGREDO
  const esperaMs = op.esperaMs || ESPERA_MS
  const esperaMaxMs = op.esperaMaxMs || ESPERA_MAX_MS
  const firmeMs = op.firmeMs || FIRME_MS
  const espacoMs = op.espacoMs !== undefined ? op.espacoMs : ESPACO_WORKERS_MS
  const graceMs = op.graceMs !== undefined ? op.graceMs : REAPER_GRACE_MS
  const maxWorkers = op.maxWorkers || MAX_WORKERS
  const heapTotalMb = op.heapTotalMb || HEAP_TOTAL_MB
  const heapMinMb = op.heapMinMb || HEAP_MIN_MB
  const execArgvDoPai = op.execArgv || process.execArgv
  const baileys7Contas = op.baileys7Contas !== undefined ? op.baileys7Contas : BAILEYS7_CONTAS
  const baileys7QuedasMax = op.baileys7QuedasMax || BAILEYS7_QUEDAS_MAX
  const pool = op.pool !== undefined ? op.pool
    : (process.env.DATABASE_URL ? new Pool({ connectionString: process.env.DATABASE_URL, max: 2 }) : null)
  const noBanco = op.noBanco || criarLogDoBanco(log, process.env.WA_QR_LOG_DB === '0' ? null : pool)

  const estado = {
    workers: new Map(),          // contaId -> { contaId, porta, filho, subiuEm, semWorkerDesde, quedas, espera, timerSubida, parando }
    contasEsperadas: new Set(),  // as pareadas, pela última leitura do banco
    desde: Date.now(),
    encerrando: false,
    filaDeSubida: [],            // contas esperando a vez (o espaço entre subidas)
    timerFila: null
  }

  // ---------------------------------------------------------------- o worker

  function registro (contaId) {
    let w = estado.workers.get(contaId)
    if (!w) {
      w = { contaId, porta: portaLivre(estado, portaBase), filho: null, subiuEm: null,
        semWorkerDesde: Date.now(), quedas: 0, espera: esperaMs, timerSubida: null, parando: false,
        // Fase 4: a versão que ESTA conta usa agora. Nasce da lista do ambiente e
        // só muda num sentido — 7 pode virar 6 quando o v7 não para de pé; 6 nunca
        // vira 7 sozinho. Voltar tem que ser decisão do serviço; avançar, de gente.
        baileys: baileysDaConta(contaId, baileys7Contas), quedasBaileys7: 0 }
      estado.workers.set(contaId, w)
    }
    return w
  }

  function subirWorker (contaId) {
    if (estado.encerrando) return null
    const w = registro(contaId)
    w.timerSubida = null
    if (w.filho) return w.filho          // já tem
    const nAgora = Math.max(estado.contasEsperadas.size, [...estado.workers.values()].filter((x) => x.filho).length + 1)
    let filho
    try {
      filho = forkar(caminhoWorker, [], {
        env: Object.assign({}, process.env, {
          WA_QR_WORKER: '1',                 // é isto que faz o server.js ser o serviço
          WA_QR_CONTA: String(contaId),      // ...de UMA conta
          WA_QR_BAILEYS: String(w.baileys),   // ...com esta versão da biblioteca
          PORT: String(w.porta)
        }),
        execArgv: execArgvDoWorker(execArgvDoPai, nAgora, heapTotalMb, heapMinMb),
        stdio: 'inherit'
      })
    } catch (e) {
      // fork falhando (sem descritor, sem memória) não pode derrubar quem segura a
      // porta: tenta de novo depois da espera, como se o worker tivesse morrido.
      log.error({ contaId, e: String(e), esperaMs: w.espera }, 'supervisor: não consegui dar fork no worker')
      const espera = w.espera
      w.espera = Math.min(espera * 2, esperaMaxMs)
      w.timerSubida = setTimeout(() => subirWorker(contaId), espera)
      if (w.timerSubida.unref) w.timerSubida.unref()
      return null
    }
    w.filho = filho
    w.subiuEm = Date.now()
    w.semWorkerDesde = null
    // `baileys` em TODA linha desta conta: é o que permite contar quedas por versão
    // com um group by, que é a medição inteira da Fase 4.
    log.info({ contaId, pid: filho.pid, porta: w.porta, baileys: w.baileys, workers: vivos().length },
      'supervisor: worker no ar')
    noBanco('info', 'supervisor: worker no ar',
      { pid: filho.pid, porta: w.porta, quedas: w.quedas, baileys: w.baileys }, contaId)

    filho.on('exit', (codigo, sinal) => {
      // Só reage à morte do worker ATUAL desta conta. Um worker antigo terminando
      // tarde não pode ressuscitar nada nem mexer no relógio do novo.
      if (w.filho !== filho) return
      w.filho = null
      w.semWorkerDesde = Date.now()
      if (estado.encerrando || w.parando) {
        log.info({ contaId, pid: filho.pid, codigo, sinal }, 'supervisor: worker fechou')
        if (w.parando) estado.workers.delete(contaId)
        return
      }
      w.quedas++
      // FASE 4: o v7 não pode custar um chip. Código 3 é o worker dizendo "não
      // consegui nem carregar a biblioteca" (require de ESM num Node velho, ou o
      // engine-requirements do pacote) — não adianta tentar de novo, volta na hora.
      // Qualquer outra morte gasta uma tentativa; esgotadas, volta também. O v6
      // nunca vira v7 por conta própria: avançar é decisão de gente.
      if (w.baileys === 7) {
        w.quedasBaileys7++
        const naoCarregou = codigo === 3
        if (naoCarregou || w.quedasBaileys7 >= baileys7QuedasMax) {
          w.baileys = 6
          const porque = naoCarregou
            ? 'o worker não conseguiu carregar o Baileys 7 (código 3: Node sem require de ESM, ou engine-requirements)'
            : 'o worker do Baileys 7 morreu ' + w.quedasBaileys7 + ' vezes'
          log.error({ contaId, codigo, quedasBaileys7: w.quedasBaileys7 },
            'supervisor: desistindo do Baileys 7 nesta conta — volta pro 6.7.24 (' + porque + ')')
          noBanco('error', 'supervisor: desistindo do Baileys 7 nesta conta — volta pro 6.7.24',
            { codigo, quedasBaileys7: w.quedasBaileys7, porque }, contaId)
        }
      }
      // A ESPERA SÓ VOLTA A ZERO DEPOIS DE TEMPO DE PÉ, não a cada worker que sobe.
      // Mesmo raciocínio do sessaoFirme, e pelo mesmo motivo: zerar no arranque
      // desarma a dobra, e um worker que morre a cada 40s reiniciaria pra sempre
      // com a espera inicial sem o log nunca dizer "isto não para de acontecer".
      const dePeMs = w.subiuEm ? Date.now() - w.subiuEm : null
      if (dePeMs !== null && dePeMs >= firmeMs) w.espera = esperaMs
      const espera = w.espera
      log.error({ contaId, pid: filho.pid, codigo, sinal, quedas: w.quedas, esperaMs: espera, dePeMs, baileys: w.baileys },
        'supervisor: o worker morreu — subindo outro')
      noBanco('error', 'supervisor: o worker morreu — subindo outro',
        { pid: filho.pid, codigo, sinal, quedas: w.quedas, esperaMs: espera, dePeMs, baileys: w.baileys }, contaId)
      w.espera = Math.min(espera * 2, esperaMaxMs)
      w.timerSubida = setTimeout(() => subirWorker(contaId), espera)
      if (w.timerSubida.unref) w.timerSubida.unref()
    })
    filho.on('error', (e) => log.error({ contaId, pid: filho.pid, e: String(e) }, 'supervisor: erro no worker'))
    return filho
  }

  function pararWorker (contaId, motivo) {
    const w = estado.workers.get(contaId)
    if (!w) return
    clearTimeout(w.timerSubida)
    w.parando = true
    if (!w.filho) { estado.workers.delete(contaId); return }
    log.info({ contaId, pid: w.filho.pid, motivo }, 'supervisor: parando worker')
    noBanco('info', 'supervisor: parando worker', { pid: w.filho.pid, motivo }, contaId)
    try { w.filho.kill('SIGTERM') } catch (_) {}
  }

  function vivos () { return [...estado.workers.values()].filter((w) => w.filho) }

  // Sobe as contas UMA de cada vez, com espaço entre elas. É a fila que impede o
  // arranque de N handshakes no mesmo segundo — o `subindo: [36, 34, 23]` do
  // carimbo, agora em processos separados mas ainda dividindo a CPU.
  function enfileirarSubida (contaId) {
    if (estado.workers.get(contaId) && estado.workers.get(contaId).filho) return
    if (estado.filaDeSubida.includes(contaId)) return
    estado.filaDeSubida.push(contaId)
    if (!estado.timerFila) andarNaFila()
  }
  function andarNaFila () {
    estado.timerFila = null
    const contaId = estado.filaDeSubida.shift()
    if (contaId === undefined) return
    subirWorker(contaId)
    // O espaço vale MESMO com a fila vazia agora. A primeira versão só armava o
    // timer se já houvesse alguém esperando — e como o reconciliar enfileira as
    // contas uma a uma, síncrono, cada uma encontrava a fila vazia e subia na
    // hora: as três no mesmo segundo, que é exatamente o que a fila existe pra
    // impedir. O teste pegou. Agora o timer é um "resfriamento": quem chegar
    // dentro dele espera a vez.
    estado.timerFila = setTimeout(andarNaFila, espacoMs)
    if (estado.timerFila.unref) estado.timerFila.unref()
  }

  // ------------------------------------------------------------ reconciliação

  // Lê as contas pareadas e faz o mapa de workers bater com elas.
  async function reconciliar (contasPareadas) {
    let contas = contasPareadas
    if (!contas) {
      if (!pool) return
      try {
        const r = await pool.query(SQL_CONTAS_PAREADAS)
        contas = r.rows.map((l) => parseInt(l.conta_id, 10))
      } catch (e) {
        // Banco fora do ar não pode virar "parar todo mundo": na dúvida, nada muda.
        log.warn({ e: String(e) }, 'supervisor: não consegui ler as contas pareadas — deixo como está')
        return
      }
    }
    estado.contasEsperadas = new Set(contas)
    for (const contaId of contas) {
      if (estado.workers.size >= maxWorkers && !estado.workers.has(contaId)) {
        log.error({ contaId, maxWorkers }, 'supervisor: teto de workers atingido — esta conta fica sem worker')
        continue
      }
      enfileirarSubida(contaId)
    }
    const agora = Date.now()
    for (const w of estado.workers.values()) {
      if (estado.contasEsperadas.has(w.contaId) || w.parando) continue
      // Conta sem credencial no banco. Ou saiu (/sair apagou as creds), ou está no
      // meio do primeiro pareamento — e aí tem a folga.
      const dePe = w.subiuEm ? agora - w.subiuEm : Infinity
      if (dePe >= graceMs) pararWorker(w.contaId, 'conta não está mais pareada')
    }
  }

  // ------------------------------------------------------------- roteamento

  function contaDaUrl (url) {
    const partes = (url || '').split('?')[0].split('/').filter(Boolean)
    if (partes[0] !== 'session' || !partes[1]) return null
    const n = parseInt(partes[1], 10)
    return n > 0 ? n : null
  }

  function responder (res, status, corpo) {
    if (res.headersSent) return res.destroy()
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(corpo))
  }

  // Repassa a requisição inteira pro worker da conta SEM juntar o corpo na memória.
  // `enviar-midia` manda arquivo; bufferizar aqui traria de volta, no supervisor, o
  // problema de memória que a gente veio tirar do caminho.
  function repassar (req, res) {
    const contaId = contaDaUrl(req.url)
    if (!contaId) return responder(res, 404, { ok: false, erro: 'rota' })
    let w = estado.workers.get(contaId)
    if (!w || (!w.filho && !w.timerSubida)) {
      // Conta sem worker. Pode ser o primeiro pareamento — e aí tem que subir sob
      // demanda. Mas o SEGREDO é conferido AQUI, antes de abrir processo: o worker
      // é quem autentica de verdade, só que ele ainda não existe, e abrir processo
      // pra qualquer requisição de fora seria dar ao mundo o botão de fork.
      if (req.headers['x-wa-secret'] !== segredo) return responder(res, 403, { ok: false, erro: 'segredo' })
      if (estado.encerrando) return responder(res, 503, { ok: false, erro: 'encerrando' })
      if (estado.workers.size >= maxWorkers) return responder(res, 503, { ok: false, erro: 'teto de workers' })
      log.info({ contaId }, 'supervisor: conta sem worker — subindo sob demanda')
      subirWorker(contaId)
      w = estado.workers.get(contaId)
    }
    if (!w) return responder(res, 503, { ok: false, erro: 'worker fora do ar' })
    // Worker recém-nascido ainda não abriu a porta. Em vez de 503 na cara do painel
    // (que está mostrando um QR pra alguém com o celular na mão), espera um pouco.
    const jovem = w.subiuEm && (Date.now() - w.subiuEm) < 15000
    repassarPara(w, req, res, jovem ? 20 : 0)
  }

  function repassarPara (w, req, res, tentativas) {
    // Se o cliente desistir no meio, o cano pro worker morre junto — senão fica
    // requisição órfã consumindo o worker que a gente está tentando aliviar. Um
    // listener só, por fora do laço de tentativas: registrar um por tentativa
    // vazava (o Node avisou, 11 listeners de 'aborted').
    let atual = null
    req.once('aborted', () => { if (atual) atual.destroy() })
    const tentar = (restantes) => {
      const upstream = http.request({
        host: '127.0.0.1', port: w.porta, method: req.method, path: req.url, headers: req.headers
      }, (resp) => {
        res.writeHead(resp.statusCode || 502, resp.headers)
        resp.pipe(res)
      })
      atual = upstream
      upstream.on('error', (e) => {
        if (restantes > 0 && e && e.code === 'ECONNREFUSED' && !res.headersSent && !req.readableEnded) {
          // ainda subindo: espera meio segundo e tenta de novo, sem ter consumido o corpo
          return setTimeout(() => tentar(restantes - 1), 500)
        }
        log.warn({ contaId: w.contaId, url: req.url, e: String(e) }, 'supervisor: o worker não atendeu')
        responder(res, 503, { ok: false, erro: 'worker fora do ar' })
      })
      // Só começa a despejar o corpo quando a conexão abriu — assim a tentativa
      // seguinte (se houver) ainda encontra o corpo inteiro por ler. Socket
      // reaproveitado (keep-alive) já vem conectado e não emite 'connect' de
      // novo; esperar por ele nesse caso penduraria a requisição pra sempre.
      upstream.on('socket', (s) => {
        if (s.connecting) s.once('connect', () => req.pipe(upstream))
        else req.pipe(upstream)
      })
    }
    tentar(tentativas)
  }

  const servidor = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/saude') {
      const ok = saudavel(estado, Date.now(), op.toleranciaMs || SAUDE_TOLERANCIA_MS)
      return responder(res, ok ? 200 : 503, {
        ok, workers: vivos().length, esperadas: estado.contasEsperadas.size,
        quedas: [...estado.workers.values()].reduce((n, w) => n + w.quedas, 0)
      })
    }
    return repassar(req, res)
  })

  // ------------------------------------------------------------- encerramento

  // SIGTERM é como o Render pede pra sair. Repassar a TODOS os workers e ESPERAR
  // todos: cada um solta a trava da própria conta no encerrar(). Sair antes deixa
  // travas presas 180s, com a instância nova esperando e os chips no chão.
  function encerrar (sinal) {
    if (estado.encerrando) return
    estado.encerrando = true
    clearTimeout(estado.timerFila)
    for (const w of estado.workers.values()) clearTimeout(w.timerSubida)
    const abertos = vivos()
    log.info({ sinal, workers: abertos.length }, 'supervisor: encerrando')
    noBanco('info', 'supervisor: encerrando', { sinal, workers: abertos.length })
    try { servidor.close() } catch (_) {}
    const sair = () => { try { pool && pool.end().catch(() => {}) } catch (_) {} process.exit(0) }
    if (!abertos.length) return sair()
    let faltam = abertos.length
    const forca = setTimeout(() => {
      log.warn({ faltam }, 'supervisor: worker(s) não fecharam a tempo — matando')
      for (const w of vivos()) { try { w.filho.kill('SIGKILL') } catch (_) {} }
      sair()
    }, op.sigtermEsperaMs || SIGTERM_ESPERA_MS)
    if (forca.unref) forca.unref()
    for (const w of abertos) {
      w.filho.on('exit', () => { if (--faltam === 0) { clearTimeout(forca); sair() } })
      try { w.filho.kill('SIGTERM') } catch (_) { if (--faltam === 0) { clearTimeout(forca); sair() } }
    }
  }

  return { servidor, estado, subirWorker, pararWorker, reconciliar, enfileirarSubida, saudavel,
    repassar, encerrar, log, porta, portaBase, noBanco, vivos, contaDaUrl }
}

// Abre a porta, lê as contas e sobe os workers. Separado de `iniciarSupervisor`
// porque quem chama isto na produção é o `server.js` — e um `require` sozinho não
// sobe nada (o teste de fumaça da Fase 2 pegou isso na primeira tentativa).
function rodar (opcoes) {
  const sup = iniciarSupervisor(opcoes)
  process.on('SIGTERM', () => sup.encerrar('SIGTERM'))
  process.on('SIGINT', () => sup.encerrar('SIGINT'))
  sup.servidor.listen(sup.porta, () => {
    sup.log.info({ PORT: sup.porta, portaBase: sup.portaBase }, 'supervisor no ar')
    sup.noBanco('info', 'supervisor no ar', { PORT: sup.porta, portaBase: sup.portaBase })
    // Um pouco de folga antes de subir workers: o health check do Render bate logo
    // que a porta abre, e a primeira resposta tem que ser rápida.
    setTimeout(() => sup.reconciliar().catch((e) => sup.log.error({ e: String(e) }, 'supervisor: reconciliar falhou')), 2000)
    const t = setInterval(() => {
      sup.reconciliar().catch((e) => sup.log.error({ e: String(e) }, 'supervisor: reconciliar falhou'))
    }, (opcoes && opcoes.reconciliaMs) || RECONCILIA_MS)
    if (t.unref) t.unref()
  })
  return sup
}

if (require.main === module) rodar()

module.exports = { iniciarSupervisor, rodar, criarLogDoBanco, execArgvDoWorker, saudavel, portaLivre, baileysDaConta, SQL_CONTAS_PAREADAS }
