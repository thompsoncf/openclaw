'use strict'
// O CONTATO QUE NÃO TEM SESSÃO NENHUMA — e por que prendê-lo é o oposto do conserto.
//
// ── o que foi medido (conta 38, chip da Liberal, 16–18/09/2026) ──────────────
//
// Em 48h, com a conta 34 do lado como controle e o mesmo volume de tráfego:
//
//   conta 34 (chip antigo) ... 172 entradas,  1 id falhou,  1 não chegou  (0,6%)
//   conta 38 (chip novo) ..... 182 entradas, 27 ids falharam, 13 não chegaram (7,1%)
//
// Treze mensagens de cliente que ninguém da corretora viu, na conversa mais
// movimentada dela (538 mensagens desde 17/08, viva no mesmo dia). E o log já
// dizia exatamente o que estava acontecendo:
//
//   13x  'sessão deste contato não decifra, mas não há sessão gravada pra apagar'
//   24x  'quarentena: este contato não decifra e não houve sessão pra refazer'
//    3x  'badSession(500): religando sem apagar sessão'   (16/09, 11h/12h/13h)
//
// ── o defeito, que é de ORDENAÇÃO e não de @lid ──────────────────────────────
//
// A primeira leitura foi "o tratamento de @lid está furado". Está certo: o
// `usuariosDoPeer` junta lid e número desde 08/09, e no caso medido o lid ESTAVA
// no mapa, apontando pro número certo. O furo é outro.
//
// `limparSessaoDoPeer` devolvia `0` para duas coisas diferentes:
//
//   a) a trava de 1h ainda está de molho          → é enxurrada, prender está certo
//   b) NÃO EXISTE sessão gravada pra apagar       → prender é o oposto do conserto
//
// e quem chama tratava as duas igual: quarentena de 30 min, que descarta o contato
// ANTES do decryptMessageNode. No caso (b) o que curaria é justamente a próxima
// mensagem dele — um PreKeyWhisperMessage reconstrói a sessão e decifra. A
// quarentena joga fora, sem abrir, a única coisa que consertaria.
//
// O comentário do autor da quarentena aceita o custo ("ela JÁ está sendo perdida —
// o que não decifra não vira mensagem em lugar nenhum"). Isso é verdade para um
// contato com sessão QUEBRADA. Para um contato SEM sessão, não é: ali a mensagem
// seguinte não está perdida, está sendo recusada na porta.
//
// ── a regra nova, e o que ela NÃO afrouxa ────────────────────────────────────
//
// Um perdão só, por contato, por janela: na primeira vez que a limpeza disser
// "não havia sessão", o contato passa, pra o prekey ter chance. Se ele voltar sem
// sessão na mesma janela, entra em quarentena como antes.
//
// Isso preserva a trava de 07/09 (conta 34, dois contatos pararam o serviço três
// vezes em três minutos, event loop travado 20,7s/23,2s/40,6s): naquele caso os
// contatos TINHAM sessão — a limpeza apagou três — e além disso uma enxurrada
// tropeça no segundo passe em segundos. Um perdão não segura enxurrada nenhuma.
//
// Precisa de Postgres (o SEM_SESSAO só se prova com a tabela vazia de sessões):
//
//     cd services/wa-qr && npm install
//     createdb wa_semreg_test
//     WA_SEMREG_TEST_URL=postgresql://postgres@localhost:5432/wa_semreg_test \
//       node teste-sessao-sem-registro.js

const fs = require('fs')
const path = require('path')
const { Pool } = require('pg')

const URL = process.env.WA_SEMREG_TEST_URL
if (!URL) {
  console.error('Falta WA_SEMREG_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}

process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'
process.env.WA_QR_LIMPAR_SESSAO_ESPERA_MS = '400'

const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)

const pool = new Pool({ connectionString: URL })

const CONTA = 38
// o par real medido em produção, com o número mascarado: o lid ESTÁ no mapa
const LID = '537307000000000'
const NUM = '558694000000'
// um contato com sessão de verdade, pro caso (a) continuar valendo
const COM_SESSAO = '558681885930'

async function semear ({ comSessaoDoAlvo }) {
  await pool.query('delete from wa_qr_auth where conta_id=$1', [CONTA])
  const linhas = [
    ['creds', '{"registered":true}'],
    ['lidmap-' + LID, '"' + NUM + '@s.whatsapp.net"'],
    ['session-' + COM_SESSAO + '.0', '{"v":0}'],
    ['session-' + COM_SESSAO + '.33', '{"v":33}']
  ]
  if (comSessaoDoAlvo) linhas.push(['session-' + NUM + '.0', '{"a":0}'])
  for (const [arquivo, conteudo] of linhas) {
    await pool.query(
      'insert into wa_qr_auth (conta_id, arquivo, conteudo) values ($1,$2,$3)',
      [CONTA, arquivo, conteudo])
  }
}

;(async () => {
  await pool.query(`create table if not exists wa_qr_auth (
      conta_id bigint not null, arquivo text not null, conteudo text not null,
      atualizado timestamptz not null default now(),
      primary key (conta_id, arquivo))`)

  const srv = require('./server.js')
  const {
    limparSessaoDoPeer, decidirAposLimpeza, SEM_SESSAO, usuariosDoPeer,
    ultimaLimpezaDePeer, lidMaps, peersEmQuarentena, peersSemSessao,
    QUARENTENA_PEER_MS
  } = srv

  const T0 = 1758000000000
  const limpar = () => {
    ultimaLimpezaDePeer.clear()
    peersEmQuarentena.clear()
    if (peersSemSessao) peersSemSessao.clear()
  }

  // ── 1. os dois zeros deixam de ser o mesmo zero ───────────────────────────
  console.log('\nA limpeza distingue "não havia sessão" de "a trava está de molho":')
  t('SEM_SESSAO existe e não se confunde com 0 nem com contagem de apagadas',
    typeof SEM_SESSAO === 'number' && SEM_SESSAO < 0)

  // o mapa lid->número precisa estar carregado, senão o teste mediria outra coisa.
  // Semeado à mão de propósito: quem carrega do banco é interno ao arranque da
  // sessão, e amarrar o teste nele mediria o arranque, não a decisão.
  lidMaps.set(CONTA, new Map([[LID + '@lid', NUM + '@s.whatsapp.net']]))
  await semear({ comSessaoDoAlvo: false })
  limpar()
  t('o lid do alvo está no mapa (senão o teste não é sobre o que eu quero)',
    usuariosDoPeer(CONTA, LID + '@lid').includes(NUM))

  const semReg = await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  t('contato sem sessão nenhuma devolve SEM_SESSAO, não 0', semReg === SEM_SESSAO)

  await semear({ comSessaoDoAlvo: true })
  limpar()
  const apagou = await limparSessaoDoPeer(CONTA, LID + '@lid', 'retry esgotado')
  t('contato COM sessão continua devolvendo a contagem', apagou === 1)
  const demolho = await limparSessaoDoPeer(CONTA, LID + '@lid', 'de novo')
  t('a trava de frequência continua devolvendo 0 (e não SEM_SESSAO)', demolho === 0)

  // ── 2. a decisão que vem depois ───────────────────────────────────────────
  console.log('\nO que se faz com cada resposta:')
  limpar()
  t('curou (apagou sessão) → não prende, igual a 08/09',
    decidirAposLimpeza(CONTA, LID + '@lid', 2, T0) === 'curou')
  limpar()
  t('não havia sessão, 1ª vez → deixa passar pro prekey reconstruir',
    decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0) === 'espera-prekey')
  t('...e o contato NÃO fica calado nessa hora',
    srv.deveIgnorarNoBaileys(LID + '@lid', CONTA, T0 + 1) === false)
  t('não havia sessão, 2ª vez na janela → aí sim prende',
    decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0 + 1000) === 'quarentena')
  t('...e agora está calado de verdade',
    srv.deveIgnorarNoBaileys(LID + '@lid', CONTA, T0 + 1001) === true)

  limpar()
  t('trava de molho (0) prende na primeira — é o caso da enxurrada de 07/09',
    decidirAposLimpeza(CONTA, LID + '@lid', 0, T0) === 'quarentena')

  // ── 3. o perdão é POR CONTATO e POR CONTA, e vence ────────────────────────
  console.log('\nO perdão é estreito:')
  limpar()
  decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0)
  t('o perdão de um contato não vale pro vizinho',
    decidirAposLimpeza(CONTA, COM_SESSAO + '@s.whatsapp.net', SEM_SESSAO, T0) === 'espera-prekey')
  t('nem pra mesma pessoa noutra conta',
    decidirAposLimpeza(99, LID + '@lid', SEM_SESSAO, T0) === 'espera-prekey')
  // o mesmo contato pelo NÚMERO é o mesmo contato: senão alternar lid/número
  // renovaria o perdão pra sempre e a enxurrada nunca seria contida
  t('lid e número são o MESMO contato pro perdão',
    decidirAposLimpeza(CONTA, NUM + '@s.whatsapp.net', SEM_SESSAO, T0 + 10) === 'quarentena')

  limpar()
  decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0)
  t('passada a janela, ganha outro perdão (a sessão pode ter sido refeita no meio)',
    decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0 + QUARENTENA_PEER_MS + 1) === 'espera-prekey')

  // ── 3b. o mapa do perdão não pode só crescer ──────────────────────────────
  console.log('\nO perdão não vaza memória:')
  limpar()
  // dez contatos pegam o perdão e somem pra sempre — é o caso comum, não o raro
  for (let i = 0; i < 10; i++) {
    decidirAposLimpeza(CONTA, '5586000000' + i + '@s.whatsapp.net', SEM_SESSAO, T0)
  }
  t('as entradas ficam enquanto o perdão vale', peersSemSessao.size === 10)
  decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0 + QUARENTENA_PEER_MS + 1)
  t('vencidas, somem sozinhas na decisão seguinte — mapa que só cresce é vazamento',
    peersSemSessao.size === 1)

  limpar()
  decidirAposLimpeza(CONTA, LID + '@lid', SEM_SESSAO, T0)
  decidirAposLimpeza(99, LID + '@lid', SEM_SESSAO, T0)
  srv.esquecerQuarentena(CONTA)
  t('conta esquecida leva o perdão dela junto', peersSemSessao.size === 1)
  t('...e não leva o da vizinha', peersSemSessao.has('99:' + LID))

  // ── 4. travas de leitura do fonte ─────────────────────────────────────────
  console.log('\nO que não pode voltar:')
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
  t('o branch "não há sessão gravada" devolve SEM_SESSAO, não 0',
    /não há sessão gravada pra apagar[\s\S]{0,500}return SEM_SESSAO/.test(src))
  t('quem chama decide pelo decidirAposLimpeza, e não por `apagadas > 0` na mão',
    /decidirAposLimpeza\(contaId, peer,/.test(src))
  // a quarentena mudou de LUGAR (saiu do .then, foi pro decisor), não de existência
  t('a quarentena continua existindo pro caso da enxurrada',
    /function decidirAposLimpeza[\s\S]{0,1600}porPeerEmQuarentena\(contaId, jid, agora, QUARENTENA_PEER_MS\)/.test(src))
  t('e o caminho "enxurrada insistindo" continua sendo o padrão de quem não curou',
    /return 'quarentena'\s*\n\}/.test(src))
  t('o disjuntor continua contando fora dessa decisão',
    /contarFalhaDeDecifrar\(contaId, agora, DECIFRAR_TETO, DECIFRAR_JANELA_MS\)\) \{[\s\S]{0,200}abrirDisjuntor/.test(src))

  console.log('\n' + ok.length + ' ok')
  for (const b of bad) console.log('FALHOU: ' + b)
  await pool.end()
  await srv.pool.end().catch(() => {})
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
