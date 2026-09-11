'use strict'
// O disjuntor para a CONTA INTEIRA. Este teste fixa quando ele deve e quando não deve.
//
// OS DOIS CASOS REAIS, que pedem respostas opostas:
//
// 11/09/2026, conta 34 (chip principal da Prime). Parou duas vezes — 12:07 e 12:19 —
// por 60 falhas de decifragem em 60s. Abrindo o número: 15 ids DISTINTOS, UM contato
// (126770388963473@lid), todas `fromMe: true`, ou seja o eco das mensagens que o
// próprio dono mandou pelo celular. O retry repete cada mensagem até 3 vezes: 15 × 4
// = 60, o teto exato. Nada de cliente se perdia, e mesmo assim o chip saiu do ar em
// horário comercial, com a espera dobrando (10, 20, 40 min) porque as mesmas 15
// voltavam a cada reconexão. Para UM contato já existe a quarentena, que cala só ele.
//
// 20/08/2026. Outro aparelho assumiu a credencial e TODO eco passou a chegar
// indecifrável: 1119 falhas numa hora, de todos os interlocutores ao mesmo tempo. Aí
// nenhuma quarentena por contato dá conta — é a conta inteira que está comprometida,
// e parar é o certo. Foi este caso que criou o disjuntor.
//
// A regra que separa os dois: QUANTOS CONTATOS distintos estão falhando.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-disjuntor-contatos.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { contarContatoComFalha, contatosComFalha, DISJUNTOR_MIN_CONTATOS,
  contarFalhaDeDecifrar, falhasDeDecifrar,
  DECIFRAR_TETO, DECIFRAR_JANELA_MS } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}
function limpar () { contatosComFalha.clear(); falhasDeDecifrar.clear() }

const T0 = Date.parse('2026-09-11T12:07:01-03:00')
const ECO = '126770388963473@lid'      // o contato real do caso da Prime

// --- 1. o caso da Prime: um contato só --------------------------------------
console.log('\n11/09, conta 34: 60 falhas, UM contato (o chip não pode parar):')
limpar()
let estourouTeto = false
for (let i = 0; i < DECIFRAR_TETO; i++) {
  const n = contarContatoComFalha(34, ECO, T0 + i, DECIFRAR_JANELA_MS)
  conferir.ultimo = n
  if (contarFalhaDeDecifrar(34, T0 + i, DECIFRAR_TETO, DECIFRAR_JANELA_MS)) estourouTeto = true
}
conferir(estourouTeto === true,
  'o teto de falhas ESTOURA — a enxurrada é real, isso não mudou')
conferir(conferir.ultimo === 1,
  'mas os 60 vêm de UM contato só — é o número que decide')
conferir(conferir.ultimo < DISJUNTOR_MIN_CONTATOS,
  'abaixo do mínimo de contatos: o disjuntor NÃO abre, a conta segue atendendo')

// --- 2. o caso de 20/08: a credencial inteira --------------------------------
console.log('\n20/08, credencial assumida por outro aparelho (a conta TEM que parar):')
limpar()
let contatos = 0
for (let i = 0; i < DECIFRAR_TETO; i++) {
  // cada falha vem de um interlocutor diferente — é o retrato daquele incidente
  contatos = contarContatoComFalha(34, '5586990000' + (i % 30) + '@s.whatsapp.net',
    T0 + i, DECIFRAR_JANELA_MS)
  contarFalhaDeDecifrar(34, T0 + i, DECIFRAR_TETO, DECIFRAR_JANELA_MS)
}
conferir(contatos >= DISJUNTOR_MIN_CONTATOS,
  'muitos contatos falhando: o disjuntor abre, como sempre abriu')
conferir(contatos === 30, 'e o log diz quantos eram (30 interlocutores distintos)')

// --- 3. dois contatos já bastam ---------------------------------------------
console.log('\nO limiar é "mais de um", não "muitos":')
limpar()
conferir(contarContatoComFalha(34, ECO, T0, DECIFRAR_JANELA_MS) === 1, 'um contato')
conferir(contarContatoComFalha(34, '558699999999@s.whatsapp.net', T0 + 1, DECIFRAR_JANELA_MS) === 2,
  'chegou o segundo: já não é mais "um chat problemático"')
conferir(DISJUNTOR_MIN_CONTATOS === 2,
  'e o mínimo é 2 — a quarentena cobre um; a partir de dois o problema é da conta')

// --- 4. a janela esquece ------------------------------------------------------
console.log('\nA contagem é de uma JANELA, não do dia inteiro:')
limpar()
contarContatoComFalha(34, ECO, T0, DECIFRAR_JANELA_MS)
conferir(contarContatoComFalha(34, '558699999999@s.whatsapp.net',
  T0 + DECIFRAR_JANELA_MS + 1, DECIFRAR_JANELA_MS) === 1,
'contato de uma janela vencida sai da conta — senão o total só cresceria e toda ' +
  'conta acabaria "com muitos contatos" depois de um dia')

// --- 5. o mesmo aparelho em vários dispositivos conta UMA vez -----------------
console.log('\nO mesmo contato em aparelhos diferentes é UM contato:')
limpar()
contarContatoComFalha(34, '126770388963473:12@lid', T0, DECIFRAR_JANELA_MS)
const mesmo = contarContatoComFalha(34, '126770388963473:47@lid', T0 + 1, DECIFRAR_JANELA_MS)
conferir(mesmo === 1,
  'o sufixo do dispositivo é cortado (usuarioDoJid) — senão um contato com 3 ' +
  'aparelhos viraria "3 contatos" e derrubaria a conta sozinho')

// --- 6. é por conta ----------------------------------------------------------
console.log('\nPor conta, nunca global:')
limpar()
contarContatoComFalha(34, ECO, T0, DECIFRAR_JANELA_MS)
contarContatoComFalha(34, '558699999999@s.whatsapp.net', T0, DECIFRAR_JANELA_MS)
conferir(contarContatoComFalha(36, ECO, T0, DECIFRAR_JANELA_MS) === 1,
  'a conta 36 começa do zero — a enxurrada da vizinha não a derruba')

// --- 7. sem jid identificável ------------------------------------------------
console.log('\nFalha sem contato identificável:')
limpar()
conferir(contarContatoComFalha(34, null, T0, DECIFRAR_JANELA_MS) === 1,
  'conta como um contato próprio')
conferir(contarContatoComFalha(34, ECO, T0 + 1, DECIFRAR_JANELA_MS) === 2,
  'e não se funde com um contato real — na dúvida o disjuntor tem que poder agir')

// --- 8. a trava de leitura do fonte ------------------------------------------
console.log('\nA condição no código é E, não OU:')
const fs = require('fs')
const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
conferir(/if \(contarFalhaDeDecifrar\([\s\S]{0,120}\) \{[\s\S]{0,200}if \(nContatos >= DISJUNTOR_MIN_CONTATOS\)/.test(src),
  'o teto de falhas e o mínimo de contatos são checados juntos')
conferir(/const nContatos = contarContatoComFalha\(contaId, peer, agora/.test(src),
  'e a contagem de contatos roda SEMPRE, não só quando o teto estoura ' +
  '(senão a janela dela chegaria vazia na hora de decidir)')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
