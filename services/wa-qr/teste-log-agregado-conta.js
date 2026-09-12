'use strict'
// O AGREGADO DO LOG TEM QUE DIZER DE QUAL CHIP ELE É.
//
// O caso real, 12/09/2026, uma hora depois de o #676 subir. A captura do console do
// libsignal funcionou de primeira e a tabela respondeu o que antes era ZERO:
//
//   14:08:26 | conta=- | total=2 | {"error|Session error:Error: Bad MAC": 1, ...}
//
// Quarenta linhas em 66 segundos — uns 1.800 `Bad MAC` por hora. E aí parou o
// diagnóstico, no campo `conta`: nulo. Sabia-se QUANTO, não se sabia DE QUEM — e "de
// qual chip" é a primeira pergunta que qualquer um faz, porque é ela que decide se o
// número é da conta que está no Baileys 7 em teste ou de uma que deveria estar calma.
//
// Desde a fase 3 cada worker atende UMA conta (`WA_QR_CONTA`), então o processo SABE
// de quem é o número. As linhas normais do log já saíam carimbadas (o `contaId` do
// objeto vira coluna); os três agregados — fila cheia, suprimidas do Baileys e console
// do libsignal — nasceram sem carimbo porque foram escritos antes de existir worker
// por conta.
//
// Este teste roda num processo com `WA_QR_CONTA` setado (é o que o supervisor faz);
// o caso do processo único, sem conta, fica no teste-log-banco.js, que roda sem a
// variável. Um valor de `MINHA_CONTA` por processo, então dois arquivos.
//
// Precisa de Postgres (grava de verdade). Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_qr_test
//     psql wa_qr_test -f ../../db/migracoes/158_wa_qr_log.sql
//     WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-log-agregado-conta.js

const URL = process.env.WA_QR_TEST_URL
if (!URL) {
  console.error('Falta WA_QR_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}
const CONTA = 35

process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = 'silent'
process.env.WA_QR_CONTA = String(CONTA)      // ANTES do require: MINHA_CONTA é lida na carga

const s = require('./server')

let falhas = 0
function conferir (ok, descricao, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}

async function limpar () {
  s._logFila.length = 0
  s._logSuprimidas.clear()
  s._logConsole.clear()
  await s.pool.query('delete from wa_qr_log')
}

async function agregado (like) {
  const r = await s.pool.query(
    "select conta_id, nivel, msg, dados from wa_qr_log where msg like $1 order by id", [like])
  return r.rows
}

async function testeMinhaConta () {
  console.log('\no worker sabe de quem ele é')
  conferir(s.MINHA_CONTA === CONTA, 'MINHA_CONTA saiu de WA_QR_CONTA',
    'MINHA_CONTA=' + s.MINHA_CONTA)
}

async function testeConsoleDoLibsignal () {
  // O caso que originou o teste: o Bad MAC do libsignal, com o texto real de produção.
  console.log('\no agregado do console do libsignal vem com a conta')
  await limpar()
  for (let i = 0; i < 40; i++) s.contarConsole('error', ['Session error:Error: Bad MAC'])
  await s.gravarLogsPendentes()
  const ag = await agregado('log: console do libsignal%')
  conferir(ag.length === 1, 'sai UMA linha de agregado', 'linhas=' + ag.length)
  conferir(ag.length === 1 && Number(ag[0].conta_id) === CONTA,
    'e ela diz de qual chip são os Bad MAC',
    ag.length ? 'conta_id=' + ag[0].conta_id : '-')
  conferir(ag.length === 1 && ag[0].dados.total === 40, 'com o total certo (40)',
    ag.length ? String(ag[0].dados.total) : '-')

  // E a consulta que o diagnóstico faz de verdade: filtrar por conta_id.
  const daConta = await s.pool.query(
    "select count(*)::int n from wa_qr_log where conta_id=$1 and msg like 'log: console%'",
    [CONTA])
  conferir(daConta.rows[0].n === 1,
    'filtrar por conta_id acha a linha — é assim que o diagnóstico começa (§1)')
}

async function testeSuprimidasDoBaileys () {
  console.log('\no agregado das suprimidas do Baileys também')
  await limpar()
  for (let i = 0; i < 118; i++) s.contarSuprimida('warn', { contaId: CONTA }, 'failed to decrypt message')
  await s.gravarLogsPendentes()
  const ag = await agregado('log: linhas do Baileys suprimidas%')
  conferir(ag.length === 1 && Number(ag[0].conta_id) === CONTA,
    'as 118 decifragens falhas ficam atribuídas à conta',
    ag.length ? 'conta_id=' + ag[0].conta_id : 'nenhuma linha')
}

async function testeFilaCheia () {
  console.log('\ne o aviso de fila cheia')
  await limpar()
  const TETO = 500                           // WA_QR_LOG_FILA_MAX padrão
  for (let i = 0; i < TETO + 7; i++) s.enfileirarLog('info', { contaId: CONTA, i }, 'linha ' + i)
  await s.gravarLogsPendentes()
  await s.gravarLogsPendentes()
  await s.gravarLogsPendentes()
  const ag = await agregado('log: fila cheia%')
  conferir(ag.length === 1 && Number(ag[0].conta_id) === CONTA && ag[0].dados.perdidas === 7,
    'o descarte diz quantas linhas e de qual conta',
    ag.length ? 'conta_id=' + ag[0].conta_id + ' perdidas=' + ag[0].dados.perdidas : 'nenhuma linha')
}

async function testeNadaAGravar () {
  console.log('\nsem nada pendente, nenhum agregado nasce')
  await limpar()
  await s.gravarLogsPendentes()
  const r = await s.pool.query("select count(*)::int n from wa_qr_log where msg like 'log: %'")
  conferir(r.rows[0].n === 0, 'silêncio quando não há o que contar', 'linhas=' + r.rows[0].n)
}

async function testeFonte () {
  // Trava de leitura: os três agregados têm que usar a MESMA forma. O defeito de
  // origem foi justamente um insert que esqueceu a coluna — e um insert esquecido
  // não falha, só grava nulo.
  console.log('\nos três inserts de agregado carimbam, no fonte')
  const fs = require('fs')
  const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
  const comCarimbo = src.match(/insert into wa_qr_log \(conta_id, nivel, msg, dados\) values\s*\n\s*\(\$1,/g) || []
  conferir(comCarimbo.length === 3, 'os três agregados inserem com conta_id',
    'encontrados=' + comCarimbo.length)
  const semCarimbo = src.match(/insert into wa_qr_log \(nivel, msg, dados\)/g) || []
  conferir(semCarimbo.length === 0, 'e nenhum insert de agregado ficou sem a coluna',
    'sem carimbo=' + semCarimbo.length)
  conferir(/\[MINHA_CONTA, JSON\.stringify/.test(src),
    'o valor vem de MINHA_CONTA, não de um contaId avulso')
}

;(async () => {
  try {
    await testeMinhaConta()
    await testeConsoleDoLibsignal()
    await testeSuprimidasDoBaileys()
    await testeFilaCheia()
    await testeNadaAGravar()
    await testeFonte()
    await limpar()
  } catch (e) {
    console.error('erro no teste:', e && e.stack || e)
    falhas++
  }
  await s.pool.end()
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo passou\n')
  process.exit(falhas ? 1 : 0)
})()
