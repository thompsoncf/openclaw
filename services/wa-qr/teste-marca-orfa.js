'use strict'
// A MARCA DE ÓRFÃ É O QUE FAZ A CONTA VOLTAR. Este teste fixa que um evento
// retardatário não pode apagá-la.
//
// O caso real, 11/09/2026, conta 34 (chip principal da Prime). Tudo no mesmo segundo,
// nesta ordem, no wa_qr_log:
//
//   15:19:02  disjuntor abre → socket descartado (disjuntor_guerra_de_sessao)
//   15:19:02  messages.upsert recebido (n: 15)    ← o lote que JÁ estava em curso
//   15:19:02  saída ignorada × 15
//
// `abrirDisjuntor` põe `substituidaEm` justamente pra que o vigia retome a conta
// depois da espera (40 min, naquela abertura). O lote retardatário chamou `marcarVivo`
// e apagou a marca. E `sessaoOrfa` EXIGE a marca: sem ela devolve false pra sempre.
//
// Resultado medido: às 16:13 a conta 34 estava `desconectado`, `temSock: false`,
// `iniciando: false`, `substituida_em: null`, `tentativas_440: 3`, sem UMA linha de log
// desde as 15:19 — 54 minutos, enquanto as contas 23 e 36 seguiam entregando no mesmo
// processo. Não era a espera de 40 minutos: o vigia tinha desistido da conta pra
// sempre, e só um deploy a traria de volta.
//
// O mesmo vale pro caminho do 440, que marca órfã do mesmo jeito.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-marca-orfa.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { marcarVivo, sessaoOrfa, abrirDisjuntor, sessoes, esperaPos440,
  ESPERA_POS_440_MS, partidas, terminouAPartida, comecouAPartida } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}
function sockFalso () { return { end () {}, ev: { on () {} } } }
function limpar () { sessoes.clear(); partidas.clear() }

const T0 = Date.parse('2026-09-11T15:19:02-03:00')

// --- 1. o caso da Prime, na ordem em que aconteceu ---------------------------
console.log('\n11/09, conta 34: o lote retardatário chega depois do disjuntor:')
limpar()
sessoes.set(34, { sock: sockFalso(), status: 'conectado', abertoEm: T0 - 600000 })

;(async () => {
  // o disjuntor para a conta: descarta o socket e marca a órfã
  await abrirDisjuntor(34).catch(() => {})   // o registrarSessoes no fim quer banco
  const s = sessoes.get(34)
  conferir(!s.sock, 'o socket foi descartado')
  conferir(!!s.substituidaEm, 'a marca de órfã foi posta — é o que devolve a conta ao vigia')
  const marcaAntes = s.substituidaEm

  // ...e SÓ ENTÃO o lote de 15 mensagens termina de ser processado
  marcarVivo(34, true)
  conferir(s.substituidaEm === marcaAntes,
    'o messages.upsert retardatário NÃO apaga a marca (era o defeito de 11/09)')

  // e o vigia volta a enxergar a conta quando a espera vence
  const espera = esperaPos440(s, ESPERA_POS_440_MS)
  conferir(sessaoOrfa(s, marcaAntes + espera - 1000, ESPERA_POS_440_MS) === false,
    'antes da espera vencer o vigia ainda não mexe')
  conferir(sessaoOrfa(s, marcaAntes + espera + 1000, ESPERA_POS_440_MS) === true,
    'vencida a espera o vigia RETOMA a conta — era isto que não acontecia mais')

  // --- 2. sem socket nada do marcarVivo vale ---------------------------------
  console.log('\nO retardatário não falsifica nenhum outro sinal:')
  limpar()
  sessoes.set(34, { sock: null, substituidaEm: T0, reconexoesMudas: 3, ultimoEvento: T0 })
  comecouAPartida(34, T0)
  marcarVivo(34, true)
  const s2 = sessoes.get(34)
  conferir(s2.reconexoesMudas === 3,
    'não zera reconexoesMudas — senão afrouxaria o teto do vigia na encarnação seguinte')
  conferir(s2.ultimoEvento === T0, 'não renova o carimbo de vida de um socket morto')
  conferir(partidas.has(34) === true,
    'e não encerra a partida: quem não tem socket não terminou de subir')

  // --- 3. com socket, tudo segue como sempre --------------------------------
  console.log('\nCom socket de pé, o marcarVivo faz o de sempre:')
  limpar()
  sessoes.set(34, { sock: sockFalso(), substituidaEm: T0, reconexoesMudas: 3, abertoEm: T0 })
  comecouAPartida(34, T0)
  marcarVivo(34, true)
  const s3 = sessoes.get(34)
  conferir(s3.substituidaEm === null, 'quem entrega COM socket deixa de ser órfã')
  conferir(s3.reconexoesMudas === 0, 'e zera a desconfiança do vigia')
  conferir(partidas.has(34) === false, 'e encerra a partida')

  // --- 4. conta que nem existe ----------------------------------------------
  console.log('\nConta fora do mapa:')
  limpar()
  let explodiu = false
  try { marcarVivo(99, true) } catch (e) { explodiu = true }
  conferir(explodiu === false, 'não explode')

  // --- 5. o /enviar ficava trancado pelo mesmo campo -------------------------
  //
  // A marca não é só do vigia. O `/enviar` recusa religar enquanto a conta está
  // `paradoPeloDisjuntor` E ainda não é órfã — a condição exata no código é
  // `s.paradoPeloDisjuntor && !sessaoOrfa(...)`. Com a marca apagada, `sessaoOrfa`
  // devolve false pra sempre, então a recusa também é pra sempre: a conta ficava
  // trancada pelos DOIS lados, o vigia que não retoma e o painel que não religa.
  console.log('\nA marca destranca os dois lados, não só o vigia:')
  const semMarca = { sock: null, substituidaEm: null, tentativasPos440: 3,
    paradoPeloDisjuntor: true }
  conferir(sessaoOrfa(semMarca, T0 + 86400000, ESPERA_POS_440_MS) === false,
    'sem marca a conta nunca fica órfã, nem um dia depois')
  conferir((semMarca.paradoPeloDisjuntor &&
    !sessaoOrfa(semMarca, T0 + 86400000, ESPERA_POS_440_MS)) === true,
  '...e por isso o /enviar recusaria religar pra sempre — o painel não a traria de volta')
  const comMarca = { sock: null, substituidaEm: T0, tentativasPos440: 3,
    paradoPeloDisjuntor: true }
  const esperaMax = esperaPos440(comMarca, ESPERA_POS_440_MS)
  conferir((comMarca.paradoPeloDisjuntor &&
    !sessaoOrfa(comMarca, T0 + esperaMax + 1000, ESPERA_POS_440_MS)) === false,
  'com a marca de pé, vencida a espera o /enviar volta a religar')

  // --- 6. a trava de leitura do fonte ---------------------------------------
  console.log('\nA guarda está no código:')
  const fs = require('fs')
  const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
  conferir(/function marcarVivo \(contaId, entregouMensagem\) \{[\s\S]{0,3000}?if \(!s \|\| !s\.sock\) return/.test(src),
    'marcarVivo desiste sem socket, ANTES de tocar em qualquer campo')
  conferir(/function sessaoOrfa \([\s\S]{0,200}?!s\.substituidaEm\) return false/.test(src),
    'e sessaoOrfa continua exigindo a marca — é por isso que ela não pode se perder')

  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})()
