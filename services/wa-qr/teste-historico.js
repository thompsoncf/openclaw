'use strict'
// Gate do histórico (deveSincronizarHistorico) — o que decide se um BLOB inteiro vai
// ser baixado da rede e descompactado na memória.
//
// Por que isto merece teste próprio: na fonte do Baileys o gate roda ANTES do download
// (Socket/chats.js:779 -> Utils/process-message.js:158-167), e o download
// (Utils/history.js:10-20) faz Buffer.concat + inflate + decode, com as cópias
// coexistindo no pico. Aceitar um tipo grande aqui é aceitar centenas de MB de memória
// EXTERNA — que o --max-old-space-size não limita. Foi assim que a instância de 512MB
// morreu, reproduzido: apagar o dispositivo no celular e parear de novo.
//
// Um "só mais este tipo" acrescentado sem pensar traz o estouro de volta, e o sintoma
// aparece só num pareamento real, em produção. Daí o teste.
//
// Não entra no pytest: é Node. Manual, e não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-historico.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { deveSincronizarHistorico, TIPO_HIST } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

console.log('\ntipos que devem ser RECUSADOS (o blob não pode nem ser baixado)')
conferir(deveSincronizarHistorico(TIPO_HIST.INITIAL_BOOTSTRAP) === false,
  'INITIAL_BOOTSTRAP — o blob do pareamento, que estourou a instância de 512MB')
conferir(deveSincronizarHistorico(TIPO_HIST.FULL) === false,
  'FULL — backfill da conta inteira (meses/anos)')
conferir(deveSincronizarHistorico(TIPO_HIST.ON_DEMAND) === false,
  'ON_DEMAND — pedido sob demanda; a gente não pede')

console.log('\ntipos que devem ser ACEITOS')
conferir(deveSincronizarHistorico(TIPO_HIST.RECENT) === true,
  'RECENT — a janela recente, a importação de conversa que sobrou')
conferir(deveSincronizarHistorico(TIPO_HIST.PUSH_NAME) === true,
  'PUSH_NAME — só nomes, sem mensagem: barato e é a melhor fonte de nome')

console.log('\nentrada estranha não pode virar "aceita" nem estourar')
for (const [valor, rotulo] of [[undefined, 'undefined'], [null, 'null'],
  [999, 'número desconhecido'], ['RECENT', 'string em vez do enum'], [{}, 'objeto']]) {
  let ok = false
  try { ok = deveSincronizarHistorico(valor) === false } catch (_) { ok = false }
  conferir(ok, rotulo + ' → recusado, sem exceção')
}

// A trava que importa a longo prazo: alguém acrescentar um tipo aqui sem perceber o
// custo. Se este teste falhar porque o conjunto mudou DE PROPÓSITO, leia o comentário
// do deveSincronizarHistorico antes de só atualizar o número.
console.log('\no conjunto aceito é exatamente {RECENT, PUSH_NAME}')
const todos = [TIPO_HIST.INITIAL_BOOTSTRAP, TIPO_HIST.FULL, TIPO_HIST.ON_DEMAND,
  TIPO_HIST.RECENT, TIPO_HIST.PUSH_NAME]
const aceitos = todos.filter(deveSincronizarHistorico)
conferir(aceitos.length === 2, 'exatamente 2 tipos aceitos (achou ' + aceitos.length + ')')

console.log('\n' + (falhas ? falhas + ' FALHA(S)' : 'tudo passou'))
process.exit(falhas ? 1 : 0)
