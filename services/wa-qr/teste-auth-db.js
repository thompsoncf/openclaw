'use strict'
// Teste do estado de auth (auth-db.js) — foco na CHAVE DA AGENDA.
//
// Roda contra um Postgres descartável, com o protobuf de verdade do Baileys
// (proto.Message.AppStateSyncKeyData), que é o valor que o WhatsApp entrega no
// APP_STATE_SYNC_KEY_SHARE. Serve pra responder duas perguntas que só o banco
// responde: a chave da agenda ATRAVESSA a serialização inteira? E, quando a
// gravação falha, o log diz QUAL chave era e o erro é relançado (sem relançar,
// o commit do Baileys "dá certo" e a chave se perde de vez)?
//
// Não entra no pytest: precisa de Node + as dependências do serviço. Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_auth_test
//     WA_AUTH_TEST_URL=postgresql://postgres:postgres@localhost:5432/wa_auth_test \
//       node teste-auth-db.js

const { Pool } = require('pg')
const { proto } = require('@whiskeysockets/baileys')
const { useDbAuthState } = require('./auth-db')

const URL = process.env.WA_AUTH_TEST_URL
if (!URL) {
  console.error('Falta WA_AUTH_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}

const CONTA = 23
const KEY_ID = 'AAAAACPQ'
const ARQ = 'app-state-sync-key-' + KEY_ID
const pool = new Pool({ connectionString: URL })

// pino falso: guarda o que foi logado pra poder conferir
const linhas = []
const mk = (nivel) => (a, b) => linhas.push({
  nivel, obj: (typeof a === 'object' && a) || {}, msg: (typeof a === 'string' ? a : b) || ''
})
const log = { info: mk('info'), warn: mk('warn'), error: mk('error') }
const achou = (t) => linhas.filter((l) => l.msg.includes(t))

const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)

function chaveDaAgenda () {
  return proto.Message.AppStateSyncKeyData.fromObject({
    keyData: Buffer.from('0123456789abcdef0123456789abcdef', 'hex'),
    fingerprint: { rawId: 42, currentIndex: 0, deviceIndexes: [0, 1] },
    timestamp: 1786646016
  })
}

;(async () => {
  await pool.query('drop table if exists wa_qr_auth')
  await pool.query(`create table wa_qr_auth (
    conta_id bigint not null, arquivo text not null, conteudo text not null,
    atualizado timestamptz not null default now(), primary key (conta_id, arquivo))`)

  const { state } = await useDbAuthState(pool, CONTA, log)
  const keyData = chaveDaAgenda()

  // 1. o caminho que o Baileys percorre no APP_STATE_SYNC_KEY_SHARE
  await state.keys.set({ 'app-state-sync-key': { [KEY_ID]: keyData } })
  const r = await pool.query('select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2',
    [CONTA, ARQ])
  t('a chave da agenda é gravada no banco', r.rowCount === 1)
  t('log registra a chegada da chave', achou('chegou chave da agenda').length === 1)
  t('log confirma a gravação', achou('chave da agenda GRAVADA').length === 1)

  // 2. ida e volta: tem que voltar protobuf, não objeto solto (senão o Baileys
  //    não decifra a agenda e o sintoma vira "App state key not present")
  const lido = await state.keys.get('app-state-sync-key', [KEY_ID])
  t('releitura devolve AppStateSyncKeyData', !!lido[KEY_ID] && typeof lido[KEY_ID].toJSON === 'function')
  t('keyData sobrevive à ida e volta',
    Buffer.from(lido[KEY_ID].keyData).toString('hex') === '0123456789abcdef0123456789abcdef')
  t('timestamp sobrevive', String(lido[KEY_ID].timestamp) === '1786646016')

  // 3. chave pedida e ausente — o sintoma exato de "myAppStateKeyId existe mas a
  //    chave não". Tem que gritar, não passar batido.
  linhas.length = 0
  await state.keys.get('app-state-sync-key', ['NAOEXISTE'])
  t('avisa quando o Baileys pede chave que não está gravada', achou('NÃO estão gravadas').length === 1)

  // 4. remoção da chave da agenda não é rotina: fica no log
  linhas.length = 0
  await state.keys.set({ 'app-state-sync-key': { [KEY_ID]: null } })
  t('apagar a chave da agenda vira aviso', achou('APAGANDO a chave da agenda').length === 1)
  t('e ela sai mesmo da tabela',
    (await pool.query('select 1 from wa_qr_auth where conta_id=$1 and arquivo=$2', [CONTA, ARQ])).rowCount === 0)

  // 5. falha de banco: log com NOME da chave + relançar
  linhas.length = 0
  const poolQuebrado = {
    query: async (sql) => {
      if (/insert into wa_qr_auth/.test(sql)) throw new Error('null value in column "conteudo"')
      return { rows: [], rowCount: 0 }
    }
  }
  const quebrado = await useDbAuthState(poolQuebrado, CONTA, log)
  let relancou = false
  try { await quebrado.state.keys.set({ 'app-state-sync-key': { [KEY_ID]: keyData } }) } catch (e) { relancou = true }
  t('erro de gravação é relançado (o Baileys ainda repete 10x)', relancou)
  const erro = achou('FALHA ao gravar chave')[0]
  t('erro é logado', !!erro)
  t('erro diz qual chave era', !!erro && erro.obj.arquivo === ARQ)
  t('erro marca que é a chave da agenda', !!erro && /AGENDA/.test(erro.msg))
  t('erro traz a mensagem do banco', !!erro && /conteudo/.test(String(erro.obj.e)))

  // 6. os outros tipos seguem iguais, e sem poluir o log
  linhas.length = 0
  await state.keys.set({ 'pre-key': { 7: { public: Buffer.from('aa', 'hex'), private: Buffer.from('bb', 'hex') } } })
  t('pre-key grava normal',
    (await pool.query("select 1 from wa_qr_auth where conta_id=$1 and arquivo='pre-key-7'", [CONTA])).rowCount === 1)
  t('e não gera linha de log de agenda', achou('agenda').length === 0)

  ok.forEach((n) => console.log('  ok   ' + n))
  bad.forEach((n) => console.log('  FALHOU  ' + n))
  console.log(`\n${ok.length} ok, ${bad.length} falhou`)
  await pool.end()
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error('ERRO NO TESTE:', e); process.exit(1) })
