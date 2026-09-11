'use strict'
// O WORKER TEM QUE RECONHECER A PRÓPRIA CONTA NO QUE O BANCO DEVOLVE. Este teste
// existe porque a regra derrubou os três chips no primeiro deploy da fase 3.
//
// 11/09/2026, 17:46. O supervisor subiu um worker por conta (23, 34, 36), cada um
// com WA_QR_CONTA. Cada worker leu as contas pareadas do banco, filtrou pela sua e
// achou NENHUMA:
//
//   17:46:16  worker 23   restaurarSessoes: nenhuma conta pareada pra religar
//   17:46:21  worker 34   restaurarSessoes: nenhuma conta pareada pra religar
//   17:46:26  worker 36   restaurarSessoes: nenhuma conta pareada pra religar
//   17:47:09  a instância velha levou SIGTERM e soltou as três travas
//
// Daí em diante, nada: zero linhas das contas no log, zero entradas. O painel
// mostrava `conectado` — retrato velho, escrito pela instância antiga às 17:46:55.
//
// A causa: `wa_qr_auth.conta_id` é bigint, e o driver do Postgres entrega bigint
// como TEXTO ('34'). WA_QR_CONTA chega como NÚMERO (34). `'34' === 34` é falso.
//
// O teste de fumaça não pegou porque o banco descartável não tinha credencial
// pareada — a comparação nunca rodou contra um bigint de verdade. Este teste
// alimenta a regra com ids em TEXTO, exatamente como o driver entrega.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-worker-conta.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { contasDesteWorker } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

// --- 1. o caso real: bigint vem como texto, WA_QR_CONTA como número ----------
console.log('\n11/09 17:46, o que o driver entrega contra o que o env traz:')
const doBanco = ['23', '34', '36']          // bigint -> texto, como o pg devolve
const doEnv = parseInt('34', 10)            // WA_QR_CONTA -> número, como o server faz
const minhas = contasDesteWorker(doBanco, doEnv)
conferir(minhas.length === 1, 'o worker da 34 acha UMA conta — a sua (era zero, e os três chips caíram)')
conferir(String(minhas[0]) === '34', 'e é a 34 mesmo')

// --- 2. os três workers, um por um -------------------------------------------
console.log('\nCada worker fica só com a sua:')
for (const c of [23, 34, 36]) {
  const r = contasDesteWorker(doBanco, c)
  conferir(r.length === 1 && String(r[0]) === String(c), 'worker ' + c + ' -> [' + c + ']')
}

// --- 3. os dois sentidos da mistura de tipos ---------------------------------
console.log('\nIndependente de quem veio como texto e quem veio como número:')
conferir(contasDesteWorker([23, 34, 36], '34').length === 1, 'ids número, conta texto')
conferir(contasDesteWorker([23, 34, 36], 34).length === 1, 'ids número, conta número')
conferir(contasDesteWorker(['23', '34', '36'], '34').length === 1, 'ids texto, conta texto')

// --- 4. sem WA_QR_CONTA, é o serviço de sempre -------------------------------
console.log('\nSem WA_QR_CONTA (um processo, todas as contas):')
conferir(contasDesteWorker(doBanco, null).length === 3, 'null -> todas')
conferir(contasDesteWorker(doBanco, 0).length === 3, '0 -> todas (parseInt de env vazio)')

// --- 5. conta que não está pareada -------------------------------------------
console.log('\nWorker de conta que não tem credencial no banco:')
conferir(contasDesteWorker(doBanco, 99).length === 0,
  'fica vazio — é o caso legítimo de "nenhuma conta pareada pra religar" (pareamento novo, ainda sem QR)')

// --- 6. a trava de leitura do fonte ------------------------------------------
console.log('\nO restaurarSessoes usa a regra, e a regra compara por texto:')
const fs = require('fs')
const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
conferir(/contas = contasDesteWorker\(contas, MINHA_CONTA\)/.test(src),
  'restaurarSessoes passa pela função — não por um filter inline (que foi onde o === entrou)')
conferir(/String\(c\) === String\(minhaConta\)/.test(src),
  'e a comparação é String === String, que é o idioma do arquivo (ver castigo.get(String(contaId)))')
conferir(!/contas\.filter\(\(c\) => c === MINHA_CONTA\)/.test(src),
  'a forma que derrubou os chips não existe mais')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
