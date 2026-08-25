'use strict'
// Teste do conserto automático de sessão podre (code 500 = badSession).
//
// O buraco que este teste fecha: o handler de 'close' do server.js tratava 440
// (connectionReplaced) e 401 (loggedOut) e mandava TODO o resto pro religa-genérico.
// O 500 é o WhatsApp dizendo, com todas as letras, que os registros de sessão do
// libsignal não servem mais — e religar com os MESMOS registros só recompra o 500.
//
// Medido na conta 23 (Rawilson) em 24–25/08/2026:
//
//     24 fechamentos com code 500 em 24h   (a 34 teve 6, a 36 teve 2)
//     2.242 'Bad MAC' por hora             (100% do ciframento que entrava falhando)
//     ZERO messages.upsert em 4 dias       (entrada morta; o ENVIO funcionava)
//     entrada por dia: 235 → 76 → 62 → 0   (três dias até morrer de vez)
//
// Apagar à mão os 187 'session-*' e religar levou o Bad MAC a 0/h e os fechamentos
// a 0. É esse conserto que o limparSessoesSignal automatiza.
//
// O que o teste protege, que é onde isso vira desastre se quebrar:
//
//   - apaga SÓ 'session-*'. Se um dia pegar 'creds' junto, o cliente perde o
//     pareamento e tem que pegar o celular — exatamente o que a regra 1 do
//     CLAUDE.md proíbe, e o que custou a manhã de 22/08;
//   - pre-key sobrevive: é DELE que o libsignal reconstrói a sessão. Apagar
//     junto deixaria a conta sem como refazer sessão nenhuma;
//   - tem trava de frequência. Num flapping de 500 o religamento vem de 2,5 em
//     2,5s; sem a trava a gente apagaria as sessões a cada volta, e cada limpeza
//     obriga TODOS os contatos a refazer sessão de uma vez — cada refazimento
//     come um pre-key do servidor. O conserto viraria a tempestade;
//   - falha de banco NÃO sobe: sem a limpeza a reconexão volta ao comportamento
//     antigo (ruim), mas com exceção aqui ela não acontece (pior);
//   - o 500 continua sendo 500 no Baileys (trava contra upgrade da lib);
//   - o handler chama a limpeza ANTES do setTimeout que religa — o
//     makeCacheableSignalKeyStore guarda chave por 5min em memória, então limpar
//     com o socket de pé não adianta.
//
// Precisa de Postgres (apaga de verdade). Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_badsession_test
//     WA_BADSESSION_TEST_URL=postgresql://postgres@localhost:5432/wa_badsession_test \
//       node teste-badsession.js

const fs = require('fs')
const path = require('path')
const { Pool } = require('pg')
const { DisconnectReason } = require('@whiskeysockets/baileys')

const URL = process.env.WA_BADSESSION_TEST_URL
if (!URL) {
  console.error('Falta WA_BADSESSION_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}

const CONTA = 23
const OUTRA = 34
const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)

process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = 'teste'
// janela curta o suficiente pra o teste esperar de verdade sem virar teste lento
process.env.WA_QR_LIMPAR_SESSAO_ESPERA_MS = '400'

const pool = new Pool({ connectionString: URL })

// o cofre de uma conta como ele é em produção: creds + as chaves todas
async function semear (contaId) {
  await pool.query('delete from wa_qr_auth where conta_id=$1', [contaId])
  const linhas = [['creds', '{"registered":false,"nextPreKeyId":2057}']]
  for (let i = 1; i <= 5; i++) linhas.push(['session-558' + i + '@s.whatsapp.net', '{"s":' + i + '}'])
  for (let i = 1; i <= 4; i++) linhas.push(['pre-key-' + i, '{"p":' + i + '}'])
  linhas.push(['sender-key-120363@g.us::1', '{"sk":1}'])
  linhas.push(['app-state-sync-key-AAAA', '{"a":1}'])
  linhas.push(['app-state-sync-version-regular', '{"v":1}'])
  linhas.push(['lidmap-35626837647552', '"558681121733"'])
  linhas.push(['agenda-completa', '"1"'])
  for (const [arquivo, conteudo] of linhas) {
    await pool.query(
      'insert into wa_qr_auth (conta_id, arquivo, conteudo) values ($1,$2,$3)',
      [contaId, arquivo, conteudo])
  }
}

const contar = async (contaId, like) => (await pool.query(
  'select count(*)::int n from wa_qr_auth where conta_id=$1 and arquivo like $2',
  [contaId, like])).rows[0].n

;(async () => {
  await pool.query('drop table if exists wa_qr_auth cascade')
  await pool.query(`create table wa_qr_auth (
      conta_id bigint not null,
      arquivo text not null,
      conteudo text not null,
      atualizado timestamptz not null default now(),
      primary key (conta_id, arquivo))`)
  await pool.query('drop table if exists wa_qr_log cascade')
  await pool.query(`create table wa_qr_log (
      id bigserial primary key, conta_id bigint, nivel text, msg text,
      dados jsonb, criado_em timestamptz not null default now())`)

  const srv = require('./server.js')
  const { limparSessoesSignal, ultimaLimpezaDeSessao, LIMPAR_SESSAO_ESPERA_MS } = srv

  // ── o constante do Baileys ────────────────────────────────────────────────
  t('badSession é 500 no Baileys instalado', DisconnectReason.badSession === 500)
  t('500 não colide com os códigos já tratados',
    DisconnectReason.badSession !== DisconnectReason.loggedOut &&
    DisconnectReason.badSession !== DisconnectReason.connectionReplaced)

  // ── o caminho feliz: apaga sessão, preserva o resto ───────────────────────
  await semear(CONTA)
  t('semeou 5 sessões', (await contar(CONTA, 'session-%')) === 5)
  const n = await limparSessoesSignal(CONTA, 'teste')
  t('devolveu quantas apagou', n === 5)
  t('sessões foram embora', (await contar(CONTA, 'session-%')) === 0)

  // o coração do teste: o que NÃO pode ter sido tocado
  t('CREDS PRESERVADA — sem isso o cliente repareia', (await contar(CONTA, 'creds')) === 1)
  t('pre-key preservada — é dela que a sessão renasce', (await contar(CONTA, 'pre-key-%')) === 4)
  t('sender-key preservada', (await contar(CONTA, 'sender-key-%')) === 1)
  t('app-state preservado', (await contar(CONTA, 'app-state-%')) === 2)
  t('lidmap preservado', (await contar(CONTA, 'lidmap-%')) === 1)
  t('chave da agenda preservada', (await contar(CONTA, 'agenda-%')) === 1)
  // 15 semeadas − 5 sessões = 10. Conferir o TOTAL pega o que as linhas acima não
  // pegariam: uma chave de tipo novo levada junto sem ninguém notar.
  t('só as 5 sessões sumiram do cofre — nada mais', (await contar(CONTA, '%')) === 10)

  // ── a trava de frequência ────────────────────────────────────────────────
  await semear(CONTA)
  const segunda = await limparSessoesSignal(CONTA, 'teste-repetido')
  t('segunda chamada na janela NÃO apaga (senão o flapping vira tempestade)',
    segunda === 0 && (await contar(CONTA, 'session-%')) === 5)

  await new Promise((r) => setTimeout(r, LIMPAR_SESSAO_ESPERA_MS + 60))
  const terceira = await limparSessoesSignal(CONTA, 'teste-depois-da-janela')
  t('passada a janela, volta a apagar', terceira === 5)

  // ── a trava é POR CONTA ──────────────────────────────────────────────────
  await semear(CONTA); await semear(OUTRA)
  await limparSessoesSignal(CONTA, 'teste')
  const naOutra = await limparSessoesSignal(OUTRA, 'teste')
  t('a espera de uma conta não segura a outra',
    naOutra === 5 && (await contar(OUTRA, 'session-%')) === 0)
  t('limpar uma conta não encosta no cofre da outra',
    (await contar(OUTRA, 'creds')) === 1 && (await contar(CONTA, 'creds')) === 1)

  // ── falha de banco não pode subir ────────────────────────────────────────
  ultimaLimpezaDeSessao.clear()
  await pool.query('alter table wa_qr_auth rename to wa_qr_auth_escondida')
  let subiu = false
  let r = null
  try { r = await limparSessoesSignal(CONTA, 'teste-sem-tabela') } catch (_) { subiu = true }
  t('erro de banco NÃO sobe (a reconexão tem que seguir)', !subiu && r === 0)
  await pool.query('alter table wa_qr_auth_escondida rename to wa_qr_auth')

  // ── trava de leitura do fonte: o handler tem que chamar isso ─────────────
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
  t('o close handler trata badSession',
    /if \(code === DisconnectReason\.badSession\)\s*\{\s*await limparSessoesSignal/.test(src))
  const iBad = src.indexOf('code === DisconnectReason.badSession')
  const iRelig = src.indexOf('iniciarSessao(contaId).catch', iBad)
  t('a limpeza vem ANTES do religamento (o cache do store é por socket)',
    iBad > 0 && iRelig > iBad)
  t('a limpeza mira só session- (nunca creds)',
    /delete from wa_qr_auth where conta_id=\$1 and arquivo like 'session-%'/.test(src))
  t('limparTudo (que apaga o cofre) continua só no caminho de logout',
    src.indexOf('await limparTudo()') > 0 &&
    /deslogado[\s\S]{0,2000}await limparTudo\(\)/.test(src))

  console.log(ok.length + ' ok')
  for (const b of bad) console.log('FALHOU: ' + b)
  await pool.end()
  await srv.pool.end().catch(() => {})
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
