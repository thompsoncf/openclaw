'use strict'
// O vão do handshake (emHandshake) — o guarda que faltava no iniciarSessao.
//
// O caso real, conta 35 em 22/08/2026. Um stream:error fechou a conexão às
// 21:12:18.953; o handler do 'close' agendou a volta pra 2,5s e outra chamada entrou
// 0,95s depois. As duas abriram socket:
//
//   21:12:20.703  socket criado, registrando listeners    ← socket A
//   21:12:21.453  iniciarSessao: começando                ← o timer dos 2,5s
//   21:12:21.808  socket descartado {motivo: substituido}  ← mata o A no meio do login
//   21:12:24.508  WhatsApp conectado                      ← socket B
//
// Dali em diante a conta ENVIAVA e não recebia mais nada — nem o eco das próprias
// mensagens, que é o sinal de que o aparelho saiu da lista de dispositivos da conta no
// servidor do WhatsApp. Não voltou com religamento forçado, nem com socket novo, nem
// reiniciando o processo inteiro (conferido em 23/08, 01:54: restart limpo, socket
// único, zero messages.upsert depois). Custou um pareamento novo.
//
// O guarda antigo era `s.iniciando || (status === 'conectado' && s.sock)`, e entre os
// dois havia um vão: `iniciando` cai no `finally`, que roda quando a função RETORNA —
// logo depois de registrar os listeners e ANTES de o WhatsApp responder o login.
//
// Por que merece teste próprio: esta regra decide ABRIR socket em produção. Frouxa
// demais, volta a permitir dois logins na mesma credencial e o estrago é irreversível
// pelo software (só pareamento resolve). Apertada demais, uma sessão trava e a conta
// nunca mais religa — que é o outro jeito de deixar cliente sem receber.
//
// Não entra no pytest: é Node. Manual, e não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-handshake.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { emHandshake, HANDSHAKE_MS } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const AGORA = 1_700_000_000_000
const TETO = 20_000
const sock = () => ({ _descartado: false })

// ── o caso que custou o pareamento ────────────────────────────────────────────
// socket criado, listeners registrados, login ainda em curso: `iniciando` já é false
// e `status` ainda não é 'conectado'. É AQUI que a segunda chamada tem que ser barrada.
conferir(
  emHandshake({ sock: sock(), handshakeDesde: AGORA - 1100, status: 'reconectando' },
    AGORA, TETO) === true,
  'socket subindo há 1,1s (o intervalo exato do incidente) está protegido')

conferir(
  emHandshake({ sock: sock(), handshakeDesde: AGORA - 3800, status: 'reconectando' },
    AGORA, TETO) === true,
  'e o pior handshake já medido em produção (3,8s) também')

// ── as três saídas, que são o que impede a trava eterna ───────────────────────
conferir(
  emHandshake({ sock: Object.assign(sock(), { _descartado: true }),
    handshakeDesde: AGORA - 1000 }, AGORA, TETO) === false,
  'socket descartado não protege nada — senão o descarte prendia a conta pra sempre')

conferir(
  emHandshake({ sock: sock(), handshakeDesde: AGORA - 1000, abertoEm: AGORA - 500 },
    AGORA, TETO) === false,
  'socket que ABRIU sai do handshake — dali em diante quem guarda é o status conectado')

conferir(
  emHandshake({ sock: sock(), handshakeDesde: AGORA - TETO - 1 }, AGORA, TETO) === false,
  'handshake que passou do teto libera — login pendurado não pode prender a conta')

conferir(
  emHandshake({ sock: sock(), handshakeDesde: AGORA - TETO + 1 }, AGORA, TETO) === true,
  'e um milissegundo antes do teto ainda protege (a borda é < e não <=)')

// ── nada de falso positivo ────────────────────────────────────────────────────
conferir(emHandshake(null, AGORA, TETO) === false, 'sessão inexistente não protege')
conferir(emHandshake({}, AGORA, TETO) === false, 'sessão sem socket não protege')
conferir(
  emHandshake({ sock: sock() }, AGORA, TETO) === false,
  'socket sem carimbo de handshake não protege — só o iniciarSessao carimba')

// Sessão restaurada por um deploy antigo, de antes deste campo existir: sem carimbo,
// segue o comportamento de sempre em vez de bloquear a conta.
conferir(
  emHandshake({ sock: sock(), status: 'conectado', abertoEm: AGORA - 60_000 },
    AGORA, TETO) === false,
  'sessão de pé há um minuto não é confundida com handshake')

// ── o valor de produção ───────────────────────────────────────────────────────
// Folga larga sobre os 3,8s do pior caso e MUITO abaixo da volta do vigia (5min), pra
// um handshake travado ser resolvido pelo vigia e não ficar preso neste guarda.
conferir(HANDSHAKE_MS >= 10_000 && HANDSHAKE_MS <= 60_000,
  'o teto de produção fica entre 10s e 60s (é ' + Math.round(HANDSHAKE_MS / 1000) + 's)')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
