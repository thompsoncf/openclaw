'use strict'
/*
 * ZAQ · wa-qr — Outbox do repasse ao web (tabela wa_qr_entrada_fila, migração 261).
 *
 * POR QUE EXISTE, com data e número. Em 15/09/2026 a conta 38 foi pareada às 09:56
 * e o sync de histórico dela derrubou o web. Nos ~2 minutos de 502 o wa-qr perdeu
 * TRÊS mensagens de cliente (contas 23 e 34) e um eco de saída: o repasse era um
 * `fetch` único, sem timeout e sem retentativa, então cada falha virava uma linha
 * `warn` e a mensagem sumia. O WhatsApp já a deu por entregue e não reenvia.
 *
 * A vazão do histórico foi consertada em separado; isso tratou a CAUSA daquele dia.
 * Aqui se trata a CONSEQUÊNCIA, que independe do motivo: web em deploy, 5xx, pico,
 * worker preso — a mensagem fica gravada e entra quando der.
 *
 * COMO FUNCIONA. `enfileirar` grava ANTES de qualquer rede. `drenar` entrega em
 * ordem de chegada o que está pendente e na hora, marca `entregue_em` no 2xx e, em
 * qualquer outra coisa, agenda a próxima tentativa com espera crescente. Nunca
 * apaga. Depois de PARAR_EM tentativas a linha vira dead-letter (`parada_em`): sai
 * do caminho pra não segurar a fila da conta, e fica na tabela pra alguém olhar.
 *
 * TRÊS DECISÕES QUE VALEM SER LIDAS ANTES DE MEXER:
 *
 * 1. O POST ACONTECE FORA DA TRANSAÇÃO. A forma óbvia — abrir transação, `for
 *    update skip locked`, postar dentro — segura uma das QUATRO conexões do pool
 *    (`max: 4`) durante uma chamada de rede que pode levar 15s. Um web lento
 *    travaria o log, a trava de sessão e o `auth-db` junto, que é decifragem
 *    parando. Então a transação só ARRENDA as linhas (empurra `proxima_em` pra
 *    frente e solta), o POST corre solto, e o resultado volta num update curto. Se
 *    o processo morrer no meio, o arrendamento vence e outro drenador pega.
 *
 * 2. ORDEM. O drenador de uma conta PARA na primeira falha da rodada. Entregar a
 *    mensagem 3 antes da 2 embaralha a conversa do vendedor; esperar 5s não.
 *
 * 3. TABELA AUSENTE. Quem cria a tabela é a migração do web. Se o wa-qr subir
 *    antes (acontece — ver README, "A janela entre os dois deploys"), `enfileirar`
 *    devolve `null` e quem chama cai no fetch direto de sempre. Nunca fica pior do
 *    que era.
 */

const ROTAS = {
  entrada: '/webhooks/wa-qr',
  saida: '/webhooks/wa-qr/saida'
}

// espera entre tentativas, por número de falhas já ocorridas; a última vale sempre
const ESPERAS_MS = [5e3, 30e3, 120e3, 600e3, 1800e3, 3600e3]
const PARAR_EM = parseInt(process.env.WA_QR_FILA_PARAR_EM || '12', 10)      // ~6h
const TIMEOUT_MS = parseInt(process.env.WA_QR_FILA_TIMEOUT_MS || '15000', 10)
const LOTE = parseInt(process.env.WA_QR_FILA_LOTE || '20', 10)
const RETENCAO_DIAS = parseInt(process.env.WA_QR_FILA_RETENCAO_DIAS || '7', 10)
// Arrendamento: quanto tempo uma linha pega pra entrega fica invisível pros outros
// drenadores. Tem que ser maior que o timeout do POST, senão dois drenadores
// postam a mesma mensagem — o Python dedup por provider_sid aguenta, mas é ida à
// rede à toa.
const ARRENDO_MS = parseInt(process.env.WA_QR_FILA_ARRENDO_MS || '60000', 10)

function proximaEspera (tentativas) {
  return ESPERAS_MS[Math.min(Math.max(tentativas, 0), ESPERAS_MS.length - 1)]
}

function tabelaAusente (e) {
  // 42P01 = undefined_table
  return !!(e && (e.code === '42P01' || /wa_qr_entrada_fila.*does not exist/i.test(String(e))))
}

// Grava. Devolve o id da linha nova, `false` se já estava na fila (reentrega do
// Baileys) ou `null` se a tabela ainda não existe (quem chama vai direto).
async function enfileirar (pool, { contaId, rota, sid, corpo }) {
  if (!ROTAS[rota]) throw new Error('rota desconhecida: ' + rota)
  try {
    const r = await pool.query(
      `insert into wa_qr_entrada_fila (conta_id, rota, provider_sid, corpo)
       values ($1, $2, $3, $4::jsonb)
       on conflict (conta_id, rota, provider_sid) where provider_sid <> '' do nothing
       returning id`,
      [contaId, rota, sid || '', JSON.stringify(corpo)])
    return r.rows.length ? r.rows[0].id : false
  } catch (e) {
    if (tabelaAusente(e)) return null
    throw e
  }
}

// Um POST. Devolve { ok, status, erro }. Nunca lança.
async function postar (deps, rota, corpo) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), deps.timeoutMs || TIMEOUT_MS)
  try {
    const r = await deps.fetch(deps.appUrl + ROTAS[rota], {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-wa-secret': deps.segredo },
      body: JSON.stringify(corpo),
      signal: ctl.signal
    })
    return { ok: r.ok, status: r.status, erro: r.ok ? null : ('http ' + r.status) }
  } catch (e) {
    return { ok: false, status: null, erro: String(e && e.name === 'AbortError' ? 'timeout' : e) }
  } finally { clearTimeout(t) }
}

// ARRENDA as linhas: marca `proxima_em` no futuro pra ninguém mais pegar, e SOLTA a
// transação. Nenhuma rede acontece aqui dentro — ver a decisão 1 no topo.
async function arrendar (pool, { contaId = null, lote = LOTE, arrendoMs = ARRENDO_MS } = {}) {
  const r = await pool.query(
    `update wa_qr_entrada_fila
        set proxima_em = now() + ($3 || ' milliseconds')::interval
      where id in (
        select id from wa_qr_entrada_fila
         where entregue_em is null and parada_em is null and proxima_em <= now()
           and ($1::bigint is null or conta_id = $1)
         order by id
         for update skip locked
         limit $2)
      returning id, conta_id, rota, provider_sid, corpo, tentativas`,
    [contaId, lote, String(arrendoMs)])
  // ORDENA AQUI, e não confie no `order by` lá de dentro: o RETURNING de um UPDATE
  // devolve as linhas na ordem em que o Postgres as ATUALIZOU, que não é a da
  // subconsulta. Sem esta linha o drenador postava fora de ordem — e ordem é
  // metade do motivo deste módulo existir (ver a decisão 2 no topo). Quem pegou
  // foi o teste, não a leitura: o SQL "parecia" ordenado.
  return r.rows.sort((x, y) => (BigInt(x.id) < BigInt(y.id) ? -1 : BigInt(x.id) > BigInt(y.id) ? 1 : 0))
}

// Devolve pra fila as linhas que foram ARRENDADAS mas não chegaram a ser tentadas
// (o drenador parou na primeira falha — ver a decisão 2 no topo). Sem isto elas
// ficariam invisíveis até o arrendamento vencer, ou seja, o web voltar e a conta
// continuar muda por um minuto. Foi o teste que pegou.
async function soltarArrendo (pool, ids) {
  if (!ids || !ids.length) return
  await pool.query(
    'update wa_qr_entrada_fila set proxima_em = now() where id = any($1::bigint[])', [ids])
}

async function marcarEntregue (pool, id, status) {
  await pool.query(
    `update wa_qr_entrada_fila set entregue_em = now(), ultimo_status = $2, ultimo_erro = null
      where id = $1`, [id, status])
}

async function marcarFalha (pool, item, res) {
  const tentativas = item.tentativas + 1
  const parada = tentativas >= PARAR_EM
  await pool.query(
    `update wa_qr_entrada_fila
        set tentativas = $2, proxima_em = now() + ($3 || ' milliseconds')::interval,
            ultimo_status = $4, ultimo_erro = $5,
            parada_em = case when $6 then now() else parada_em end
      where id = $1`,
    [item.id, tentativas, String(proximaEspera(tentativas)), res.status,
      String(res.erro || '').slice(0, 500), parada])
  return { tentativas, parada }
}

// Entrega o que está pendente e na hora, em ordem, pra UMA conta (ou todas, se
// contaId for null — modo processo único). Para na primeira falha.
// Devolve { entregues, falhou, paradas }.
async function drenar (pool, deps, { contaId = null, lote = LOTE, arrendoMs = ARRENDO_MS } = {}) {
  const out = { entregues: 0, falhou: false, paradas: 0 }
  let itens
  try {
    itens = await arrendar(pool, { contaId, lote, arrendoMs })
  } catch (e) {
    if (!tabelaAusente(e)) deps.log.warn({ e: String(e) }, 'fila: não deu pra ler a fila')
    return out
  }
  for (let k = 0; k < itens.length; k++) {
    const item = itens[k]
    // as que ainda não foram tentadas nesta rodada, pra soltar se a gente parar
    const naoTentadas = () => itens.slice(k + 1).map((x) => x.id)
    const res = await postar(deps, item.rota, item.corpo)
    try {
      if (res.ok) {
        await marcarEntregue(pool, item.id, res.status)
        out.entregues++
        // Estes dois textos são procurados pelo checklist do CLAUDE.md §1 e pelas
        // queries do README — continuam saindo, como sempre.
        deps.log.info({ contaId: item.conta_id, rota: item.rota },
          item.rota === 'entrada' ? 'entrada repassada ao webhook ✓' : 'saída repassada ao webhook ✓')
        if (item.tentativas > 0) {
          deps.log.info({ contaId: item.conta_id, rota: item.rota, tentativas: item.tentativas + 1 },
            'fila: entregue com atraso ✓ — esta é a mensagem que antes se perdia')
        }
        continue
      }
      const { tentativas, parada } = await marcarFalha(pool, item, res)
      out.falhou = true
      if (parada) {
        out.paradas++
        deps.log.error({ contaId: item.conta_id, rota: item.rota, id: item.id, tentativas, erro: res.erro },
          'fila: mensagem PARADA (dead-letter) — esgotou as tentativas; olhar wa_qr_entrada_fila')
      } else {
        deps.log.warn({ contaId: item.conta_id, rota: item.rota, tentativas,
          status: res.status, erro: res.erro,
          proximaEmS: Math.round(proximaEspera(tentativas) / 1000) },
        'fila: entrega falhou, vai tentar de novo')
      }
      // ordem: não passa por cima da que falhou — e as de trás voltam pra fila
      // em vez de ficarem penduradas no arrendamento
      await soltarArrendo(pool, naoTentadas())
      break
    } catch (e) {
      // Falhou ao GRAVAR o resultado. A linha está arrendada e volta sozinha
      // quando o arrendamento vencer — nada se perde, só atrasa.
      if (!tabelaAusente(e)) deps.log.warn({ e: String(e) }, 'fila: não deu pra marcar o resultado')
      try { await soltarArrendo(pool, naoTentadas()) } catch (_) {}
      break
    }
  }
  return out
}

// Retenção: só o que já foi entregue, e só depois de RETENCAO_DIAS.
async function limparEntregues (pool) {
  try {
    await pool.query(
      `delete from wa_qr_entrada_fila
        where entregue_em is not null and entregue_em < now() - ($1 || ' days')::interval`,
      [String(RETENCAO_DIAS)])
  } catch (_) {}
}

module.exports = { ROTAS, ESPERAS_MS, PARAR_EM, TIMEOUT_MS, LOTE, ARRENDO_MS,
  proximaEspera, tabelaAusente, enfileirar, postar, arrendar, soltarArrendo, drenar, limparEntregues }
