'use strict'
// Pra onde vai um envio (alvoDoEnvio): número de pessoa vira jid @s.whatsapp.net,
// e jid de GRUPO passa como veio. É o que deixa o Raio-X de segunda chegar no
// grupo dos vendedores sem o onWhatsApp tentar "corrigir" um jid que não é
// número. Puro, sem rede:
//
//     cd services/wa-qr && node teste-alvo-envio.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
const assert = require('assert')
const { alvoDoEnvio, jidDe } = require('./server')

// pessoa: o número vira jid, com o DDI do Brasil quando falta
assert.deepStrictEqual(alvoDoEnvio('86999990000'), { jid: '5586999990000@s.whatsapp.net', grupo: false })
assert.deepStrictEqual(alvoDoEnvio('+55 (86) 99999-0000'), { jid: '5586999990000@s.whatsapp.net', grupo: false })
assert.strictEqual(alvoDoEnvio('86999990000').jid, jidDe('86999990000'))

// grupo: passa como veio, marcado
assert.deepStrictEqual(alvoDoEnvio('120363012345678901@g.us'), { jid: '120363012345678901@g.us', grupo: true })
assert.deepStrictEqual(alvoDoEnvio('  5586999990000-1600000000@g.us '), { jid: '5586999990000-1600000000@g.us', grupo: true })

// o que NÃO é grupo nem número: nada
assert.deepStrictEqual(alvoDoEnvio(''), { jid: null, grupo: false })
assert.deepStrictEqual(alvoDoEnvio('status@broadcast'), { jid: null, grupo: false })
assert.deepStrictEqual(alvoDoEnvio('abc@g.us'), { jid: null, grupo: false })   // jid de grupo é só dígito e hífen

console.log('teste-alvo-envio: ok')
process.exit(0)
