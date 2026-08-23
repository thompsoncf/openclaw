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
//     createdb wa_qr_test
//     psql wa_qr_test -f ../../db/migracoes/158_wa_qr_log.sql
//     psql wa_qr_test -f ../../db/migracoes/182_wa_qr_backoff_persistente.sql
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
    'select conta_id, nivel, msg, dados, criado_em from wa_qr_log ' + (where || '') + ' order by id',
    args || [])
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

async function testeContaComoTexto () {
  // O defeito que a própria tabela denunciou no primeiro minuto de produção: o driver
  // do Postgres devolve bigint como STRING, então o contaId que sai de uma consulta é
  // '35' e não 35 — e com a checagem crua de número TODA linha dessas nascia sem
  // conta. O log gravava bonito e era inútil pro único uso que justifica a tabela.
  console.log('\ncontaId em texto (é o que o driver do Postgres devolve) vira coluna igual')
  await limpar()
  s.enfileirarLog('info', { contaId: '35' }, 'iniciarSessao: começando')
  s.enfileirarLog('info', { contaId: 35 }, 'e o número puro segue valendo')
  await s.gravarLogsPendentes()
  const da35 = await linhas('where conta_id=35')
  conferir(da35.length === 2, 'as duas formas caem na mesma conta', 'linhas=' + da35.length)

  console.log('\nmas o que não é conta continua nulo, sem virar zero')
  await limpar()
  for (const [valor, rotulo] of [['', 'string vazia'], [null, 'null'], ['abc', 'texto qualquer'],
    [undefined, 'ausente'], [{}, 'objeto']]) {
    s.enfileirarLog('info', { contaId: valor }, 'linha de ' + rotulo)
  }
  await s.gravarLogsPendentes()
  const nulas = await linhas('where conta_id is null')
  conferir(nulas.length === 5, 'as cinco ficaram sem conta em vez de virar 0',
    'nulas=' + nulas.length)
}

async function testeHoraDoEvento () {
  // O lote sai de 2 em 2s; com `default now()` a leva inteira nascia com o mesmo
  // instante, e num incidente é justamente a ordem/o intervalo que se lê.
  console.log('\na hora é a do evento, não a da gravação em lote')
  await limpar()
  s.enfileirarLog('info', { contaId: CONTA }, 'primeira')
  await new Promise((r) => setTimeout(r, 1100))
  s.enfileirarLog('info', { contaId: CONTA }, 'segunda, um segundo depois')
  await s.gravarLogsPendentes()          // as duas gravam no MESMO insert
  const [a, b] = await linhas('where conta_id=$1', [CONTA])
  const dif = new Date(b.criado_em) - new Date(a.criado_em)
  conferir(dif >= 1000, 'as duas linhas do mesmo lote preservam o intervalo real',
    'diferença=' + dif + 'ms')
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

  // A espera anti-guerra de sessão (migração 182) mora AQUI de propósito: era só
  // memória, e o reinício que a própria guerra provocava zerava a espera. Ver
  // teste-guerra-sessao.js pro caso completo — este confere que o retrato leva os
  // dois campos junto, que é a única forma de eles chegarem no processo seguinte.
  const quando = agora - 7 * 60 * 1000
  s.sessoes.set(CONTA, { status: 'desconectado', substituidaEm: quando, tentativasPos440: 2 })
  await s.registrarSessoes()
  const r3 = await s.pool.query(
    'select substituida_em, tentativas_440 from wa_qr_sessao_estado where conta_id=$1', [CONTA])
  conferir(r3.rows[0] && r3.rows[0].substituida_em &&
    Math.abs(new Date(r3.rows[0].substituida_em).getTime() - quando) < 1000,
  'a hora da substituição vai pro retrato')
  conferir(r3.rows[0] && r3.rows[0].tentativas_440 === 2,
    'e o contador de tentativas também — é ele que dobra a espera')
  // segunda rodada não pode empilhar linha
  s.sessoes.get(CONTA).status = 'reconectando'
  await s.registrarSessoes()
  const r2 = await s.pool.query('select status from wa_qr_sessao_estado where conta_id=$1', [CONTA])
  conferir(r2.rows.length === 1 && r2.rows[0].status === 'reconectando',
    'a linha é sobrescrita, não duplicada')

  // O RETRATO DA CONTA QUE SAIU TEM QUE SUMIR.
  //
  // registrarSessoes percorre o mapa `sessoes`. Quem sai do mapa — os dois logouts,
  // o 401 do celular e o botão Desconectar — para de ser sobrescrito e a linha
  // CONGELA no último estado, que é sempre 'conectado'. A conta 7 ficou assim:
  // 'conectado' carimbado em 21/08 00:05, cofre zerado, sem aluguel, ainda lá três
  // dias depois. Não chega ao painel (nenhum Python lê esta tabela), mas engana quem
  // depura — e enganou, duas vezes, na madrugada de 23/08.
  s.sessoes.set(CONTA, { status: 'conectado', sock: {}, ultimoEvento: Date.now() })
  await s.registrarSessoes()
  const antes = await s.pool.query(
    'select count(*)::int n from wa_qr_sessao_estado where conta_id=$1', [CONTA])
  conferir(antes.rows[0].n === 1, 'antes do logout a conta tem retrato')

  s.sessoes.delete(CONTA)
  await s.apagarRetratoDaSessao(CONTA)
  const depois = await s.pool.query(
    'select count(*)::int n from wa_qr_sessao_estado where conta_id=$1', [CONTA])
  conferir(depois.rows[0].n === 0, 'depois do logout o retrato some — não congela em "conectado"')

  // ...e sem levar a vizinha junto: o delete é por conta_id, não uma faxina.
  const vizinha = await s.pool.query(
    'select count(*)::int n from wa_qr_sessao_estado where conta_id=23', [])
  conferir(vizinha.rows[0].n === 1, 'e a conta vizinha, que não deslogou, continua no retrato')

  // Apagar de novo (ou de uma conta que nunca teve linha) não pode estourar: o
  // logout chama isto sem saber se a linha existe.
  let explodiu = false
  try { await s.apagarRetratoDaSessao(CONTA); await s.apagarRetratoDaSessao(999) } catch (_) { explodiu = true }
  conferir(!explodiu, 'apagar retrato inexistente é no-op, não erro')
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
    await testeContaComoTexto()
    await testeHoraDoEvento()
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
