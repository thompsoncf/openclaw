'use strict'
// O filtro que roda ANTES de decifrar (deveIgnorarNoBaileys) e as duas constantes
// de retentativa.
//
// Por que isto merece teste próprio: é a única regra do serviço que descarta
// mensagem SEM olhar o conteúdo. Errar pra menos custa CPU — foi o que matou a
// instância 7 vezes em 20/08. Errar pra MAIS é pior e silencioso: mensagem de
// cliente que some antes de qualquer log, porque o Baileys nem chega a decifrar.
//
// O caso real: depois que outro aparelho assumiu a credencial, todo eco de
// mensagem passou a chegar indecifrável — 1119 'failed to decrypt' numa hora,
// contra 50-150 num dia inteiro. Cada falha vira pedido de reenvio (5x, de 250 em
// 250ms por padrão, cada um com uma ida ao Postgres no getMessage), a CPU única do
// contêiner saturou, o event loop travou 25-73s e o /saude não respondeu em 5s.
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-ignorar-jid.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const { deveIgnorarNoBaileys, ehConversaValida,
  MAX_RETRY_DECIFRAR, RETRY_DELAY_MS } = require('./server')

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + '  ' + descricao)
  if (!ok) falhas++
}

// --- o que TEM que ser ignorado (volume alto, valor zero) -------------------
console.log('\nIgnora antes de decifrar:')
conferir(deveIgnorarNoBaileys('status@broadcast') === true,
  'status@broadcast — o grosso do volume; o app já descartava depois de decifrar')
conferir(deveIgnorarNoBaileys('120363420205108265@newsletter') === true,
  'canal (@newsletter) — propaganda, nunca virou lead')

// --- o que NÃO pode ser ignorado --------------------------------------------
console.log('\nDeixa passar:')
conferir(deveIgnorarNoBaileys('558694095301@s.whatsapp.net') === false,
  'conversa normal (@s.whatsapp.net)')
conferir(deveIgnorarNoBaileys('133157005312040@lid') === false,
  'conversa por lid (@lid) — é como a maioria chega hoje')
conferir(deveIgnorarNoBaileys('558698392961-1607041815@g.us') === false,
  'GRUPO passa de propósito: não vira lead, mas alimenta repassarContatos')

// --- robustez ---------------------------------------------------------------
console.log('\nEntrada torta não derruba:')
for (const v of [null, undefined, '', 0, {}]) {
  conferir(deveIgnorarNoBaileys(v) === false,
    JSON.stringify(v) + ' — na dúvida deixa passar (descartar é o erro caro)')
}

// --- O INVARIANTE QUE IMPORTA -----------------------------------------------
// Tudo que a gente corta no Baileys tem que ser algo que o app JÁ jogava fora.
// Se um dia alguém acrescentar um sufixo aqui sem checar o ehConversaValida,
// este teste quebra antes de a mensagem sumir em produção.
console.log('\nInvariante — nada que o app usaria é cortado antes:')
const amostra = [
  'status@broadcast', '120363420205108265@newsletter',
  '558694095301@s.whatsapp.net', '133157005312040@lid',
  '558698392961-1607041815@g.us', '5586999999999@s.whatsapp.net'
]
for (const jid of amostra) {
  const cortado = deveIgnorarNoBaileys(jid)
  const usavel = ehConversaValida(jid)
  conferir(!(cortado && usavel),
    jid + ' — cortado=' + cortado + ' usavel=' + usavel)
}

// --- as constantes ----------------------------------------------------------
console.log('\nRetentativa (padrão do Baileys: 5 tentativas / 250ms):')
conferir(MAX_RETRY_DECIFRAR === 2, 'tentativas caíram de 5 pra 2 (amplificação /2,5)')
conferir(RETRY_DELAY_MS >= 2000, 'intervalo subiu pra >=2s, pro event loop respirar')
conferir(MAX_RETRY_DECIFRAR >= 1,
  'mas nao zero: falha passageira de rede ainda merece uma segunda chance')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
