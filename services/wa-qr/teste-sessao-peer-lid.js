'use strict'
// A limpeza cirúrgica de sessão: os DOIS identificadores do contato, e pelo key
// store (não por SQL).
//
// O caso real, Prime Eventos (conta 34), 08/09/2026. O chip foi pareado 21:01; a
// partir das 22:07 tudo que o painel mandava pro dono chegava como "Aguardando
// mensagem" — chegava e NÃO ABRIA. No cofre estavam os mesmos três aparelhos duas
// vezes:
//
//   session-558681885930.0/.33/.35     ← pelo NÚMERO — tocada no ENVIO  07:43:47
//   session-35626837647552.0/.33/.35   ← pelo LID    — tocada no RETRY  07:43:54
//
// Ciframos com a sessão de um lado; o aparelho dele estava no outro. E a limpeza
// que deveria consertar mirava só o identificador do jid: registrou `apagadas: 0`
// duas vezes no mesmo dia, procurando o LID enquanto a sessão viva estava sob o
// número. Depois de apagar os dois conjuntos na mão, as mensagens voltaram a abrir
// e o "pediram reenvio" sumiu do log.
//
// Precisa de Postgres descartável (§2 do CLAUDE.md — nunca produção):
//
//     createdb zaq_waqr_peerlid
//     DATABASE_URL=postgres://localhost/zaq_waqr_peerlid node teste-sessao-peer-lid.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://localhost/zaq_waqr_peerlid'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { pool, usuariosDoPeer, limparSessaoDoPeer, ultimaLimpezaDePeer,
  LIMPAR_SESSAO_ESPERA_MS, lidMaps, sessoes } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

const CONTA = 34
const NUM = '558681885930'
const LID = '35626837647552'
const OUTRO = '558699406516'          // uma cliente de verdade: não pode ser tocada

async function semear () {
  await pool.query('delete from wa_qr_auth where conta_id=$1', [CONTA])
  const linhas = []
  for (const d of ['0', '33', '35']) {
    linhas.push(['session-' + NUM + '.' + d], ['session-' + LID + '.' + d])
  }
  linhas.push(['session-' + OUTRO + '.0'], ['creds'], ['pre-key-1'],
    ['app-state-sync-key-AAAAAIWZ'])
  for (const [arquivo] of linhas) {
    await pool.query(
      `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
       values ($1,$2,'{}',now()) on conflict (conta_id, arquivo) do nothing`,
      [CONTA, arquivo])
  }
  ultimaLimpezaDePeer.clear()
}
const conta = async (like) => (await pool.query(
  'select count(*)::int n from wa_qr_auth where conta_id=$1 and arquivo like $2',
  [CONTA, like])).rows[0].n

async function main () {
  await pool.query(`create table if not exists wa_qr_auth (
    conta_id bigint not null, arquivo text not null, conteudo text not null,
    atualizado timestamptz not null default now(), primary key (conta_id, arquivo))`)

  // o mapa que o serviço já mantém: lid -> número
  lidMaps.set(CONTA, new Map([[LID + '@lid', NUM + '@s.whatsapp.net']]))

  // --- 1. os dois nomes da mesma pessoa ---------------------------------------
  console.log('\nOs dois identificadores do mesmo contato:')
  conferir(usuariosDoPeer(CONTA, LID + '@lid').sort().join(',') === [LID, NUM].sort().join(','),
    'vindo pelo LID, acha também o número (era esse o `apagadas: 0` da Prime)')
  conferir(usuariosDoPeer(CONTA, NUM + '@s.whatsapp.net').sort().join(',') === [LID, NUM].sort().join(','),
    'vindo pelo número, acha também o LID (a volta, que o mapa não indexa)')
  conferir(usuariosDoPeer(CONTA, '5586' + '99999999@s.whatsapp.net').join(',') === '558699999999',
    'contato sem par conhecido devolve só ele mesmo')
  conferir(usuariosDoPeer(CONTA, '558698392961-1607041815@g.us').length === 0,
    'GRUPO continua fora: não tem sessão de contato pra apagar')
  conferir(usuariosDoPeer(CONTA, 'status@broadcast').length === 0, 'status também fica de fora')
  conferir(usuariosDoPeer(999, LID + '@lid').join(',') === LID,
    'o mapa é por conta: outra conta não herda o par')

  // --- 2. apaga os DOIS conjuntos --------------------------------------------
  console.log('\nA limpeza, chamada com o LID (foi assim que a Prime falhou):')
  await semear()
  conferir(await conta('session-%') === 7, 'semeado: 3 do número + 3 do LID + 1 de outra pessoa')
  const n = await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  conferir(n === 6, 'apagou 6 — os dois conjuntos, não 0 e não 3')
  conferir(await conta('session-' + NUM + '.%') === 0, 'sumiram as do número')
  conferir(await conta('session-' + LID + '.%') === 0, 'sumiram as do LID')

  // --- 3. o que NÃO pode ser tocado (§0 e §1) ---------------------------------
  console.log('\nO que a limpeza não encosta:')
  conferir(await conta('session-' + OUTRO + '.%') === 1,
    'a sessão da outra cliente fica intacta — a limpeza é cirúrgica de verdade')
  conferir(await conta('creds') === 1, 'creds intacto (apagar isso obriga a parear de novo)')
  conferir(await conta('pre-key-%') === 1, 'pre-keys intactas')
  conferir(await conta('app-state-sync-key-%') === 1, 'chave da agenda intacta')

  // --- 4. a trava de 1h, agora por contato ------------------------------------
  console.log('\nA trava de frequência:')
  await semear()
  await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  conferir(await limparSessaoDoPeer(CONTA, NUM + '@s.whatsapp.net', 'retry esgotado') === 0,
    'chamar pelo OUTRO nome do mesmo contato respeita a trava (é o mesmo contato)')
  conferir(LIMPAR_SESSAO_ESPERA_MS >= 3600000, 'e a trava continua sendo de pelo menos 1h')

  // --- 5. pelo KEY STORE, pra invalidar o cache -------------------------------
  // O Baileys lê por makeCacheableSignalKeyStore: apagar só no Postgres deixa a
  // sessão velha na memória e o socket a reescreve na mensagem seguinte.
  console.log('\nPassa pelo key store quando há socket (senão o cache reescreve):')
  await semear()
  const pedidos = []
  sessoes.set(CONTA, { sock: { authState: { keys: {
    set: async (dados) => {
      pedidos.push(dados)
      for (const id in dados.session) {
        await pool.query('delete from wa_qr_auth where conta_id=$1 and arquivo=$2',
          [CONTA, 'session-' + id])
      }
    }
  } } } })
  const n2 = await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  sessoes.delete(CONTA)
  conferir(pedidos.length === 1, 'usou o store em vez de SQL cru')
  conferir(Object.keys(pedidos[0].session).length === 6,
    'mandou os 6 aparelhos de uma vez, dos dois identificadores')
  conferir(Object.values(pedidos[0].session).every((v) => v === null),
    'com null — é o null que zera o cache E chama o nosso apagar()')
  conferir(n2 === 6, 'e reportou 6 apagadas')

  // --- 6. o número relatado tem que ser o REAL --------------------------------
  console.log('\nO `apagadas` não pode voltar a ser palpite:')
  await semear()
  sessoes.set(CONTA, { sock: { authState: { keys: { set: async () => {} } } } })  // store que não apaga
  const n3 = await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  sessoes.delete(CONTA)
  conferir(n3 === 0,
    'store que não apagou nada reporta 0 — foi esse número que denunciou o defeito')
  conferir(await conta('session-%') === 7, 'e de fato nada saiu')

  await pool.query('delete from wa_qr_auth where conta_id=$1', [CONTA])
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  await pool.end()
  process.exit(falhas ? 1 : 0)
}

main().catch((e) => { console.error(e); process.exit(1) })
