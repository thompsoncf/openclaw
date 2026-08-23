"""O dia é o de Brasília, não o do servidor.

O servidor roda em UTC. `date.today()` ali devolve o dia SEGUINTE das 21h às
23h59 de Brasília, e foi assim que o lançamento 733 ("Casa do Churrasco",
conta 3) nasceu errado: criado em 22/08/2026 às 21:21 BRT — 00:21 UTC do dia 23
— e gravado com `data = 2026-08-23`. Os lançamentos 731 e 732, das 20:08 e
20:09, ficaram certos: a fronteira é 21h em ponto.

Doze lançamentos em quatro contas nasceram assim desde 04/07/2026.

Estes testes rodam com o processo em UTC de propósito — é a configuração do
servidor, e é onde o bug aparece. Se alguém trocar `hoje()` de volta por
`date.today()`, ou se a variável TZ for a única defesa e o contêiner ficar sem
a base de fusos, é aqui que estoura.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from finance import relogio
from finance.models import Lancamento, Tipo

# 22/08/2026 21:21 BRT — o instante exato do lançamento 733
LANC_733_UTC = datetime(2026, 8, 23, 0, 21, tzinfo=timezone.utc)


def test_o_instante_do_lancamento_733_e_dia_22_nao_23():
    assert relogio.dia_br(LANC_733_UTC) == date(2026, 8, 22)
    # ...e é isto que o date.today() de um servidor em UTC teria respondido:
    assert LANC_733_UTC.date() == date(2026, 8, 23), "a premissa do bug"


@pytest.mark.parametrize("hora_utc, dia_esperado", [
    (datetime(2026, 8, 22, 23, 8, tzinfo=timezone.utc), 22),   # 20:08 BRT — o 732, certo
    (datetime(2026, 8, 22, 23, 59, tzinfo=timezone.utc), 22),  # 20:59 BRT — véspera da fronteira
    (datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc), 22),    # 21:00 BRT — a fronteira
    (datetime(2026, 8, 23, 0, 21, tzinfo=timezone.utc), 22),   # 21:21 BRT — o 733
    (datetime(2026, 8, 23, 2, 59, tzinfo=timezone.utc), 22),   # 23:59 BRT — o último do dia
    (datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc), 23),    # 00:00 BRT — aí sim virou
])
def test_a_fronteira_do_dia_e_meia_noite_de_brasilia(hora_utc, dia_esperado):
    assert relogio.dia_br(hora_utc).day == dia_esperado


def test_data_ingenua_e_tratada_como_utc():
    """Coluna lida sem fuso, ou `utcnow()` antigo, chega ingênua. Tratar como
    LOCAL é o que produz o erro de 3h no sentido contrário."""
    assert relogio.dia_br(datetime(2026, 8, 23, 0, 21)) == date(2026, 8, 22)


def test_hoje_nao_depende_da_variavel_de_ambiente(monkeypatch):
    """TZ só funciona se o contêiner tiver /usr/share/zoneinfo; sem isso ela é
    ignorada em silêncio. A conta aqui é explícita e vale com TZ ou sem."""
    monkeypatch.setenv("TZ", "UTC")
    agora = relogio.agora()
    assert agora.tzinfo is not None, "agora() sempre devolve data COM fuso"
    assert agora.utcoffset() == timedelta(hours=-3)
    assert relogio.hoje() == agora.date()


def test_hoje_bate_com_o_relogio_de_brasilia():
    esperado = (datetime.now(timezone.utc) + timedelta(hours=-3)).date()
    assert relogio.hoje() == esperado


def test_lancamento_novo_nasce_com_a_data_de_brasilia():
    """O ponto que queimou o cliente: o default do dataclass."""
    assert Lancamento(tipo=Tipo.DESPESA, valor_centavos=100,
                      categoria="outros").data == relogio.hoje()
    assert Lancamento.criar(Tipo.DESPESA, 1.0, "outros").data == relogio.hoje()


def test_data_explicita_continua_mandando():
    """Lançamento retroativo é digitado de propósito e não pode ser 'corrigido'."""
    ontem = relogio.hoje() - timedelta(days=1)
    assert Lancamento.criar(Tipo.DESPESA, 1.0, "outros", data=ontem).data == ontem


def test_ninguem_voltou_a_usar_date_today_no_caminho_do_lancamento():
    """Trava de regressão por leitura do fonte: `date.today()` de volta em
    models.py ou no livro_caixa traz o bug inteiro junto."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    for arq in ("finance/models.py", "finance/livro_caixa.py"):
        texto = (base / arq).read_text(encoding="utf-8")
        # o comentário explicativo cita o nome; conta só as CHAMADAS
        assert "date.today()" not in texto.replace("o date.today() já é", ""), (
            f"{arq} voltou a usar date.today() — use finance.relogio.hoje()")


def test_health_denuncia_o_fuso_pela_HORA_nao_pela_data(monkeypatch):
    """O /health existe pra responder "o TZ pegou?" sem esperar dar 21h.

    Conferido em 23/08 às 13h56: com TZ=UTC e com TZ=America/Sao_Paulo os campos
    `hoje_processo` e `confere` saem IDÊNTICOS — nas outras 21 horas do dia UTC e
    Brasília concordam. Quem separa é a HORA. Um diagnóstico baseado em data
    passaria com o fuso errado, que é exatamente a armadilha deste bug.
    """
    from web.app import _fuso_do_processo

    monkeypatch.setenv("TZ", "UTC")
    d = _fuso_do_processo()
    assert d["agora_brasilia"] == relogio.agora().strftime("%d/%m %H:%M")
    assert d["hoje_app"] == relogio.hoje().isoformat()
    # o campo que denuncia: a hora local do processo contra a de Brasília
    assert "agora_local" in d and "tz_processo" in d
