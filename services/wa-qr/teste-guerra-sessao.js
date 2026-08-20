'use strict'
// A guerra de sessão: o disjuntor da enxurrada de decifragem e a espera que
// sobrevive ao restart.
//
// O CASO REAL (20/08/2026, serviço zaq-waqr no Render). Às 13:16 a conta 34 foi
// substituída por outra sessão pela enésima vez no dia. A partir dali cada eco de
// mensagem chegou indecifrável: 1119 'failed to decrypt message' numa hora, contra
// 50-150 num dia inteiro normal. Cada falha faz o Baileys pedir reenvio, o WhatsApp
// reentregar e falhar de novo — trabalho de criptografia em rajada num contêiner de
// 1 CPU. O event loop travou por 25, 34, 40, 66, 71 e 73 SEGUNDOS. Como o /saude do
// health check do Render desiste em 5s, a instância foi morta e reiniciada SETE
// vezes na mesma hora.
//
// E aqui está o que este arquivo tranca. A defesa contra guerra de sessão já
// existia e estava certa: depois de um 440 a retomada espera 5, 10, 20, 40, 80
// minutos (esperaPos440). Só que o contador vivia na MEMÓRIA do processo. Cada
// morte zerava a espera, e o restaurarSessoes religava a conta na hora, pegava o
// mesmo lote indecifrável e recomeçava o ciclo — quanto pior a briga, mais rápido a
// gente voltava pra ela. A proteção era desarmada justamente pelo reinício que a
// própria briga provocava.
//
// Duas travas, então:
//
//   1. o disjuntor: enxurrada de falhas ao decifrar = parar esta conta, sem apagar
//      nada, e entrar na mesma espera do 440. O 440 nem sempre chega pra avisar —
//      dá pra ficar com o socket de pé sem conseguir decifrar coisa alguma;
//   2. a espera GRAVADA (migração 182): um processo novo tem que saber que a conta
//      está de castigo. É a regressão do incidente, e é o `restart` aqui embaixo.
//
// Precisa de Postgres (a espera é gravada de verdade). Banco descartável, nunca
// produção — este arquivo cria as tabelas de que precisa:
//
//     cd services/wa-qr && npm install
//     createdb wa_qr_test
//     WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-guerra-sessao.js

const URL = process.env.WA_QR_TEST_URL
if (!URL) {
  console.error('Falta WA_QR_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}
process.env.DATABASE_URL = URL
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = 'silent'
// o arranque espaça as contas em 30s de propósito (ver ESPACO_CONTAS_MS); aqui
// isso seria só espera morta multiplicada por cada cenário
process.env.WA_QR_ESPACO_CONTAS_MS = '0'

const s = require('./server')
const { contarFalhaDeDecifrar, abrirDisjuntor, falhasDeDecifrar, backoffGravado,
  restaurarSessoes, registrarSessoes, esperaPos440, sessoes, _ganchos,
  ESPERA_POS_440_MS } = s

let falhas = 0
function conferir (ok, descricao, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}

const CONTA = 34
const VIZINHA = 23
const MIN = 60 * 1000
const T0 = 1_700_000_000_000
const TETO = 60
const JANELA = 60 * 1000

async function prepararBanco () {
  await s.pool.query(`
    create table if not exists wa_qr_sessao_estado (
      conta_id bigint primary key, status text not null default '',
      ultimo_evento timestamptz, mudo_s bigint,
      religamentos integer not null default 0, detalhe jsonb,
      atualizado timestamptz not null default now());
    create table if not exists wa_qr_auth (
      conta_id bigint not null, arquivo text not null, conteudo text not null,
      primary key (conta_id, arquivo));`)
  // a própria migração 182, aplicada como em produção
  await s.pool.query(`
    alter table wa_qr_sessao_estado
      add column if not exists substituida_em timestamptz,
      add column if not exists tentativas_440 integer not null default 0;`)
}

async function limpar () {
  s._logFila.length = 0
  falhasDeDecifrar.clear()
  sessoes.clear()
  await s.pool.query('delete from wa_qr_sessao_estado')
  await s.pool.query('delete from wa_qr_auth')
}

// conta pareada aos olhos do restaurarSessoes: creds com `me` preenchido
async function parear (contaId) {
  await s.pool.query(
    `insert into wa_qr_auth (conta_id, arquivo, conteudo) values ($1,'creds',$2)
     on conflict (conta_id, arquivo) do update set conteudo=excluded.conteudo`,
    [contaId, JSON.stringify({ me: { id: '55869@s.whatsapp.net' } })])
}

// substitui o iniciarSessao (que abriria socket de verdade e falaria com o
// WhatsApp) por um espião: o que interessa é QUEM foi religado, não como
function espiaoDeReligamento () {
  const religadas = []
  const original = _ganchos.iniciarSessao
  _ganchos.iniciarSessao = async (contaId) => { religadas.push(String(contaId)) }
  return { religadas, desfazer: () => { _ganchos.iniciarSessao = original } }
}

;(async () => {
  await prepararBanco()

  // ── o contador: aritmética pura, sem socket ────────────────────────────────
  console.log('\ncontador da enxurrada (contarFalhaDeDecifrar)')
  falhasDeDecifrar.clear()
  let disparou = 0
  for (let i = 1; i < TETO; i++) {
    if (contarFalhaDeDecifrar(CONTA, T0 + i, TETO, JANELA)) disparou++
  }
  conferir(disparou === 0, 'abaixo do teto não dispara (' + (TETO - 1) + ' falhas)')
  conferir(contarFalhaDeDecifrar(CONTA, T0 + TETO, TETO, JANELA) === true,
    'a falha que completa o teto dispara')

  // O teto separa DOIS ritmos medidos em produção, e é essa distância que ele
  // precisa respeitar: o dia inteiro de 19/08, com o serviço saudável, deu 50 a
  // 150 falhas por HORA somando todas as contas (~2/min); a hora do incidente deu
  // 1119 numa conta só (~80/min). O teto de 60/min fica no meio, muito longe do
  // ruído normal — punir conta saudável é o erro caro aqui, porque o castigo custa
  // minutos de WhatsApp parado.
  falhasDeDecifrar.clear()
  disparou = 0
  for (let i = 0; i < 600; i++) {            // 1 falha a cada 5s por 50 minutos
    if (contarFalhaDeDecifrar(CONTA, T0 + i * 5000, TETO, JANELA)) disparou++
  }
  conferir(disparou === 0,
    'ritmo normal (12 falhas/min por 50min) nunca abre o disjuntor', 'disparos: ' + disparou)

  falhasDeDecifrar.clear()
  disparou = 0
  for (let i = 0; i < 80; i++) {             // o ritmo medido no incidente
    if (contarFalhaDeDecifrar(CONTA, T0 + i * 750, TETO, JANELA)) disparou++
  }
  conferir(disparou === 1,
    'no ritmo do incidente (80/min) o disjuntor abre — e uma vez só no minuto',
    'disparos: ' + disparou)

  falhasDeDecifrar.clear()
  disparou = 0
  for (let i = 0; i < TETO * 3; i++) {       // 180 falhas coladas
    if (contarFalhaDeDecifrar(CONTA, T0 + i, TETO, JANELA)) disparou++
  }
  // Não é UMA vez: o contador zera ao disparar, então volta a estourar a cada teto
  // cheio (180 falhas = 3). O que ele impede é o disparo POR MENSAGEM, que daria
  // 121 aqui. As sobras são inofensivas — abrirDisjuntor sai na hora quando a
  // sessão já está sem socket, que é o teste logo abaixo.
  conferir(disparou === 3,
    'em rajada dispara a cada teto cheio, não a cada mensagem depois do teto',
    'disparos: ' + disparou)

  falhasDeDecifrar.clear()
  disparou = 0
  for (let i = 1; i < TETO; i++) {
    contarFalhaDeDecifrar(CONTA, T0 + i, TETO, JANELA)
    if (contarFalhaDeDecifrar(VIZINHA, T0 + i, TETO, JANELA)) disparou++
  }
  conferir(disparou === 0, 'o contador é POR CONTA: duas contas somando não estouram o teto')

  // ── o disjuntor ────────────────────────────────────────────────────────────
  console.log('\ndisjuntor (abrirDisjuntor)')
  await limpar()
  let fechado = false
  const sock = { end: () => { fechado = true } }
  sessoes.set(CONTA, { status: 'conectado', sock, abertoEm: Date.now() - MIN, qr: 'x' })
  sessoes.set(VIZINHA, { status: 'conectado', sock: { end: () => {} }, abertoEm: Date.now() })
  await abrirDisjuntor(CONTA)
  const d = sessoes.get(CONTA)
  conferir(fechado === true, 'o socket é fechado de verdade')
  conferir(d.sock === null && d.status === 'desconectado' && d.qr === null,
    'a sessão fica desconectada e sem socket')
  conferir(d.abertoEm === null,
    'o carimbo da encarnação sai — senão a PRÓXIMA nasceria "firme" sem ter subido')
  conferir(typeof d.substituidaEm === 'number',
    'e a conta entra na fila do vigia (substituidaEm carimbado)')
  const viz = sessoes.get(VIZINHA)
  conferir(viz.sock !== null && viz.status === 'conectado',
    'a conta VIZINHA continua trabalhando — o castigo é de uma conta só')

  const gravado = await backoffGravado()
  conferir(gravado.has(String(CONTA)),
    'a espera é gravada NA HORA: se a instância morrer em seguida, ela vale no processo novo')

  await limpar()
  sessoes.set(CONTA, { status: 'desconectado', sock: null })
  await abrirDisjuntor(CONTA)
  conferir(!sessoes.get(CONTA).substituidaEm,
    'sessão que já caiu por outro caminho (440, vigia, deploy) não é carimbada de novo')

  // ── a espera atravessa o restart ───────────────────────────────────────────
  console.log('\na espera sobrevive ao restart (a regressão do incidente)')
  await limpar()
  await parear(CONTA)
  await parear(VIZINHA)
  // processo 1: a conta 34 leva o disjuntor (ou um 440) e o estado é gravado
  sessoes.set(CONTA, { status: 'conectado', sock: { end: () => {} } })
  sessoes.set(VIZINHA, { status: 'conectado', sock: { end: () => {} } })
  await abrirDisjuntor(CONTA)
  await registrarSessoes()

  // processo 2: o Render matou a instância e subiu outra. A memória zerou.
  sessoes.clear()
  let espiao = espiaoDeReligamento()
  await restaurarSessoes()
  espiao.desfazer()
  conferir(!espiao.religadas.includes(String(CONTA)),
    'a conta de castigo NÃO é religada no arranque — era isto que realimentava o ciclo')
  conferir(espiao.religadas.includes(String(VIZINHA)),
    'e a vizinha sobe normal: o castigo não pode virar apagão do serviço')
  const rec = sessoes.get(String(CONTA)) || sessoes.get(CONTA)
  conferir(!!rec && typeof rec.substituidaEm === 'number',
    'o estado é recriado em memória pro vigia poder resgatar no prazo')

  console.log('\na espera vencida solta a conta')
  await limpar()
  await parear(CONTA)
  // 6 minutos atrás, primeira tentativa: a espera é de 5min, já venceu
  await s.pool.query(
    `insert into wa_qr_sessao_estado (conta_id, substituida_em, tentativas_440)
     values ($1, now() - interval '6 minutes', 0)`, [CONTA])
  espiao = espiaoDeReligamento()
  await restaurarSessoes()
  espiao.desfazer()
  conferir(espiao.religadas.includes(String(CONTA)),
    'passados os 5min da primeira tentativa, o arranque religa')

  console.log('\na espera DOBRA, e a dobra também atravessa o restart')
  await limpar()
  await parear(CONTA)
  // 4ª tentativa: 5 × 2^3 = 40min. 6 minutos não bastam mais.
  await s.pool.query(
    `insert into wa_qr_sessao_estado (conta_id, substituida_em, tentativas_440)
     values ($1, now() - interval '6 minutes', 3)`, [CONTA])
  espiao = espiaoDeReligamento()
  await restaurarSessoes()
  espiao.desfazer()
  conferir(!espiao.religadas.includes(String(CONTA)),
    'na 4ª tentativa a espera é de 40min — 6 não soltam a conta')
  conferir(esperaPos440({ tentativasPos440: 3 }, ESPERA_POS_440_MS) === 40 * MIN,
    'e a conta da dobra continua sendo 5/10/20/40/80')

  console.log('\nsem a espera gravada, o arranque é o de sempre')
  await limpar()
  await parear(CONTA)
  espiao = espiaoDeReligamento()
  await restaurarSessoes()
  espiao.desfazer()
  conferir(espiao.religadas.includes(String(CONTA)),
    'conta sem castigo nenhum religa normal')

  await s.pool.query('alter table wa_qr_sessao_estado rename to wa_qr_estado_escondido')
  espiao = espiaoDeReligamento()
  let explodiu = false
  try { await restaurarSessoes() } catch (_) { explodiu = true }
  espiao.desfazer()
  await s.pool.query('alter table wa_qr_estado_escondido rename to wa_qr_sessao_estado')
  conferir(!explodiu && espiao.religadas.includes(String(CONTA)),
    'migração 182 não aplicada (ou banco fora do ar): religa mesmo assim, em vez de ' +
    'deixar todo mundo sem WhatsApp')

  // ── o round-trip do estado ─────────────────────────────────────────────────
  console.log('\nregistrarSessoes ↔ backoffGravado')
  await limpar()
  const quando = Date.now() - 3 * MIN
  sessoes.set(CONTA, { status: 'desconectado', substituidaEm: quando, tentativasPos440: 2 })
  await registrarSessoes()
  const volta = await backoffGravado()
  const g = volta.get(String(CONTA))
  conferir(!!g && Math.abs(g.substituidaEm - quando) < 1000, 'a hora da substituição volta inteira')
  conferir(!!g && g.tentativas === 2, 'e o contador de tentativas também')

  await limpar()
  sessoes.set(CONTA, { status: 'conectado', sock: { end: () => {} } })
  await registrarSessoes()
  conferir(!(await backoffGravado()).has(String(CONTA)),
    'conta trabalhando não aparece na lista de castigo')

  await limpar()
  await s.pool.query('drop table if exists wa_qr_auth')
  await s.pool.end()
  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})()
