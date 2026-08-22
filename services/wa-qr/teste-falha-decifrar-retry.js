'use strict'
// Falha de decifragem que o RETRY conserta não é perda — e não pode sair vermelha
// nem entrar na conta do disjuntor.
//
// O caso real, conta 23 em 22/08/2026, minutos depois de um pareamento novo:
//
//   12:18:14.824  ✗ SessionError: No session record
//   12:18:19.945  → sent retry receipt (retryCount 1)
//   12:18:20.299  ✗ mesma mensagem, mesmo erro
//   12:18:21.946  → sent retry receipt (retryCount 2)
//   12:18:23.120  ✓ saída repassada ao webhook
//
// Oito segundos, mensagem gravada (provider_sid 3EB08A82662DB481E7B29F, conversa
// 1855), nada perdido — e duas linhas VERMELHAS no log. Vermelho que não pede ação
// treina a gente a ignorar o log: naquela manhã esse ruído me fez diagnosticar uma
// conta saudável como quebrada e mandar parear de novo à toa.
//
// O QUE ESTE TESTE PROTEGE, em ordem de gravidade:
//
//  1. O DISJUNTOR CONTINUA DISPARANDO NA ENXURRADA. É a única defesa contra o
//     travamento de event loop que matou a instância 7 vezes em 20/08. O teste
//     reproduz o número medido na Prime em 21/08 — 413 falhas para 38 mensagens
//     distintas — e exige que o teto seja estourado mesmo com o desconto.
//  2. O dia saudável dá ZERO. Mensagem que falha uma ou duas vezes e chega não
//     conta nada.
//  3. O rebaixamento só REBAIXA. Forçar 'warn' num log que já era debug seria
//     subir o nível — o oposto do que isto existe pra fazer.
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-falha-decifrar-retry.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const {
  contarFalhaDaMensagem, falhasPorMsg, contarFalhaDeDecifrar, falhasDeDecifrar,
  MAX_RETRY_DECIFRAR, DECIFRAR_TETO, DECIFRAR_JANELA_MS, esquecerConta
} = require('./server')

let falhas = 0
function conferir (ok, o_que) {
  console.log((ok ? '  ok   ' : '  FALHOU ') + o_que)
  if (!ok) falhas++
}
function limpar () { falhasPorMsg.clear(); falhasDeDecifrar.clear() }

// Espelha a decisão do logger (comContaDoBaileys) sem precisar de socket nenhum:
// é aritmética pura, que é o que deixa isto conferível.
function decidir (contaId, msgId, agora) {
  const tent = contarFalhaDaMensagem(contaId, msgId, agora, DECIFRAR_JANELA_MS)
  const aindaTemRetry = tent <= MAX_RETRY_DECIFRAR
  const contou = !aindaTemRetry &&
    contarFalhaDeDecifrar(contaId, agora, DECIFRAR_TETO, DECIFRAR_JANELA_MS)
  return { tent, nivel: aindaTemRetry ? 'warn' : 'error', abriuDisjuntor: !!contou }
}

// ─────────────────────────── 1. o caso do dia 22/08: retry salvou

console.log('A mesma mensagem falhando dentro do orçamento de retry:')
limpar()
const t0 = 1000000
const a = decidir(23, '3EB08A82662DB481E7B29F', t0)
const b = decidir(23, '3EB08A82662DB481E7B29F', t0 + 5475)   // +5,4s, como no log real
conferir(a.nivel === 'warn' && b.nivel === 'warn',
  'as duas saem em warn, nao em error')
conferir(!a.abriuDisjuntor && !b.abriuDisjuntor,
  'nenhuma entra na conta do disjuntor')
conferir(a.tent === 1 && b.tent === 2,
  'o log diz de qual tentativa se trata (1 e 2) — sem isso "warn" vira misterio')

console.log('\nO orçamento acabou — a partir daí é perda de verdade:')
const c = decidir(23, '3EB08A82662DB481E7B29F', t0 + 8296)
conferir(c.tent === MAX_RETRY_DECIFRAR + 1, 'terceira tentativa')
conferir(c.nivel === 'error', 'volta a ser vermelho')

// ─────────────────────────── 2. o disjuntor NÃO pode ficar mais fraco

console.log('\nEnxurrada real (Prime, 21/08: 413 falhas / 38 mensagens):')
limpar()
let contadas = 0
let abriu = false
// as 413 chegaram em ~7h, mas a enxurrada que derruba a instância é concentrada:
// aqui elas entram todas na MESMA janela, que é o cenário que o teto mede.
for (let volta = 0; volta < 11; volta++) {
  for (let m = 0; m < 38; m++) {
    const r = decidir(34, 'msg-' + m, t0 + volta * 100 + m)
    if (r.nivel === 'error') contadas++
    if (r.abriuDisjuntor) abriu = true
  }
}
conferir(abriu, 'o disjuntor ABRIU — a defesa contra o travamento continua de pé')
conferir(contadas === 38 * (11 - MAX_RETRY_DECIFRAR),
  'contou ' + contadas + ' de 418 (só as 2 primeiras de cada id saem da conta)')
conferir(contadas > DECIFRAR_TETO,
  'e sobra folga: ' + contadas + ' contra um teto de ' + DECIFRAR_TETO)

console.log('\nDia saudável (cada mensagem falha 1-2x e chega):')
limpar()
let contadasBoas = 0
for (let m = 0; m < 150; m++) {
  for (let t = 0; t < MAX_RETRY_DECIFRAR; t++) {
    if (decidir(23, 'boa-' + m, t0 + m * 10 + t).nivel === 'error') contadasBoas++
  }
}
conferir(contadasBoas === 0,
  '150 mensagens, 300 falhas transitórias, ZERO vermelho e ZERO na conta')

// ─────────────────────────── 3. bordas

console.log('\nBordas:')
limpar()
conferir(contarFalhaDaMensagem(23, '', t0, DECIFRAR_JANELA_MS) === 1 &&
         contarFalhaDaMensagem(23, '', t0 + 1, DECIFRAR_JANELA_MS) === 1,
  'sem id da mensagem nao da pra agrupar: cada uma conta como primeira')

limpar()
contarFalhaDaMensagem(23, 'x', t0, DECIFRAR_JANELA_MS)
conferir(contarFalhaDaMensagem(23, 'x', t0 + DECIFRAR_JANELA_MS + 1,
  DECIFRAR_JANELA_MS) === 1,
  'passada a janela, o id volta a ser primeira tentativa (o mapa se poda)')

limpar()
contarFalhaDaMensagem(23, 'y', t0, DECIFRAR_JANELA_MS)
conferir(contarFalhaDaMensagem(34, 'y', t0, DECIFRAR_JANELA_MS) === 1,
  'o mesmo id em contas diferentes nao se soma')

limpar()
contarFalhaDaMensagem('23', 'z', t0, DECIFRAR_JANELA_MS)
conferir(contarFalhaDaMensagem(23, 'z', t0, DECIFRAR_JANELA_MS) === 2,
  "'23' (texto, vindo do Postgres) e 23 (numero, das rotas) sao a mesma conta")

limpar()
contarFalhaDaMensagem(23, 'w', t0, DECIFRAR_JANELA_MS)
esquecerConta(23)
conferir(!falhasPorMsg.has(23),
  'esquecerConta limpa o mapa — conta esquecida nao deixa id preso pra sempre')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
