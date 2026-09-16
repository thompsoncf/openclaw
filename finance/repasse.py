"""De quem é o lead, quem passou pra quem, e por quê.

POR QUE ESTE MÓDULO EXISTE
Pedido do dono em 16/09/2026: "preciso que com o próprio vendedor mude de quem é
quem, porque existem leads que já foram atendidos no passado e eles precisam se
entender".

Até aqui só o DONO trocava (`painel_prospeccao`: `pode_atribuir = papel ==
"dono"`), e a troca **não deixava rastro**: `prospeccao.vendedor_id` era
sobrescrito e nenhuma tabela guardava quem tinha antes. Esse segundo ponto é o que
impedia os vendedores de se entenderem — sem histórico ninguém prova quem atendeu
primeiro — e é por isso que eu não consegui nem medir o tamanho do problema: o
dado nunca tinha sido guardado.

COMO O PROBLEMA NASCE. `distribuicao.atribuir_se_sem_dono` **nunca rouba** lead que
já tem dono. Mas quando o mesmo número escreve de novo, nasce um lead NOVO, sem
dono, e o rodízio entrega pro próximo da fila. O contato é o mesmo; a linha na
tabela, não. Foi assim com a Lêda Lopes na conta 34: 20/08 com a Jacqueline, 24/08
com o Thiago, mesmo número.

A REGRA: O VENDEDOR DÁ, E NUNCA PEGA
Passar o próprio lead é abrir mão do que é seu — no pior caso alguém entrega um
lead que era dele. Puxar o do colega mexe no que é do outro e abre a porta pro
roubo de lead, que num time comissionado é briga na certa. Se o combinado for o
contrário, quem TEM é que passa.

Dono e gestor passam nos dois sentidos: os dois já enxergam a carteira inteira na
visão de equipe, e é deles o desempate quando os vendedores não se entendem.

TODA TROCA VIRA LINHA em `lead_repasse` — inclusive as do dono e as do gestor.
Guardar só as do vendedor deixaria o histórico mentindo por omissão justamente nos
casos em que alguém questiona a decisão.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

#: Papéis que podem RECEBER um lead. É o mesmo conjunto que `cockpit_dono` já
#: usava pra validar destino — o dono e o gestor também vendem em várias contas.
PAPEIS_DESTINO = ("vendedor", "gestor", "dono")

#: Papéis que passam lead de QUALQUER UM, nos dois sentidos.
PAPEIS_MANDAM = ("dono", "gestor")


def pode_passar(papel: str | None, membro_id, dono_atual) -> bool:
    """Esta pessoa pode passar ESTE lead?

    Dono e gestor: qualquer lead. Vendedor: só o que é dele — e "dele" é
    `prospeccao.vendedor_id`, não "ele já falou nessa conversa".

    Lead SEM DONO é do dono/gestor: deixar qualquer vendedor repassar um órfão
    seria dar a ele o poder de escolher o dono de um lead que nunca foi seu, que é
    exatamente o "pegar" que esta regra existe pra impedir. Pra ficar com um órfão
    o caminho continua sendo o rodízio.
    """
    if papel in PAPEIS_MANDAM:
        return True
    if papel != "vendedor" or membro_id is None or dono_atual is None:
        return False
    return int(membro_id) == int(dono_atual)


def _dono_do_lead(c, conta_id: int, lead_id: int):
    r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    return (True, r[0]) if r else (False, None)


def passar(pool, conta_id: int, lead_id: int, para_id: int, *, por_id=None,
           papel: str | None = None, motivo: str = "") -> dict:
    """Passa o lead pra outra pessoa e ANOTA. Devolve {ok} ou {ok: False, erro}.

    A conversa vai junto (`conversas.responsavel_membro_id`): sem isso o inbox
    segue mostrando o nome antigo pra quem acabou de receber.

    A anotação é feita na MESMA transação da troca, e de propósito — um repasse
    sem rastro é o estado de antes, e é ele que este módulo existe pra acabar. Se
    a tabela não puder receber a linha, a troca não acontece.
    """
    para_id = int(para_id)
    with pool.connection() as c:
        existe, de_id = _dono_do_lead(c, conta_id, lead_id)
        if not existe:
            return {"ok": False, "erro": "lead_invalido"}
        if not pode_passar(papel, por_id, de_id):
            return {"ok": False, "erro": "sem_permissao"}
        if de_id is not None and int(de_id) == para_id:
            return {"ok": False, "erro": "ja_e_dele"}
        alvo = c.execute(
            "select 1 from membros where id=%s and conta_id=%s and ativo and papel = any(%s)",
            (para_id, conta_id, list(PAPEIS_DESTINO))).fetchone()
        if not alvo:
            return {"ok": False, "erro": "destino_invalido"}
        c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (para_id, lead_id, conta_id))
        c.execute("update conversas set responsavel_membro_id=%s "
                  "where conta_id=%s and prospeccao_id=%s", (para_id, conta_id, lead_id))
        c.execute(
            """insert into lead_repasse (conta_id, prospeccao_id, de_membro_id,
                                         para_membro_id, por_membro_id, motivo)
               values (%s,%s,%s,%s,%s,%s)""",
            (conta_id, lead_id, de_id, para_id, por_id, (motivo or "").strip()[:200] or None))
        c.commit()
    return {"ok": True, "de": de_id, "para": para_id}


def historico(pool, conta_id: int, lead_id: int, limite: int = 10) -> list[dict]:
    """"Quem já atendeu": os repasses deste lead, do mais novo pro mais velho.

    Best-effort: a ficha do lead abre sem o bloco se a consulta falhar (base sem a
    267 ainda). Histórico é o que se olha quando há dúvida — não pode ser o que
    derruba a tela de quem está atendendo.
    """
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select r.criado_em,
                          coalesce(nullif(de.nome,''), de.email, ''),
                          coalesce(nullif(pa.nome,''), pa.email, ''),
                          coalesce(nullif(po.nome,''), po.email, ''),
                          coalesce(r.motivo,'')
                     from lead_repasse r
                     left join membros de on de.id = r.de_membro_id
                     left join membros pa on pa.id = r.para_membro_id
                     left join membros po on po.id = r.por_membro_id
                    where r.conta_id=%s and r.prospeccao_id=%s
                    order by r.id desc limit %s""",
                (conta_id, lead_id, int(limite))).fetchall()
    except Exception as e:  # noqa: BLE001 — ver o docstring
        _log.warning("histórico de repasse do lead %s: %s: %s",
                     lead_id, type(e).__name__, e)
        return []
    return [{"quando": r[0], "de": r[1], "para": r[2], "por": r[3], "motivo": r[4]}
            for r in rows]


def ja_atendeu_o_numero(pool, conta_id: int, lead_id: int) -> list[dict]:
    """Quem JÁ ATENDEU este mesmo número, em outro lead.

    É o histórico que a tabela nova não tem como saber — o caso da Lêda são DOIS
    leads diferentes, e nenhum repasse aconteceu entre eles. Sem esta leitura o
    bloco "quem já atendeu" nasceria vazio justamente nos casos que motivaram o
    pedido.

    Casa pelos 8 últimos dígitos, que é como o resto da casa casa telefone (ver
    `painel_prospeccao._conversas_onde`).
    """
    try:
        with pool.connection() as c:
            rows = c.execute(
                """with eu as (
                     select right(regexp_replace(coalesce(whatsapp, telefone, ''),
                                                 '\\D', '', 'g'), 8) num
                       from prospeccao where id=%s and conta_id=%s)
                   select p.id, coalesce(nullif(mb.nome,''), mb.email, ''), p.criado_em,
                          p.vendedor_id
                     from prospeccao p
                     left join membros mb on mb.id = p.vendedor_id
                    where p.conta_id=%s and p.id <> %s
                      and length((select num from eu)) = 8
                      and right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''),
                                               '\\D', '', 'g'), 8) = (select num from eu)
                    order by p.criado_em desc limit 5""",
                (lead_id, conta_id, conta_id, lead_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("outros leads do número (lead %s): %s: %s",
                     lead_id, type(e).__name__, e)
        return []
    return [{"lead_id": r[0], "vendedor": r[1], "quando": r[2], "vendedor_id": r[3]}
            for r in rows]


def recebidos_novos(pool, conta_id: int, membro_id: int) -> list[dict]:
    """"Fulano te passou a Lêda": o que me passaram e eu ainda não abri.

    Passar sem avisar seria o lead sumir da tela de um e aparecer na do outro,
    calado — e quem recebe precisa saber POR QUÊ, que é o motivo escrito por quem
    passou. O aviso sai da Fila sozinho quando ele abre o lead (`marcar_visto`);
    não tem botão de fechar porque fechar aqui é a mesma coisa que ir ver.
    """
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select r.id, r.prospeccao_id, coalesce(p.empresa,'Lead'),
                          coalesce(nullif(de.nome,''), de.email, ''), coalesce(r.motivo,''),
                          r.criado_em
                     from lead_repasse r
                     join prospeccao p on p.id = r.prospeccao_id
                     left join membros de on de.id = r.por_membro_id
                    where r.conta_id=%s and r.para_membro_id=%s and r.visto_em is null
                      and r.por_membro_id is distinct from %s
                    order by r.id desc limit 5""",
                (conta_id, membro_id, membro_id)).fetchall()
    except Exception as e:  # noqa: BLE001 — a Fila não cai por causa de um aviso
        _log.warning("repasses recebidos (%s/%s): %s: %s",
                     conta_id, membro_id, type(e).__name__, e)
        return []
    return [{"id": r[0], "lead_id": r[1], "empresa": r[2], "de": r[3],
             "motivo": r[4], "quando": r[5]} for r in rows]


def marcar_visto(pool, conta_id: int, lead_id: int, membro_id: int) -> None:
    """Quem recebeu abriu o lead: o aviso some da Fila dele.

    Best-effort: abrir o lead não pode falhar porque a anotação falhou."""
    try:
        with pool.connection() as c:
            c.execute("update lead_repasse set visto_em=now() "
                      " where conta_id=%s and prospeccao_id=%s and para_membro_id=%s"
                      "   and visto_em is null", (conta_id, lead_id, membro_id))
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("marcar repasse visto (%s/%s): %s: %s",
                     lead_id, membro_id, type(e).__name__, e)


def colegas(pool, conta_id: int, exceto_id) -> list[dict]:
    """Pra quem dá pra passar: membros ativos da conta que podem receber lead,
    menos quem já tem. Conta de um vendedor só devolve lista vazia — e aí a tela
    não mostra o botão, porque passar pra ninguém não é ação."""
    with pool.connection() as c:
        rows = c.execute(
            "select id, coalesce(nullif(nome,''), email) from membros "
            " where conta_id=%s and ativo and papel = any(%s) and id is distinct from %s"
            " order by coalesce(nullif(nome,''), email)",
            (conta_id, list(PAPEIS_DESTINO), exceto_id)).fetchall()
    return [{"id": r[0], "nome": r[1]} for r in rows]
