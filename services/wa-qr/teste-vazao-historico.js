'use strict'
// A VAZÃO DO REPASSE DO HISTÓRICO.
//
// O CASO REAL, 15/09/2026. A conta 38 foi pareada às 09:56 e o sync de histórico
// dela despejou no web, em dois minutos e meio:
//
//     ~5.176 POST /webhooks/wa-qr/historico   (um por MENSAGEM)
//     ~3.799 POST /webhooks/wa-qr/contatos
//
// Oito conversas em paralelo, sem pausa nenhuma — uns 60 req/s contra um web de
// dois workers que também serve o painel. O web parou de responder ao /saude, o
// Render matou a instância, e durante os 502 o wa-qr perdeu TRÊS mensagens de
// cliente das contas 23 e 34, porque o repasse era um fetch único sem retentativa.
//
// O teto de ondas (HIST_ONDAS_MAX) não pega isso: ele limita quantas ondas se
// BAIXA, não a que velocidade o que foi baixado vira requisição. Este arquivo
// tranca a diferença.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-vazao-historico.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'
process.env.APP_URL = process.env.APP_URL || 'http://web.local'

const s = require('./server')

let falhas = 0
function conferir (ok, descricao, detalhe) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao + (detalhe ? '  -> ' + detalhe : ''))
  if (!ok) falhas++
}

// --- 1. os padrões são de vazão, não de enxurrada -----------------------------
console.log('\nOs padrões:')
conferir(s.HIST_CONCORRENCIA < 8,
  'a concorrência caiu do 8 fixo que derrubou o web', 'hoje=' + s.HIST_CONCORRENCIA)
conferir(s.HIST_PAUSA_MS > 0, 'existe pausa entre POSTs da mesma conversa',
  s.HIST_PAUSA_MS + 'ms')
conferir(s.HIST_RECUO_MS > s.HIST_PAUSA_MS,
  'e o recuo depois de um não-ok é MAIOR que a pausa normal — web afogado recebe menos',
  s.HIST_RECUO_MS + 'ms > ' + s.HIST_PAUSA_MS + 'ms')
conferir(s.HIST_TIMEOUT_MS > 0 && s.HIST_TIMEOUT_MS <= 30000,
  'e o POST tem timeout — web que PENDURA não trava a corrente pra sempre',
  s.HIST_TIMEOUT_MS + 'ms')

// O número que interessa: requisições por segundo no pior caso (latência zero).
const tetoPorSegundo = s.HIST_CONCORRENCIA * (1000 / s.HIST_PAUSA_MS)
console.log('\nTeto de requisições por segundo (latência zero, o pior caso):')
conferir(tetoPorSegundo <= 25,
  'fica bem abaixo dos ~60/s medidos no incidente', Math.round(tetoPorSegundo) + '/s')

;(async () => {

// --- 2. enviarHistorico diz se o web aceitou ----------------------------------
//
// É disso que o recuo depende: sem o retorno, quem chama não tem como saber que
// precisa ir mais devagar.
console.log('\nenviarHistorico devolve o veredito do web:')
const originalFetch = global.fetch
let chamadas = 0
global.fetch = async () => { chamadas++; return { ok: true, status: 200 } }
conferir(await s.enviarHistorico(1, '{}') === true, 'true quando o web aceita (200)')
global.fetch = async () => ({ ok: false, status: 502 })
conferir(await s.enviarHistorico(1, '{}') === false, 'false no 502 — é o que dispara o recuo')
global.fetch = async () => { throw new Error('ECONNREFUSED') }
conferir(await s.enviarHistorico(1, '{}') === false, 'false quando a rede cai, sem lançar')

console.log('\nWeb que pendura não trava a corrente:')
global.fetch = async (u, o) => new Promise((_, rej) => {
  o.signal.addEventListener('abort', () => { const e = new Error('abort'); e.name = 'AbortError'; rej(e) })
})
const t0 = Date.now()
const r = await s.enviarHistorico(1, '{}')
const levou = Date.now() - t0
conferir(r === false, 'devolve false em vez de esperar pra sempre')
conferir(levou < s.HIST_TIMEOUT_MS + 3000, 'e desiste dentro do timeout', levou + 'ms')
global.fetch = originalFetch

// --- 3. a pausa é real ---------------------------------------------------------
console.log('\nA pausa existe de verdade (dormir):')
const t1 = Date.now()
await s.dormir(60)
const dormiu = Date.now() - t1
conferir(dormiu >= 55, 'dormir(60) espera mesmo ~60ms', dormiu + 'ms')

// --- 4. trava de leitura do fonte ---------------------------------------------
//
// O desenho tem que continuar no código: ordem sequencial DENTRO da conversa (é o
// que impede a conversa de sair embaralhada no painel) e pausa entre os POSTs.
console.log('\nO desenho está no código:')
const fs = require('fs')
const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
conferir(/comLimiteDeConcorrencia\(\[\.\.\.porChat\.values\(\)\], HIST_CONCORRENCIA,/.test(src),
  'a concorrência vem da constante, não de um 8 solto no meio do laço')
conferir(/await dormir\(ok \? HIST_PAUSA_MS : HIST_RECUO_MS\)/.test(src),
  'a pausa depende de o web ter aceitado — recuo quando não')
// O buraco que eu mesmo abri na primeira versão: com `if (i < grupo.length - 1)`,
// mil conversas de UMA mensagem passariam sem pausa nenhuma, porque quem termina
// uma conversa pega a próxima na hora. É a forma exata do incidente de 15/09.
conferir(!/if \(i < grupo\.length - 1\) await dormir/.test(src),
  'a pausa vale pro ÚLTIMO POST da conversa também — senão conversa de 1 mensagem escapa')
conferir(/for \(let i = 0; i < grupo\.length; i\+\+\) \{[\s\S]{0,200}?await enviarHistorico/.test(src),
  'e dentro da conversa continua sequencial (ordem da conversa preservada)')
conferir(/signal: ctl\.signal/.test(src) && /HIST_TIMEOUT_MS/.test(src),
  'o POST do histórico leva AbortController com o timeout')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
