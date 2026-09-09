'use strict'
// A mensagem presa (o laço de 50 em 50 min) e o carimbo da PARTIDA.
//
// O caso real, noite de 08→09/09/2026, empresa fechada, ninguém escrevendo:
//
//   conta 34  22:22 23:30 00:20 01:10 02:50 03:40 04:30 05:20 06:10 07:00 07:50
//             3A4B3E86598B9C5136C1  ← o MESMO id de ack, onze quedas seguidas
//   conta 23  dois ids se alternando: 2A6B205ED068FA8B66B0 e 4AB466C8764E2FB6F11D
//   conta 36  nenhum  (sem re-pareamento há 20 dias)
//
// E o detalhe que corrigiu o diagnóstico: a conta 34 teve ZERO falhas de decifragem
// naquela noite. Não é a mensagem que não abre — é o `ack` dela que o servidor
// recusa. O conserto mora no Baileys 7; o que este código faz é reconhecer o padrão
// sozinho, em vez de alguém descobrir na mão lendo log.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-mensagem-presa.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { contarQuedaPresa, esquecerQuedasPresas, quedasPresas, PRESA_AVISA_EM,
  comecouAPartida, terminouAPartida, quemEstaSubindo, partidas,
  medindo, oQueEstaEmCurso } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

const PRESA = '3A4B3E86598B9C5136C1'      // o id real da conta 34 naquela noite
const OUTRA = '2A6B205ED068FA8B66B0'
const T0 = Date.parse('2026-09-08T22:22:48-03:00')
const min = (n) => T0 + n * 60000

// --- 1. o laço da conta 34 --------------------------------------------------
console.log('\nO laço, reproduzindo a noite da conta 34:')
quedasPresas.clear()
conferir(contarQuedaPresa(34, PRESA, T0, 3) === null, '1ª queda: uma queda é uma queda')
conferir(contarQuedaPresa(34, PRESA, min(68), 3) === null, '2ª com o mesmo id: ainda pode ser coincidência')
const laco = contarQuedaPresa(34, PRESA, min(118), 3)
conferir(laco !== null, '3ª com o mesmo id: agora é laço, e o serviço diz')
conferir(laco.id === PRESA, 'e diz QUAL mensagem — o id vai pro log e pro aviso')
conferir(laco.vezes === 3, 'com quantas quedas ela já causou')
conferir(Math.round((min(118) - laco.desde) / 60000) === 118, 'e desde quando (pra saber se é de hoje ou de ontem)')

// --- 2. avisa UMA vez -------------------------------------------------------
console.log('\nAviso repetido é aviso ignorado:')
conferir(contarQuedaPresa(34, PRESA, min(168), 3) === null, '4ª queda pela mesma mensagem não avisa de novo')
conferir(contarQuedaPresa(34, PRESA, min(218), 3) === null, 'nem a 5ª — as onze da noite dariam UM aviso')

// --- 3. id diferente é outra história ---------------------------------------
console.log('\nDuas quedas por mensagens diferentes são duas quedas, não um laço:')
quedasPresas.clear()
conferir(contarQuedaPresa(23, PRESA, T0, 3) === null, 'queda com um id')
conferir(contarQuedaPresa(23, OUTRA, min(50), 3) === null, 'queda com OUTRO id: a contagem zera')
conferir(contarQuedaPresa(23, OUTRA, min(100), 3) === null, 'segue contando o novo, do começo')
conferir(contarQuedaPresa(23, OUTRA, min(150), 3) !== null, 'e só denuncia quando o NOVO se repete o bastante')

// --- 4. é por conta ---------------------------------------------------------
console.log('\nPor conta, nunca global:')
quedasPresas.clear()
contarQuedaPresa(34, PRESA, T0, 3); contarQuedaPresa(34, PRESA, min(50), 3)
conferir(contarQuedaPresa(36, PRESA, min(60), 3) === null,
  'o mesmo id noutra conta começa do zero (a 36 não estava no laço)')
conferir(contarQuedaPresa(34, PRESA, min(100), 3) !== null, 'e a conta 34 chega ao teto sozinha')

// --- 5. robustez ------------------------------------------------------------
console.log('\nEntrada torta não derruba nem mente:')
quedasPresas.clear()
for (const v of [null, undefined, '']) {
  conferir(contarQuedaPresa(34, v, T0, 3) === null, JSON.stringify(v) + ' — sem id não há laço a apontar')
}
conferir(quedasPresas.size === 0, 'e nada é guardado por queda sem id')

// --- 6. pareou de novo, história nova ---------------------------------------
console.log('\nQuem pareia de novo começa do zero:')
quedasPresas.clear()
contarQuedaPresa(34, PRESA, T0, 3); contarQuedaPresa(34, PRESA, min(50), 3)
esquecerQuedasPresas(34)
conferir(quedasPresas.size === 0, 'esquecerQuedasPresas limpa a conta')
conferir(contarQuedaPresa(34, PRESA, min(100), 3) === null,
  'o cofre é outro: a mensagem presa do cofre velho não é mais assunto')

conferir(PRESA_AVISA_EM >= 2, 'o teto exige repetição — 1 queda nunca vira aviso')

// --- 7. O CARIMBO DA PARTIDA -------------------------------------------------
// Os dois piores travamentos medidos (12.490ms e 5.227ms) saíram com emCurso: []
// e decifragem: {} — não era operação nossa. O que rodava era o arranque do
// Baileys, que não passa por função nossa nenhuma.
console.log('\nO carimbo da partida (o que emCurso: [] não explicava):')
partidas.clear()
conferir(quemEstaSubindo(T0).length === 0, 'parado, ninguém está subindo')
comecouAPartida(23, T0)
const subindo = quemEstaSubindo(T0 + 4000)
conferir(subindo.length === 1 && subindo[0].conta === 23,
  'durante o arranque o carimbo diz QUAL conta está subindo')
conferir(subindo[0].haMs === 4000, 'e há quanto tempo — 12s de partida explica um travamento de 12s')
comecouAPartida(34, T0 + 5000)
const dois = quemEstaSubindo(T0 + 9000)
conferir(dois.length === 2 && dois[0].conta === 23,
  'duas subindo juntas aparecem as duas, da mais antiga pra mais nova')
terminouAPartida(23)
conferir(quemEstaSubindo(T0 + 9000).length === 1,
  'entregou conversa (marcarVivo) → sai do carimbo; socket aberto sozinho não basta')
partidas.clear()

// o upsert agora é carimbado: era por onde o backlog do arranque entrava sem rastro
;(async () => {
  console.log('\nO backlog do arranque agora tem rótulo:')
  let dentro = null
  await medindo('messages.upsert', 34, async () => { dentro = oQueEstaEmCurso(Date.now()) })
  conferir(dentro.length === 1 && dentro[0].op === 'messages.upsert',
    'messages.upsert passa por medindo() — era a onda de backlog sem rótulo')
  conferir(oQueEstaEmCurso(Date.now()).length === 0, 'e sai ao terminar')

  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})()
