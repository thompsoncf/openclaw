'use strict'
// Trava de sessão única por conta, para o WhatsApp não ser disputado por dois
// processos ao mesmo tempo.
//
// O PROBLEMA
// O Render faz deploy sem downtime: sobe a instância NOVA antes de matar a
// velha. As duas rodam o mesmo restaurarSessoes() e abrem socket com a MESMA
// credencial. O WhatsApp não tolera isso — derruba uma das duas com 440
// (connectionReplaced). Em 15/08/2026 a conta 35 abriu 7 sessões entre 13:53 e
// 20:00 e TODAS morreram assim. Cada morte reinicia o ciclo: reconecta, baixa a
// agenda inteira de novo, redespeja tudo no webhook. O serviço tem
// numInstances:1 no render.yaml — nunca foi escala horizontal, é a janela de
// sobreposição do deploy.
//
// POR QUE UMA TABELA E NÃO pg_advisory_lock
// Advisory lock de SESSÃO mora na conexão, e a conexão aqui passa pelo pooler do
// Supabase. Em modo transaction o pooler multiplexa: a mesma conexão física
// atende transações de clientes diferentes, então um lock tomado fora de
// transação não tem dono estável — ele pode sumir, ou pior, ficar presente para
// quem não o pediu. Daria pra contornar segurando uma transação aberta pra
// sempre (é o que aplicar_migracoes faz, mas por segundos, não por dias), só que
// idle-in-transaction eterno é justamente o que os poolers matam. Uma linha com
// prazo de validade funciona igual em qualquer pooler, e ainda dá pra
// consultar quem está segurando o quê — coisa que advisory lock não deixa ver.
//
// COMO FUNCIONA
// Cada instância tem uma identidade (host:pid:boot). Pegar a trava é um único
// INSERT ... ON CONFLICT DO UPDATE que só sobrescreve se a linha for nossa
// (renovação) ou se o prazo do outro tiver vencido. É atômico: duas instâncias
// tentando ao mesmo tempo, uma ganha e a outra recebe zero linha.
//
// O prazo é curto (60s) e renovado por um batimento (20s). Se o processo morre —
// SIGKILL, OOM, queda da máquina — ninguém renova, o prazo vence e a próxima
// instância assume em no máximo um TTL. Não existe trava presa pra sempre.
//
// E o inverso importa igual: se o batimento NÃO conseguir renovar, perdemos a
// exclusividade e temos que largar o socket na hora, senão viram dois de novo —
// é pra isso que serve o callback `aoPerder`.

const os = require('os')

// 60s de prazo com batimento a cada 20s dá três tentativas antes de vencer: uma
// lentidão do banco não derruba a sessão, mas uma instância morta libera a conta
// em no máximo um minuto.
const TTL_MS = 60000
const RENOVA_MS = 20000

// host:pid:boot. O boot entra porque o Render reusa nome de host entre deploys, e
// sem ele uma instância nova poderia se confundir com a antiga e renovar a trava
// que era da outra — exatamente o que a trava existe pra impedir.
const IDENTIDADE = `${os.hostname()}:${process.pid}:${Date.now().toString(36)}`

const TABELA_NAO_EXISTE = '42P01'

function criarTrava (pool, log, opcoes) {
  const o = opcoes || {}
  const ttlMs = o.ttlMs || TTL_MS
  const renovaMs = o.renovaMs || RENOVA_MS
  const dono = o.dono || IDENTIDADE
  const aoPerder = o.aoPerder || (() => {})
  // conta_id chega de dois lugares com tipos diferentes: as rotas fazem
  // parseInt (número) e o restaurarSessoes lê do Postgres, que devolve bigint
  // como STRING. Sem normalizar, a mesma conta viraria duas entradas no Set e o
  // `segura()` mentiria dependendo de quem perguntou.
  const num = (contaId) => Number(contaId)
  const seguradas = new Set()
  // contaId -> dono que nos barrou da última vez. Serve só pra não repetir a
  // mesma linha de log a cada tentativa (ver `pegar`).
  const barradas = new Map()
  let batimento = null
  // A migração 157 vem pelo serviço web (o wa-qr não roda migração). Nos minutos
  // entre um deploy e outro a tabela pode não existir ainda; nesse caso a gente
  // segue SEM trava, que é o comportamento de sempre, em vez de deixar o
  // WhatsApp de todo mundo no chão esperando um deploy alheio. Fica avisando no
  // log em toda tentativa pra não virar um degradado silencioso.
  let semTabela = false

  function ligarBatimento () {
    if (batimento || !seguradas.size) return
    batimento = setInterval(() => {
      renovar().catch((e) => log.warn({ e: String(e) }, 'trava: batimento falhou'))
    }, renovaMs)
    // unref pro batimento não segurar o processo vivo sozinho no shutdown
    if (batimento.unref) batimento.unref()
  }

  function desligarBatimentoSeVazio () {
    if (batimento && !seguradas.size) { clearInterval(batimento); batimento = null }
  }

  async function pegar (contaIdCru) {
    const contaId = num(contaIdCru)
    if (semTabela) {
      log.warn({ contaId }, 'trava: tabela wa_qr_sessao_lock ainda não existe — seguindo SEM trava')
      return true
    }
    try {
      const r = await pool.query(
        `insert into wa_qr_sessao_lock (conta_id, dono, expira_em, atualizado)
              values ($1, $2, now() + make_interval(secs => $3), now())
         on conflict (conta_id) do update
                set dono = excluded.dono, expira_em = excluded.expira_em,
                    atualizado = now()
              where wa_qr_sessao_lock.dono = excluded.dono
                 or wa_qr_sessao_lock.expira_em < now()
          returning dono`,
        [contaId, dono, ttlMs / 1000])
      if (!r.rowCount) {
        // Alguém vivo está com a conta. Normal durante um deploy: a instância
        // velha ainda não saiu. Quem chamou tenta de novo mais tarde.
        //
        // Só loga quando MUDA de dono (ou na primeira vez). A tentativa se
        // repete a cada 15s por conta; logar sempre encheria o log de uma linha
        // que diz a mesma coisa — e o segundo SELECT, que só existe pra dizer
        // QUEM está segurando, sairia junto a cada volta.
        const q = await pool.query(
          'select dono from wa_qr_sessao_lock where conta_id=$1', [contaId])
        const donoAtual = q.rows[0] && q.rows[0].dono
        if (barradas.get(contaId) !== donoAtual) {
          barradas.set(contaId, donoAtual)
          log.info({ contaId, donoAtual, eu: dono },
            'trava: conta está com outra instância — não vou abrir socket')
        }
        return false
      }
      const novo = !seguradas.has(contaId)
      seguradas.add(contaId)
      barradas.delete(contaId)
      ligarBatimento()
      if (novo) log.info({ contaId, dono }, 'trava: peguei a conta ✓')
      return true
    } catch (e) {
      if (e && e.code === TABELA_NAO_EXISTE) {
        semTabela = true
        log.warn({ contaId }, 'trava: tabela wa_qr_sessao_lock ainda não existe — seguindo SEM trava')
        return true
      }
      // Banco fora do ar não pode virar "pode abrir socket": é justamente quando
      // não dá pra provar exclusividade. Recusa e deixa quem chamou tentar depois.
      log.error({ contaId, e: String((e && e.message) || e) }, 'trava: falha ao pegar — NÃO vou abrir socket')
      return false
    }
  }

  async function renovar () {
    if (semTabela || !seguradas.size) return
    const contas = [...seguradas]
    const r = await pool.query(
      `update wa_qr_sessao_lock
          set expira_em = now() + make_interval(secs => $3), atualizado = now()
        where conta_id = any($1::bigint[]) and dono = $2
        returning conta_id`,
      [contas, dono, ttlMs / 1000])
    const renovadas = new Set(r.rows.map((l) => num(l.conta_id)))
    for (const contaId of contas) {
      if (renovadas.has(contaId)) continue
      // Não renovou: ou o prazo venceu e outra instância assumiu, ou a linha
      // sumiu. De um jeito ou de outro não somos mais donos — largar o socket
      // agora é o que impede a guerra de sessões que a trava veio evitar.
      seguradas.delete(contaId)
      log.error({ contaId, dono }, 'trava: PERDI a conta (não renovou) — largando a sessão')
      try { aoPerder(contaId) } catch (e) { log.warn({ contaId, e: String(e) }, 'trava: aoPerder falhou') }
    }
    desligarBatimentoSeVazio()
  }

  async function soltar (contaIdCru) {
    const contaId = num(contaIdCru)
    seguradas.delete(contaId)
    desligarBatimentoSeVazio()
    if (semTabela) return
    try {
      await pool.query('delete from wa_qr_sessao_lock where conta_id=$1 and dono=$2',
        [contaId, dono])
      log.info({ contaId }, 'trava: soltei a conta')
    } catch (e) {
      // Não soltar não é fatal: o prazo vence sozinho em até um TTL.
      log.warn({ contaId, e: String((e && e.message) || e) }, 'trava: falha ao soltar (o prazo vence sozinho)')
    }
  }

  // Chamado no SIGTERM. Soltar tudo de uma vez é o que faz o deploy trocar de
  // dono em segundos em vez de esperar o prazo vencer.
  async function soltarTudo () {
    const contas = [...seguradas]
    seguradas.clear()
    desligarBatimentoSeVazio()
    if (semTabela || !contas.length) return
    try {
      await pool.query('delete from wa_qr_sessao_lock where conta_id = any($1::bigint[]) and dono=$2',
        [contas, dono])
      log.info({ contas }, 'trava: soltei tudo (saindo)')
    } catch (e) {
      log.warn({ e: String((e && e.message) || e) }, 'trava: falha ao soltar tudo (o prazo vence sozinho)')
    }
  }

  return { pegar, soltar, soltarTudo, renovar, dono, ttlMs, renovaMs,
    segura: (contaId) => seguradas.has(num(contaId)),
    contas: () => [...seguradas] }
}

module.exports = { criarTrava, IDENTIDADE, TTL_MS, RENOVA_MS }
