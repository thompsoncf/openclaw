'use strict'
// FASE 4: O BAILEYS 7 NUM CHIP DE TESTE, SEM PODER CUSTAR UM CHIP.
//
// Dois Baileys convivem no node_modules: o 6.7.24 de sempre em
// `@whiskeysockets/baileys` e o 7.0.0-rc14 sob o apelido `baileys7`. Cada worker
// carrega UM; quem escolhe é o supervisor, por conta, via WA_QR_BAILEYS7_CONTAS.
//
// O que este teste fixa, e por quê:
//
//   1. A ESCOLHA é por conta e falha fechada (lista vazia = ninguém muda).
//   2. O HistorySyncType existe nos DOIS — no rc14 ele mudou de lugar
//      (proto.Message.HistorySyncNotification -> proto.HistorySync) e sem o
//      fallback o worker v7 morria nessa linha, antes de abrir porta.
//   3. O par código->número: o rc14 aposentou `key.senderPn` e pôs `remoteJidAlt`.
//      A direção importa — quando o chat é @lid o Alt é telefone, quando o chat já
//      é telefone o Alt é o @lid. Devolver o Alt sem olhar trocaria um telefone
//      por um código, e o serviço inteiro identifica conversa por número.
//
// O que este teste NÃO prova, e nenhum teste aqui provaria: pareamento e troca de
// mensagem. O container não alcança o WhatsApp (web.whatsapp.com responde 000), e
// medido lado a lado o v6 e o v7 param no MESMO ponto — 'socket criado,
// registrando listeners', sem QR nos dois. Por isso a Fase 4 é uma semana de
// campo num chip de teste, não uma suíte.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-baileys7.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const fs = require('fs')
const path = require('path')
const { baileysDaConta } = require('./supervisor')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

// --- 1. quem roda no v7 ------------------------------------------------------
console.log('\nA escolha da versão é por conta, e falha fechada:')
conferir(baileysDaConta(23, '') === 6, 'lista VAZIA: ninguém muda — é o padrão, e é o que protege o cliente')
conferir(baileysDaConta(23, '23') === 7, 'conta na lista: v7')
conferir(baileysDaConta(34, '23') === 6, 'conta fora da lista: v6, mesmo com vizinha no v7')
conferir(baileysDaConta(36, '23, 36') === 7, 'lista com espaço depois da vírgula')
conferir(baileysDaConta('23', '23') === 7 && baileysDaConta(23, ['23']) === 7,
  'id como texto ou número dá o mesmo — o === entre texto e número já derrubou três chips hoje')
conferir(baileysDaConta(2, '23') === 6, 'a conta 2 NÃO entra por ser pedaço de "23"')
conferir(baileysDaConta(23, 'abc') === 6, 'lixo na variável não liga o v7 em ninguém')

// --- 2. os dois carregam, e dão os mesmos símbolos ---------------------------
console.log('\nAs duas bibliotecas estão instaladas e expõem o que o serviço usa:')
const v6 = require('@whiskeysockets/baileys')
const v7 = require('baileys7')
conferir(require('@whiskeysockets/baileys/package.json').version.startsWith('6.'), 'v6 é 6.x')
conferir(require('baileys7/package.json').version.startsWith('7.'), 'v7 é 7.x')
for (const s of ['default', 'DisconnectReason', 'makeCacheableSignalKeyStore', 'fetchLatestBaileysVersion',
  'proto', 'BufferJSON', 'normalizeMessageContent', 'downloadMediaMessage', 'jidNormalizedUser', 'initAuthCreds']) {
  conferir(typeof v6[s] === typeof v7[s] && v6[s] !== undefined,
    'os dois têm ' + s + ' (' + typeof v7[s] + ')')
}

// --- 3. o enum que mudou de lugar --------------------------------------------
console.log('\nHistorySyncType — mudou de lugar no rc14:')
const tipo = (b) => (b.proto.Message.HistorySyncNotification && b.proto.Message.HistorySyncNotification.HistorySyncType) ||
  (b.proto.HistorySync && b.proto.HistorySync.HistorySyncType)
conferir(tipo(v6) && typeof tipo(v6).FULL === 'number', 'v6: achado pelo caminho antigo')
conferir(tipo(v7) && typeof tipo(v7).FULL === 'number', 'v7: achado pelo caminho novo (sem isto o worker morria antes de abrir porta)')
conferir(v7.proto.Message.HistorySyncNotification.HistorySyncType === undefined,
  'e o caminho antigo REALMENTE sumiu no v7 — o fallback não é decoração')
for (const k of ['INITIAL_BOOTSTRAP', 'FULL', 'RECENT']) {
  conferir(tipo(v6)[k] === tipo(v7)[k], 'o valor de ' + k + ' é o mesmo nos dois (' + tipo(v7)[k] + ')')
}

// --- 4. o par código->número -------------------------------------------------
//
// O serviço identifica conversa por NÚMERO. Trocar um telefone por um @lid aqui
// criaria conversa órfã e lead duplicado — é o defeito que este bloco impede.
console.log('\nO número real do contato, nas duas versões:')
const { numeroAlternativo } = require('./server')
conferir(numeroAlternativo({ remoteJid: '1267@lid', senderPn: '5586988@s.whatsapp.net' }) === '5586988@s.whatsapp.net',
  'v6: senderPn continua valendo')
conferir(numeroAlternativo({ remoteJid: '1267@lid', remoteJidAlt: '5586988@s.whatsapp.net' }) === '5586988@s.whatsapp.net',
  'v7: remoteJidAlt vale quando o chat é @lid e o Alt é telefone')
conferir(numeroAlternativo({ remoteJid: '5586988@s.whatsapp.net', remoteJidAlt: '1267@lid' }) === null,
  'v7 ao contrário: chat já é telefone e o Alt é o @lid — NÃO devolve o código no lugar do número')
conferir(numeroAlternativo({ remoteJid: '5586988@s.whatsapp.net' }) === null, 'sem Alt nenhum: null, e o chamador usa o remoteJid')
conferir(numeroAlternativo(null) === null && numeroAlternativo(undefined) === null, 'key ausente não explode')

// --- 5. a volta automática pro v6 --------------------------------------------
console.log('\nO v7 não pode custar um chip (leitura do fonte):')
const sup = fs.readFileSync(path.join(__dirname, 'supervisor.js'), 'utf8')
conferir(/codigo === 3/.test(sup),
  'código 3 (não carregou a biblioteca) volta a conta pro v6 na PRIMEIRA vez')
conferir(/w\.quedasBaileys7 >= baileys7QuedasMax/.test(sup),
  'e quedas repetidas do v7 também devolvem a conta pro v6')
conferir(/w\.baileys = 6/.test(sup) && !/w\.baileys = 7/.test(sup),
  'o caminho automático só anda num sentido: 7→6. Ligar o v7 é decisão de gente')
conferir(/baileys: w\.baileys/.test(sup),
  'e toda linha do supervisor carrega a versão — é assim que se conta queda por versão')

const srv = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8')
conferir(/process\.exit\(3\)/.test(srv), 'o worker sai com código 3 quando não consegue carregar o Baileys')
conferir(/require\(BAILEYS_VERSAO === 7 \? 'baileys7' : '@whiskeysockets\/baileys'\)/.test(srv),
  'e a escolha da biblioteca está num lugar só')
const auth = fs.readFileSync(path.join(__dirname, 'auth-db.js'), 'utf8')
conferir(/WA_QR_BAILEYS === '7' \? 'baileys7'/.test(auth),
  'o auth-db carrega a MESMA versão do worker — misturar proto de versões diferentes é bug silencioso no pareamento')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
