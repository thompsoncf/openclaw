'use strict'
// O SUPERVISOR SEGURA A PORTA E SOBE UM WORKER POR CONTA. Este teste fixa o que as
// Fases 2 e 3 existem pra provar.
//
// O caso real, 11/09/2026, medido com o carimbo da Fase 1:
//
//   16:36:30  event loop travou  atrasoMs: 43.472  subindo: [36, 34, 23] (~50s cada)
//   16:37:23  event loop travou  atrasoMs: 45.575  decifragem: { 34: 30 }
//   16:37:33  wa-qr no ar        ← o Render matou a instância e subiu outra
//
// Quarenta e cinco segundos de event loop parado com as três contas subindo juntas
// num processo só. O /saude não responde, o Render desiste, e os TRÊS chips caem
// por causa de um. A RSS estava em 118 MB: não faltava memória, faltava loop.
//
// Sobe workers DE MENTIRA (http trivial) ou nenhum, então não precisa de banco,
// de WhatsApp nem do Baileys:
//
//     cd services/wa-qr && node teste-supervisor.js

process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const http = require('node:http')
const fs = require('fs')
const path = require('path')
const { iniciarSupervisor, execArgvDoWorker, saudavel, portaLivre, SQL_CONTAS_PAREADAS } = require('./supervisor')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}
const dorme = (ms) => new Promise((r) => setTimeout(r, ms))
const SEG = 'segredo-de-teste'

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

// Um fork de mentira que lembra com que env e execArgv foi chamado.
const filhos = []
function forkFalso (caminho, args, opts) {
  const ouvintes = {}
  const f = {
    pid: 1000 + filhos.length, env: opts.env, execArgv: opts.execArgv,
    on (ev, fn) { (ouvintes[ev] = ouvintes[ev] || []).push(fn) },
    kill (sinal) { this.matou = sinal || 'SIGTERM' },
    morrer (codigo, sinal) { (ouvintes.exit || []).forEach((fn) => fn(codigo, sinal)) }
  }
  filhos.push(f)
  return f
}
// Desliga um supervisor de teste sem deixar timer pendente. Sem isto, o reinício
// agendado de um bloco disparava DENTRO do bloco seguinte e empurrava um filho a
// mais no `filhos` — o teste do SIGTERM viu quatro workers onde esperava três.
function desligar (sup) {
  sup.estado.encerrando = true
  clearTimeout(sup.estado.timerFila)
  for (const w of sup.estado.workers.values()) clearTimeout(w.timerSubida)
}
function base (extra) {
  return Object.assign({ fork: forkFalso, pool: null, noBanco: () => {}, segredo: SEG,
    esperaMs: 10, esperaMaxMs: 80, espacoMs: 0, graceMs: 0 }, extra || {})
}

;(async () => {
  // --- 1. o teto de heap repartido -----------------------------------------
  //
  // O Start Command é `node --max-old-space-size=1024 server.js` e o fork herda
  // isso. Com N workers cada um herdaria 1 GB: três workers = 3 GB autorizados num
  // plano de 2 GB. É a diferença entre isolamento e OOM.
  console.log('\nO teto de heap é repartido entre os workers:')
  const herdado = ['--max-old-space-size=1024']
  conferir(execArgvDoWorker(herdado, 1, 1536, 384).join(' ') === '--max-old-space-size=1536',
    'um worker fica com o total (Fase 2 continua igual)')
  conferir(execArgvDoWorker(herdado, 3, 1536, 384).join(' ') === '--max-old-space-size=512',
    'três workers: 512 cada — cabe nos 2 GB do plano com folga pro nativo')
  conferir(execArgvDoWorker(herdado, 10, 1536, 384).join(' ') === '--max-old-space-size=384',
    'dez workers: o piso segura em 384 (a onda de 20/08 levou UMA conta a 314 MB)')
  conferir(!execArgvDoWorker(herdado, 3, 1536, 384).some((a, i, arr) => arr.indexOf(a) !== i),
    'e o teto herdado do pai é SUBSTITUÍDO, não empilhado')
  conferir(execArgvDoWorker(['--inspect', '--max-old-space-size=1024'], 2, 1536, 384).join(' ') === '--inspect --max-old-space-size=768',
    'outros argumentos do Node atravessam intactos')

  // --- 2. a regra do /saude, sem subir nada ---------------------------------
  //
  // Com um worker por conta a regra muda de figura: UM worker morto NÃO pode
  // derrubar a instância — seria devolver ao Render o poder de matar os três
  // chips por causa de um.
  console.log('\nA regra do /saude com N workers:')
  const T = 1000000
  const est = (workers, esperadas) => ({ workers: new Map(workers), contasEsperadas: new Set(esperadas), desde: T - 999999 })
  conferir(saudavel(est([], []), T, 120000) === true, 'sem conta esperada: de pé (arranque, serviço vazio)')
  conferir(saudavel(est([[34, { filho: {} }], [23, { filho: null, semWorkerDesde: T - 999000 }]], [34, 23]), T, 120000) === true,
    'UM worker morto há muito tempo e outro vivo: de pé — um chip não derruba os outros')
  conferir(saudavel(est([[34, { filho: null, semWorkerDesde: T - 5000 }]], [34]), T, 120000) === true,
    'todos reiniciando há 5s: de pé, o Render não precisa saber')
  conferir(saudavel(est([[34, { filho: null, semWorkerDesde: T - 130000 }], [23, { filho: null, semWorkerDesde: T - 130000 }]], [34, 23]), T, 120000) === false,
    'TODOS mortos há mais de 2 min: vermelho — aí o serviço está quebrado e esconder seria pior')
  conferir(saudavel(est([], [34]), T, 120000) === false,
    'conta esperada que nunca ganhou worker, há muito: vermelho')

  // --- 3. portas determinísticas -------------------------------------------
  console.log('\nPortas:')
  const e3 = { workers: new Map([[34, { porta: 10001 }], [23, { porta: 10002 }]]) }
  conferir(portaLivre(e3, 10001) === 10003, 'a próxima livre a partir da base')
  e3.workers.delete(34)
  conferir(portaLivre(e3, 10001) === 10001, 'buraco deixado por quem saiu é reaproveitado')

  // --- 4. reconciliar: sobe um worker por conta, com o env certo ------------
  console.log('\nReconciliar sobe um worker por conta:')
  filhos.length = 0
  const sup4 = iniciarSupervisor(base({ execArgv: ['--max-old-space-size=1024'] }))
  await sup4.reconciliar([23, 34, 36])
  await dorme(20)
  conferir(filhos.length === 3, 'três contas pareadas → três workers')
  conferir(filhos.map((f) => f.env.WA_QR_CONTA).sort().join(',') === '23,34,36',
    'cada um com WA_QR_CONTA da sua conta')
  conferir(filhos.every((f) => f.env.WA_QR_WORKER === '1'), 'e WA_QR_WORKER=1 (é o que faz o server.js ser o serviço)')
  conferir(new Set(filhos.map((f) => f.env.PORT)).size === 3, 'portas todas diferentes')
  conferir(filhos.every((f) => f.execArgv.includes('--max-old-space-size=512')),
    'e o teto de heap repartido em três (512 cada), não o 1024 herdado')
  conferir(sup4.estado.contasEsperadas.size === 3, 'as três viram "esperadas" pro /saude')

  // reconciliar de novo com as mesmas contas não sobe nada
  await sup4.reconciliar([23, 34, 36]); await dorme(20)
  conferir(filhos.length === 3, 'reconciliar de novo com as mesmas contas: nada muda')

  // uma conta saiu (/sair apagou as creds): o worker dela é parado
  await sup4.reconciliar([23, 36]); await dorme(20)
  const w34 = filhos.find((f) => f.env.WA_QR_CONTA === '34')
  conferir(w34.matou === 'SIGTERM', 'conta que deixou de estar pareada: o worker leva SIGTERM')
  w34.morrer(0, null); await dorme(10)
  conferir(!sup4.estado.workers.has(34) && filhos.length === 3, 'e some do mapa sem ser ressuscitado')
  desligar(sup4)

  // --- 5. a folga do primeiro pareamento ------------------------------------
  //
  // O painel chama /iniciar, o worker sobe, mostra o QR, e a pessoa leva um tempo
  // pra escanear. Nesse meio tempo NÃO existe credencial no banco — parar o worker
  // aqui devolveria "desconectado" pra quem está com o celular na mão.
  console.log('\nA folga do primeiro pareamento:')
  filhos.length = 0
  const sup5 = iniciarSupervisor(base({ graceMs: 60 }))
  sup5.subirWorker(77)                       // subiu sob demanda, ainda sem creds
  await sup5.reconciliar([]); await dorme(10)
  conferir(filhos[0].matou === undefined, 'worker jovem sem credencial NÃO é parado — está pareando')
  await dorme(70)
  await sup5.reconciliar([]); await dorme(10)
  conferir(filhos[0].matou === 'SIGTERM', 'passada a folga e ainda sem credencial: aí sim é parado')
  desligar(sup5)

  // --- 6. subida escalonada -------------------------------------------------
  console.log('\nArranque escalonado:')
  filhos.length = 0
  const sup6 = iniciarSupervisor(base({ espacoMs: 40 }))
  await sup6.reconciliar([1, 2, 3])
  await dorme(10)
  conferir(filhos.length === 1, 'sobe o primeiro na hora...')
  await dorme(50)
  conferir(filhos.length === 2, '...o segundo depois do espaço...')
  await dorme(50)
  conferir(filhos.length === 3, '...e o terceiro depois do outro — nunca os três no mesmo segundo')
  desligar(sup6)

  // --- 7. o supervisor responde /saude SOZINHO e roteia por conta ------------
  console.log('\nRoteamento por conta:')
  const vistos = {}
  async function workerFalso (rotulo) {
    const s = http.createServer((req, res) => {
      let n = 0
      req.on('data', (d) => { n += d.length })
      req.on('end', () => {
        vistos[rotulo] = { url: req.url, bytes: n, segredo: req.headers['x-wa-secret'] }
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ ok: true, quem: rotulo, eco: n }))
      })
    })
    await new Promise((r) => s.listen(0, '127.0.0.1', r))
    return s
  }
  const wA = await workerFalso('A')
  const wB = await workerFalso('B')
  const sup7 = iniciarSupervisor(base())
  // dois "workers" já de pé, cada um numa porta — o mapa aponta pra eles
  sup7.estado.workers.set(34, { contaId: 34, porta: wA.address().port, filho: {}, subiuEm: Date.now() - 60000, quedas: 0 })
  sup7.estado.workers.set(23, { contaId: 23, porta: wB.address().port, filho: {}, subiuEm: Date.now() - 60000, quedas: 0 })
  sup7.estado.contasEsperadas = new Set([34, 23])
  await new Promise((r) => sup7.servidor.listen(0, r))
  const p7 = sup7.servidor.address().port

  const saude = await pedir(p7, '/saude')
  conferir(saude.status === 200 && JSON.parse(saude.corpo).workers === 2,
    '/saude é do supervisor e conta os workers vivos')
  const grande = 'x'.repeat(3 * 1024 * 1024)
  const rA = await pedir(p7, '/session/34/enviar-midia', { metodo: 'POST', corpo: grande, headers: { 'x-wa-secret': SEG } })
  const rB = await pedir(p7, '/session/23/status', { headers: { 'x-wa-secret': SEG } })
  conferir(JSON.parse(rA.corpo).quem === 'A' && JSON.parse(rB.corpo).quem === 'B',
    'conta 34 vai pro worker da 34, conta 23 pro da 23')
  conferir(vistos.A.bytes === grande.length, 'os 3 MB do corpo chegam inteiros no worker certo')
  conferir(vistos.A.segredo === SEG, 'e o x-wa-secret atravessa — o worker é quem autentica')
  conferir(vistos.B.url === '/session/23/status', 'caminho preservado')
  const r404 = await pedir(p7, '/qualquer', { headers: { 'x-wa-secret': SEG } })
  conferir(r404.status === 404, 'rota sem conta: 404 aqui mesmo, sem incomodar worker nenhum')

  // --- 8. conta sem worker: sobe sob demanda, MAS só com o segredo ------------
  //
  // O worker é quem autentica de verdade — só que ele ainda não existe. Abrir
  // processo pra qualquer requisição de fora seria dar ao mundo o botão de fork.
  console.log('\nConta sem worker:')
  filhos.length = 0
  const rSem = await pedir(p7, '/session/99/iniciar', { metodo: 'POST' })
  conferir(rSem.status === 403 && filhos.length === 0,
    'SEM segredo: 403 e NENHUM processo aberto')
  const rErr = await pedir(p7, '/session/99/iniciar', { metodo: 'POST', headers: { 'x-wa-secret': 'errado' } })
  conferir(rErr.status === 403 && filhos.length === 0, 'segredo errado: idem')
  // com o segredo certo o supervisor sobe o worker (de mentira, que nunca abre
  // porta) e, como ele não atende, responde 503 depois de tentar
  const antes = Date.now()
  const rCom = await pedir(p7, '/session/99/status', { headers: { 'x-wa-secret': SEG } })
  conferir(filhos.length === 1 && filhos[0].env.WA_QR_CONTA === '99',
    'com o segredo certo: sobe o worker da conta 99 sob demanda')
  conferir(rCom.status === 503 && JSON.parse(rCom.corpo).ok === false,
    'e como este worker de mentira nunca abre porta, responde 503 — não pendura o painel')
  conferir(Date.now() - antes >= 1000, 'depois de INSISTIR um pouco (o worker recém-nascido ainda está abrindo a porta)')
  sup7.servidor.close(); wA.close(); wB.close(); desligar(sup7)

  // --- 9. worker morre: só ELE volta, e a espera cresce ---------------------
  console.log('\nCiclo de vida por conta:')
  filhos.length = 0
  const usadas = []
  const logFalso = { info () {}, warn () {}, error (o) { if (o && o.esperaMs) usadas.push([o.contaId, o.esperaMs]) } }
  const sup9 = iniciarSupervisor(base({ log: logFalso, firmeMs: 50 }))
  await sup9.reconciliar([34, 23]); await dorme(20)
  const f34 = filhos.find((f) => f.env.WA_QR_CONTA === '34')
  const f23 = filhos.find((f) => f.env.WA_QR_CONTA === '23')
  f34.morrer(1, null)
  conferir(sup9.estado.workers.get(34).filho === null && sup9.estado.workers.get(23).filho === f23,
    'a 34 morreu: a 23 nem percebe')
  await dorme(30)
  conferir(filhos.filter((f) => f.env.WA_QR_CONTA === '34').length === 2, 'e só a 34 sobe de novo')
  conferir(sup9.estado.workers.get(34).quedas === 1 && sup9.estado.workers.get(23).quedas === 0,
    'a queda é contada na conta certa')
  sup9.estado.workers.get(34).filho.morrer(1, null); await dorme(60)
  const f34c = sup9.estado.workers.get(34).filho
  await dorme(60)                              // este fica de pé mais que o firmeMs
  f34c.morrer(1, null)
  conferir(usadas.map((u) => u[1]).join(',') === '10,20,10',
    'a espera dobra a cada queda rápida (10, 20) e volta ao início depois de tempo de pé')
  conferir(usadas.every((u) => u[0] === 34), 'e é por conta — a 23 nunca entrou nessa escada')
  desligar(sup9)

  // --- 10. SIGTERM espera TODOS os workers ---------------------------------
  //
  // Cada worker solta a trava da própria conta no encerrar(). Sair antes de
  // qualquer um deles deixa aquela trava presa 180s.
  console.log('\nSIGTERM (o deploy):')
  filhos.length = 0
  const exitReal = process.exit
  let saiu = false
  process.exit = () => { saiu = true }
  try {
    const sup10 = iniciarSupervisor(base())
    await sup10.reconciliar([34, 23, 36]); await dorme(20)
    sup10.encerrar('SIGTERM')
    conferir(filhos.every((f) => f.matou === 'SIGTERM'), 'repassa o SIGTERM aos TRÊS')
    conferir(saiu === false, 'e não sai antes de nenhum')
    filhos[0].morrer(0, null); filhos[1].morrer(0, null)
    conferir(saiu === false, 'dois fecharam, um falta: ainda não sai')
    filhos[2].morrer(0, null)
    conferir(saiu === true, 'só sai quando o ÚLTIMO fecha')
    const n = filhos.length
    await dorme(40)
    conferir(filhos.length === n, 'e nenhuma dessas mortes fez subir worker novo')
  } finally {
    process.exit = exitReal
  }

  // --- 11. a trava de leitura do fonte --------------------------------------
  console.log('\nO código faz o que este teste diz:')
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
  conferir(/if \(require\.main === module && !process\.env\.WA_QR_WORKER && process\.env\.WA_QR_SUPERVISOR !== '0'\) \{\s*\n\s*require\('\.\/supervisor'\)\.rodar\(\)\s*\n\s*return\s*\n\s*\}/.test(src),
    'server.js delega pro supervisor e PARA ali, chamando rodar() — importar não basta')
  conferir(src.indexOf("require('./supervisor')") < src.indexOf("require('@whiskeysockets/baileys')"),
    'e a delegação vem antes do Baileys, medido por posição no arquivo')
  conferir(/const MINHA_CONTA = parseInt\(process\.env\.WA_QR_CONTA/.test(src),
    'o worker lê WA_QR_CONTA')
  // Esta trava fixava `contas.filter((c) => c === MINHA_CONTA)` — a forma que
  // derrubou os três chips em 11/09 às 17:46 (bigint chega como texto, o === dava
  // falso). Uma trava de leitura garante que o código tem a FORMA esperada, não
  // que a forma está certa; quem prova a regra é teste-worker-conta.js, com ids em
  // texto. Aqui só se exige que o restaurarSessoes passe pela função pura.
  conferir(/contas = contasDesteWorker\(contas, MINHA_CONTA\)/.test(src),
    'e o restaurarSessoes religa SÓ a conta dele — pela função pura, que compara por texto')
  conferir(/if \(MINHA_CONTA && contaId !== MINHA_CONTA\) \{[\s\S]{0,400}?return json\(res, 421/.test(src),
    'e rota de OUTRA conta é recusada — atender seria abrir socket de conta que já tem worker (guerra de sessão)')
  const sqlWorker = src.match(/select conta_id from wa_qr_auth\s+where arquivo = 'creds' and conteudo::json->'me'->>'id' is not null/)
  conferir(!!sqlWorker && /where arquivo = 'creds' and conteudo::json->'me'->>'id' is not null/.test(SQL_CONTAS_PAREADAS),
    'supervisor e worker usam a MESMA definição de "conta pareada" — duas definições seriam duas verdades')

  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})().catch((e) => { console.error('erro no teste:', e); process.exit(1) })
