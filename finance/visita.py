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
     com fornecedor, e que OU está ligado a um card do funil OU tem o título
     começando com "Visita" — é como o Cockpit batiza ("Visita — {quem}") e como a
     equipe digita ("VISITA TÉCNICA - PEDRO"). O "Retornar contato", lembrete que
     nasce sozinho com o lead novo, não é visita.
  2. DE QUEM É: do dono do card; sem card, de quem marcou.
  3. Visita sem card CONTA, em todas as telas.
  4. Cancelada não conta. Pré-reserva conta (é visita marcada de fato).

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


def sql_e_visita(a: str = "e") -> str:
    """A ESPÉCIE: este compromisso é uma visita? Não olha status nem data — quem
    conta decide isso (`sql_conta`), e a aba Agenda precisa classificar até o
    cancelado, que aparece lá com o filtro de status."""
    return (f"({a}.tipo_evento is null"
            f" and coalesce({a}.tipo, '') <> 'fornecedor'"
            f" and coalesce({a}.titulo, '') !~* '{_RE_RETORNO}'"
            f" and ({a}.prospeccao_id is not null or coalesce({a}.titulo, '') ~* '{_RE_TITULO}'))")


def sql_conta(a: str = "e") -> str:
    """A visita que ENTRA NA CONTA: a espécie, menos a cancelada."""
    return f"({sql_e_visita(a)} and coalesce({a}.status, 'ativo') <> 'cancelado')"


def sql_vendedor(a: str = "e") -> str:
    """De quem é a visita: o dono do card; sem card, quem marcou.

    Subconsulta, e não join, pra caber em qualquer lugar — no WHERE de quem já tem
    um `p` com outro sentido, no SELECT, num filtro de vendedor."""
    return (f"coalesce((select pv.vendedor_id from prospeccao pv"
            f" where pv.id = {a}.prospeccao_id and pv.conta_id = {a}.conta_id), {a}.membro_id)")


def sql_nome_sem_card(a: str = "e") -> str:
    """O nome da pessoa quando a visita não tem card: o título sem o "Visita —"
    ("Visita Técnica - Renata" → "Renata"). Com card, o nome vem do card."""
    return (f"nullif(btrim(regexp_replace(coalesce({a}.titulo, ''),"
            r" '^\s*visita\s*(t[ée]cnica)?\s*[—–:-]?\s*', '', 'i')), '')")


def eh_visita(*, titulo=None, tipo_evento=None, tipo=None, prospeccao_id=None) -> bool:
    """A mesma espécie de `sql_e_visita`, em Python — pro teste fixar as duas."""
    import re
    if (tipo_evento or "").strip() or (tipo or "") == "fornecedor":
        return False
    t = titulo or ""
    if re.match(_RE_RETORNO, t, re.I):
        return False
    return prospeccao_id is not None or bool(re.match(_RE_TITULO, t, re.I))
