"""O recorte de tempo das telas: as pílulas de período e o par (início, fim).

Estava dentro de `web/painel_relatorios.py`, privado (`_intervalo`, `_dia`,
`_fim_do_mes`). Saiu daqui pra fora quando a tela de Origens precisou do MESMO
recorte — inclusive do período personalizado, que já estava pronto lá. Copiar
seria criar duas réguas de data pra divergirem na primeira mudança; importar um
`_privado` de um módulo de tela seria pior ainda.

Nada aqui toca banco: é data pura, e é por isso que dá pra testar sem Postgres.
"""
from __future__ import annotations

from datetime import date, timedelta

#: as pílulas do histórico. "Período específico" NÃO entra aqui, e não é
#: esquecimento: as abas de Relatórios chamam `intervalo(periodo)` sem `de`/`ate`,
#: então escolher datas ali cairia calado no mês corrente — filtro que mente é
#: pior que filtro que não existe.
PERIODOS = [
    ("mes", "Este mês"),
    ("mes_passado", "Mês passado"),
    ("90d", "Últimos 90 dias"),
    ("ano", "Este ano"),
    ("todos", "Todo o período"),
]

#: as pílulas de quem olha pra frente (Agenda) — e de quem aceita datas na mão.
PERIODOS_AGENDA = [
    ("mes", "Este mês"),
    ("mes_passado", "Mês passado"),
    ("prox30", "Próximos 30 dias"),
    ("prox90", "Próximos 90 dias"),
    ("ano", "Este ano"),
    ("todos", "Todo o período"),
    ("personalizado", "Período específico…"),
]

#: as pílulas da tela de Origens: olha pra trás, como o histórico, mas aceita
#: datas na mão — a agência compara com a semana que ela mesma fechou no painel
#: dela, e "este mês" não bate com "28/08 a 03/09".
PERIODOS_ORIGENS = [
    ("7d", "7 dias"),
    ("14d", "14 dias"),
    ("30d", "30 dias"),
    ("mes", "Este mês"),
    ("personalizado", "Período específico…"),
]

ROTULO = dict(PERIODOS) | dict(PERIODOS_AGENDA) | dict(PERIODOS_ORIGENS)


def dia(s) -> date | None:
    """A data que vem do `<input type="date">`: sempre AAAA-MM-DD, nunca o texto
    que o usuário vê. O navegador mostra dd/mm/aaaa em aparelho brasileiro e
    manda ISO no formulário — quem formata é ele, não nós."""
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def fim_do_mes(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def intervalo(periodo: str, de=None, ate=None,
              ate_o_fim: bool = False) -> tuple[date, date]:
    """O par (início, fim) do período pedido.

    `ate_o_fim` estica "este mês"/"este ano" até o ÚLTIMO dia em vez de parar
    hoje. Só a pílula Agenda liga isso (decisão do dono em 31/08/2026): num
    relatório de histórico, "este mês" que vai além de hoje mostraria linha
    nenhuma; numa agenda, parar em hoje esconde justamente a festa que ainda vai
    acontecer.

    `de`/`ate` só valem com `periodo='personalizado'`. Data faltando ou torta cai
    no mês corrente — filtro quebrado não pode virar tela vazia sem explicação.
    Invertidas (de > ate), são trocadas: é engano de digitação, não pedido.
    """
    hoje = date.today()
    if periodo == "personalizado":
        d, a = dia(de), dia(ate)
        if d and a:
            return (a, d) if d > a else (d, a)
        if d:
            return d, fim_do_mes(d)
        if a:
            return a.replace(day=1), a
        return hoje.replace(day=1), fim_do_mes(hoje) if ate_o_fim else hoje
    if periodo == "todos":
        return date(2000, 1, 1), hoje
    if periodo == "mes_passado":
        fim = hoje.replace(day=1) - timedelta(days=1)
        return fim.replace(day=1), fim
    # As janelas de N dias contam HOJE como um dos dias: "7 dias" é a semana que
    # inclui hoje, não oito. É o que a agência lê no painel dela, e divergir por
    # um dia numa comparação lado a lado parece erro do Zaq.
    if periodo == "7d":
        return hoje - timedelta(days=6), hoje
    if periodo == "14d":
        return hoje - timedelta(days=13), hoje
    if periodo == "30d":
        return hoje - timedelta(days=29), hoje
    if periodo == "90d":
        return hoje - timedelta(days=90), hoje
    if periodo == "prox30":
        return hoje, hoje + timedelta(days=30)
    if periodo == "prox90":
        return hoje, hoje + timedelta(days=90)
    if periodo == "ano":
        return date(hoje.year, 1, 1), date(hoje.year, 12, 31) if ate_o_fim else hoje
    return hoje.replace(day=1), fim_do_mes(hoje) if ate_o_fim else hoje  # "mes"
