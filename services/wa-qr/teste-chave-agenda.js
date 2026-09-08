'use strict'
// O laço do resyncAgenda quando falta a chave que decodifica os patches, e o
// carimbo que diz QUEM travou o event loop.
//
// O caso real (24h de produção, 07/09):
//
//   conta 23 — 71 pedidos de agenda, 71 avisos de 'chave AAAAAIWZ faltando'. Um pra um.
//   conta 34 — 31 pedidos, 21 do mesmo.
//   conta 36 — 34 pedidos, ZERO. É o controle: isso não é o normal.
//
// O mecanismo: o Baileys pede a chave, ela não está gravada, decodePatches estoura,
// o catch DELE apaga a versão da coleção, a versão volta a 0, e o pedido seguinte
// pede o snapshot INTEIRO (243 KB, ~4 mil contatos de uma vez) pra falhar na mesma
// chave. O teto que existia (MAX_INSISTENCIAS_AGENDA) não segurava porque mora no
// objeto da sessão: some no religamento, e a conta 23 reconecta ~28x/dia.
//
// Precisa de Postgres descartável (§2 do CLAUDE.md — nunca produção):
//
//     createdb zaq_waqr_agenda
//     DATABASE_URL=postgres://localhost/zaq_waqr_agenda node teste-chave-agenda.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://localhost/zaq_waqr_agenda'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const srv = require('./server')
const { useDbAuthState, MARCA_CHAVE_FALTANDO } = require('./auth-db')
const { pool, agendaTrancadaPorChave, VALVULA_CHAVE_FALTANDO_MS,
  medindo, oQueEstaEmCurso, decifragemPorConta, emCurso,
  contarFalhaDeDecifrar, DECIFRAR_JANELA_MS, falhasDeDecifrar } = srv

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

const CONTA = 999123
const mudo = { info () {}, warn () {}, error () {} }

async function limpar () {
  await pool.query('delete from wa_qr_auth where conta_id=$1', [CONTA])
}
async function marca () {
  const r = await pool.query('select conteudo, atualizado from wa_qr_auth where conta_id=$1 and arquivo=$2',
    [CONTA, MARCA_CHAVE_FALTANDO])
  return r.rows[0] || null
}

async function main () {
  await pool.query(`create table if not exists wa_qr_auth (
    conta_id bigint not null, arquivo text not null, conteudo text not null,
    atualizado timestamptz not null default now(), primary key (conta_id, arquivo))`)
  await limpar()

  const { state } = await useDbAuthState(pool, CONTA, mudo)

  // --- 1. a marca nasce quando o Baileys pede uma chave que não existe ---------
  console.log('\nA marca nasce sozinha, no lugar onde a falta é descoberta:')
  conferir(await marca() === null, 'antes de qualquer pedido, não existe marca')

  const vazio = await state.keys.get('app-state-sync-key', ['AAAAAIWZ'])
  conferir(vazio.AAAAAIWZ === null || vazio.AAAAAIWZ === undefined,
    'a chave pedida realmente não está gravada')
  const m1 = await marca()
  conferir(m1 !== null, 'pedir chave inexistente GRAVA a marca')
  conferir(JSON.parse(m1.conteudo).ids.includes('AAAAAIWZ'),
    'a marca guarda o id que faltou (AAAAAIWZ, o da conta 23)')

  // --- 2. a marca não se renova (senão a válvula nunca vence) ------------------
  console.log('\nA marca guarda a PRIMEIRA falta, não a última:')
  await pool.query(`update wa_qr_auth set atualizado = now() - interval '10 hours'
                     where conta_id=$1 and arquivo=$2`, [CONTA, MARCA_CHAVE_FALTANDO])
  const antes = (await marca()).atualizado
  await state.keys.get('app-state-sync-key', ['AAAAAIWZ'])
  const depois = (await marca()).atualizado
  conferir(antes.getTime() === depois.getTime(),
    'pedir de novo NÃO empurra a data — se empurrasse, a válvula de 24h nunca venceria')

  // --- 3. o portão fecha, e é ele que quebra o laço ----------------------------
  console.log('\nO portão:')
  const t1 = await agendaTrancadaPorChave(CONTA, Date.now())
  conferir(t1 !== null, 'com a marca de pé, a agenda está trancada (o pedido é pulado)')
  conferir(t1.ids.includes('AAAAAIWZ'), 'e o portão diz QUAL chave falta, pra ir pro log')

  const outra = await agendaTrancadaPorChave(CONTA + 1, Date.now())
  conferir(outra === null, 'é POR CONTA: a 36 (que não tem o problema) continua pedindo')

  // --- 4. a válvula: nenhuma conta fica presa numa conclusão minha -------------
  console.log('\nA válvula de tempo:')
  const dozeHoras = Date.now() - 12 * 60 * 60 * 1000
  conferir(await agendaTrancadaPorChave(CONTA, dozeHoras + VALVULA_CHAVE_FALTANDO_MS - 1000) !== null,
    'antes de 24h continua trancada')
  await pool.query(`update wa_qr_auth set atualizado = now() - interval '25 hours'
                     where conta_id=$1 and arquivo=$2`, [CONTA, MARCA_CHAVE_FALTANDO])
  conferir(await agendaTrancadaPorChave(CONTA, Date.now()) === null,
    'passadas 24h a porta REABRE sozinha — 1 decodificação por dia, contra as ~71')

  // --- 5. a chave chegando limpa a marca --------------------------------------
  console.log('\nQuando a chave que faltava finalmente chega:')
  await pool.query(`update wa_qr_auth set atualizado = now() where conta_id=$1 and arquivo=$2`,
    [CONTA, MARCA_CHAVE_FALTANDO])
  conferir(await agendaTrancadaPorChave(CONTA, Date.now()) !== null, 'trancada de novo (preparo)')

  await state.keys.set({ 'app-state-sync-key': { OUTRACHV: { keyData: Buffer.from('x') } } })
  conferir(await marca() !== null,
    'chave de OUTRO id não abre a porta — senão o laço voltava a cada religamento')

  await state.keys.set({ 'app-state-sync-key': { AAAAAIWZ: { keyData: Buffer.from('x') } } })
  conferir(await marca() === null, 'a chave QUE FALTAVA chegou: a marca sai')
  conferir(await agendaTrancadaPorChave(CONTA, Date.now()) === null,
    'e a agenda volta a ser pedida, sem religar nada')

  // --- 6. o que o portão NÃO faz (§0 e §1) ------------------------------------
  console.log('\nO que o portão não faz:')
  const chaves = await pool.query(
    `select count(*)::int n from wa_qr_auth where conta_id=$1 and arquivo like 'app-state-sync-key-%'`,
    [CONTA])
  conferir(chaves.rows[0].n === 2, 'as chaves gravadas continuam lá — o portão não apaga cofre')
  await limpar()
  await state.keys.get('app-state-sync-key', ['ZZZ'])
  const so = await pool.query('select arquivo from wa_qr_auth where conta_id=$1', [CONTA])
  conferir(so.rows.length === 1 && so.rows[0].arquivo === MARCA_CHAVE_FALTANDO,
    'a marca é UMA linha de controle, não mexe em credencial nem em canal')

  // --- 7. falha de banco não pode virar bloqueio novo -------------------------
  console.log('\nNa dúvida, pede (o portão é economia, não trava):')
  const queryOriginal = pool.query.bind(pool)
  pool.query = async () => { throw new Error('banco fora do ar') }
  const naFalha = await agendaTrancadaPorChave(CONTA, Date.now())
  pool.query = queryOriginal
  conferir(naFalha === null,
    'select que estoura devolve null = comportamento de antes, não porta fechada')

  // --- 8. O CARIMBO ------------------------------------------------------------
  console.log('\nO carimbo do travamento (o que substitui o provedor de métricas):')
  conferir(oQueEstaEmCurso(Date.now()).length === 0, 'parado, não há nada em curso')

  let dentro = null
  await medindo('resyncAgenda', 23, async () => {
    await new Promise((r) => setTimeout(r, 20))
    dentro = oQueEstaEmCurso(Date.now())
  })
  conferir(dentro.length === 1 && dentro[0].op === 'resyncAgenda' && dentro[0].conta === 23,
    'durante a operação o carimbo diz a operação E a conta — era isso que faltava em 07/09')
  conferir(dentro[0].haMs >= 15, 'e há quanto tempo ela está rodando (o suspeito é o mais antigo)')
  conferir(oQueEstaEmCurso(Date.now()).length === 0, 'terminou, sai do carimbo')

  await medindo('x', 1, async () => { throw new Error('estourou') }).catch(() => {})
  conferir(emCurso.size === 0, 'operação que ESTOURA também sai — senão o carimbo mentiria pra sempre')

  const aninhado = []
  await medindo('repassarContatos', 34, () =>
    medindo('resyncAgenda', 23, async () => { aninhado.push(...oQueEstaEmCurso(Date.now())) }))
  conferir(aninhado.length === 2, 'duas operações ao mesmo tempo aparecem as duas')
  conferir(aninhado[0].op === 'repassarContatos',
    'e vêm da mais antiga pra mais nova — quem começou antes é quem está segurando')

  // o outro suspeito, que roda dentro do Baileys e não passa por medindo()
  falhasDeDecifrar.delete(34)
  const agora = Date.now()
  for (let i = 0; i < 5; i++) contarFalhaDeDecifrar(34, agora, 999, DECIFRAR_JANELA_MS)
  conferir(decifragemPorConta(agora)[34] === 5,
    'o carimbo leva junto a decifragem por conta — o suspeito que medindo() não alcança')
  falhasDeDecifrar.delete(34)
  conferir(Object.keys(decifragemPorConta(Date.now())).length === 0,
    'sem falhas, não polui a linha')

  await limpar()
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  await pool.end()
  process.exit(falhas ? 1 : 0)
}

main().catch((e) => { console.error(e); process.exit(1) })
