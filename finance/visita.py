"""A VISITA, escrita uma vez: o que conta como visita, de quem ela é, e o nome.

A DIVERGÊNCIA QUE TROUXE ISTO (24/09/2026). O gestor da Prime (conta 34) pôs o
Relatório → Funil e o Raio-X lado a lado, no mesmo período e pro mesmo vendedor, e
achou 4 visitas do Pedro Yan num e 3 no outro. As duas telas liam a mesma tabela
com réguas diferentes:

  - o Raio-X fazia INNER JOIN com o lead: a visita digitada na Agenda sem card
    ("Visita Técnica - Renata", 05/09) não existia pra ele — e era justamente a
    cliente que tinha fechado contrato três dias depois;
  - o Relatório dava a visita pra quem MARCOU (`eventos_agenda.membro_id`), o
    Raio-X pro DONO DO CARD (`prospeccao.vendedor_id`);
  - o Raio-X exigia `tipo = 'empresa'`, o Relatório olhava o título.

E uma quarta, que só aparecia depois da data: a festa criada pela aprovação do
orçamento nascia sem `tipo_evento` (10 das 11 da Prime), então o Raio-X passava a
contá-la como visita "sem resposta" assim que o dia da festa passava.

A RÉGUA (decisão do dono, 24/09/2026):

  1. O QUE É VISITA: compromisso que não é festa (`tipo_evento` vazio), que não é
     com fornecedor, e que tem o título começando com "Visita" — é como o Cockpit
     batiza ("Visita — {quem}") e como a equipe digita ("VISITA TÉCNICA - PEDRO").
     Em quem NÃO vende festa, vale também o compromisso ligado a um card (a
     reunião do recorrente, a cotação, a avaliação). O "Retornar contato",
     lembrete que nasce sozinho com o lead novo, nunca é visita.
  2. DE QUEM É: do dono do card; sem card, de quem marcou. Card sem dono deixa a
     visita sem dono — ela não cai em quem marcou só porque o card está vago.
  3. Visita sem card CONTA, em todas as telas.
  4. Cancelada não conta. Pré-reserva conta (é visita marcada de fato).

POR QUE, NA FESTA, SÓ O TÍTULO (regra 6). Quem vende festa marca a festa na mesma
Agenda, e 12 das 43 festas da Prime foram digitadas SEM tipo ("Formatura -
Beatriz"). Ligada ao card da cliente, uma dessas contaria como visita — e passado
o dia, como visita "sem resposta" que ninguém nunca vai responder. Na Prime toda
visita de verdade já se chama "Visita…" (o Cockpit batiza assim); o card só serve
pra dizer de QUEM ela é.

POR QUE O TÍTULO SÓ VALE "VISITA", E NÃO "REUNIÃO" (regra 6 do CLAUDE.md). Medido
em 24/09 nas duas pontas. Na ZAQ (conta 3, recorrente), os 20 compromissos
"Reunião…" dos últimos 120 dias misturam cliente ("Reunião com Paulo Contador")
com reunião de dentro ("REUNIAO ALINHAMENTO MARKETING"), e nenhum está ligado a
card. Na Prime as 3 "Reunião…" também são de dentro. Contar "Reunião" solta
somaria reunião de equipe como reunião de venda. Então a reunião do recorrente
conta quando está LIGADA a um card — e é por isso que o formulário da Agenda
passou a perguntar de qual card é o compromisso.

O QUE ESTA RÉGUA NÃO DECIDE: se o compromisso OCUPA a data pra venda. Essa é outra
pergunta, com outro custo de erro (vender o mesmo sábado duas vezes), e continua
em `finance.agenda.eh_visita` / `estado_da_data`, que olham só o título.

As funções devolvem SQL sem parâmetro e sem `%`, pra poderem ser coladas em
consulta que já usa `%s` sem precisar de escape.
"""
from __future__ import annotations

#: o começo do título que diz "visita" mesmo sem card
_RE_TITULO = r"^\s*visita"
#: o lembrete automático do lead novo (web/painel_prospeccao.py) — nunca é visita
_RE_RETORNO = r"^\s*retornar contato"


def sql_e_visita(a: str = "e", *, festa: bool = False) -> str:
    """A ESPÉCIE: este compromisso é uma visita? Não olha status nem data — quem
    conta decide isso (`sql_conta`), e a aba Agenda precisa classificar até o
    cancelado, que aparece lá com o filtro de status.

    `festa=True` é a conta que vende festa (`finance.vendas.vende_data`): aí só o
    título diz que é visita — ver o docstring do módulo."""
    ligado = "" if festa else f"{a}.prospeccao_id is not null or "
    # A CONSULTA DA CLÍNICA NÃO É VISITA (migração 355): ela tem card e não tem
    # tipo_evento, e cabia aqui. Contada como visita, virava "visita" no Cockpit
    # (remarcável por fora da grade) e no Raio-X. `to_jsonb` lê `situacao` sem exigir
    # a coluna: banco sem a 355 continua igual.
    return (f"((to_jsonb({a}) ->> 'situacao') is null and {a}.tipo_evento is null"
            f" and coalesce({a}.tipo, '') <> 'fornecedor'"
            f" and coalesce({a}.titulo, '') !~* '{_RE_RETORNO}'"
            f" and ({ligado}coalesce({a}.titulo, '') ~* '{_RE_TITULO}'))")


def sql_conta(a: str = "e", *, festa: bool = False) -> str:
    """A visita que ENTRA NA CONTA: a espécie, menos a cancelada."""
    return f"({sql_e_visita(a, festa=festa)} and coalesce({a}.status, 'ativo') <> 'cancelado')"


def sql_vendedor(a: str = "e") -> str:
    """De quem é a visita: o dono do card; sem card, quem marcou.

    Com card, é SEMPRE o dono do card — mesmo vago (aí a visita fica sem dono, a
    linha "Outros" do Raio-X). Cair em quem marcou só porque o card está sem
    vendedor daria a mesma visita a pessoas diferentes em blocos diferentes.

    Subconsulta, e não join, pra caber em qualquer lugar — no WHERE de quem já tem
    um `p` com outro sentido, no SELECT, num filtro de vendedor."""
    return (f"(case when {a}.prospeccao_id is not null"
            f" then (select pv.vendedor_id from prospeccao pv"
            f" where pv.id = {a}.prospeccao_id and pv.conta_id = {a}.conta_id)"
            f" else {a}.membro_id end)")


def vende_festa(pool, conta_id: int) -> bool:
    """A conta vende festa? É o `festa=` das funções acima. Tolerante como a
    própria `vende_data`: sem resposta, a régua larga (a de quem não vende festa)."""
    try:
        from finance.vendas import vende_data
        return bool(vende_data(pool, conta_id))
    except Exception:  # noqa: BLE001
        return False


def eh_visita(*, titulo=None, tipo_evento=None, tipo=None, prospeccao_id=None,
             festa: bool = False) -> bool:
    """A mesma espécie de `sql_e_visita`, em Python — pro teste fixar as duas."""
    import re
    if (tipo_evento or "").strip() or (tipo or "") == "fornecedor":
        return False
    t = titulo or ""
    if re.match(_RE_RETORNO, t, re.I):
        return False
    return (prospeccao_id is not None and not festa) or bool(re.match(_RE_TITULO, t, re.I))
