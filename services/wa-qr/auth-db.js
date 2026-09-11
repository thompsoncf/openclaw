'use strict'
// Estado de autenticação do Baileys guardado no Postgres (tabela wa_qr_auth),
// por conta_id. Sobrevive a restart do serviço sem precisar de disco persistente.
// Segue o padrão do useMultiFileAuthState, trocando arquivos por linhas na tabela.
//
// VISIBILIDADE DA ESCRITA (por que estes logs existem)
// A chave que decifra a agenda do celular (app-state-sync-key) é gravada pelo
// Baileys DENTRO de uma transação (Utils/process-message.js), e o commit dessa
// transação ENGOLE a falha: addTransactionCapability tenta 10x de 3 em 3s e
// desiste sem relançar (Utils/auth-utils.js). O `creds.update` com
// myAppStateKeyId, por outro lado, sai FORA da transação — sempre, tendo gravado
// ou não. Medido em produção: creds com myAppStateKeyId 'AAAAACPQ' e ZERO linha
// app-state-sync-key-* na tabela, sem um erro sequer no log. Sem saber o que a
// escrita estourou não dá pra consertar, e é justamente essa chave que segura a
// agenda inteira (95% das conversas ficam com o número cru no lugar do nome).
//
// Por isso: toda escrita que falha é logada COM O NOME da chave e o erro, e a
// exceção é RELANÇADA — engolir aqui faria o commit do Baileys "dar certo" e a
// chave se perderia de vez, sem nem o retry dele.

// A MESMA biblioteca do worker (ver o topo do server.js). Este módulo cria as
// credenciais iniciais (initAuthCreds) e monta objetos proto pra entregar ao
// socket; misturar o proto/initAuthCreds de uma versão com o socket de outra é
// pedir um bug silencioso na hora do pareamento.
const { initAuthCreds, BufferJSON, proto } = require(process.env.WA_QR_BAILEYS === '7' ? 'baileys7' : '@whiskeysockets/baileys')

const TIPO_AGENDA = 'app-state-sync-key'
const ehChaveDaAgenda = (arquivo) => String(arquivo || '').startsWith(TIPO_AGENDA)

// A MARCA DA CHAVE QUE FALTA (o laço da conta 23)
// Medido em 24h de produção: 71 pedidos de agenda, 71 avisos de chave faltando —
// um pra um. O Baileys pedia 'AAAAAIWZ', que não está gravada; decodePatches
// estoura, o catch DELE apaga a versão da coleção, a versão volta a 0, e o pedido
// seguinte pede o snapshot INTEIRO de novo (243 KB, ~4 mil contatos decodificados
// de uma vez) — pra falhar na mesma chave. A conta 34 tinha 21 do mesmo; a 36,
// zero, e é o controle que mostra que isso não é o normal.
//
// O teto que existia (MAX_INSISTENCIAS_AGENDA) não segurava porque vive no objeto
// da sessão: some no religamento, e a conta 23 reconecta ~28x/dia. Por isso a marca
// é DURÁVEL, guardada como linha de controle na própria wa_qr_auth — mesmo lugar e
// mesmo destino da 'agenda-completa', que já é uma marca nossa e não uma chave do
// Baileys. Some junto com as credenciais no Desconectar, que é o comportamento certo:
// pareou de novo, tenta de novo.
//
// Ela NÃO apaga nada e NÃO desliga a agenda: só para de PEDIR enquanto pedir é
// garantidamente inútil. Sai sozinha quando a chave que faltava é finalmente
// gravada (é o `gravar` abaixo que a tira), e o server ainda tem uma válvula de
// tempo pra nunca ficar preso pra sempre numa conclusão minha.
const MARCA_CHAVE_FALTANDO = 'agenda-chave-faltando'

// logger opcional: sem ele o módulo continua funcionando calado (o server.js passa o pino)
const _mudo = { info () {}, warn () {}, error () {} }

async function useDbAuthState (pool, contaId, log) {
  const logger = log || _mudo

  const ler = async (arquivo) => {
    const r = await pool.query(
      'select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2',
      [contaId, arquivo])
    if (!r.rows[0]) return null
    return JSON.parse(r.rows[0].conteudo, BufferJSON.reviver)
  }
  const gravar = async (arquivo, dado) => {
    const v = JSON.stringify(dado, BufferJSON.replacer)
    // `conteudo` é `text not null`. Serialização vazia (undefined) viraria NULL e o
    // banco recusaria com uma mensagem que não diz de QUAL chave veio — falha aqui,
    // com nome, antes de chegar lá.
    if (v === undefined) {
      const erro = new Error(
        `serialização vazia para '${arquivo}' (typeof dado = ${typeof dado})`)
      logger.error({ contaId, arquivo }, 'auth: ' + erro.message)
      throw erro
    }
    try {
      await pool.query(
        `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
         values ($1,$2,$3, now())
         on conflict (conta_id, arquivo)
         do update set conteudo=excluded.conteudo, atualizado=now()`,
        [contaId, arquivo, v])
    } catch (e) {
      logger.error({ contaId, arquivo, bytes: v.length, e: String((e && e.message) || e) },
        'auth: FALHA ao gravar chave' + (ehChaveDaAgenda(arquivo) ? ' — é a chave da AGENDA' : ''))
      throw e            // relança: o Baileys tem retry (10x/3s); engolir perderia a chave
    }
    if (ehChaveDaAgenda(arquivo)) {
      logger.info({ contaId, arquivo, bytes: v.length }, 'auth: chave da agenda GRAVADA ✓')
      // Chegou chave nova: se era JUSTAMENTE a que faltava, a marca sai e o
      // resyncAgenda volta a poder pedir. Só pra o id que estava na marca — chave
      // regravada com o mesmo id de sempre não é notícia nova, e limpar a marca
      // por qualquer escrita traria o laço de volta a cada religamento.
      const id = arquivo.slice(TIPO_AGENDA.length + 1)
      try {
        const m = await pool.query(
          'select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2',
          [contaId, MARCA_CHAVE_FALTANDO])
        if (m.rows[0]) {
          const faltavam = (JSON.parse(m.rows[0].conteudo) || {}).ids || []
          if (faltavam.includes(id)) {
            await pool.query('delete from wa_qr_auth where conta_id=$1 and arquivo=$2',
              [contaId, MARCA_CHAVE_FALTANDO])
            logger.info({ contaId, id },
              'auth: chegou a chave da agenda que faltava — a agenda pode ser pedida de novo')
          }
        }
      } catch (e) {
        // não relança: a marca é otimização nossa, não pode derrubar a gravação da chave
        logger.warn({ contaId, id, e: String((e && e.message) || e) },
          'auth: falha ao limpar a marca da chave faltando')
      }
    }
  }
  const apagar = async (arquivo) => {
    // apagar a chave da agenda não é rotina — se acontecer, é isso que explica a
    // agenda sumir depois de ter vindo. Fica no log pra não virar mistério de novo.
    if (ehChaveDaAgenda(arquivo)) {
      logger.warn({ contaId, arquivo }, 'auth: APAGANDO a chave da agenda')
    }
    await pool.query('delete from wa_qr_auth where conta_id=$1 and arquivo=$2',
      [contaId, arquivo])
  }

  const creds = (await ler('creds')) || initAuthCreds()

  return {
    state: {
      creds,
      keys: {
        get: async (tipo, ids) => {
          const data = {}
          await Promise.all(ids.map(async (id) => {
            let val = await ler(`${tipo}-${id}`)
            if (tipo === TIPO_AGENDA && val) {
              val = proto.Message.AppStateSyncKeyData.fromObject(val)
            }
            data[id] = val
          }))
          // o Baileys pedindo uma chave de agenda que não está aqui é o sintoma
          // exato de "myAppStateKeyId existe mas a chave não" — denuncia na hora
          if (tipo === TIPO_AGENDA) {
            const faltando = ids.filter((id) => !data[id])
            if (faltando.length) {
              logger.warn({ contaId, pedidas: ids.length, faltando },
                'auth: o Baileys pediu chave(s) da agenda que NÃO estão gravadas')
              // grava a marca (ver MARCA_CHAVE_FALTANDO). `do nothing` de propósito:
              // a marca guarda a PRIMEIRA vez que a chave faltou, e é dessa data que
              // a válvula de tempo do server se orienta. Reescrever a cada pedido
              // empurraria o relógio pra frente e a válvula nunca venceria — o
              // remédio viraria a doença. Se outra chave faltar depois, a marca já
              // está de pé e a porta já está fechada; não há o que registrar de novo.
              try {
                await pool.query(
                  `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
                   values ($1, $2, $3, now())
                   on conflict (conta_id, arquivo) do nothing`,
                  [contaId, MARCA_CHAVE_FALTANDO,
                    JSON.stringify({ ids: faltando, desde: new Date().toISOString() })])
              } catch (e) {
                logger.warn({ contaId, e: String((e && e.message) || e) },
                  'auth: falha ao gravar a marca da chave faltando')
              }
            }
          }
          return data
        },
        set: async (data) => {
          const tarefas = []
          for (const categoria in data) {
            for (const id in data[categoria]) {
              const val = data[categoria][id]
              const arquivo = `${categoria}-${id}`
              tarefas.push(val ? gravar(arquivo, val) : apagar(arquivo))
            }
          }
          // registra a TENTATIVA antes de escrever: se a gravação estourar, o log
          // ainda mostra que a chave chegou até aqui (a transação do Baileys some
          // com o erro depois de 10 tentativas)
          if (data[TIPO_AGENDA]) {
            logger.info({ contaId, ids: Object.keys(data[TIPO_AGENDA]),
              lote: Object.keys(data).join(',') },
            'auth: chegou chave da agenda pra gravar')
          }
          await Promise.all(tarefas)
        }
      }
    },
    saveCreds: () => gravar('creds', creds),
    // apaga TODO o estado da conta (logout / desconectar de vez)
    limparTudo: async () => {
      await pool.query('delete from wa_qr_auth where conta_id=$1', [contaId])
    }
  }
}

module.exports = { useDbAuthState, MARCA_CHAVE_FALTANDO, TIPO_AGENDA }
