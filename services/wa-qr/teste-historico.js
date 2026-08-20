'use strict'
// Histórico importado, as duas peneiras:
//
//   1. deveSincronizarHistorico — decide se um BLOB inteiro vai ser baixado da rede e
//      descompactado na memória (o que estourou a instância).
//   2. prepararHistorico — decide, mensagem a mensagem, o que vira conversa no Zaq e,
//      quando descarta, POR QUÊ (o motivo que alimenta a linha de log da onda).
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
// prepararHistorico devolve `sem_app_url` sem isto — e aí TODO teste da peneira passaria
// pelo motivo errado, verde e sem valor nenhum.
process.env.APP_URL = process.env.APP_URL || 'http://localhost:8000'

const { deveSincronizarHistorico, deveSeguirNoHistorico, prepararHistorico, TIPO_HIST,
  lidMaps } = require('./server')

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

// ─────────────────────────────────────────────────────────────────────────────
// Peneira mensagem a mensagem (prepararHistorico)
//
// O motivo do descarte não é enfeite de log: num pareamento real a onda descartou 5000
// de 5000 mensagens, e `descartadas: 5000` não diz se aquilo foi certo (grupo, status,
// mensagem velha) ou se foi conversa perdida (@lid sem número no mapa). É esse número
// que decide se vale construir a resolução atrasada de @lid.

const CONTA = 777
const agora = Math.floor(Date.now() / 1000)
const DIA = 24 * 3600

function msg (extra) {
  return Object.assign({
    key: { remoteJid: '5511999998888@s.whatsapp.net', id: 'ABC', fromMe: false },
    message: { conversation: 'oi, tudo bem?' },
    messageTimestamp: agora - DIA
  }, extra)
}
const motivoDe = (m) => prepararHistorico(CONTA, m).motivo

console.log('\nmensagem boa é aceita (e sai pronta pro POST)')
const boa = prepararHistorico(CONTA, msg())
conferir(!boa.motivo, 'sem motivo de descarte')
conferir(boa.chat === '5511999998888@s.whatsapp.net', 'agrupa pelo jid do chat')
const corpo = JSON.parse(boa.corpo || '{}')
conferir(corpo.conta_id === CONTA && corpo.sender === '5511999998888' &&
  corpo.texto === 'oi, tudo bem?' && corpo.de_mim === false,
'corpo com conta, número, texto e de_mim')

// Regressão conhecida: fromMe já ficou de fora uma vez e o histórico importado nascia
// só com o lado do cliente, conversa pela metade.
console.log('\nmensagem que o vendedor mandou (fromMe) entra também')
const minha = prepararHistorico(CONTA, msg({
  key: { remoteJid: '5511999998888@s.whatsapp.net', id: 'X', fromMe: true }
}))
conferir(!minha.motivo && minha.chat === '5511999998888@s.whatsapp.net',
  'aceita e agrupa pelo CHAT, não pelo autor')
conferir(JSON.parse(minha.corpo || '{}').de_mim === true, 'marcada como de_mim')

console.log('\ncada descarte tem nome próprio')
conferir(motivoDe(msg({ message: {} })) === 'sem_texto',
  'sem_texto — mídia sem legenda, mensagem de protocolo')
conferir(motivoDe(msg({ key: { remoteJid: '120363000000000000@g.us', id: 'G' } })) === 'grupo',
  'grupo')
conferir(motivoDe(msg({ key: { remoteJid: '120363000000000000@newsletter', id: 'N' } })) === 'canal',
  'canal — mesmo formato numérico do grupo, sufixo diferente')
conferir(motivoDe(msg({ key: { remoteJid: 'status@broadcast', id: 'S' } })) === 'status',
  'status')
conferir(motivoDe(msg({ key: { remoteJid: '', id: 'V' } })) === 'status',
  'jid vazio cai no mesmo balde, sem virar conversa')
conferir(motivoDe(msg({ messageTimestamp: 0 })) === 'sem_data', 'sem_data')
conferir(motivoDe(msg({ messageTimestamp: agora - 40 * DIA })) === 'fora_da_janela',
  'fora_da_janela — mais velha que os 30 dias')

console.log('\n@lid: com mapa vira conversa, sem mapa vira contagem')
const dolid = () => msg({ key: { remoteJid: '4477@lid', id: 'L', fromMe: false } })
lidMaps.delete(CONTA)
const perdida = prepararHistorico(CONTA, dolid())
conferir(perdida.motivo === 'lid_sem_mapa', 'sem o mapa → lid_sem_mapa')
conferir(perdida.jid === '4477@lid',
  'devolve o jid — a onda conta CONTATOS distintos, não mensagens')

lidMaps.set(CONTA, new Map([['4477@lid', '5511777776666@s.whatsapp.net']]))
const achada = prepararHistorico(CONTA, dolid())
conferir(!achada.motivo && achada.chat === '5511777776666@s.whatsapp.net',
  'com o mapa → aceita, já com o número real')
conferir(JSON.parse(achada.corpo || '{}').sender === '5511777776666',
  'o sender é o telefone, nunca o código @lid')
lidMaps.delete(CONTA)

// A trava que sobrevive ao tempo: alguém acrescenta um descarte novo e devolve null (o
// contrato antigo) ou esquece o motivo. Aí a soma não fecha e a linha de log passa a
// mentir em silêncio — que é exatamente o problema que este trabalho veio consertar.
console.log('\ntoda mensagem sai aceita OU com motivo — a soma tem que fechar')
const lote = [msg(), msg({ message: {} }), msg({ key: { remoteJid: 'x@g.us', id: 'g' } }),
  msg({ messageTimestamp: agora - 90 * DIA }), dolid(), msg({ key: { remoteJid: 'status@broadcast', id: 's' } })]
let aceitas = 0; let comMotivo = 0
for (const m of lote) {
  const r = prepararHistorico(CONTA, m)
  if (!r) { comMotivo = -999; break }          // devolveu null: contrato quebrado
  if (r.motivo) comMotivo++; else aceitas++
}
conferir(aceitas === 1, 'uma aceita no lote (achou ' + aceitas + ')')
conferir(aceitas + comMotivo === lote.length,
  'aceitas + motivos === total (' + aceitas + '+' + comMotivo + ' de ' + lote.length + ')')

// ---------------------------------------------------------------- teto de ondas
//
// Aceitar o RECENT não é aceitar RECENT sem fim. Conta 7, 20/08: 63 ondas de ~5.000
// mensagens em 8 minutos — 315 mil mensagens — com a peneira devolvendo
// `descartadas: 4995` em TODAS. O heap foi de 47MB a 314MB contra um teto de 320 e o
// Node abortou com `JavaScript heap out of memory` no meio, derrubando junto os
// outros três chips que a instância segurava.
//
// A regra tem que ser decidida ANTES do download, com o que já se sabe das ondas
// anteriores — por isso ela é aritmética pura sobre o placar acumulado.
console.log('\nteto de ondas do histórico')
const SEM_NADA = 5
const MAX = 40

conferir(deveSeguirNoHistorico(undefined, SEM_NADA, MAX) === true,
  'primeira onda da sessão: sempre passa')
conferir(deveSeguirNoHistorico({ ondas: 0, aproveitadas: 0 }, SEM_NADA, MAX) === true,
  'placar zerado: passa')
conferir(deveSeguirNoHistorico({ ondas: 4, aproveitadas: 0 }, SEM_NADA, MAX) === true,
  'quatro ondas sem nada: ainda dá o benefício da dúvida')
conferir(deveSeguirNoHistorico({ ondas: 5, aproveitadas: 0 }, SEM_NADA, MAX) === false,
  'cinco ondas e ZERO aproveitada: chega — é o caso da conta 7')
conferir(deveSeguirNoHistorico({ ondas: 30, aproveitadas: 1 }, SEM_NADA, MAX) === true,
  'trinta ondas mas uma serviu: segue, quem aproveita tem direito à janela')
conferir(deveSeguirNoHistorico({ ondas: 40, aproveitadas: 900 }, SEM_NADA, MAX) === false,
  'quarenta ondas: freio de mão, aproveitando ou não')
conferir(deveSeguirNoHistorico({ ondas: 63, aproveitadas: 0 }, SEM_NADA, MAX) === false,
  'as 63 ondas que mataram a instância nunca teriam passado da quinta')

console.log('\n' + (falhas ? falhas + ' FALHA(S)' : 'tudo passou'))
process.exit(falhas ? 1 : 0)
