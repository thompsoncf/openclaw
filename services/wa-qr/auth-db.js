'use strict'
// Estado de autenticação do Baileys guardado no Postgres (tabela wa_qr_auth),
// por conta_id. Sobrevive a restart do serviço sem precisar de disco persistente.
// Segue o padrão do useMultiFileAuthState, trocando arquivos por linhas na tabela.

const { initAuthCreds, BufferJSON, proto } = require('@whiskeysockets/baileys')

async function useDbAuthState (pool, contaId) {
  const ler = async (arquivo) => {
    const r = await pool.query(
      'select conteudo from wa_qr_auth where conta_id=$1 and arquivo=$2',
      [contaId, arquivo])
    if (!r.rows[0]) return null
    return JSON.parse(r.rows[0].conteudo, BufferJSON.reviver)
  }
  const gravar = async (arquivo, dado) => {
    const v = JSON.stringify(dado, BufferJSON.replacer)
    await pool.query(
      `insert into wa_qr_auth (conta_id, arquivo, conteudo, atualizado)
       values ($1,$2,$3, now())
       on conflict (conta_id, arquivo)
       do update set conteudo=excluded.conteudo, atualizado=now()`,
      [contaId, arquivo, v])
  }
  const apagar = async (arquivo) => {
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
            if (tipo === 'app-state-sync-key' && val) {
              val = proto.Message.AppStateSyncKeyData.fromObject(val)
            }
            data[id] = val
          }))
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

module.exports = { useDbAuthState }
