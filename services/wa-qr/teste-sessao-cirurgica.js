'use strict'
// A limpeza de sessão virou CIRÚRGICA: apaga a sessão de UM contato, não de todos.
//
// ── por que ──────────────────────────────────────────────────────────────────
//
// Em 24/08/2026 a conta 23 (Rawilson) estava com 100% do ciframento de entrada
// falhando. Apagar à mão os 187 'session-*' e religar consertou. Em 26/08 eu
// automatizei isso no code 500 (badSession) — e o dado de produção de 02/09 diz
// que a automação foi um erro:
//
//   26/08 10:47Z  a limpeza automática vai ao ar
//   26/08 11:18Z  o Rawilson pareia a conta 23 (o TERCEIRO pareamento)
//   26/08 14:35Z  última mensagem. Durou 3h18.
//   02/09         sete dias de silêncio; 65 conexões e ZERO messages.upsert em 48h;
//                 a limpeza rodou 27 vezes, mais 20 barradas pela trava, sem trazer
//                 a conta de volta.
//
// O erro é de CATEGORIA. O 500 é evento de CONEXÃO — o WhatsApp diz que a sessão do
// stream não presta. Ele não diz, e não tem como dizer, que a sessão com CADA
// interlocutor está podre. Apagar todas por causa dele rasga o ratchet de centenas
// de conversas de uma vez; a conta 23 tem 153 sender-keys, é cheia de grupo. E quem
// está do outro lado não fica sabendo: continua cifrando com o ratchet velho.
//
// O sinal que aponta um interlocutor de verdade é a falha de decifragem — ela vem
// com o remoteJid. E o gatilho é prova, não palpite: só depois de o Baileys pedir
// reenvio MAX_RETRY_DECIFRAR vezes e as três falharem. Aí sim aquela sessão
// específica não serve, e é a única que se apaga.
//
// ── o que este teste protege ─────────────────────────────────────────────────
//
//   - apaga SÓ a família de dispositivos daquele contato. A sessão do vizinho,
//     que está conversando bem, não pode ser tocada — é a diferença inteira entre
//     esta função e a que ela substituiu;
//   - creds, pre-key, sender-key, app-state e lidmap sobrevivem, igual antes:
//     sem creds o cliente teria que pegar o celular, que é o que a regra 1 do
//     CLAUDE.md proíbe;
//   - o LIKE não pode virar limpeza geral. O nome do registro é montado por
//     concatenação, então um jid com '%' ou '_' apagaria sessão dos outros —
//     por isso o usuário é validado como só-dígitos ANTES de chegar no SQL;
//   - trava de frequência POR CONTATO, não por conta: um contato quebrado não
//     pode consumir a cota de limpeza dos outros;
//   - conta vizinha nunca é tocada;
//   - falha de banco não sobe: quem chama é o logger, no meio do processamento
//     da mensagem, e uma exceção ali derrubaria a entrega.
//
// Precisa de Postgres (apaga de verdade). Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_cirurgica_test
//     WA_CIRURGICA_TEST_URL=postgresql://postgres@localhost:5432/wa_cirurgica_test \
//       node teste-sessao-cirurgica.js

const fs = require('fs')
const path = require('path')
const { Pool } = require('pg')

const URL = process.env.WA_CIRURGICA_TEST_URL
if (!URL) {
  console.error('Falta WA_CIRURGICA_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}

const CONTA = 23
const OUTRA = 34
const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)

process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = 'teste'
process.env.WA_QR_LIMPAR_SESSAO_ESPERA_MS = '400'

const pool = new Pool({ connectionString: URL })

// O contato que quebrou e dois vizinhos saudáveis — é a vizinhança que importa aqui.
const QUEBRADO = '101378441629922'          // o @lid real da conta 23 em 31/08
const VIZINHO = '558681885930'
const OUTRO = '558694095516'

async function semear (contaId) {
  await pool.query('delete from wa_qr_auth where conta_id=$1', [contaId])
  const linhas = [['creds', '{"registered":true}']]
  // o contato quebrado, com dois dispositivos (é assim que o WhatsApp guarda)
  linhas.push(['session-' + QUEBRADO + '.0', '{"q":0}'])
  linhas.push(['session-' + QUEBRADO + '.33', '{"q":33}'])
  // os vizinhos, que têm que sobreviver
  linhas.push(['session-' + VIZINHO + '.0', '{"v":0}'])
  linhas.push(['session-' + VIZINHO + '.2', '{"v":2}'])
  linhas.push(['session-' + OUTRO + '.0', '{"o":0}'])
  // e o resto do cofre
  for (let i = 1; i <= 4; i++) linhas.push(['pre-key-' + i, '{"p":' + i + '}'])
  linhas.push(['sender-key-120363@g.us::1', '{"sk":1}'])
  linhas.push(['app-state-sync-key-AAAA', '{"a":1}'])
  linhas.push(['lidmap-' + QUEBRADO, '"558681121733"'])
  for (const [arquivo, conteudo] of linhas) {
    await pool.query(
      'insert into wa_qr_auth (conta_id, arquivo, conteudo) values ($1,$2,$3)',
      [contaId, arquivo, conteudo])
  }
}

async function tem (contaId, arquivo) {
  const r = await pool.query(
    'select 1 from wa_qr_auth where conta_id=$1 and arquivo=$2', [contaId, arquivo])
  return r.rowCount === 1
}

async function contar (contaId, padrao) {
  const r = await pool.query(
    'select count(*)::int n from wa_qr_auth where conta_id=$1 and arquivo like $2',
    [contaId, padrao])
  return r.rows[0].n
}

const espera = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  await pool.query(`create table if not exists wa_qr_auth (
      conta_id bigint not null, arquivo text not null, conteudo text not null,
      atualizado timestamptz not null default now(),
      primary key (conta_id, arquivo))`)

  const srv = require('./server.js')
  const { limparSessaoDoPeer, usuarioDoJid, ultimaLimpezaDePeer, LIMPAR_TUDO_NO_500 } = srv

  // ── o jid vira usuário ────────────────────────────────────────────────────
  t('lê o @lid', usuarioDoJid('101378441629922@lid') === '101378441629922')
  t('lê o número comum', usuarioDoJid('558681885930@s.whatsapp.net') === '558681885930')
  t('descarta o dispositivo do jid', usuarioDoJid('558681885930:33@s.whatsapp.net') === '558681885930')
  // O NÚMERO DO GRUPO TAMBÉM É SÓ DÍGITO, então a validação de dígitos não basta —
  // sem o corte por sufixo a limpeza miraria 'session-<id do grupo>.%', não
  // consertaria contato nenhum e ainda pareceria ter agido.
  t('grupo não tem sessão de contato', usuarioDoJid('120363@g.us') === '')
  t('lista de transmissão e canal também ficam de fora',
    usuarioDoJid('123@broadcast') === '' && usuarioDoJid('123@newsletter') === '')
  t('vazio não vira nada', usuarioDoJid('') === '' && usuarioDoJid(null) === '' &&
    usuarioDoJid(undefined) === '')
  // O QUE MAIS IMPORTA DESTE BLOCO: sem a validação, um '%' no jid faria o LIKE
  // apagar a sessão de todo mundo — a limpeza cirúrgica viraria a geral, calada.
  t('curinga do LIKE não passa', usuarioDoJid('%@s.whatsapp.net') === '' &&
    usuarioDoJid('_@lid') === '' && usuarioDoJid('55%86@s.whatsapp.net') === '')

  // ── o corte ───────────────────────────────────────────────────────────────
  await semear(CONTA)
  await semear(OUTRA)
  const n = await limparSessaoDoPeer(CONTA, QUEBRADO + '@lid', 'teste')
  t('apagou os dois dispositivos do contato quebrado', n === 2)
  t('a sessão do quebrado foi embora',
    !(await tem(CONTA, 'session-' + QUEBRADO + '.0')) &&
    !(await tem(CONTA, 'session-' + QUEBRADO + '.33')))

  // ── e a vizinhança ────────────────────────────────────────────────────────
  t('o vizinho continua inteiro',
    (await tem(CONTA, 'session-' + VIZINHO + '.0')) &&
    (await tem(CONTA, 'session-' + VIZINHO + '.2')))
  t('o outro contato também', await tem(CONTA, 'session-' + OUTRO + '.0'))
  t('sobraram 3 sessões (era 5)', (await contar(CONTA, 'session-%')) === 3)

  // ── o cofre ───────────────────────────────────────────────────────────────
  t('creds intacta (ninguém pega o celular)', await tem(CONTA, 'creds'))
  t('pre-key intacta (é dela que a sessão renasce)', (await contar(CONTA, 'pre-key-%')) === 4)
  t('sender-key intacta', await tem(CONTA, 'sender-key-120363@g.us::1'))
  t('app-state intacta', await tem(CONTA, 'app-state-sync-key-AAAA'))
  t('lidmap intacto', await tem(CONTA, 'lidmap-' + QUEBRADO))

  // ── a conta vizinha ───────────────────────────────────────────────────────
  t('a outra conta não foi tocada', (await contar(OUTRA, 'session-%')) === 5)

  // ── a trava é POR CONTATO ─────────────────────────────────────────────────
  await semear(CONTA)
  ultimaLimpezaDePeer.clear()
  await limparSessaoDoPeer(CONTA, QUEBRADO + '@lid', 'primeira')
  const repetida = await limparSessaoDoPeer(CONTA, QUEBRADO + '@lid', 'de novo')
  t('o mesmo contato não é limpo duas vezes seguidas', repetida === 0)
  // ...e o vizinho não paga por isso: um contato quebrado não pode consumir a cota
  // dos outros, que é o que aconteceria com uma trava por CONTA
  const doVizinho = await limparSessaoDoPeer(CONTA, VIZINHO + '@s.whatsapp.net', 'outro')
  t('outro contato pode ser limpo na mesma janela', doVizinho === 2)
  await espera(450)
  await semear(CONTA)
  const depois = await limparSessaoDoPeer(CONTA, QUEBRADO + '@lid', 'passou a janela')
  t('passada a janela, o mesmo contato pode de novo', depois === 2)

  // ── jid que não dá contato ────────────────────────────────────────────────
  await semear(CONTA)
  const grupo = await limparSessaoDoPeer(CONTA, '120363@g.us', 'grupo')
  t('grupo não apaga sessão nenhuma',
    grupo === 0 && (await contar(CONTA, 'session-%')) === 5)
  const vazio = await limparSessaoDoPeer(CONTA, null, 'sem jid')
  t('jid vazio não apaga nada',
    vazio === 0 && (await contar(CONTA, 'session-%')) === 5)

  // ── falha de banco não sobe ───────────────────────────────────────────────
  await pool.query('alter table wa_qr_auth rename to wa_qr_auth_escondida')
  let subiu = false
  let r = null
  ultimaLimpezaDePeer.clear()
  try { r = await limparSessaoDoPeer(CONTA, QUEBRADO + '@lid', 'sem tabela') } catch (_) { subiu = true }
  t('erro de banco NÃO sobe (quem chama é o logger da entrega)', !subiu && r === 0)
  await pool.query('alter table wa_qr_auth_escondida rename to wa_qr_auth')

  // ── travas de leitura do fonte ────────────────────────────────────────────
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
  t('o gatilho é o esgotamento do retry, não o 500',
    /if \(!aindaTemRetry\) \{[\s\S]{0,900}limparSessaoDoPeer\(contaId,/.test(src))
  // Em grupo o remoteJid é o GRUPO e quem não decifra é o membro. Mirar o grupo
  // não consertaria ninguém — e o teste do usuarioDoJid acima sozinho não pegaria
  // isso, porque lá o corte só devolve ''.
  t('em grupo mira o participant, não o grupo',
    /obj\.key\.participant \|\| obj\.key\.remoteJid/.test(src))
  t('o 500 não apaga mais tudo sozinho',
    !/if \(code === DisconnectReason\.badSession\)\s*\{\s*await limparSessoesSignal/.test(src))
  t('a limpeza geral está desligada por padrão', LIMPAR_TUDO_NO_500 === false)
  t('o SQL cirúrgico mira o contato, não o like solto',
    /arquivo like 'session-' \|\| \$2 \|\| '\.%'/.test(src))
  t('o usuário é validado como só-dígitos antes do SQL',
    /\/\^\[0-9\]\+\$\/\.test\(u\)/.test(src))
  t('o disjuntor continua contando (a limpeza não substitui o aviso)',
    /contarFalhaDeDecifrar\(contaId, agora, DECIFRAR_TETO, DECIFRAR_JANELA_MS\)\) \{[\s\S]{0,200}abrirDisjuntor/.test(src))

  console.log(ok.length + ' ok')
  for (const b of bad) console.log('FALHOU: ' + b)
  await pool.end()
  await srv.pool.end().catch(() => {})
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
