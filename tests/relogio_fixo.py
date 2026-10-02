"""O relógio dos testes que dependem de "hoje": o servidor às 23h de Brasília.

Em 27/09/2026, às 00:10 UTC, nove testes falharam no CI do #876 — e voltavam a
falhar todo dia das 21h à meia-noite de Brasília. Eram duas doenças com o mesmo
sintoma:

1. O CÓDIGO usava `date.today()` (o dia do servidor, em UTC) onde a regra é do
   Brasil: a baixa das 22h caía no dia seguinte, o link da reforma ganhava um dia
   de validade, o CNO "vencia" um dia antes. O mesmo bug do lançamento 733
   (ver `finance/relogio.py`), em outros cantos.
2. O TESTE guardava `HOJE = date.today()` na COLETA. A suíte começou às 23:55
   UTC; quando o teste rodou já era meia-noite, e o código via outro dia.

Os dois só apareciam num intervalo de três horas por dia — o resto do tempo UTC e
Brasília concordam e o teste passa com o fuso errado. Por isso o relógio aqui é
FIXO, e fixo justamente dentro desse intervalo: 23h de Brasília do dia em que a
suíte começou, que no servidor (UTC) já são 02h do dia SEGUINTE. Nesse instante:

* `finance.relogio.hoje()` (o certo) responde o dia de Brasília: `HOJE`;
* `date.today()` e `datetime.now()` (o errado) respondem o dia seguinte.

Então código que voltar a usar o dia do servidor numa regra de negócio falha
aqui a QUALQUER hora — não só quando o CI calha de rodar à noite. E `HOJE` não
muda se a suíte atravessar a meia-noite.

O que NÃO é fixado: o relógio do Postgres (`now()`, `current_date`). Por isso a
regra continua sendo passar o dia de Brasília como parâmetro, e não confiar no
`current_date` do banco — que também está em UTC.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from finance import relogio

#: O dia de Brasília em que a suíte começou. É o "hoje" de todo teste que usa
#: o relógio fixo — no lugar do `date.today()` de antes.
HOJE = relogio.hoje()

#: 23h de Brasília de HOJE. No servidor em UTC já é 02h do dia seguinte.
AGORA = datetime.combine(HOJE, time(23), relogio.BR)


class _MetaDate(type):
    # `isinstance(x, date)` continua valendo para as datas de verdade depois que
    # o nome `date` do módulo passa a apontar para a imitação.
    def __instancecheck__(cls, obj):
        return isinstance(obj, date)


class _MetaDatetime(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


class _DateDoServidor(date, metaclass=_MetaDate):
    @classmethod
    def today(cls):
        return AGORA.astimezone(timezone.utc).date()


class _DatetimeDoServidor(datetime, metaclass=_MetaDatetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:  # o servidor roda em UTC: sem fuso, é a hora de lá
            return AGORA.astimezone(timezone.utc).replace(tzinfo=None)
        return AGORA.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return cls.now()

    @classmethod
    def today(cls):
        return cls.now()


@pytest.fixture
def servidor_as_23h(monkeypatch):
    """Põe o processo às 23h de Brasília de `HOJE` (02h UTC do dia seguinte).

    Troca os nomes `date` e `datetime` nos módulos do sistema (`finance.*` e
    `web.*`) já carregados — inclusive no `finance.relogio`, que é o que faz o
    `hoje()` de Brasília responder `HOJE`. Módulo importado depois disto, ou
    import feito dentro da função, continua com o relógio de verdade.
    """
    import sys
    for nome, mod in list(sys.modules.items()):
        if mod is None or not nome.startswith(("finance.", "web.")):
            continue
        if getattr(mod, "date", None) is date:
            monkeypatch.setattr(mod, "date", _DateDoServidor)
        if getattr(mod, "datetime", None) is datetime:
            monkeypatch.setattr(mod, "datetime", _DatetimeDoServidor)
    assert relogio.hoje() == HOJE
    assert relogio.agora() == AGORA
    return AGORA


# ------------------------------------------------------------ a suíte inteira na virada
#
# `SIMULA_VIRADA=1 pytest` roda TODOS os testes como se fosse entre 21h e meia-noite
# de Brasília: `date.today()` (o dia do servidor, em UTC) responde AMANHÃ, e o
# `relogio.hoje()` continua no dia de Brasília. É a única hora em que os dois
# discordam — e era preciso esperar a noite (ou o CI cair nela) pra ver o que
# quebrava. Teste que falha só com isto ligado compara o "hoje" do código com o
# dia do servidor: use `HOJE` (deste arquivo) ou `relogio.hoje()`.
#
# Só o `date.today()` muda; o relógio do Postgres e o `datetime.now()` ficam como
# estão, pra não acusar diferença de hora que não existe.

class _DateDaVirada(date, metaclass=_MetaDate):
    @classmethod
    def today(cls):
        return relogio.hoje() + timedelta(days=1)


_RAIZES_SIMULADAS = ("finance.", "web.", "core.", "contas.", "services.", "db.", "tests.")


def simular_virada(monkeypatch) -> None:
    import sys
    for nome, mod in list(sys.modules.items()):
        if mod is None or not nome.startswith(_RAIZES_SIMULADAS) or nome == "finance.relogio":
            continue
        if getattr(mod, "date", None) is date:
            monkeypatch.setattr(mod, "date", _DateDaVirada)
