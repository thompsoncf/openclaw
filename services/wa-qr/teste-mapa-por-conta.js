'use strict'
// A chave do mapa de sessões: número e texto são a MESMA conta.
//
// O CASO REAL (21/08/2026, Prime Eventos). O painel mostrava o chip 2 assim:
//
//     Chip Campanha Thiago · +55 86 9443-6805 · desconectado
//
// e o chip estava recebendo 25 mensagens em 3 horas, a última 4 minutos antes.
// Perguntando ao serviço direto, dez vezes seguidas:
//
//     GET /session/36/status -> {"status":"desconectado","mudoMs":null}
//
// enquanto a tabela wa_qr_sessao_estado — escrita pelo MESMO processo, percorrendo
// o MESMO mapa, no mesmo minuto — dizia status 'conectado', temSock true, trava
// true. Uma instância só nos logs. Não era discordância entre processos.
//
// Era o tipo da chave. O `restaurarSessoes` lê conta_id do Postgres, que devolve
// bigint como STRING, e religou a conta como '36'. A rota faz parseInt e procurou
// por 36. `sessoes.get(36)` não acha `'36'`, e o `|| { status: 'desconectado' }`
// respondeu o que a rota inventou em vez do que o serviço sabia.
//
// O módulo da trava já tinha tropeçado nisto e resolvido (sessao-lock.js: "a mesma
// conta viraria duas entradas no Set e o `segura()` mentiria dependendo de quem
// perguntou"). O mapa das sessões ficou de fora.
//
// E o /enviar erra igual, só que ele não desiste: religa. Isso criava uma SEGUNDA
// entrada com um SEGUNDO socket na mesma credencial, sem fechar o primeiro — dois
// sockets no mesmo número, que é exatamente o que faz o WhatsApp derrubar um com
// 440. O serviço fabricava a própria guerra de sessão.
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-mapa-por-conta.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { sessoes, lidMaps, falhasDeDecifrar, ondasDeHistorico,
  tentativasDeTrava } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const POR_CONTA = [['sessoes', sessoes], ['lidMaps', lidMaps],
  ['falhasDeDecifrar', falhasDeDecifrar], ['ondasDeHistorico', ondasDeHistorico],
  ['tentativasDeTrava', tentativasDeTrava]]

console.log('\no que o restaurarSessoes guarda, a rota HTTP encontra')
for (const [nome, mapa] of POR_CONTA) {
  mapa.clear()
  mapa.set('36', { marca: 'do banco' })          // restaurarSessoes: texto
  const achou = mapa.get(36)                     // a rota: parseInt
  conferir(!!achou && achou.marca === 'do banco',
    nome + ": guardado como '36', achado como 36")
  conferir(mapa.size === 1, nome + ': e não viraram duas entradas')
  mapa.clear()
}

console.log('\n...e o contrário também, que é o caminho do /iniciar')
for (const [nome, mapa] of POR_CONTA) {
  mapa.clear()
  mapa.set(36, { marca: 'da rota' })
  const achou = mapa.get('36')
  conferir(!!achou && achou.marca === 'da rota',
    nome + ': guardado como 36, achado como ' + "'36'")
  mapa.clear()
}

console.log('\nas outras portas do mapa contam a mesma verdade')
sessoes.clear()
sessoes.set('36', { status: 'conectado' })
conferir(sessoes.has(36) === true, 'has(36) enxerga o que entrou como texto')
conferir(sessoes.get(36).status === 'conectado', 'get devolve a sessão de verdade')
conferir(sessoes.delete(36) === true, 'delete(36) apaga o que entrou como texto')
conferir(sessoes.size === 0, 'e o mapa fica vazio de verdade')

console.log('\no /status não pode mais responder por conta de ninguém')
sessoes.clear()
sessoes.set('36', { status: 'conectado', sock: {}, ultimoEvento: 1 })
// é ISTO que a rota faz: `sessoes.get(contaId) || { status: 'desconectado' }`
const resposta = sessoes.get(36) || { status: 'desconectado' }
conferir(resposta.status === 'conectado',
  'chip que está de pé não é mais dado como desconectado — era o furo de 21/08')
conferir(!!resposta.sock,
  'e o /enviar acha o socket em vez de abrir um segundo na mesma credencial')

console.log('\niterar continua entregando a conta e a sessão')
sessoes.clear()
sessoes.set('36', { status: 'conectado' })
sessoes.set(34, { status: 'conectado' })
const vistos = [...sessoes].map(([k]) => k)
conferir(vistos.length === 2 && vistos.every((k) => typeof k === 'number'),
  'registrarSessoes percorre duas contas, as duas como número')
sessoes.clear()

console.log('\nchave composta não precisa disto (o + já vira texto)')
conferir(('' + 36 + ':abc') === ('' + '36' + ':abc'),
  'enviadas/jidsResolvidos/lidsPendentes seguem de Map comum, sem mudança')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
