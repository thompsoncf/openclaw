'use strict'
// O espelho do log no Postgres (wa_qr_log) e o retrato das sessões (wa_qr_sessao_estado).
//
// POR QUE ISTO EXISTE, e por que tem teste próprio.
//
// Num chamado real a sessão de uma cliente ficou muda por horas. Deu pra provar PELO
// BANCO que ela tinha emudecido — mensagens, contatos, ecos e mapa de @lid daquela
// conta param todos no mesmo minuto, enquanto as vizinhas seguiam gravando — e não
// deu pra saber POR QUÊ, porque o motivo estava só no log do Render, que não se lê de
// fora (o dashboard exige sessão de navegador e api.render.com cai em 403 na política
// de egresso do agente). Cada rodada de diagnóstico virava "abra o dashboard, filtre
// pela conta e me diga o que aparece" — que é exatamente o tempo que a gente estava
// tentando não perder.
//
// O risco de consertar isso é trocar um problema por outro maior: log é volume, e
// volume no caminho de mensagem derruba o serviço que a gente está tentando observar.
// Daí o que este teste protege, que é justamente o que segura o volume:
//
//   - a fila TEM teto, e o que passa dele é descartado (nunca acumulado);
//   - o descarte é CONTADO e aparece na própria tabela — buraco silencioso num
//     diagnóstico é pior que diagnóstico faltando;
//   - debug/trace nunca vão pro banco;
//   - o objeto logado vira jsonb, com contaId virando coluna (é por ele que todo
//     diagnóstico filtra) e com teto de tamanho;
//   - gravar em lote, e falha de gravação NUNCA sobe (o serviço não pode cair
//     porque o log caiu).
//
// Precisa de Postgres (grava de verdade). Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_qr_test && psql wa_qr_test -f ../../db/migracoes/158_wa_qr_log.sql
//     WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-log-banco.js

const URL = process.env.WA_QR_TEST_URL
if (!URL) {
  console.error('Falta WA_QR_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}
process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = 'silent'

const s = require('./server')

let falhas = 0
function conferir (ok, descricao, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}

const CONTA = 35

async function limpar () {
  s._logFila.length = 0
  await s.pool.query('delete from wa_qr_log')
  await s.pool.query('delete from wa_qr_sessao_estado')
  s.sessoes.clear()
}

async function linhas (where, args) {
  const r = await s.pool.query(
    'select conta_id, nivel, msg, dados from wa_qr_log ' + (where || '') + ' order by id', args || [])
  return r.rows
}

async function testeGravacao () {
  console.log('\na linha do log chega no banco, separada por conta')
  await limpar()
  s.enfileirarLog('info', { contaId: CONTA, type: 'notify', n: 2 }, 'messages.upsert recebido')
  s.enfileirarLog('warn', { contaId: 23, code: 440 }, 'conexão fechou')
  s.enfileirarLog('info', 'wa-qr no ar')                       // sem conta: linha do serviço
  await s.gravarLogsPendentes()
  const todas = await linhas()
  conferir(todas.length === 3, 'as três linhas gravaram', 'gravadas=' + todas.length)
  const da35 = await linhas('where conta_id=$1', [CONTA])
  conferir(da35.length === 1 && da35[0].msg === 'messages.upsert recebido',
    'filtrar por conta_id devolve só a dela — é como todo diagnóstico começa')
  conferir(da35[0].dados && da35[0].dados.type === 'notify' && da35[0].dados.n === 2,
    'o resto do objeto virou jsonb', JSON.stringify(da35[0].dados))
  conferir(da35[0].dados.contaId === undefined,
    'contaId não se repete dentro do jsonb — ele já é coluna')
  const doServico = await linhas('where conta_id is null')
  conferir(doServico.length === 1 && doServico[0].msg === 'wa-qr no ar',
    'linha do serviço (sem conta) também é guardada')
  conferir(doServico[0].dados === null, 'sem objeto, dados fica nulo em vez de {}')
}

async function testeNivelEFila () {
  console.log('\no que NÃO pode entrar')
  await limpar()
  s.enfileirarLog('debug', { contaId: CONTA }, 'detalhe interno')
  s.enfileirarLog('trace', { contaId: CONTA }, 'detalhe interno')
  conferir(s._logFila.length === 0, 'debug e trace nem entram na fila')

  console.log('\nfila cheia: descarta e CONTA o que perdeu')
  await limpar()
  const TETO = 500                       // WA_QR_LOG_FILA_MAX padrão
  for (let i = 0; i < TETO + 30; i++) s.enfileirarLog('info', { contaId: CONTA, i }, 'linha ' + i)
  conferir(s._logFila.length === TETO, 'a fila parou no teto em vez de crescer sem fim',
    'fila=' + s._logFila.length)
  await s.gravarLogsPendentes()          // lote de 200
  await s.gravarLogsPendentes()
  await s.gravarLogsPendentes()
  const perda = await linhas("where msg like 'log: fila cheia%'")
  conferir(perda.length === 1 && perda[0].dados.perdidas === 30,
    'a perda ficou registrada na própria tabela, com o número',
    perda.length ? JSON.stringify(perda[0].dados) : 'nenhuma linha de aviso')
}

async function testeDadosEstranhos () {
  console.log('\nobjeto estranho não pode derrubar nem virar linha gigante')
  await limpar()
  const ciclo = { contaId: CONTA }
  ciclo.eu = ciclo
  let explodiu = false
  try { s.enfileirarLog('error', ciclo, 'com ciclo') } catch (_) { explodiu = true }
  conferir(!explodiu, 'objeto cíclico não estoura no caminho da mensagem')
  s.enfileirarLog('info', { contaId: CONTA, gordo: 'x'.repeat(20000) }, 'payload enorme')
  await s.gravarLogsPendentes()
  const todas = await linhas('where conta_id=$1', [CONTA])
  conferir(todas.length === 2, 'as duas linhas gravaram mesmo assim', 'gravadas=' + todas.length)
  const gorda = todas.find((l) => l.msg === 'payload enorme')
  conferir(gorda && gorda.dados && typeof gorda.dados._truncado === 'string' &&
    JSON.stringify(gorda.dados).length < 4200,
  'o payload enorme foi truncado, e ainda é jsonb válido')
  const msgLonga = 'y'.repeat(900)
  s.enfileirarLog('info', { contaId: CONTA }, msgLonga)
  await s.gravarLogsPendentes()
  const cortada = (await linhas('where conta_id=$1', [CONTA])).pop()
  conferir(cortada.msg.length === 500, 'mensagem longa entra cortada', 'tam=' + cortada.msg.length)
}

async function testeSessoes () {
  console.log('\nretrato das sessões: uma linha por conta, sobrescrita')
  await limpar()
  const agora = Date.now()
  s.sessoes.set(CONTA, { status: 'conectado', sock: {}, ultimoEvento: agora - 3 * 3600 * 1000,
    reconexoesMudas: 2 })
  s.sessoes.set(23, { status: 'conectado', sock: {}, ultimoEvento: agora - 5000 })
  await s.registrarSessoes()
  const r = await s.pool.query('select * from wa_qr_sessao_estado order by conta_id')
  conferir(r.rows.length === 2, 'uma linha por sessão viva', 'linhas=' + r.rows.length)
  const muda = r.rows.find((l) => Number(l.conta_id) === CONTA)
  conferir(muda.status === 'conectado' && Number(muda.mudo_s) >= 10700,
    'o caso da conta muda fica escrito: "conectado" e 3h sem entregar nada',
    'status=' + muda.status + ' mudo_s=' + muda.mudo_s)
  conferir(muda.religamentos === 2, 'e quantas vezes o vigia já tentou religar sem sucesso')
  conferir(muda.detalhe && muda.detalhe.temSock === true, 'o detalhe diz se ainda há socket')
  // segunda rodada não pode empilhar linha
  s.sessoes.get(CONTA).status = 'reconectando'
  await s.registrarSessoes()
  const r2 = await s.pool.query('select status from wa_qr_sessao_estado where conta_id=$1', [CONTA])
  conferir(r2.rows.length === 1 && r2.rows[0].status === 'reconectando',
    'a linha é sobrescrita, não duplicada')
}

async function testeFalhaNaoSobe () {
  console.log('\ngravação falhando não pode derrubar o serviço')
  await limpar()
  await s.pool.query('alter table wa_qr_log rename to wa_qr_log_escondida')
  s.enfileirarLog('error', { contaId: CONTA }, 'algo importante')
  let explodiu = false
  try { await s.gravarLogsPendentes() } catch (_) { explodiu = true }
  await s.pool.query('alter table wa_qr_log_escondida rename to wa_qr_log')
  conferir(!explodiu, 'tabela sumida: a gravação falha calada, o serviço segue')
  s.sessoes.set(CONTA, { status: 'conectado', ultimoEvento: Date.now() })
  await s.pool.query('alter table wa_qr_sessao_estado rename to wa_qr_sessao_escondida')
  explodiu = false
  try { await s.registrarSessoes() } catch (_) { explodiu = true }
  await s.pool.query('alter table wa_qr_sessao_escondida rename to wa_qr_sessao_estado')
  conferir(!explodiu, 'o mesmo vale pro retrato das sessões')
}

;(async () => {
  try {
    await testeGravacao()
    await testeNivelEFila()
    await testeDadosEstranhos()
    await testeSessoes()
    await testeFalhaNaoSobe()
    await limpar()
  } catch (e) {
    console.error('erro no teste:', e && e.stack || e)
    falhas++
  }
  await s.pool.end()
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo passou\n')
  process.exit(falhas ? 1 : 0)
})()
