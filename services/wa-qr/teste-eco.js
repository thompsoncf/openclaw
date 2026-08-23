'use strict'
// O eco: a prova de que a ENTRADA está viva (esperarEco + confirmarEco + cobrarEcos),
// e o contador do vigia que se auto-absolvia (marcarVivo).
//
// O caso real, conta 35 em 22/08/2026. Ela morreu pra entrada às 21:12 e o serviço
// passou SEIS horas religando de 46 em 46 minutos sem nunca dizer isso a ninguém. Duas
// coisas conspiraram:
//
// 1. Silêncio de entrada não prova nada. Essa conta não recebe uma mensagem sequer
//    depois das 21h em dia nenhum (medido: zero na hora 22, em dez dias). Um alerta
//    por silêncio ou gritaria toda noite ou não gritaria nunca.
//
// 2. O contador `reconexoesMudas`, que faz o teto do vigia dobrar, era zerado pelo
//    `marcarVivo` — que era chamado por QUALQUER frame, inclusive o resyncAgenda que o
//    próprio serviço dispara depois de cada religamento. O serviço se absolvia com
//    tráfego que ele mesmo provocou. No wa_qr_log: a conta 23 religou 19 vezes
//    seguidas entre 21/08 20:36 e 22/08 09:39 e as 19 linhas dizem `religamentos: 0`,
//    `mudoMin: 46`. A conta 36, que escapou disso, mostra o mecanismo funcionando:
//    0 -> 1 -> 2, com o teto indo de 46 pra 90 e pra 181 minutos.
//
// O eco resolve o (1): toda mensagem que sai volta como upsert com fromMe, em 0,5 a
// 1,5s. Não depende de cliente nenhum escrever, e é o primeiro sinal a sumir quando o
// aparelho sai da lista de dispositivos da conta. A conta 35 enviou às 21:07, 22:35,
// 22:37 e 23:55 — e não recebeu o eco de nenhuma.
//
// Não entra no pytest: é Node. Manual, e não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-eco.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const srv = require('./server')
const { esperarEco, confirmarEco, cobrarEcos, ecosPendentes, marcarVivo, tetoMudo,
  sessoes } = srv

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const CONTA = 35
const AGORA = 1_700_000_000_000
const LIMITE = 90_000
const AVISA = 2
const eco = (id) => ({ key: { id, fromMe: true } })

function limpar () {
  ecosPendentes.clear(); sessoes.clear()
  sessoes.set(CONTA, { status: 'conectado', sock: {}, ultimoEvento: AGORA })
}

// ── o caminho feliz: mandou, o eco voltou ─────────────────────────────────────
limpar()
esperarEco(CONTA, 'MSG1', AGORA)
conferir(ecosPendentes.get(CONTA).size === 1, 'envio fica aguardando o eco')
confirmarEco(CONTA, [eco('MSG1')])
conferir(ecosPendentes.get(CONTA).size === 0, 'o eco que volta tira o envio da espera')
cobrarEcos(AGORA + LIMITE * 5, LIMITE, AVISA)
conferir(!sessoes.get(CONTA).enviosSemEco, 'e nada é cobrado depois — a conta está viva')

// ── o caso da conta 35: mandou e o eco nunca veio ────────────────────────────
limpar()
esperarEco(CONTA, 'MSG1', AGORA)
cobrarEcos(AGORA + LIMITE - 1, LIMITE, AVISA)
conferir(!sessoes.get(CONTA).enviosSemEco,
  'dentro do prazo o eco ainda pode chegar — nada é cobrado')
cobrarEcos(AGORA + LIMITE + 1, LIMITE, AVISA)
conferir(sessoes.get(CONTA).enviosSemEco === 1, 'passou do prazo: primeiro envio sem eco')
conferir(ecosPendentes.get(CONTA).size === 0, 'e o id vencido sai da espera (não recobra)')

esperarEco(CONTA, 'MSG2', AGORA + 200_000)
cobrarEcos(AGORA + 200_000 + LIMITE + 1, LIMITE, AVISA)
conferir(sessoes.get(CONTA).enviosSemEco === 2,
  'segundo envio sem eco — é aqui que o log manda parear de novo')

// ── e o contrário: um eco atrasado desarma a suspeita ─────────────────────────
esperarEco(CONTA, 'MSG3', AGORA + 400_000)
confirmarEco(CONTA, [eco('MSG3')])
conferir(sessoes.get(CONTA).enviosSemEco === 0, 'o eco voltando zera a suspeita')

// ── nada de falso positivo ───────────────────────────────────────────────────
limpar()
esperarEco(CONTA, 'MSG1', AGORA)
confirmarEco(CONTA, [{ key: { id: 'MSG1', fromMe: false } }])
conferir(ecosPendentes.get(CONTA).size === 1,
  'mensagem RECEBIDA com o mesmo id não conta como eco (eco é fromMe)')
confirmarEco(CONTA, [eco('OUTRO')])
conferir(ecosPendentes.get(CONTA).size === 1, 'eco de outra mensagem não desarma este')
esperarEco(CONTA, null, AGORA)
conferir(ecosPendentes.get(CONTA).size === 1, 'envio sem id não entra na espera')

limpar()
cobrarEcos(AGORA + LIMITE * 10, LIMITE, AVISA)
conferir(!sessoes.get(CONTA).enviosSemEco, 'conta que não mandou nada não é cobrada')

// teto de pendentes: o estouro só acontece quando o eco parou de vir
limpar()
for (let i = 0; i < 80; i++) esperarEco(CONTA, 'M' + i, AGORA + i)
conferir(ecosPendentes.get(CONTA).size === 50, 'a espera tem teto (50) e não cresce sem fim')
conferir(!ecosPendentes.get(CONTA).has('M0') && ecosPendentes.get(CONTA).has('M79'),
  'e quem sai é o mais VELHO — o envio recente é o que interessa cobrar')

// ── o contador do vigia: só entrega de conversa absolve ──────────────────────
limpar()
const s = sessoes.get(CONTA)
s.reconexoesMudas = 3
marcarVivo(CONTA)
conferir(s.reconexoesMudas === 3,
  'frame de agenda/contato NÃO zera a desconfiança (era o auto-absolvimento)')
conferir(s.ultimoEvento >= AGORA, 'mas conta como cano aberto — ultimoEvento anda')

marcarVivo(CONTA, true)
conferir(s.reconexoesMudas === 0, 'entrega de conversa (messages.upsert) zera')

// e o efeito prático: com o contador de pé, o teto dobra como a conta 36 mostrou
conferir(tetoMudo({ reconexoesMudas: 0 }, 45) === 45, 'teto sem religamento: 45min')
conferir(tetoMudo({ reconexoesMudas: 1 }, 45) === 90, 'com 1 religamento: 90min')
conferir(tetoMudo({ reconexoesMudas: 2 }, 45) === 180, 'com 2: 180min (a curva da conta 36)')
conferir(tetoMudo({ reconexoesMudas: 9 }, 45) === 45 * 16, 'e para de dobrar em 4 (16x)')

sessoes.clear(); ecosPendentes.clear()
console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
