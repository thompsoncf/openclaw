"""FUNDIR DUAS ETAPAS: os leads de uma passam para a outra, com histórico.

Pedido pelo dono em 12/09/2026, ao aprovar o "Projeto Adaptado" da Prime:

    "NOVO → CONTACTADO → NEGOCIAÇÃO → FECHADO (...) A passagem para NEGOCIAÇÃO
     ocorre quando houver avanço comercial concreto: visita agendada ou realizada,
     proposta/orçamento em negociação, pagamento de sinal, contrato."

Na Prime isso significa juntar "Agendado Visita" (14 leads) e "Proposta" (20) numa
coluna só. Até aqui a Régua sabia RENOMEAR, REORDENAR, tirar do quadro e remover
etapa VAZIA — a mensagem de remover diz literalmente "mova-os antes de remover", e
não existia o "mova-os". Este módulo é esse verbo que faltava.

POR QUE ISTO NÃO É UM `update ... set status=` E PRONTO (CLAUDE.md §0)
Mover lead é mexer em informação do cliente. Três coisas seguram isso aqui:

1. **Cada lead movido vira uma linha em `funil_movimentos`**, com motivo `fusao` e
   o membro que apertou o botão. Sem isso o relatório de tempo por etapa passaria a
   contar que 14 pessoas "avançaram para Proposta" num segundo, e ninguém saberia
   dizer de onde vieram — nem pra desfazer.

2. **A etapa de origem não é apagada.** Ela sai do QUADRO (`sai_do_quadro`, migração
   238) e continua existindo: os movimentos antigos apontam pra chave dela, os
   relatórios de histórico leem essa chave, e apagá-la deixaria o passado órfão.
   Quem quiser remover de vez usa o botão de remover, que agora funciona — a etapa
   ficou vazia.

3. **Etapa fixa não funde.** 'novo' é a entrada, 'ganho' e 'perdido' são o
   resultado: as três estão fixas nas consultas de fechamento.

O QUE A FUSÃO NÃO DECIDE
Ela não renomeia o destino. Se o dono quer que "Proposta" passe a se chamar
"Negociação", isso é o campo de rótulo, uma linha acima na mesma tela — duas
decisões, dois cliques, cada um reversível sozinho.
"""
from __future__ import annotations

from finance import funil_regua as _fr

#: motivo gravado em `funil_movimentos` — é por ele que se reconhece (e se desfaz)
MOTIVO = "fusao"


def _etapa(c, conta_id: int, chave: str) -> dict | None:
    r = c.execute(
        """select chave, rotulo, fixa, coalesce(sai_do_quadro,false)
             from funil_etapas where conta_id=%s and chave=%s""",
        (conta_id, chave)).fetchone()
    return {"chave": r[0], "rotulo": r[1], "fixa": r[2], "sai_do_quadro": r[3]} if r else None


def quantos(c, conta_id: int, chave: str) -> int:
    """Leads ATIVOS na etapa. `estagio='lead'` porque a base de captados usa o mesmo
    campo `status` com outro sentido, e fundir não pode arrastar quem nem virou lead."""
    return c.execute(
        """select count(*) from prospeccao
            where conta_id=%s and estagio='lead' and status=%s""",
        (conta_id, chave)).fetchone()[0]


def plano(c, conta_id: int, de: str, para: str) -> dict:
    """O que a fusão faria. `erro` preenchido = não dá, e diz por quê.

    Nada aqui escreve no banco: é o que a tela mostra antes de perguntar "confirma?".
    """
    o, d = _etapa(c, conta_id, de), _etapa(c, conta_id, para)
    base = {"de": de, "para": para, "leads": 0, "erro": None,
            "rotulo_de": (o or {}).get("rotulo"), "rotulo_para": (d or {}).get("rotulo")}
    if not o or not d:
        return dict(base, erro="etapa")
    if de == para:
        return dict(base, erro="mesma")
    if o["fixa"]:
        return dict(base, erro="fixa")
    return dict(base, leads=quantos(c, conta_id, de))


def fundir(c, conta_id: int, de: str, para: str, membro_id: int | None = None) -> dict:
    """Move os leads e tira a etapa de origem do quadro. Devolve o plano + `movidos`.

    A ordem importa e é esta: PRIMEIRO o histórico de cada lead, DEPOIS o update em
    massa. Se o insert do histórico falhar, a transação inteira volta e nenhum lead
    andou — o contrário (mover e depois tentar registrar) deixaria leads movidos sem
    rastro, que é exatamente o estado impossível de desfazer.
    """
    p = plano(c, conta_id, de, para)
    if p["erro"]:
        return dict(p, movidos=0)
    ids = [r[0] for r in c.execute(
        """select id from prospeccao
            where conta_id=%s and estagio='lead' and status=%s order by id""",
        (conta_id, de)).fetchall()]
    for lid in ids:
        _fr.registrar_movimento(c, conta_id, lid, de, para, MOTIVO, membro_id)
    if ids:
        c.execute(
            """update prospeccao set status=%s, atualizado_em=now()
                where conta_id=%s and estagio='lead' and status=%s""",
            (para, conta_id, de))
    # a etapa vazia some do QUADRO, não do banco: os movimentos antigos apontam
    # pra chave dela, e apagá-la deixaria o passado órfão
    c.execute("update funil_etapas set sai_do_quadro=true where conta_id=%s and chave=%s",
              (conta_id, de))
    return dict(p, movidos=len(ids))


def desfazer(c, conta_id: int, de: str, para: str) -> dict:
    """Devolve pra `de` os leads que a fusão levou pra `para` — e só esses.

    A fusão grava de onde cada lead veio, então desfazer não é adivinhação: são os
    leads com um movimento `fusao` de→para que AINDA estão em `para`. Quem andou
    depois da fusão (foi pra Fechado, foi perdido) fica onde está — desfazer uma
    mudança de coluna não pode desfazer a venda que aconteceu no meio.
    """
    ids = [r[0] for r in c.execute(
        """select distinct m.prospeccao_id
             from funil_movimentos m join prospeccao p on p.id = m.prospeccao_id
            where m.conta_id=%s and m.de=%s and m.para=%s and m.motivo=%s
              and p.status=%s and p.estagio='lead'""",
        (conta_id, de, para, MOTIVO, para)).fetchall()]
    for lid in ids:
        _fr.registrar_movimento(c, conta_id, lid, para, de, MOTIVO + "_desfeito", None)
        c.execute("update prospeccao set status=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (de, lid, conta_id))
    c.execute("update funil_etapas set sai_do_quadro=false where conta_id=%s and chave=%s",
              (conta_id, de))
    return {"de": de, "para": para, "voltaram": len(ids)}
