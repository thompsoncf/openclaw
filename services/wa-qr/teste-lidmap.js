'use strict'
// Teste dos caches em memória do server.js contra um Postgres descartável.
//
// O alvo principal é a gravação EM LOTE do mapa @lid->telefone (gravarLidsPendentes).
// Ela troca "uma query por par" por um insert multi-linha montado à mão, com os
// $1,$2,$3... numerados no laço — é exatamente o tipo de código onde um erro de índice
// passa despercebido, e a falha seria silenciosa do jeito pior: o mapa continua certo em
// memória e só some no deploy seguinte, trazendo de volta o bug do eco de mensagem em
// chat @lid que não acha o número.
//
// Confere também o teto por bytes do cache de enviadas (mensagem grande vai só pro
// banco) e a limpeza por conta (esquecerConta não pode levar junto a conta vizinha).
//
// Não entra no pytest: precisa de Node + as dependências do serviço. Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_qr_test
//     WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-lidmap.js

const URL = process.env.WA_QR_TEST_URL
if (!URL) {
  console.error('Falta WA_QR_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}
// o server.js exige estas envs pra carregar; PORT não importa (não sobe servidor no require)
process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const s = require('./server')

let falhas = 0
function conferir (ok, descricao, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}

const CONTA = 23
const VIZINHA = 2   // prefixo "2:" não pode casar com as chaves da conta 23

async function limpar () {
  await s.pool.query('delete from wa_qr_auth where conta_id = any($1)', [[CONTA, VIZINHA]])
  await s.pool.query('delete from wa_qr_enviadas where conta_id = any($1)', [[CONTA, VIZINHA]])
  s.lidMaps.clear(); s.lidsPendentes.clear(); s.enviadas.clear(); s.jidsResolvidos.clear()
}

async function testeLoteGrande () {
  console.log('\nlote grande (atravessa o corte de 500 por statement)')
  await limpar()
  const N = 1200
  for (let i = 0; i < N; i++) {
    s.aprenderLid(CONTA, '11111111111' + i + '@lid', '5511' + String(i).padStart(9, '0') + '@s.whatsapp.net')
  }
  conferir(s.lidsPendentes.size === N, 'os ' + N + ' pares ficaram pendentes', 'pendentes=' + s.lidsPendentes.size)
  await s.gravarLidsPendentes()
  conferir(s.lidsPendentes.size === 0, 'buffer esvaziou depois do flush')
  const r = await s.pool.query(
    `select count(*)::int n from wa_qr_auth where conta_id=$1 and arquivo like 'lidmap-%'`, [CONTA])
  conferir(r.rows[0].n === N, 'as ' + N + ' linhas chegaram no banco', 'no banco=' + r.rows[0].n)

  // o conteúdo tem que bater par a par — um erro de índice nos $n embaralharia
  // arquivo e conteudo sem quebrar o insert
  const amostra = await s.pool.query(
    `select arquivo, conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2`,
    [CONTA, 'lidmap-111111111117@lid'])
  conferir(amostra.rowCount === 1 &&
    JSON.parse(amostra.rows[0].conteudo) === '5511000000007@s.whatsapp.net',
  'arquivo e conteudo não se embaralharam', amostra.rows[0] && amostra.rows[0].conteudo)
}

async function testeReescrita () {
  console.log('\nreescrita do mesmo par (o on conflict precisa atualizar, não estourar)')
  await limpar()
  s.aprenderLid(CONTA, '999@lid', '5511111111111@s.whatsapp.net')
  await s.gravarLidsPendentes()
  // número mudou pro MESMO lid: tem que sobrescrever
  s.lidMaps.clear()
  s.aprenderLid(CONTA, '999@lid', '5511222222222@s.whatsapp.net')
  await s.gravarLidsPendentes()
  const r = await s.pool.query(
    'select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2', [CONTA, 'lidmap-999@lid'])
  conferir(r.rowCount === 1 && JSON.parse(r.rows[0].conteudo) === '5511222222222@s.whatsapp.net',
    'o par novo sobrescreveu o antigo', r.rows[0] && r.rows[0].conteudo)
}

async function testeDedupNoMesmoLote () {
  console.log('\nmesmo par duas vezes ANTES do flush (o Postgres recusa lote com repetição)')
  await limpar()
  s.aprenderLid(CONTA, '888@lid', '5511333333333@s.whatsapp.net')
  s.lidMaps.clear()   // força passar de novo pelo caminho de gravação
  s.aprenderLid(CONTA, '888@lid', '5511444444444@s.whatsapp.net')
  conferir(s.lidsPendentes.size === 1,
    'o buffer colapsou as duas escritas numa só (senão o insert levaria "cannot affect row a second time")',
    'pendentes=' + s.lidsPendentes.size)
  await s.gravarLidsPendentes()
  const r = await s.pool.query(
    'select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2', [CONTA, 'lidmap-888@lid'])
  conferir(r.rowCount === 1 && JSON.parse(r.rows[0].conteudo) === '5511444444444@s.whatsapp.net',
    'ficou o último valor', r.rows[0] && r.rows[0].conteudo)
}

async function testeEsquecerConta () {
  console.log('\nesquecerConta não pode levar junto a conta vizinha')
  await limpar()
  s.aprenderLid(CONTA, '777@lid', '5511555555555@s.whatsapp.net')
  s.aprenderLid(VIZINHA, '777@lid', '5511666666666@s.whatsapp.net')
  s.jidsResolvidos.set(CONTA + ':5511555555555@s.whatsapp.net', 'x')
  s.jidsResolvidos.set(VIZINHA + ':5511666666666@s.whatsapp.net', 'y')
  s.enviadas.set(CONTA + ':ABC', {})
  s.enviadas.set(VIZINHA + ':ABC', {})

  s.esquecerConta(VIZINHA)
  conferir(s.lidMaps.has(CONTA) && !s.lidMaps.has(VIZINHA), 'lidMaps: saiu só a vizinha')
  conferir(s.jidsResolvidos.size === 1 && s.jidsResolvidos.has(CONTA + ':5511555555555@s.whatsapp.net'),
    'jidsResolvidos: saiu só a vizinha', 'restaram ' + [...s.jidsResolvidos.keys()].join(','))
  conferir(s.enviadas.size === 1 && s.enviadas.has(CONTA + ':ABC'),
    'enviadas: saiu só a vizinha', 'restaram ' + [...s.enviadas.keys()].join(','))
  conferir(s.lidsPendentes.size === 1 && [...s.lidsPendentes.keys()][0].startsWith(CONTA + ' '),
    'lidsPendentes: saiu só a vizinha', 'restaram ' + [...s.lidsPendentes.keys()].join(','))

  // e a conta 23 não pode ser levada pelo prefixo da conta 2
  s.esquecerConta(CONTA)
  conferir(s.lidMaps.size === 0 && s.jidsResolvidos.size === 0 && s.enviadas.size === 0,
    'a conta 23 sai quando é ela mesma que é esquecida')
}

async function testeTetoDeBytesDasEnviadas () {
  console.log('\ncache de enviadas: teto por BYTES, com o banco de rede de segurança')
  await limpar()
  const pequena = { key: { id: 'PEQ' }, message: { conversation: 'oi' } }
  // ~40 KB de "thumbnail": passa longe do teto de 8 KB
  const grande = { key: { id: 'GRA' }, message: { conversation: 'x'.repeat(40000) } }
  s.guardarEnviada(CONTA, pequena)
  s.guardarEnviada(CONTA, grande)
  conferir(s.enviadas.has(CONTA + ':PEQ'), 'mensagem pequena ficou na memória')
  conferir(!s.enviadas.has(CONTA + ':GRA'), 'mensagem grande NÃO ficou na memória')
  // o insert sai sem await (best-effort) — dá um fôlego pro banco antes de conferir
  await new Promise((r) => setTimeout(r, 700))
  const achada = await s.buscarEnviada(CONTA, 'GRA')
  conferir(!!achada && achada.conversation && achada.conversation.length === 40000,
    'a grande veio do BANCO pelo buscarEnviada (o retry continua funcionando)')
}

;(async () => {
  try {
    await testeLoteGrande()
    await testeReescrita()
    await testeDedupNoMesmoLote()
    await testeEsquecerConta()
    await testeTetoDeBytesDasEnviadas()
    await limpar()
  } catch (e) {
    console.error('\nerro no teste:', e && e.stack || e)
    falhas++
  }
  await s.pool.end()
  console.log('\n' + (falhas ? falhas + ' FALHA(S)' : 'tudo passou'))
  process.exit(falhas ? 1 : 0)
})()
