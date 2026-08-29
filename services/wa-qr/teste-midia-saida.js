'use strict'
// A mídia que SAI: o vendedor mandando foto, vídeo e documento pelo Zaq (passo 4).
//
// POR QUE ESTE PASSO EXISTE
// Os passos 1-3 fizeram o vendedor RECEBER mídia dentro do app. Pra MANDAR a foto
// do salão ou o PDF do orçamento ele ainda pegava o celular — e o que sai do
// celular chega sem nome, não entra no histórico e mantém viva a conexão paralela
// que o trabalho inteiro veio fechar.
//
// O QUE ESTE ARQUIVO PRENDE
//
//  1. O CONTEÚDO QUE VAI PRO BAILEYS. Cada tipo tem a sua chave — `image`, `video`,
//     `document` — e documento é o único que leva `fileName`. Trocar a chave não dá
//     erro: manda a coisa errada, e o cliente recebe um arquivo que não abre.
//
//  2. O PONTEIRO DE VOLTA. O Baileys devolve a mensagem já montada, com o
//     directPath e a mediaKey do que acabou de subir. É a MESMA `midiaDaMsg` da
//     entrada lendo esse retorno — um formato só pros dois sentidos. Sem isto a
//     bolha do vendedor viraria um "📷 Foto" cego e ele abriria o celular pra
//     conferir o que mandou, que é o hábito que este passo veio quebrar.
//
//  3. OS TETOS. Este processo tem 1 CPU e segura TODAS as sessões de WhatsApp.
//     Medido antes de escolher: opera em ~110 MB com heap de 1024 e contêiner de
//     2 GB, então 16 MB — que vira ~4 cópias entre buffer bruto, cifrado e upload —
//     cabe com folga. O que faz o teto valer é a fila de concorrência 1: sem ela,
//     N vendedores mandando junto multiplicariam isto por N.
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-midia-saida.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

// O servidor instala um handler de uncaughtException que LOGA em vez de derrubar —
// com LOG_LEVEL=silent isso engole erro de teste e o arquivo "passa" mudo. Já
// aconteceu uma vez, no teste da entrada: um export faltando saiu com código 0.
process.on('uncaughtException', (e) => {
  console.error('\nESTOUROU:', (e && e.stack) || e)
  process.exit(1)
})

const { midiaDaMsg, LIMITE_MIDIA } = require('./server')
if (typeof midiaDaMsg !== 'function' || !LIMITE_MIDIA) {
  console.error('FALHA: o server.js não exportou midiaDaMsg/LIMITE_MIDIA')
  process.exit(1)
}

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const CHAVE = Buffer.from('0123456789abcdef0123456789abcdef')
const CAMINHO = '/v/t62.7118-24/enviada_112233_n.enc'

// O que o `sendMessage` devolve: a mensagem montada, no MESMO formato da que chega.
const enviada = (conteudo) => ({ key: { id: 'OUT1', fromMe: true }, message: conteudo })

// ------------------------------------------------- o ponteiro do que acabou de sair

console.log('\no ponteiro volta do envio, igual ao da entrada')

const foto = midiaDaMsg(enviada({ imageMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'image/jpeg',
  fileLength: 184320, width: 1280, height: 960 } }))
conferir(foto && foto.tipo === 'imagem', 'foto enviada devolve tipo imagem')
conferir(foto && foto.ref.directPath === CAMINHO, 'com o endereço no CDN')
conferir(foto && foto.ref.mediaKey === CHAVE.toString('base64'),
  'e a chave em base64 — é ela que decifra quando alguém abrir a conversa')
conferir(foto && foto.meta.bytes === 184320, 'o tamanho vai pra bolha')

const doc = midiaDaMsg(enviada({ documentMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'application/pdf',
  fileLength: 167200, fileName: 'Orçamento Nº 13.pdf' } }))
conferir(doc && doc.tipo === 'documento', 'documento enviado')
conferir(doc && doc.meta.nome === 'Orçamento Nº 13.pdf',
  'o nome volta — sem ele o orçamento da empresa chega como "arquivo"')

const video = midiaDaMsg(enviada({ videoMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'video/mp4',
  fileLength: 3657490, seconds: 17 } }))
conferir(video && video.tipo === 'video', 'vídeo enviado')
conferir(video && video.marca === '🎬 Vídeo (0:17)',
  'a marca traz a duração, igual à da entrada')

console.log('\nquando o ponteiro NÃO vem')
conferir(midiaDaMsg(enviada({ conversation: 'oi' })) === null,
  'texto não tem ponteiro — e o Python trata isso sem perder a mensagem')
conferir(midiaDaMsg(enviada({ imageMessage: { mimetype: 'image/jpeg' } })) === null,
  'sem directPath nem mediaKey não se promete uma bolha que nunca carrega')

// ------------------------------------------------------------------------ os tetos

console.log('\nos tetos, que existem por causa da memória deste processo')
conferir(LIMITE_MIDIA.imagem === 5 * 1024 * 1024, 'foto: 5 MB')
// 32 e não 16: o `.mov` de iPhone que o dono tentou mandar em 28/08 passava de 16,
// e vídeo de celular passa disso com poucos segundos. O teto é NOSSO (memória deste
// processo), não do WhatsApp — o Baileys não impõe limite de bytes nenhum.
conferir(LIMITE_MIDIA.video === 32 * 1024 * 1024, 'vídeo: 32 MB')
conferir(LIMITE_MIDIA.documento === 16 * 1024 * 1024, 'documento: 16 MB')
conferir(LIMITE_MIDIA.video >= LIMITE_MIDIA.imagem,
  'vídeo não pode ter teto menor que foto — derrubaria vídeo de poucos segundos')
conferir(LIMITE_MIDIA.audio === undefined,
  'áudio NÃO entra aqui: tem rota e teto próprios (LIMITE_VOZ_BYTES, 1 MB)')
conferir(Object.keys(LIMITE_MIDIA).length === 3,
  'tipo fora da lista não tem teto, e sem teto a rota recusa antes de ler o corpo')

// ------------------------------------------------------------------------- fecho

console.log(falhas ? `\n${falhas} FALHA(S)\n` : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
