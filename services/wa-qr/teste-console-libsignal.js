'use strict'
// O QUE O libsignal GRITA NO CONSOLE TEM QUE CHEGAR NO wa_qr_log.
//
// O caso real, 12/09/2026. O dono colou no chat um erro que eu não conseguia ver de
// dentro do sistema:
//
//   Session error:Error: Bad MAC Error: Bad MAC
//       at Object.verifyMAC (.../libsignal/src/crypto.js:87:15)
//       at SessionCipher.doDecryptWhisperMessage (.../session_cipher.js:250:16)
//
// Procurei `Bad MAC` no wa_qr_log: ZERO, em toda a história. O motivo está no
// libsignal (session_cipher.js:157 e :159): ele chama `console.error` DIRETO, sem
// logger nenhum no caminho. O log do Render tem; o banco não. E o banco é onde todo
// diagnóstico desta base começa (§1 do CLAUDE.md), então a pergunta "quanto Bad MAC
// está acontecendo agora?" não tinha resposta por consulta.
//
// É o MESMO engano que o comentário do `_logSuprimidas` conta (24/08: "o log cru
// mostrava 592 Bad MAC por hora e o wa_qr_log mostrava ZERO"), mas por outra porta:
// lá o buraco era o filtro `soErro` do pino; aqui é o console.
//
// Não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && node teste-console-libsignal.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { capturarConsole, contarConsole, _logConsole, CONSOLE_NIVEIS } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}
const soma = () => [..._logConsole.values()].reduce((a, b) => a + b, 0)

// O texto REAL, copiado do painel do Render em 12/09.
const BAD_MAC = 'Session error:Error: Bad MAC Error: Bad MAC'
const PILHA = `    at Object.verifyMAC (/opt/render/project/src/services/wa-qr/node_modules/libsignal/src/crypto.js:87:15)
    at async 35626837647552.0 [as awaitable] (/opt/render/project/src/services/wa-qr/node_modules/libsignal/src/session_cipher.js:171:28)`

// --- 1. o require NÃO mexe no console ----------------------------------------
//
// Hook global instalado por import quebraria a suíte de quem só importa o módulo.
console.log('\nImportar o módulo não instala nada:')
conferir(console.error._capturado === undefined,
  'o console segue intacto depois do require — a captura é explícita, no arranque do worker')

// --- 2. conta o Bad MAC, sem perder a linha original -------------------------
console.log('\nO Bad MAC real, capturado:')
const vistos = []
const falso = { error: (...a) => vistos.push(a), warn: (...a) => vistos.push(a), info: (...a) => vistos.push(a) }
capturarConsole(falso)
_logConsole.clear()
falso.error(BAD_MAC, PILHA)
conferir(vistos.length === 1 && vistos[0][0] === BAD_MAC,
  'a linha original SAI do mesmo jeito — o log do Render não perde nada')
conferir(soma() === 1, 'e foi contada pro banco')
conferir([..._logConsole.keys()][0] === 'error|' + BAD_MAC.slice(0, 80),
  'a chave é o primeiro argumento truncado')

// --- 3. a pilha fica FORA da chave -------------------------------------------
//
// O `e.stack` carrega o id do peer (35626837647552.0). Se entrasse na chave, cada
// linha seria uma chave nova e o teto de 25 estouraria em segundos — o agregado
// viraria um balde "(outras)" sem informação.
console.log('\nA pilha não entra na chave:')
_logConsole.clear()
for (let i = 0; i < 50; i++) falso.error(BAD_MAC, PILHA.replace('35626837647552', String(i)))
conferir(_logConsole.size === 1, '50 pilhas diferentes viram UMA chave')
conferir(soma() === 50, 'e as 50 são contadas')

// --- 4. enxurrada: conta sem virar parte dela --------------------------------
//
// Em 20/08 foram 1119 falhas numa hora. Contar não pode alocar por linha.
console.log('\nEnxurrada:')
_logConsole.clear()
for (let i = 0; i < 5000; i++) falso.error(BAD_MAC, PILHA)
conferir(soma() === 5000 && _logConsole.size === 1,
  '5000 linhas, uma chave, contagem exata')

// --- 5. teto de chaves distintas ---------------------------------------------
console.log('\nMensagens distintas demais:')
_logConsole.clear()
for (let i = 0; i < 400; i++) falso.error('mensagem unica ' + i)
conferir(_logConsole.size <= 26, 'o teto segura o número de chaves (25 + o balde)')
conferir(_logConsole.has('error|(outras)'), 'e o excedente vai pro balde (outras), não desaparece')
conferir(soma() === 400, 'nenhuma linha se perde na contagem')

// --- 6. os níveis ------------------------------------------------------------
//
// O `info` do libsignal é operação normal e de alto volume (session_record.js:
// "Opening session", "Closing session"). Capturar por padrão poria uma linha no
// banco a cada janela de descarga sem dizer nada que interesse.
console.log('\nQuais níveis:')
conferir(CONSOLE_NIVEIS.includes('error') && CONSOLE_NIVEIS.includes('warn'),
  'error e warn sempre')
conferir(!CONSOLE_NIVEIS.includes('info'),
  'info NÃO por padrão (é "Opening session" do libsignal, operação normal)')
_logConsole.clear()
falso.warn('Closing open session in favor of incoming prekey bundle')
conferir(soma() === 1, 'o warn do session_builder é capturado')
falso.info('Opening session:', {})
conferir(soma() === 1, 'e o info não (sem WA_QR_CONSOLE_INFO=1)')

// --- 7. não explode com argumento estranho -----------------------------------
console.log('\nArgumentos que não são texto:')
_logConsole.clear()
let explodiu = false
try {
  falso.error(new Error('objeto de erro'))
  falso.error(undefined)
  falso.error()
  falso.error({ sem: 'mensagem' })
} catch (e) { explodiu = true }
conferir(explodiu === false, 'não explode com Error, undefined, vazio ou objeto')
conferir(soma() === 4, 'e conta os quatro')

// --- 8. instalar duas vezes não empilha --------------------------------------
console.log('\nIdempotência:')
_logConsole.clear()
capturarConsole(falso); capturarConsole(falso)
falso.error(BAD_MAC)
conferir(soma() === 1, 'instalar de novo não faz a linha ser contada em dobro')

// --- 9. a trava de leitura do fonte ------------------------------------------
console.log('\nO desenho está no código:')
const fs = require('fs')
const src = fs.readFileSync(require('path').join(__dirname, 'server.js'), 'utf8')
conferir(/const r = original\.apply\(console_, args\)\s*\n\s*if \(!dentro\)/.test(src),
  'o original é chamado PRIMEIRO — um erro nosso na contagem não engole a linha')
conferir(/let dentro = false/.test(src) && /dentro = true/.test(src),
  'e existe trava de reentrância: contar não pode se logar')
conferir(/if \(require\.main === module\) \{[\s\S]{0,400}?capturarConsole\(\)/.test(src),
  'a captura é instalada no arranque do worker, não no import')
conferir(/'log: console do libsignal \(não passa pelo pino/.test(src),
  'e o agregado tem linha PRÓPRIA — somar com o do Baileys daria um número ambíguo')
conferir(/!_logConsole\.size\) return/.test(src),
  'o descarregador não volta cedo quando só há linhas de console pendentes')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
