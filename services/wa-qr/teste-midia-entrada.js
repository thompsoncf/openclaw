'use strict'
// A mídia que chega: o PONTEIRO, nunca o arquivo.
//
// O CASO REAL (28/08/2026, Prime Eventos). Em 48 horas, 598 mensagens de um-para-um
// sem texto foram descartadas — 299 por dia, contra 124 medidos uma semana antes.
// Não são grupos nem status: esse filtro já sai da conta. É o cliente mandando foto
// de referência de decoração, print do orçamento do concorrente, vídeo do salão,
// comprovante do sinal. Nada disso existia no Zaq, e o vendedor via tudo no celular
// dele — que é a razão de ele não largar o aparelho.
//
// POR QUE PONTEIRO E NÃO ARQUIVO
// O WhatsApp já guarda a mídia cifrada no CDN dele. A mensagem traz `directPath` e
// `mediaKey`, e o `downloadContentFromMessage({mediaKey, directPath}, tipo)` não pede
// o objeto da mensagem nem o socket — só esses dois campos. Então dá pra buscar
// depois, noutro serviço, quando ALGUÉM ABRIR a conversa.
//
// Baixar aqui seria o contrário de tudo: este processo tem 1 CPU e segura todas as
// sessões. Um vídeo de 16 MB baixado e repassado em base64 são quatro cópias vivas
// (~70 MB de pico) — foi assim que a instância caiu 20 vezes em 20/08. E a maioria
// das fotos ninguém abre: seria pagar 100% da banda pelos 10% que se olha.
//
// Não entra no pytest: é Node. Manual, sem banco e sem WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-midia-entrada.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

// O servidor instala um handler de uncaughtException que LOGA em vez de derrubar —
// com LOG_LEVEL=silent isso engole erro de teste e o arquivo "passa" mudo. Foi o que
// aconteceu na primeira rodada: o export estava faltando, `midiaDaMsg` era undefined,
// e o teste saiu com código 0 depois de imprimir um cabeçalho só.
process.on('uncaughtException', (e) => {
  console.error('\nESTOUROU:', (e && e.stack) || e)
  process.exit(1)
})

const { midiaDaMsg, textoDaMsg } = require('./server')
if (typeof midiaDaMsg !== 'function' || typeof textoDaMsg !== 'function') {
  console.error('FALHA: o server.js não exportou midiaDaMsg/textoDaMsg')
  process.exit(1)
}

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const CHAVE = Buffer.from('0123456789abcdef0123456789abcdef')   // 32 bytes, como o real
const CAMINHO = '/v/t62.7118-24/12345_67890_112233_n.enc'
const msg = (conteudo) => ({ key: { id: 'X1', remoteJid: '5586999@s.whatsapp.net' },
  message: conteudo })

// --------------------------------------------------------------- os quatro tipos

console.log('\nos tipos que viram arquivo')
const foto = midiaDaMsg(msg({ imageMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'image/jpeg',
  fileLength: 184320, width: 1280, height: 960 } }))
conferir(foto && foto.tipo === 'imagem', 'imagem')
conferir(foto && foto.ref.directPath === CAMINHO, 'guarda o endereço no CDN')
conferir(foto && foto.ref.mediaKey === CHAVE.toString('base64'),
  'a chave vai em base64 (é Buffer no proto, e JSON não carrega Buffer)')
conferir(foto && foto.meta.bytes === 184320 && foto.meta.largura === 1280,
  'tamanho e dimensões pra bolha mostrar antes de carregar')
conferir(foto && foto.marca === '📷 Foto', 'a marca que vira o texto quando não há legenda')

const video = midiaDaMsg(msg({ videoMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'video/mp4', seconds: 74 } }))
conferir(video && video.tipo === 'video', 'vídeo')
conferir(video && video.marca === '🎬 Vídeo (1:14)',
  'a duração entra na marca — é o que o vendedor decide antes de gastar dado')

const doc = midiaDaMsg(msg({ documentMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'application/pdf',
  fileName: 'contrato-assinado.pdf', fileLength: 491520 } }))
conferir(doc && doc.tipo === 'documento', 'documento')
conferir(doc && doc.meta.nome === 'contrato-assinado.pdf',
  'o nome do arquivo, que é o que identifica um PDF na conversa')

const fig = midiaDaMsg(msg({ stickerMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, mimetype: 'image/webp', isAnimated: true } }))
conferir(fig && fig.tipo === 'figurinha' && fig.meta.animada === true, 'figurinha animada')

// ----------------------------------------------------- o que NÃO pode virar bolha

console.log('\nsem endereço ou sem chave não vira ponteiro')
conferir(midiaDaMsg(msg({ imageMessage: { mediaKey: CHAVE } })) === null,
  'sem directPath: não há onde buscar')
conferir(midiaDaMsg(msg({ imageMessage: { directPath: CAMINHO } })) === null,
  'sem mediaKey: não há como decifrar')
conferir(midiaDaMsg(msg({ conversation: 'oi' })) === null, 'texto puro não é mídia')
conferir(midiaDaMsg(msg({})) === null, 'mensagem vazia não estoura')
conferir(midiaDaMsg({ key: {} }) === null, 'sem message não estoura')

console.log('\náudio continua no caminho dele')
conferir(midiaDaMsg(msg({ audioMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, ptt: true, seconds: 9 } })) === null,
'áudio NÃO vira ponteiro — ele já é baixado e transcrito, e mexer nisso quebraria o que funciona')

// ------------------------------------------------------------- legenda e marca

console.log('\na legenda ganha da marca')
conferir(textoDaMsg(msg({ imageMessage: {
  directPath: CAMINHO, mediaKey: CHAVE, caption: 'queria uma decoração assim' } }))
  === 'queria uma decoração assim',
'foto COM legenda: o texto é a legenda — e essa já chegava hoje, só que perdendo a foto')
conferir(textoDaMsg(msg({ imageMessage: { directPath: CAMINHO, mediaKey: CHAVE } })) === '',
  'foto SEM legenda não tem texto — é a que era descartada, e quem resolve é a marca')

// --------------------------------------------- o que não é arquivo vira texto

console.log('\nlocalização e contato não custam armazenamento')
const loc = textoDaMsg(msg({ locationMessage: {
  degreesLatitude: -5.09194, degreesLongitude: -42.80336, name: 'Prime Eventos' } }))
conferir(loc.startsWith('📍 Localização: Prime Eventos'), 'localização vira linha legível')
conferir(loc.includes('-5.09194') && loc.includes('-42.80336'),
  'com as coordenadas, que é o que o mapa precisa')
conferir(midiaDaMsg(msg({ locationMessage: { degreesLatitude: -5, degreesLongitude: -42 } }))
  === null, 'e NÃO vira ponteiro: não há arquivo pra buscar')

conferir(textoDaMsg(msg({ contactMessage: { displayName: 'Buffet Estrela' } }))
  === '👤 Contato: Buffet Estrela', 'contato vira linha')
conferir(textoDaMsg(msg({ contactsArrayMessage: {
  contacts: [{ displayName: 'Ana' }, { displayName: 'Bruno' }] } }))
  === '👤 2 contato(s): Ana, Bruno', 'lista de contatos também')

console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
process.exit(falhas ? 1 : 0)
