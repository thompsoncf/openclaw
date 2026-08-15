'use strict'
// Vigia da sessão MUDA (sessaoMuda + marcarVivo).
//
// O caso real: a conta da Confeitaria Doce Mell parou de receber às 13:28 e ficou assim
// por horas — sem mensagem, sem eco do celular, sem contato — enquanto as outras contas
// do mesmo processo seguiam normais. O painel continuou mostrando o chip CONECTADO,
// porque o status é o que ficou na memória do último 'open', e o iniciarSessao devolve
// a sessão existente quando ela se diz conectada: nem o botão de reconectar
// ressuscitava. Só um deploy resolvia.
//
// Por que a regra merece teste próprio: ela decide RELIGAR socket de produção. Religar
// de menos deixa cliente sem receber sem ninguém saber; religar demais é o jeito rápido
// de o WhatsApp achar que é abuso e derrubar (ou banir) o número. As duas metades da
// regra são o silêncio (aqui) e o ping sem resposta (na rede, não testável offline).
//
// Não entra no pytest: é Node. Manual, e não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-vigia.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { sessaoMuda, marcarVivo, sessoes } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const LIMITE = 10 * 60 * 1000
const AGORA = 1_700_000_000_000
const SOCK = { fake: true }
// conectada e recebendo há pouco: o caso normal, que não pode ser tocado
const viva = (extra) => Object.assign(
  { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 1000 }, extra || {})

console.log('\nsessão que NÃO pode ser religada')
conferir(sessaoMuda(viva(), AGORA, LIMITE) === false,
  'conectada e com evento recente — trabalhando normal')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - LIMITE + 1 }), AGORA, LIMITE) === false,
  'calada, mas ainda dentro do limite')
conferir(sessaoMuda(viva({ status: 'reconectando' }), AGORA, LIMITE) === false,
  'reconectando — já tem quem cuide dela')
conferir(sessaoMuda(viva({ status: 'aguardando_qr', ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'aguardando QR — está esperando gente, não rede; religar mataria o QR na tela')
conferir(sessaoMuda(viva({ status: 'desconectado', ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'desconectada — não é caso do vigia')
conferir(sessaoMuda(viva({ sock: null, ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'sem socket — não há o que pingar')
conferir(sessaoMuda(viva({ iniciando: true, ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'no meio do iniciarSessao — derrubar aqui é atropelar a reconexão em curso')
conferir(sessaoMuda(viva({ ultimoEvento: 0 }), AGORA, LIMITE) === false,
  'sessão de antes do carimbo existir (ultimoEvento ausente) — na dúvida, não mexe')
for (const [valor, rotulo] of [[undefined, 'undefined'], [null, 'null'], [{}, 'objeto vazio']]) {
  let ok = false
  try { ok = sessaoMuda(valor, AGORA, LIMITE) === false } catch (_) { ok = false }
  conferir(ok, rotulo + ' → não é sessão muda, sem exceção')
}

console.log('\nsessão que PRECISA ser religada')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - LIMITE }), AGORA, LIMITE) === true,
  'silêncio exatamente no limite')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - 3 * 60 * 60 * 1000 }), AGORA, LIMITE) === true,
  'o caso da Doce Mell: 3h "conectada" sem entregar um evento')

console.log('\nmarcarVivo carimba a sessão certa')
sessoes.set(35, { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 3 * LIMITE })
sessoes.set(23, { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 3 * LIMITE })
marcarVivo(35)
conferir(sessaoMuda(sessoes.get(35), Date.now(), LIMITE) === false,
  'a conta que recebeu evento sai da mira do vigia')
conferir(sessaoMuda(sessoes.get(23), Date.now(), LIMITE) === true,
  'a conta vizinha NÃO é carimbada junto')
let explodiu = false
try { marcarVivo(999) } catch (_) { explodiu = true }
conferir(!explodiu, 'conta sem sessão em memória — no-op, sem exceção')
sessoes.delete(35); sessoes.delete(23)

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
