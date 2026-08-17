'use strict'
// Vigia da sessão MUDA (sessaoMuda + tetoMudo + marcarVivo).
//
// O caso real: a conta da Confeitaria Doce Mell parou de receber às 13:28 e ficou assim
// por horas — sem mensagem, sem eco do celular, sem contato — enquanto as outras contas
// do mesmo processo seguiam normais. O painel continuou mostrando o chip CONECTADO,
// porque o status é o que ficou na memória do último 'open', e o iniciarSessao devolve
// a sessão existente quando ela se diz conectada: nem o botão de reconectar
// ressuscitava. Só um deploy resolvia.
//
// Por que a regra merece teste próprio: ela decide RELIGAR socket de produção. Religar
// de menos deixa cliente sem receber sem ninguém saber; religar demais é o jeito rápido
// de o WhatsApp achar que é abuso e derrubar (ou banir) o número. O silêncio é testável
// aqui; o ping, não (é rede) — e o teto existe justamente porque naquele caso o ping
// respondia normalmente enquanto nada era entregue.
//
// Não entra no pytest: é Node. Manual, e não precisa de banco nem de WhatsApp:
//
//     cd services/wa-qr && npm install && node teste-vigia.js

process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://x@127.0.0.1:5432/x'
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'

const s = require('./server')
const { sessaoMuda, tetoMudo, sessaoOrfa, esperaPos440, sessaoFirme, socketAtual,
  marcarVivo, sessoes } = s

let falhas = 0
function conferir (ok, descricao) {
  console.log((ok ? '  ok   ' : '  FALHA') + ' ' + descricao)
  if (!ok) falhas++
}

const LIMITE = 10 * 60 * 1000
const AGORA = 1_700_000_000_000
const SOCK = { fake: true }
// conectada e recebendo há pouco: o caso normal, que não pode ser tocado
const viva = (extra) => Object.assign(
  { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 1000 }, extra || {})

console.log('\nsessão que NÃO pode ser religada')
conferir(sessaoMuda(viva(), AGORA, LIMITE) === false,
  'conectada e com evento recente — trabalhando normal')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - LIMITE + 1 }), AGORA, LIMITE) === false,
  'calada, mas ainda dentro do limite')
conferir(sessaoMuda(viva({ status: 'reconectando' }), AGORA, LIMITE) === false,
  'reconectando — já tem quem cuide dela')
conferir(sessaoMuda(viva({ status: 'aguardando_qr', ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'aguardando QR — está esperando gente, não rede; religar mataria o QR na tela')
conferir(sessaoMuda(viva({ status: 'desconectado', ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'desconectada — não é caso do vigia')
conferir(sessaoMuda(viva({ sock: null, ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'sem socket — não há o que pingar')
conferir(sessaoMuda(viva({ iniciando: true, ultimoEvento: AGORA - 3 * LIMITE }), AGORA, LIMITE) === false,
  'no meio do iniciarSessao — derrubar aqui é atropelar a reconexão em curso')
conferir(sessaoMuda(viva({ ultimoEvento: 0 }), AGORA, LIMITE) === false,
  'sessão de antes do carimbo existir (ultimoEvento ausente) — na dúvida, não mexe')
for (const [valor, rotulo] of [[undefined, 'undefined'], [null, 'null'], [{}, 'objeto vazio']]) {
  let ok = false
  try { ok = sessaoMuda(valor, AGORA, LIMITE) === false } catch (_) { ok = false }
  conferir(ok, rotulo + ' → não é sessão muda, sem exceção')
}

console.log('\nsessão que PRECISA ser religada')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - LIMITE }), AGORA, LIMITE) === true,
  'silêncio exatamente no limite')
conferir(sessaoMuda(viva({ ultimoEvento: AGORA - 3 * 60 * 60 * 1000 }), AGORA, LIMITE) === true,
  'o caso da Doce Mell: 3h "conectada" sem entregar um evento')

console.log('\nteto do silêncio (quando religar mesmo com o ping respondendo)')
const TETO = 45 * 60 * 1000
conferir(tetoMudo({}, TETO) === TETO,
  'sessão que nunca foi religada à toa — teto cheio, sem desconto')
conferir(tetoMudo({ reconexoesMudas: 1 }, TETO) === 2 * TETO,
  'religou uma vez e continuou muda — espera o dobro antes de insistir')
conferir(tetoMudo({ reconexoesMudas: 3 }, TETO) === 8 * TETO,
  'terceira sem resultado — 6h, não 45min')
conferir(tetoMudo({ reconexoesMudas: 9 }, TETO) === 16 * TETO,
  'a dobra tem limite: 16× (12h) e para de crescer')
conferir(tetoMudo({ reconexoesMudas: 0 }, TETO) === TETO,
  'contador zerado por um evento de verdade — volta ao teto normal')

console.log('\nmarcarVivo carimba a sessão certa')
sessoes.set(35, { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 3 * LIMITE, reconexoesMudas: 3 })
sessoes.set(23, { status: 'conectado', sock: SOCK, ultimoEvento: AGORA - 3 * LIMITE, reconexoesMudas: 3 })
marcarVivo(35)
conferir(sessaoMuda(sessoes.get(35), Date.now(), LIMITE) === false,
  'a conta que recebeu evento sai da mira do vigia')
conferir(tetoMudo(sessoes.get(35), TETO) === TETO,
  'e volta pro teto normal — a desconfiança acumulada zera junto')
conferir(sessaoMuda(sessoes.get(23), Date.now(), LIMITE) === true,
  'a conta vizinha NÃO é carimbada junto')
let explodiu = false
try { marcarVivo(999) } catch (_) { explodiu = true }
conferir(!explodiu, 'conta sem sessão em memória — no-op, sem exceção')
sessoes.delete(35); sessoes.delete(23)

// A regra que compõe o vigia com a trava de sessão (sessao-lock.js). As duas nasceram
// do MESMO incidente por caminhos diferentes: a trava impede que duas instâncias
// disputem a credencial no deploy; o vigia religa socket que morreu calado. Juntas
// sem cuidado, elas se desfazem — o vigia de uma instância que NÃO segura a conta
// religaria por cima da instância que está trabalhando, que é exatamente a guerra de
// sessões (440 em revezamento) que a trava veio acabar. Por isso: sessão que não é
// nossa, não se toca. Sem rede e sem banco — o guard corta antes de qualquer um dos dois.
// A CONTA ÓRFÃ DE 440 — o buraco que custou a noite da cliente.
//
// O caminho do 440 solta a trava e retorna, e estava certo em não reconectar na hora:
// quem substituiu pode ser o WhatsApp Web que a própria cliente abriu, e insistir
// contra ele derruba os dois em revezamento. O que faltava era o DEPOIS. Em 15/08 a
// conta 35 levou 440 às 22:46:07, soltou a trava e ficou sem sessão em lugar nenhum —
// com o serviço saudável, atendendo as vizinhas. As mensagens que o dono mandou pra
// testar não chegaram porque não havia ninguém escutando.
//
// E o vigia, que podia resgatar, se recusava: `!trava.segura()` foi escrito presumindo
// "não é minha = tem outra instância cuidando", e "não é minha" também cobre "não é de
// NINGUÉM". Estas conferências existem pra que a distinção não se perca de novo, e pra
// que a espera crescente (que é o que evita a guerra de sessões) não seja encurtada
// sem querer.
console.log('\nespera antes de retomar uma conta substituída')
const BASE = 5 * 60 * 1000
conferir(esperaPos440({}, BASE) === BASE, 'primeira tentativa: 5min')
conferir(esperaPos440({ tentativasPos440: 1 }, BASE) === 2 * BASE, 'segunda: 10min')
conferir(esperaPos440({ tentativasPos440: 3 }, BASE) === 8 * BASE, 'quarta: 40min')
conferir(esperaPos440({ tentativasPos440: 9 }, BASE) === 16 * BASE, 'teto em 16× (80min)')

console.log('\nquem É órfã (e só depois da espera)')
conferir(sessaoOrfa({ substituidaEm: AGORA - BASE }, AGORA, BASE) === true,
  'sem socket, substituída há 5min — retoma')
conferir(sessaoOrfa({ substituidaEm: AGORA - BASE + 1 }, AGORA, BASE) === false,
  'um milissegundo antes da espera — ainda não')
conferir(sessaoOrfa({ substituidaEm: AGORA - 3 * BASE, tentativasPos440: 4 }, AGORA, BASE) === false,
  'já tentou 4 vezes: a espera dela agora é 80min, e 15min não bastam')

console.log('\nquem NÃO é órfã')
conferir(sessaoOrfa({ sock: SOCK, substituidaEm: AGORA - 3 * BASE }, AGORA, BASE) === false,
  'tem socket — quem cuida dela é a regra do silêncio, não esta')
conferir(sessaoOrfa({ iniciando: true, substituidaEm: AGORA - 3 * BASE }, AGORA, BASE) === false,
  'já está subindo — não empilha uma segunda tentativa por cima')
conferir(sessaoOrfa({ status: 'desconectado' }, AGORA, BASE) === false,
  'nunca levou 440 (sem carimbo) — não é caso desta regra')
for (const [valor, rotulo] of [[undefined, 'undefined'], [null, 'null'], [{}, 'objeto vazio']]) {
  let ok = false
  try { ok = sessaoOrfa(valor, AGORA, BASE) === false } catch (_) { ok = false }
  conferir(ok, rotulo + ' → não é órfã, sem exceção')
}

;(async () => {
  console.log('\no vigia retoma a órfã — e conta a tentativa')
  const original = s._ganchos.iniciarSessao
  const originalPareada = s._ganchos.contaPareada
  // a órfã com credencial no banco é o caso normal deste bloco
  s._ganchos.contaPareada = async () => true
  const retomadas = []
  s._ganchos.iniciarSessao = async (contaId) => { retomadas.push(contaId) }
  sessoes.clear()
  // 35: órfã há bastante tempo. 34: substituída agora, ainda dentro da espera.
  sessoes.set(35, { status: 'desconectado', sock: null, substituidaEm: Date.now() - 30 * 60 * 1000 })
  sessoes.set(34, { status: 'desconectado', sock: null, substituidaEm: Date.now() })
  await s.vigiarSessoes()
  conferir(retomadas.length === 1 && retomadas[0] === 35,
    'só a que passou da espera foi retomada', 'retomadas=' + JSON.stringify(retomadas))
  conferir(sessoes.get(35).tentativasPos440 === 1, 'a tentativa ficou contada')
  conferir(Date.now() - sessoes.get(35).substituidaEm < 5000,
    'o relógio da espera reiniciou — a próxima só daqui a 10min')
  await s.vigiarSessoes()
  conferir(retomadas.length === 1, 'e na volta seguinte ela NÃO é retomada de novo')
  sessoes.clear()

  // ---------------------------------------------------------------------------
  // O caso da Doce Mell (17/08): 440 às 12:04 marcou a conta como órfã; às 12:07
  // alguém clicou "Desconectar" no painel, que APAGA a credencial do banco; às
  // 12:10 o vigia "retomou" a órfã e, sem credencial pra retomar, o socket só
  // soube pedir QR — cliente levando QR na tela de uma conta que ele mesmo tinha
  // acabado de desligar. A marca de órfã é de memória; a credencial é do banco, e
  // é o banco que manda.
  console.log('\nórfã que foi DESCONECTADA de propósito não volta sozinha')
  const semCred = []
  s._ganchos.contaPareada = async (contaId) => { semCred.push(contaId); return false }
  sessoes.set(35, { status: 'desconectado', sock: null,
    substituidaEm: Date.now() - 30 * 60 * 1000, tentativasPos440: 2 })
  await s.vigiarSessoes()
  conferir(retomadas.length === 1, 'sem credencial no banco — o vigia NÃO retoma')
  conferir(semCred.length === 1 && semCred[0] === 35, 'e foi ao banco conferir antes de decidir')
  conferir(sessoes.get(35).substituidaEm === null,
    'a marca de órfã sai — não reavalia isso a cada volta')
  conferir(sessoes.get(35).tentativasPos440 === 0, 'e o contador de retomadas zera junto')
  await s.vigiarSessoes()
  conferir(retomadas.length === 1 && semCred.length === 1,
    'na volta seguinte ela nem chega a ser avaliada de novo')
  sessoes.clear()

  // Banco fora do ar não pode virar conta parada: deixar uma sessão VIVA órfã é
  // pior que um QR à toa. Na dúvida, o vigia faz o que sempre fez.
  console.log('\nbanco fora do ar: na dúvida o vigia retoma (não perde a conta)')
  s._ganchos.contaPareada = async () => { throw new Error('banco fora do ar') }
  sessoes.set(35, { status: 'desconectado', sock: null,
    substituidaEm: Date.now() - 30 * 60 * 1000 })
  await s.vigiarSessoes()
  conferir(retomadas.length === 2 && retomadas[1] === 35,
    'credencial não pôde ser conferida — retoma assim mesmo')
  conferir(sessoes.get(35).tentativasPos440 === 1, 'e conta a tentativa normalmente')
  sessoes.clear()

  // A checagem nova mora DENTRO do ramo da órfã (que exige sock null). Quem está
  // conectado não pode nem ser consultado — muito menos tocado.
  console.log('\nconta conectada não passa nem perto da checagem de credencial')
  let consultou = false
  s._ganchos.contaPareada = async () => { consultou = true; return false }
  const sockVivo = { fake: 'vivo' }
  sessoes.set(35, { status: 'conectado', sock: sockVivo, ultimoEvento: Date.now(),
    substituidaEm: Date.now() - 30 * 60 * 1000 })
  await s.vigiarSessoes()
  conferir(consultou === false, 'tem socket vivo — o ramo da órfã nem roda')
  conferir(sessoes.get(35).sock === sockVivo && sessoes.get(35).status === 'conectado',
    'socket e status intactos')
  conferir(retomadas.length === 2, 'e ninguém foi religado')

  s._ganchos.iniciarSessao = original
  s._ganchos.contaPareada = originalPareada
  sessoes.clear()

  console.log('\nmarcarVivo tira a conta da condição de órfã')
  sessoes.set(35, { status: 'conectado', sock: SOCK, substituidaEm: AGORA - 3 * BASE,
    tentativasPos440: 3 })
  marcarVivo(35)
  conferir(sessaoOrfa(sessoes.get(35), Date.now(), BASE) === false && !sessoes.get(35).substituidaEm,
    'entregou evento: o carimbo do 440 sai')

  // ------------------------------------------------------------------------
  // sessaoFirme — a dobra do esperaPos440 não estava dobrando.
  //
  // Quem zerava `tentativasPos440` era o marcarVivo, e numa conta movimentada
  // sempre chega um recibo entre o socket subir e levar 440 de novo. Resultado
  // medido em produção na noite de 15/08: a conta 23 foi substituída às 20:42,
  // 20:48, 20:54, 21:00, 21:06, 21:12, 21:18, 21:24 e 21:30 — de 6 em 6 minutos,
  // no relógio, a noite inteira. A espera ficava presa nos 5 minutos iniciais e
  // a "guerra de sessões" que a dobra existia pra evitar rodava sozinha.
  //
  // Cada religamento desses refaz a sessão criptográfica com quem está do outro
  // lado: as mensagens enviadas em volta da troca chegam ilegíveis e o aparelho
  // do cliente mostra "Aguardando mensagem" até pedir reenvio. Foi o atraso de
  // 2min30 que apareceu no teste do agente.
  const FIRME = 15 * 60 * 1000
  console.log('\nsessaoFirme: só o tempo DE PÉ zera a contagem de retomadas')
  conferir(sessaoFirme({ abertoEm: AGORA - FIRME }, AGORA, FIRME) === true,
    'aberta há 15min: firme')
  conferir(sessaoFirme({ abertoEm: AGORA - FIRME + 1 }, AGORA, FIRME) === false,
    'um milissegundo antes: ainda não')
  conferir(sessaoFirme({}, AGORA, FIRME) === false,
    'sem abertoEm (nunca abriu, ou veio de deploy antigo): na dúvida, não é firme')
  conferir(sessaoFirme(null, AGORA, FIRME) === false, 'sessão inexistente não estoura')

  sessoes.set(35, { status: 'conectado', sock: SOCK, abertoEm: Date.now(), tentativasPos440: 3 })
  marcarVivo(35)
  conferir(sessoes.get(35).tentativasPos440 === 3,
    'subiu agora e já entregou um recibo: a contagem NÃO zera — era este o furo')

  sessoes.set(35, { status: 'conectado', sock: SOCK, abertoEm: Date.now() - FIRME - 1,
    tentativasPos440: 3 })
  marcarVivo(35)
  conferir(sessoes.get(35).tentativasPos440 === 0,
    'de pé há mais de 15min: aí sim a retomada pegou e a espera volta ao começo')

  // e o efeito prático da correção: a espera cresce a cada retomada que não segura
  conferir(esperaPos440({ tentativasPos440: 0 }, BASE) === 5 * 60 * 1000 &&
           esperaPos440({ tentativasPos440: 3 }, BASE) === 40 * 60 * 1000,
    'sem zerar à toa, a espera vai de 5min a 40min em vez de repetir 5min pra sempre')
  sessoes.clear()

  // ------------------------------------------------------------------------
  // O 'open' ATRASADO — a sessão que se dizia conectada sem ter socket.
  //
  // Medido na conta 34 em 15/08, nesta ordem:
  //
  //     22:16:15.890  conexão substituída  → s.sock = null
  //     22:16:16.402  WhatsApp conectado   ← meio segundo DEPOIS, do socket morto
  //
  // O handler de 'open' punha status='conectado' e recarimbava a sessão. Daí em
  // diante ela mentia pra todo mundo: chip verde no painel, o vigia do silêncio
  // pulando a conta (sessaoMuda exige s.sock) e só o /enviar descobrindo, na hora de
  // mandar a mensagem do cliente, que não havia socket nenhum — que é exatamente o
  // "conectado e mudo" que abriu esta investigação.
  console.log('\nevento só vale se vier do socket de agora')
  const atual = { fake: 'atual' }
  const velho = { fake: 'velho' }
  conferir(socketAtual({ sock: atual }, atual) === true, 'o socket da sessão: passa')
  conferir(socketAtual({ sock: null }, velho) === false,
    'levou 440 e a sessão soltou o socket — o open atrasado dele não entra')
  conferir(socketAtual({ sock: atual }, velho) === false,
    'já trocamos de encarnação — o socket anterior não fala pela sessão')
  conferir(socketAtual({ sock: velho }, Object.assign({ _descartado: true }, velho)) === false,
    'socket descartado não fala nem sendo o da sessão')
  conferir(socketAtual(null, atual) === false, 'sessão inexistente não estoura')
  conferir(socketAtual({ sock: atual }, null) === false, 'sem socket no evento não estoura')

  console.log('\nvigia × trava: sessão de outra instância não se religa')
  const seguraDeVerdade = s.trava.segura
  s.trava.segura = () => false
  sessoes.clear()
  const sock = { fake: true }
  sessoes.set(35, { status: 'conectado', sock, ultimoEvento: AGORA - 5 * 60 * 60 * 1000 })
  await s.vigiarSessoes()
  const depois = sessoes.get(35)
  conferir(depois.sock === sock && depois.status === 'conectado',
    'muda há 5h, mas a trava é da outra instância — socket e status intactos')
  conferir(!depois.reconexoesMudas, 'e nem contou tentativa de religamento')
  s.trava.segura = seguraDeVerdade
  sessoes.clear()

  console.log(falhas ? '\n' + falhas + ' FALHA(S)\n' : '\ntudo certo\n')
  process.exit(falhas ? 1 : 0)
})()
