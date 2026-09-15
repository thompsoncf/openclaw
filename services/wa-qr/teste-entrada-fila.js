'use strict'
// O OUTBOX DO REPASSE: grava antes, entrega depois, nunca perde.
//
// O CASO REAL, 15/09/2026. A conta 38 foi pareada às 09:56 e o sync de histórico
// dela derrubou o web. Nos ~2 minutos de 502, o wa-qr perdeu TRÊS mensagens de
// cliente (contas 23 e 34) e um eco de saída — o repasse era um `fetch` único, sem
// timeout e sem retentativa. Cada falha virou uma linha `warn` e a mensagem sumiu.
// O WhatsApp já tinha dado por entregue; não reenvia. As três estavam no celular do
// vendedor e nunca no painel.
//
// Este arquivo tranca o que impede isso de repetir. Precisa de Postgres (grava de
// verdade), nunca de produção:
//
//     createdb wa_fila_test
//     psql wa_fila_test -f ../../db/migracoes/261_wa_qr_entrada_fila.sql
//     WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_fila_test node teste-entrada-fila.js

const { Pool } = require('pg')
const fila = require('./entrada-fila')

const url = process.env.WA_QR_TEST_URL
if (!url) { console.error('Falta WA_QR_TEST_URL (banco de TESTE, descartável — nunca produção).'); process.exit(2) }
const pool = new Pool({ connectionString: url, max: 4 })
const log = { info () {}, warn () {}, error () {} }

let falhas = 0
function conferir (ok, d, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + d + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}
const deps = (f) => ({ fetch: f, appUrl: 'http://web.local', segredo: 's', log, timeoutMs: 300 })
const sempre = (resp) => async () => resp
const linha = async (id) => (await pool.query('select * from wa_qr_entrada_fila where id=$1', [id])).rows[0]
const corpo = { conta_id: 23, sender: '5586999', texto: 'oi', id: 'ABC1' }

;(async () => {
  await pool.query('truncate wa_qr_entrada_fila')

  console.log('\nGrava ANTES de qualquer rede:')
  const id1 = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'ABC1', corpo })
  conferir(!!id1, 'enfileirar devolve o id da linha nova')
  conferir((await linha(id1)).entregue_em === null, 'e ela nasce PENDENTE — nada foi entregue ainda')
  const dup = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'ABC1', corpo })
  conferir(dup === false, 'a mesma mensagem de novo (reentrega do Baileys) não duplica')
  const v1 = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: '', corpo })
  const v2 = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: '', corpo })
  conferir(v1 && v2 && v1 !== v2, 'sid vazio nunca conflita — perder mensagem sem id por dedup seria pior')
  await pool.query("delete from wa_qr_entrada_fila where provider_sid = ''")

  console.log('\nO 502 do incidente: fica na fila, com hora marcada pra voltar:')
  let r = await fila.drenar(pool, deps(sempre({ ok: false, status: 502 })), { contaId: 23 })
  let l = await linha(id1)
  conferir(r.entregues === 0 && r.falhou === true, 'não entregou e sinalizou falha')
  conferir(l.entregue_em === null && l.tentativas === 1, 'tentativas=1 e segue pendente',
    'tentativas=' + l.tentativas)
  conferir(new Date(l.proxima_em) > new Date(), 'com proxima_em no futuro')
  conferir(l.ultimo_status === 502, 'e o 502 guardado pra diagnóstico')

  console.log('\nAinda não é hora — o drenador respeita a espera:')
  r = await fila.drenar(pool, deps(sempre({ ok: true, status: 200 })), { contaId: 23 })
  conferir(r.entregues === 0, 'não entrega antes de proxima_em')

  console.log('\nO web volta: A MENSAGEM QUE ANTES SUMIA É ENTREGUE:')
  await pool.query('update wa_qr_entrada_fila set proxima_em = now() where id=$1', [id1])
  r = await fila.drenar(pool, deps(sempre({ ok: true, status: 200 })), { contaId: 23 })
  l = await linha(id1)
  conferir(r.entregues === 1 && l.entregue_em !== null, 'entregue_em preenchido')
  conferir(l.ultimo_status === 200 && l.ultimo_erro === null, 'e o erro anterior limpo')

  console.log('\nTimeout e rede caída são falha, nunca descarte:')
  const id2 = await fila.enfileirar(pool, { contaId: 23, rota: 'saida', sid: 'DEF2', corpo })
  await fila.drenar(pool, deps(async (u, o) => new Promise((_, rej) => {
    o.signal.addEventListener('abort', () => { const e = new Error('abort'); e.name = 'AbortError'; rej(e) })
  })), { contaId: 23 })
  l = await linha(id2)
  conferir(l.entregue_em === null && l.ultimo_erro === 'timeout', 'timeout -> fica na fila', l.ultimo_erro)
  await pool.query('update wa_qr_entrada_fila set proxima_em = now() where id=$1', [id2])
  await fila.drenar(pool, deps(async () => { throw new Error('ECONNREFUSED') }), { contaId: 23 })
  l = await linha(id2)
  conferir(l.entregue_em === null && /ECONNREFUSED/.test(l.ultimo_erro), 'rede caída -> fica na fila')

  console.log('\nOrdem: não passa por cima da que falhou:')
  await pool.query('truncate wa_qr_entrada_fila')
  const a = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'A', corpo })
  const b = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'B', corpo })
  let n = 0
  await fila.drenar(pool, deps(async () => { n++; return n === 1 ? { ok: false, status: 500 } : { ok: true, status: 200 } }), { contaId: 23 })
  conferir(n === 1 && (await linha(b)).entregue_em === null,
    'B não foi entregue antes de A — conversa não sai embaralhada')

  console.log('\nUma conta travada não trava a vizinha:')
  const c = await fila.enfileirar(pool, { contaId: 34, rota: 'entrada', sid: 'C', corpo })
  r = await fila.drenar(pool, deps(sempre({ ok: true, status: 200 })), { contaId: 34 })
  conferir(r.entregues === 1 && (await linha(c)).entregue_em !== null, 'a conta 34 drena com a 23 parada')

  console.log('\nDead-letter: a linha envenenada sai do caminho, mas NÃO é apagada:')
  await pool.query('update wa_qr_entrada_fila set tentativas=$2, proxima_em=now() where id=$1',
    [a, fila.PARAR_EM - 1])
  r = await fila.drenar(pool, deps(sempre({ ok: false, status: 500 })), { contaId: 23 })
  l = await linha(a)
  conferir(r.paradas === 1 && l.parada_em !== null, 'parada_em marcado')
  conferir(l.entregue_em === null && !!l, 'e a linha continua na tabela, pra alguém olhar')
  r = await fila.drenar(pool, deps(sempre({ ok: true, status: 200 })), { contaId: 23 })
  conferir((await linha(b)).entregue_em !== null, 'com a parada fora do caminho, B finalmente entra')

  console.log('\nA espera cresce e trava no teto:')
  conferir(fila.proximaEspera(0) === 5000 && fila.proximaEspera(2) === 120000 &&
           fila.proximaEspera(99) === 3600000, '5s, 2min, ..., 1h')

  // A CORREÇÃO QUE MOTIVOU O DESENHO: postar DENTRO da transação seguraria uma das
  // quatro conexões do pool por até 15s. Aqui o POST é lento de propósito e o banco
  // tem que seguir atendendo no meio dele.
  console.log('\nPOST lento NÃO segura conexão do pool (é por isso que o arrendo existe):')
  await pool.query('truncate wa_qr_entrada_fila')
  const d = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'D', corpo })
  let respondeuNoMeio = false
  const lento = fila.drenar(pool, deps(async () => {
    const t = await pool.query('select 1 as vivo')   // banco atendendo durante o POST
    respondeuNoMeio = t.rows[0].vivo === 1
    await new Promise((r2) => setTimeout(r2, 120))
    return { ok: true, status: 200 }
  }), { contaId: 23 })
  await lento
  conferir(respondeuNoMeio, 'o banco respondeu OUTRA consulta enquanto o POST corria')
  conferir((await linha(d)).entregue_em !== null, 'e a entrega foi marcada no fim')

  console.log('\nArrendamento: linha pega pra entrega fica invisível pro outro drenador:')
  await pool.query('truncate wa_qr_entrada_fila')
  const e1 = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'E', corpo })
  const pegou = await fila.arrendar(pool, { contaId: 23 })
  conferir(pegou.length === 1 && String(pegou[0].id) === String(e1), 'o primeiro drenador pegou a linha')
  const segundo = await fila.arrendar(pool, { contaId: 23 })
  conferir(segundo.length === 0, 'o segundo não vê nada — não posta a mesma mensagem duas vezes')
  conferir(new Date((await linha(e1)).proxima_em) > new Date(), 'porque o arrendo empurrou proxima_em')

  console.log('\nRetenção só apaga o que JÁ foi entregue:')
  await pool.query('truncate wa_qr_entrada_fila')
  const velhaOk = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'OK', corpo })
  const velhaPend = await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid: 'PEND', corpo })
  await pool.query("update wa_qr_entrada_fila set entregue_em = now() - interval '30 days' where id=$1", [velhaOk])
  await pool.query("update wa_qr_entrada_fila set criado_em = now() - interval '30 days' where id=$1", [velhaPend])
  await fila.limparEntregues(pool)
  conferir(!(await linha(velhaOk)), 'a entregue e velha saiu')
  conferir(!!(await linha(velhaPend)), 'a PENDENTE e velha ficou — nada some sem ter entrado')

  console.log('\nTabela ausente (a janela entre os dois deploys) -> null, sem lançar:')
  const p2 = new Pool({ connectionString: url, max: 1 })
  await p2.query('alter table wa_qr_entrada_fila rename to _x_fila')
  try {
    conferir(await fila.enfileirar(p2, { contaId: 23, rota: 'entrada', sid: 'Z', corpo }) === null,
      'enfileirar devolve null — quem chama faz o fetch direto de sempre')
    const r0 = await fila.drenar(p2, deps(sempre({ ok: true, status: 200 })), { contaId: 23 })
    conferir(r0.entregues === 0, 'e drenar não explode')
  } finally { await p2.query('alter table _x_fila rename to wa_qr_entrada_fila'); await p2.end() }

  // A ORDEM VEM DO JS, NÃO DO SQL. O `arrendar` é um UPDATE com subconsulta
  // ordenada, e o RETURNING dele devolve as linhas na ordem em que o Postgres as
  // atualizou — NÃO na do `order by`. Na primeira versão deste módulo o drenador
  // postava fora de ordem por causa disso, e o SQL "parecia" certo na leitura.
  console.log('\nO arrendo devolve em ordem de id, sempre:')
  await pool.query('truncate wa_qr_entrada_fila')
  const ids = []
  for (const sid of ['m1', 'm2', 'm3', 'm4', 'm5', 'm6']) {
    ids.push(String(await fila.enfileirar(pool, { contaId: 23, rota: 'entrada', sid, corpo })))
  }
  // embaralha proxima_em sem mexer no id: se a ordem viesse do banco, sairia trocada
  await pool.query('update wa_qr_entrada_fila set proxima_em = now() - (random() * interval \'10 seconds\')')
  const pegos = (await fila.arrendar(pool, { contaId: 23 })).map((x) => String(x.id))
  conferir(JSON.stringify(pegos) === JSON.stringify(ids),
    'as seis voltam na ordem de chegada', pegos.join(',') + ' vs ' + ids.join(','))
  await pool.query('truncate wa_qr_entrada_fila')

  console.log('\nRota desconhecida é erro de programação, não silêncio:')
  let explodiu = false
  try { await fila.enfileirar(pool, { contaId: 23, rota: 'inventada', sid: 'X', corpo }) } catch (_) { explodiu = true }
  conferir(explodiu, 'enfileirar recusa rota que não existe')

  await pool.query('truncate wa_qr_entrada_fila')
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  await pool.end()
  process.exit(falhas ? 1 : 0)
})().catch(async (e) => { console.error(e); try { await pool.end() } catch (_) {} process.exit(1) })
