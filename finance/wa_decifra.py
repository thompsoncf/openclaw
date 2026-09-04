"""Mede, dia a dia, quanto o WhatsApp deixa de decifrar — e quanto disso é perda.

O `wa_qr_log` guarda 48h (migração 158, de propósito). Enquanto a pergunta for "o
que houve agora?", isso basta; quando vira "isso piorou?", a resposta já foi
apagada. Este módulo tira uma foto por dia e guarda só o resumo.

O QUE ELE RESPONDE, que hoje ninguém sabe:

* quantas mensagens de CLIENTE não decifraram e nunca chegaram (perda real, regra 0);
* quantos ECOS das respostas da própria empresa se perderam — a mensagem foi
  entregue ao cliente, mas o painel não tem. O vendedor abre o inbox e vê o cliente
  perguntando duas vezes sem resposta, quando no celular a resposta está lá.

O SEGUNDO NÃO É "SÓ RUÍDO", e vale escrever porque o CLAUDE.md diz que é. Ele diz
no contexto certo: `fromMe: true` não é mensagem de cliente perdida e não
justifica re-parear — foi o erro de diagnóstico de 22/08 que custou 9.714 linhas
do cofre. Continua valendo. O que aquele texto não trata é o painel ficar com meia
conversa, e é isso que se mede aqui.

DUAS CONTAGENS, e a diferença importa:

    ocorrencias    linhas de log. O retry do Baileys repete o MESMO id, então isto
                   mede barulho, não estrago (110 linhas = 19 mensagens, medido em
                   04/09/2026 na conta 34).
    ids_distintos  mensagens de verdade. É o número que serve pra série temporal.

E `nunca_chegaram` é o que separa susto de prejuízo: a decifragem falha, o Baileys
tenta de novo, e boa parte chega. Só quem não chegou é perda.

CUSTO — o motivo do desenho ser em duas etapas. Cruzar id com
`mensagens.provider_sid` não tem índice que sirva: o único é
(conversa_id, provider_sid), e busca só pelo sid não o usa. É varredura de
`mensagens`. Uma vez por dia, tudo bem; a cada 2 min, não — varredura por evento
foi o que derrubou o app em 15/08 (2.446 respostas 502). Então:

    _apurar()        todo ciclo, só lê `wa_qr_log` (pequeno e indexado por data)
    _correlacionar() uma vez por dia, e só pro dia que já fechou

Reapurar as 48h a cada ciclo é de propósito: se o web ficar horas fora, o ciclo
seguinte reconstrói o que falta sozinho, sem tarefa de recuperação.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.wa_decifra")

_MSG_FALHA = "failed to decrypt message"

# Grupo, canal e status o serviço descarta de propósito (ver o CLAUDE.md, seção 1):
# eles nunca virariam mensagem, então uma falha ali não é perda de nada. Contá-los
# inflaria o número justamente com o que não interessa.
# O `%%` é escape do psycopg, não do SQL: a consulta passa por parametrização, e um
# `%@` solto seria lido como placeholder ("only '%s', '%b', '%t' are allowed").
_SO_CONVERSA = r"""
      coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like '%%@g.us'
  and coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like 'status@%%'
  and coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like '%%@newsletter'
"""


def _apurar(c) -> int:
    """Refaz o resumo das últimas 48h a partir do log. Devolve linhas gravadas.

    Linha sem id ou sem `fromMe` fica de fora: sem id não dá pra deduplicar o
    retry, e sem direção não dá pra dizer se é mensagem de cliente ou eco — os dois
    números perderiam o sentido. São raras, e o total bruto continua no log."""
    r = c.execute(f"""
        with falhas as (
          select (l.criado_em at time zone 'America/Sao_Paulo')::date as dia,
                 l.conta_id,
                 (coalesce(l.dados->>'fromMe', l.dados->'key'->>'fromMe'))::boolean as from_me,
                 coalesce(l.dados->'key'->>'id', l.dados->>'id') as msg_id
            from wa_qr_log l
           where l.msg = %s
             and l.conta_id is not null
             and l.criado_em >= now() - interval '48 hours'
             and coalesce(l.dados->>'fromMe', l.dados->'key'->>'fromMe') is not null
             and coalesce(l.dados->'key'->>'id', l.dados->>'id') is not null
             and {_SO_CONVERSA}
        )
        insert into wa_decifra_diario (dia, conta_id, from_me, ocorrencias, ids_distintos, apurado_em)
        select dia, conta_id, from_me, count(*), count(distinct msg_id), now()
          from falhas group by dia, conta_id, from_me
        on conflict (dia, conta_id, from_me) do update
           set ocorrencias   = excluded.ocorrencias,
               ids_distintos = excluded.ids_distintos,
               apurado_em    = now()
    """, (_MSG_FALHA,))
    return r.rowcount or 0


def _correlacionar(c, limite_dias: int = 7) -> int:
    """Pros dias JÁ FECHADOS e ainda não apurados: quantos daqueles ids chegaram.

    Roda uma vez por dia por linha (o índice parcial acha as pendentes na hora) e
    só enquanto o log ainda alcança o dia — passou de 48h, não há como saber, e a
    linha é fechada com o que dá pra afirmar: `chegaram=0`, tudo em
    `nunca_chegaram`, seria mentira. Então quem perdeu a janela fica com os dois
    nulos e o `correlacionado_em` marcado, dizendo "não deu pra apurar" em vez de
    inventar número."""
    pendentes = c.execute("""
        select dia, conta_id, from_me from wa_decifra_diario
         where correlacionado_em is null
           and dia < (now() at time zone 'America/Sao_Paulo')::date
           and dia >= (now() at time zone 'America/Sao_Paulo')::date - %s::int
         order by dia limit 20""", (limite_dias,)).fetchall()
    if not pendentes:
        return 0
    feitas = 0
    for (dia, conta_id, from_me) in pendentes:
        ids = [r[0] for r in c.execute(f"""
            select distinct coalesce(l.dados->'key'->>'id', l.dados->>'id')
              from wa_qr_log l
             where l.msg = %s and l.conta_id = %s
               and (coalesce(l.dados->>'fromMe', l.dados->'key'->>'fromMe'))::boolean = %s
               and (l.criado_em at time zone 'America/Sao_Paulo')::date = %s
               and coalesce(l.dados->'key'->>'id', l.dados->>'id') is not null
               and {_SO_CONVERSA}""",
            (_MSG_FALHA, conta_id, from_me, dia)).fetchall()]
        if not ids:
            # o log já rolou pra fora da janela: fecha sem número, e o nulo diz isso
            c.execute("""update wa_decifra_diario set correlacionado_em=now()
                          where dia=%s and conta_id=%s and from_me=%s""",
                      (dia, conta_id, from_me))
            feitas += 1
            continue
        chegaram = c.execute(
            "select count(distinct provider_sid) from mensagens where provider_sid = any(%s)",
            (ids,)).fetchone()[0]
        c.execute("""update wa_decifra_diario
                        set chegaram=%s, nunca_chegaram=%s, correlacionado_em=now()
                      where dia=%s and conta_id=%s and from_me=%s""",
                  (chegaram, len(ids) - chegaram, dia, conta_id, from_me))
        feitas += 1
    return feitas


def rodar(pool) -> dict:
    """Chamado pelo ticker do web (~2 min). Best-effort: quem chama já engole a
    exceção, e uma medição que falha não pode atrapalhar o que ela mede."""
    with pool.connection() as c:
        linhas = _apurar(c)
        correlacionadas = _correlacionar(c)
        c.commit()
    if linhas or correlacionadas:
        _log.info("wa_decifra: %d resumo(s) apurado(s), %d dia(s) correlacionado(s)",
                  linhas, correlacionadas)
    return {"apuradas": linhas, "correlacionadas": correlacionadas}


def resumo(pool, dias: int = 7) -> list[dict]:
    """Pra olhar de fora (script, tela, chamado): os últimos dias por conta e direção."""
    with pool.connection() as c:
        rows = c.execute("""
            select dia, conta_id, from_me, ocorrencias, ids_distintos,
                   chegaram, nunca_chegaram
              from wa_decifra_diario
             where dia >= (now() at time zone 'America/Sao_Paulo')::date - %s::int
             order by dia desc, conta_id, from_me""", (dias,)).fetchall()
    return [{"dia": d, "conta_id": ct, "from_me": fm, "ocorrencias": oc,
             "ids_distintos": idd, "chegaram": ch, "nunca_chegaram": nc}
            for (d, ct, fm, oc, idd, ch, nc) in rows]
