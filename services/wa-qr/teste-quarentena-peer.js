'use strict'
// A QUARENTENA DO CONTATO QUE NÃO DECIFRA — e as duas travas que impedem o
// religamento de jogar a conta de volta na enxurrada.
//
// O incidente que motivou (07/09/2026, conta 34 / PRIME EVENTOS): dois contatos
// com a sessão do Signal quebrada (30331126157545@lid e 20487077900426@lid)
// pararam o serviço INTEIRO três vezes em três minutos. O WhatsApp reentrega o
// que não decifra, para sempre; a limpeza cirúrgica já apagava a sessão do
// contato, mas o serviço continuava ACEITANDO as mensagens dele — e elas
// continuavam sem decifrar. O event loop travou 20,7s, 23,2s e 40,6s; o /saude do
// Render desiste em 5s, e os outros dois chips caíram 4ms depois do bloqueio
// soltar (code 408 — o ping deles venceu durante a trava).
//
// Três defeitos, três travas testadas aqui:
//   1. o contato que esgotou o retry continuava sendo ouvido  → quarentena
//   2. o disjuntor não fazia a espera crescer (ficava em 5min) → tentativasPos440
//   3. o /enviar religava por cima da espera                   → paradoPeloDisjuntor
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-quarentena-peer.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const {
  deveIgnorarNoBaileys, ehConversaValida, porPeerEmQuarentena, peerEmQuarentena,
  esquecerQuarentena, peersEmQuarentena, QUARENTENA_PEER_MS,
  esperaPos440, sessaoOrfa, ESPERA_POS_440_MS, DISJUNTOR_AVISA_EM
} = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

const CONTA = 34
const QUEBRADO = '30331126157545@lid'
const OUTRO = '558694095301@s.whatsapp.net'
const T0 = 1757000000000

function limpar () { peersEmQuarentena.clear() }

// --- 1. a quarentena cala SÓ o contato quebrado -----------------------------
console.log('\nA quarentena cala o contato que não decifra:')
limpar()
conferir(deveIgnorarNoBaileys(QUEBRADO, CONTA, T0) === false,
  'antes: o contato passa normalmente')
conferir(porPeerEmQuarentena(CONTA, QUEBRADO, T0, QUARENTENA_PEER_MS) === true,
  'entra em quarentena quando o retry se esgota')
conferir(deveIgnorarNoBaileys(QUEBRADO, CONTA, T0 + 1000) === true,
  'depois: descartado ANTES de decifrar — é isto que tira a enxurrada')
conferir(deveIgnorarNoBaileys(OUTRO, CONTA, T0 + 1000) === false,
  'o resto da carteira continua sendo ouvido — a quarentena é de UM contato')
conferir(deveIgnorarNoBaileys(QUEBRADO, 99, T0 + 1000) === false,
  'e é por CONTA: o mesmo número noutra empresa não é castigado junto')

// --- 2. tem prazo, e ele vence ----------------------------------------------
console.log('\nTem prazo (sessão quebrada às vezes se resolve sozinha):')
limpar()
porPeerEmQuarentena(CONTA, QUEBRADO, T0, QUARENTENA_PEER_MS)
conferir(peerEmQuarentena(CONTA, QUEBRADO, T0 + QUARENTENA_PEER_MS - 1) === true,
  'um instante antes de vencer, ainda calado')
conferir(peerEmQuarentena(CONTA, QUEBRADO, T0 + QUARENTENA_PEER_MS) === false,
  'vencido o prazo, volta a ser ouvido')
conferir(peersEmQuarentena.size === 0,
  'e a entrada some do mapa — não vaza memória a cada contato quebrado')
conferir(QUARENTENA_PEER_MS >= 5 * 60000,
  'o prazo não é curto a ponto de a enxurrada voltar antes de esvaziar a fila')

// --- 3. o que a quarentena NÃO pode calar -----------------------------------
console.log('\nO que ela não alcança:')
limpar()
conferir(porPeerEmQuarentena(CONTA, '558698392961-1607041815@g.us', T0) === false,
  'GRUPO não entra: quem não decifra lá é o participant, não o grupo')
conferir(porPeerEmQuarentena(CONTA, 'status@broadcast', T0) === false,
  'status não entra: já é cortado por outro caminho')
conferir(porPeerEmQuarentena(CONTA, null, T0) === false, 'jid nulo não derruba nada')
conferir(deveIgnorarNoBaileys(OUTRO) === false,
  'sem conta e sem instante, a função responde como sempre respondeu')

// --- 4. o invariante do teste vizinho continua de pé ------------------------
console.log('\nInvariante — a quarentena não corta nada que o app usaria por engano:')
limpar()
porPeerEmQuarentena(CONTA, QUEBRADO, T0, QUARENTENA_PEER_MS)
conferir(ehConversaValida(QUEBRADO) === true,
  'o contato calado É conversa válida — o corte é escolha, não engano')
conferir(deveIgnorarNoBaileys(QUEBRADO, CONTA, T0 + QUARENTENA_PEER_MS + 1) === false,
  'e ele volta sozinho: nenhuma conversa fica calada pra sempre')

// --- 5. esquecer a conta leva a quarentena junto ----------------------------
console.log('\nConta esquecida não deixa contato calado pra trás:')
limpar()
porPeerEmQuarentena(CONTA, QUEBRADO, T0, QUARENTENA_PEER_MS)
porPeerEmQuarentena(99, QUEBRADO, T0, QUARENTENA_PEER_MS)
esquecerQuarentena(CONTA)
conferir(peerEmQuarentena(CONTA, QUEBRADO, T0 + 1) === false, 'a conta esquecida sai')
conferir(peerEmQuarentena(99, QUEBRADO, T0 + 1) === true, 'a vizinha não é afetada')

// --- 6. a espera do disjuntor CRESCE ----------------------------------------
// Era este o defeito: `abrirDisjuntor` gravava substituidaEm e não mexia em
// tentativasPos440, então esperaPos440 devolvia base × 2⁰ — 5 minutos fixos, por
// mais vezes que abrisse. Em 07/09 abriu três vezes em três minutos.
console.log('\nA espera dobra a cada abertura do disjuntor:')
const base = ESPERA_POS_440_MS
conferir(esperaPos440({ tentativasPos440: 0 }, base) === base, '1ª: a base')
conferir(esperaPos440({ tentativasPos440: 1 }, base) === base * 2, '2ª: o dobro')
conferir(esperaPos440({ tentativasPos440: 2 }, base) === base * 4, '3ª: o quádruplo')
conferir(esperaPos440({ tentativasPos440: 9 }, base) === base * 16,
  'com teto: a espera cresce, mas não vira "nunca mais"')

// --- 7. o envio não religa em cima da espera --------------------------------
// A conta parada pelo disjuntor só volta pelo vigia, na hora certa. Sem isto, um
// envio religava e a conta caía de novo em 83 segundos — foi a 3ª abertura de 07/09.
console.log('\nO /enviar respeita a espera (a regra que ele consulta):')
const paradaAgora = { paradoPeloDisjuntor: true, substituidaEm: T0, tentativasPos440: 1 }
conferir(sessaoOrfa(paradaAgora, T0 + 1000, base) === false,
  'logo depois de parar, NÃO está órfã — o envio devolve desconectado')
conferir(sessaoOrfa(paradaAgora, T0 + base * 2 + 1, base) === true,
  'vencida a espera dobrada, o vigia pode retomar')
conferir(sessaoOrfa({ substituidaEm: T0, sock: {} }, T0 + base * 99, base) === false,
  'conta com socket de pé nunca é órfã — o envio segue normal')

// --- 8. o aviso existe e tem gatilho ----------------------------------------
console.log('\nO aviso do chip quebrado:')
conferir(DISJUNTOR_AVISA_EM >= 2,
  'não avisa na primeira abertura: uma é briga de sessão passageira')
conferir(typeof require('./server').avisarChipQuebrado === 'function',
  'e existe o caminho que tira o diagnóstico do log e manda pra gente')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
