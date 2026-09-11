'use strict'
// O SUPERVISOR É QUEM SEGURA A PORTA QUANDO O WORKER CONGELA. Este teste fixa as
// quatro coisas que a Fase 2 existe pra provar.
//
// O caso real, 11/09/2026, medido com o carimbo da Fase 1:
//
//   16:36:30  event loop travou  atrasoMs: 43.472  subindo: [36, 34, 23] (~50s cada)
//   16:37:23  event loop travou  atrasoMs: 45.575  decifragem: { 34: 30 }
//   16:37:33  wa-qr no ar        ← o Render matou a instância e subiu outra
//
// Quarenta e cinco segundos de event loop parado com as três contas subindo juntas.
// Nesse intervalo o /saude não responde e o Render desiste — derrubando os TRÊS
// chips por causa de um. A RSS estava em 118 MB o tempo todo: não faltava memória,
// faltava loop.
//
// Sobe um worker DE MENTIRA (um http trivial), então não precisa de banco, de
// WhatsApp, nem do Baileys:
//
//     cd services/wa-qr && node teste-supervisor.js

process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const http = require('node:http')
const fs = require('fs')
const path = require('path')
const { iniciarSupervisor } = require('./supervisor')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}
const dorme = (ms) => new Promise((r) => setTimeout(r, ms))

// Um cliente HTTP mínimo, pra não depender de nada de fora.
function pedir (porta, caminho, opcoes) {
  const op = opcoes || {}
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: porta, path: caminho,
      method: op.metodo || 'GET', headers: op.headers || {} }, (res) => {
      let corpo = ''
      res.on('data', (d) => { corpo += d })
      res.on('end', () => resolve({ status: res.statusCode, corpo, headers: res.headers }))
    })
    req.on('error', reject)
    if (op.corpo) req.write(op.corpo)
    req.end()
  })
}

;(async () => {
  // --- 1. a regra do /saude, sem subir nada ---------------------------------
  //
  // É a regra que decide se o Render mata a instância. Testada como função pura
  // porque uma regra dessas não pode depender de tempo de processo pra ser lida.
  console.log('\nA regra do /saude:')
  const sup0 = iniciarSupervisor({ fork: () => { throw new Error('não devia forkar') } })
  const T = 1000000
  conferir(sup0.saudavel({ worker: {}, semWorkerDesde: null }, T, 120000) === true,
    'worker vivo (mesmo CONGELADO, que é o caso de 16:36) — saudável')
  conferir(sup0.saudavel({ worker: null, semWorkerDesde: T - 5000 }, T, 120000) === true,
    'worker reiniciando há 5s — saudável, o Render não precisa saber')
  conferir(sup0.saudavel({ worker: null, semWorkerDesde: T - 130000 }, T, 120000) === false,
    'worker morto há mais de 2 min — NÃO saudável: esconder isso seria pior que hoje')
  conferir(sup0.saudavel({ worker: null, semWorkerDesde: T - 119000 }, T, 120000) === true,
    'a tolerância é a fronteira, e ela é respeitada')

  // --- 2. o supervisor responde /saude SOZINHO ------------------------------
  //
  // O ponto inteiro da fase: /saude não passa pelo worker. Aqui não existe worker
  // nenhum, e mesmo assim a porta responde.
  console.log('\n/saude não depende do worker:')
  const sup1 = iniciarSupervisor({ fork: () => null, porta: 0, portaWorker: 59999 })
  await new Promise((r) => sup1.servidor.listen(0, r))
  const p1 = sup1.servidor.address().port
  const saude = await pedir(p1, '/saude')
  conferir(saude.status === 200, 'responde 200 sem worker nenhum de pé')
  conferir(JSON.parse(saude.corpo).worker === false,
    'e diz `worker: false` — quem depura vê a diferença sem abrir o log')

  // --- 3. worker fora do ar não pendura o painel ----------------------------
  console.log('\nWorker fora do ar:')
  const semWorker = await pedir(p1, '/session/34/status', { headers: { 'x-wa-secret': 'x' } })
  conferir(semWorker.status === 503, 'responde 503 na hora, não fica pendurado')
  conferir(JSON.parse(semWorker.corpo).ok === false,
    'no mesmo formato que o worker usaria ({ok:false, erro})')
  sup1.servidor.close()

  // --- 4. roteamento, inclusive com corpo grande ----------------------------
  //
  // O `enviar-midia` manda arquivo. Se o supervisor juntasse o corpo na memória,
  // traria de volta pra cá o problema que a gente veio tirar do worker.
  console.log('\nRoteamento pro worker:')
  let visto = null
  const worker = http.createServer((req, res) => {
    let n = 0
    req.on('data', (d) => { n += d.length })
    req.on('end', () => {
      visto = { metodo: req.method, url: req.url, segredo: req.headers['x-wa-secret'], bytes: n }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: true, eco: n }))
    })
  })
  await new Promise((r) => worker.listen(0, '127.0.0.1', r))
  const pw = worker.address().port

  const sup2 = iniciarSupervisor({ fork: () => null, portaWorker: pw })
  await new Promise((r) => sup2.servidor.listen(0, r))
  const p2 = sup2.servidor.address().port

  const grande = 'x'.repeat(3 * 1024 * 1024)     // 3 MB, tamanho de mídia de verdade
  const r4 = await pedir(p2, '/session/34/enviar-midia', {
    metodo: 'POST', corpo: grande, headers: { 'x-wa-secret': 'segredo-de-teste' }
  })
  conferir(r4.status === 200, 'a resposta do worker volta pro cliente')
  conferir(JSON.parse(r4.corpo).eco === grande.length, 'os 3 MB do corpo chegaram inteiros')
  conferir(visto && visto.metodo === 'POST' && visto.url === '/session/34/enviar-midia',
    'método e caminho preservados')
  conferir(visto && visto.segredo === 'segredo-de-teste',
    'e o x-wa-secret atravessa — o worker é quem autentica, o supervisor não opina')

  // e o /saude continua sendo dele, não do worker
  const saude2 = await pedir(p2, '/saude')
  conferir(saude2.status === 200 && visto.url === '/session/34/enviar-midia',
    'o /saude NÃO foi repassado ao worker — é isso que o Render passa a medir')
  sup2.servidor.close(); worker.close()

  // --- 5. o worker morre e o supervisor sobe outro --------------------------
  console.log('\nCiclo de vida do worker:')
  const filhos = []
  function forkFalso () {
    const ouvintes = {}
    const f = {
      pid: 1000 + filhos.length,
      on (ev, fn) { (ouvintes[ev] = ouvintes[ev] || []).push(fn) },
      kill () { this.matou = true },
      morrer (codigo, sinal) { (ouvintes.exit || []).forEach((fn) => fn(codigo, sinal)) }
    }
    filhos.push(f)
    return f
  }
  const sup3 = iniciarSupervisor({ fork: forkFalso, esperaMs: 10, esperaMaxMs: 80 })
  sup3.subirWorker()
  conferir(filhos.length === 1 && sup3.estado.worker === filhos[0], 'sobe um worker')
  conferir(sup3.estado.semWorkerDesde === null, 'com worker de pé o relógio do /saude para')

  filhos[0].morrer(1, null)
  conferir(sup3.estado.worker === null && sup3.estado.quedas === 1,
    'morreu: o supervisor conta a queda')
  conferir(typeof sup3.estado.semWorkerDesde === 'number',
    'e o relógio do /saude começa a correr')
  await dorme(60)
  conferir(filhos.length === 2, 'e sobe outro depois da espera')
  conferir(sup3.estado.espera > 10, 'a espera CRESCE — worker que morre no arranque não vira laço apertado')
  conferir(sup3.estado.espera <= 80, 'mas tem teto')

  // o worker ANTIGO terminando tarde não pode mexer em nada do novo
  const antesDeMorrerTarde = sup3.estado.quedas
  filhos[0].morrer(0, 'SIGTERM')
  conferir(sup3.estado.quedas === antesDeMorrerTarde && sup3.estado.worker === filhos[1],
    'worker ANTIGO fechando tarde não ressuscita nada nem derruba o novo')

  // --- 5b. a espera volta a zero DEPOIS de tempo de pé -----------------------
  //
  // Mesmo raciocínio do `sessaoFirme` no worker: zerar a cada worker que sobe
  // desarmaria a dobra, e um worker que morre de 40 em 40s reiniciaria pra sempre
  // com a espera inicial, sem o log nunca dizer "isto não para de acontecer".
  console.log('\nA espera só zera com tempo de pé:')
  // Lê a espera REALMENTE usada em cada morte (o `esperaMs` da linha de log), não a
  // próxima da escada — que é o que a primeira versão deste teste media, e por isso
  // acusava falha num comportamento correto.
  const usadas = []
  const logFalso = { info () {}, warn () {}, error (o) { if (o && o.esperaMs) usadas.push(o.esperaMs) } }
  const sup3b = iniciarSupervisor({ fork: forkFalso, log: logFalso, esperaMs: 10, esperaMaxMs: 80, firmeMs: 50 })
  sup3b.subirWorker()
  sup3b.estado.worker.morrer(1, null)       // morreu na hora
  await dorme(30)
  sup3b.estado.worker.morrer(1, null)       // e de novo
  await dorme(60)
  const w2 = sup3b.estado.worker
  await dorme(60)                           // este fica de pé MAIS que o firmeMs
  w2.morrer(1, null)
  conferir(usadas[0] === 10 && usadas[1] === 20,
    'worker que cai logo: a espera dobra a cada queda (10, 20)')
  conferir(usadas[2] === 10,
    'worker que FICOU de pé e caiu uma vez: a espera volta ao início, não continua em 40')
  conferir(sup3b.estado.espera === 20,
    'e a escada recomeça do começo — se cair logo de novo, dobra a partir dali')

  // --- 5c. fork falhando não derruba quem segura a porta ---------------------
  console.log('\nSe o fork falhar:')
  let tentativas = 0
  const sup3c = iniciarSupervisor({
    esperaMs: 10,
    fork: () => { tentativas++; throw new Error('EAGAIN') }
  })
  let explodiu = false
  try { sup3c.subirWorker() } catch (e) { explodiu = true }
  conferir(explodiu === false, 'não explode — quem segura a porta não pode cair junto')
  conferir(sup3c.estado.worker === null, 'e não finge que tem worker')
  await dorme(40)
  conferir(tentativas >= 2, 'tenta de novo depois da espera, como se o worker tivesse morrido')

  // --- 6. SIGTERM espera o worker fechar ------------------------------------
  //
  // O número mais importante do arquivo: quem solta `wa_qr_sessao_lock` é o
  // encerrar() do WORKER. Supervisor que morre antes dele deixa as travas presas
  // até o prazo vencer (180s) — e a instância nova do deploy espera de braços
  // cruzados, com os chips no chão.
  console.log('\nSIGTERM (o deploy):')
  // O encerrar() termina em process.exit de verdade. Fica trocado durante TODO
  // este bloco — restaurar no meio fazia o teste sair calado no meio do caminho,
  // com código 0, parecendo que tinha passado.
  const exitReal = process.exit
  let saiu = false
  process.exit = () => { saiu = true }
  try {
    const sup4 = iniciarSupervisor({ fork: forkFalso, esperaMs: 10 })
    sup4.subirWorker()
    const atual = sup4.estado.worker
    sup4.encerrar('SIGTERM')
    conferir(atual.matou === true, 'repassa o SIGTERM ao worker')
    conferir(saiu === false, 'e NÃO sai antes dele — é o worker que solta as travas')
    atual.morrer(0, null)
    conferir(saiu === true, 'só sai quando o worker fecha')

    const sup5 = iniciarSupervisor({ fork: forkFalso, esperaMs: 10 })
    sup5.subirWorker()
    const w5 = sup5.estado.worker
    const quantosAntes = filhos.length
    sup5.encerrar('SIGTERM')
    w5.morrer(1, null)                  // morte FEIA no meio do encerramento
    await dorme(40)                     // mais que a espera de 10ms
    conferir(filhos.length === quantosAntes,
      'e nem uma morte feia durante o encerramento faz subir worker novo')
  } finally {
    process.exit = exitReal
  }

  // --- 7. a trava de leitura do fonte ---------------------------------------
  console.log('\nA decisão de entrada está no código:')
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
  conferir(/if \(require\.main === module && !process\.env\.WA_QR_WORKER && process\.env\.WA_QR_SUPERVISOR !== '0'\) \{\s*\n\s*require\('\.\/supervisor'\)\.rodar\(\)\s*\n\s*return\s*\n\s*\}/.test(src),
    'server.js delega pro supervisor e PARA ali (o return é o que impede o processo leve de virar o pesado)')
  // `require` sozinho não sobe nada — foi o defeito que o teste de fumaça pegou na
  // primeira tentativa: o processo saía calado, sem porta e sem worker.
  conferir(/require\('\.\/supervisor'\)\.rodar\(\)/.test(src),
    'e CHAMA rodar() — importar o módulo por si só não abre porta nem dá fork')
  const antesDoBaileys = src.indexOf("require('./supervisor')") < src.indexOf("require('@whiskeysockets/baileys')")
  conferir(antesDoBaileys === true, 'e a delegação vem mesmo antes do Baileys, medido por posição no arquivo')
  conferir(/WA_QR_SUPERVISOR !== '0'/.test(src),
    'e existe escape por variável de ambiente, sem tocar no Start Command do Render')

  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})().catch((e) => { console.error('erro no teste:', e); process.exit(1) })
