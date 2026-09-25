"""As regras puras da grade da clínica (finance/clinica_config.py) — sem banco.

Recorrência (toda semana, a cada 15 dias, "3ª quinta", "última sexta"), bloqueios
que cortam a faixa, e o horário livre = grade − bloqueios − ocupados.
"""
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_config as cc

MANOEL = 1
# a grade aprovada do Dr. Manoel: seg a sex, 08:00–12:00 e 13:30–16:30
GRADE = [
    {"profissional_id": MANOEL, "local_id": 10, "dias": "1,2,3,4,5", "inicio": time(8),
     "fim": time(12), "repete": "semanal", "encaixes": 2},
    {"profissional_id": MANOEL, "local_id": 10, "dias": "1,2,3,4,5", "inicio": time(13, 30),
     "fim": time(16, 30), "repete": "semanal", "encaixes": 0},
]
SEG = date(2026, 9, 28)          # segunda
AGORA = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


def _br(dia: date, h: int, m: int = 0) -> datetime:
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=timezone.utc) + timedelta(hours=3)


def test_dias_e_texto():
    assert cc.dias_de("1,2, 3,x,9,2") == [1, 2, 3]
    assert cc.dias_txt([1, 2, 3, 4, 5]) == "Seg a Sex"
    assert cc.dias_txt([2, 4]) == "Ter, Qui"
    assert cc.dias_txt([6]) == "Sáb"


def test_semanal_so_nos_dias():
    r = {"dias": "1,2,3,4,5", "repete": "semanal"}
    assert cc.acontece(r, SEG)
    assert not cc.acontece(r, SEG + timedelta(days=5))       # sábado


def test_quinzenal_semana_sim_semana_nao():
    r = {"dias": "6", "repete": "quinzenal", "referencia": date(2026, 10, 3)}   # sábado
    assert cc.acontece(r, date(2026, 10, 3))
    assert not cc.acontece(r, date(2026, 10, 10))
    assert cc.acontece(r, date(2026, 10, 17))
    assert cc.acontece(r, date(2026, 9, 19))                  # pra trás também


def test_mensal_terceira_quinta_e_ultima_sexta():
    r = {"dias": "4", "repete": "mensal", "semana_do_mes": 3}
    assert cc.acontece(r, date(2026, 10, 15))                 # 3ª quinta de outubro
    assert not cc.acontece(r, date(2026, 10, 8))
    assert not cc.acontece(r, date(2026, 10, 22))
    ult = {"dias": "5", "repete": "mensal", "semana_do_mes": 5}
    assert cc.acontece(ult, date(2026, 10, 30))               # última sexta
    assert not cc.acontece(ult, date(2026, 10, 23))


def test_faixas_do_dia_e_bloqueio_parcial():
    f = cc.faixas_do_dia(GRADE, [], MANOEL, SEG)
    assert cc.faixas_txt(f) == "08:00–12:00 e 13:30–16:30"
    b = [{"profissional_id": MANOEL, "de": SEG, "ate": SEG, "inicio": time(10), "fim": time(14)}]
    f = cc.faixas_do_dia(GRADE, b, MANOEL, SEG)
    assert cc.faixas_txt(f) == "08:00–10:00 e 14:00–16:30"


def test_bloqueio_do_dia_todo_e_da_clinica_toda():
    congresso = [{"profissional_id": MANOEL, "de": SEG, "ate": SEG + timedelta(days=1),
                  "inicio": None, "fim": None}]
    assert cc.faixas_do_dia(GRADE, congresso, MANOEL, SEG) == []
    assert cc.faixas_do_dia(GRADE, congresso, MANOEL, SEG + timedelta(days=2))
    feriado = [{"profissional_id": None, "de": SEG, "ate": SEG, "inicio": None, "fim": None}]
    assert cc.faixas_do_dia(GRADE, feriado, MANOEL, SEG) == []
    # bloqueio de outro profissional não mexe no dele
    outro = [{"profissional_id": 99, "de": SEG, "ate": SEG, "inicio": None, "fim": None}]
    assert cc.faixas_do_dia(GRADE, outro, MANOEL, SEG)


def test_inicios_cabem_a_duracao_inteira():
    assert cc.inicios_na_faixa(time(8), time(12), 60)[-1] == time(11)
    assert cc.inicios_na_faixa(time(8), time(12), 30)[-1] == time(11, 30)
    assert cc.inicios_na_faixa(time(8), time(9), 15) == [time(8), time(8, 15), time(8, 30), time(8, 45)]
    assert cc.inicios_na_faixa(time(8), time(8, 20), 30) == []


def test_livres_tira_passado_ocupado_e_bloqueio():
    ocupado = [(_br(SEG, 8), _br(SEG, 8, 30))]
    livres = cc.livres_puros(GRADE, [], MANOEL, 30, SEG, 1, AGORA, ocupados=ocupado)
    inicios = [(x["inicio"] - timedelta(hours=3)).strftime("%H:%M") for x in livres]
    assert inicios[:3] == ["08:30", "09:00", "09:30"]
    assert "12:00" not in inicios and "13:30" in inicios and inicios[-1] == "16:00"
    # um de 60 min não começa às 08:00 por causa das 08:00–08:30 ocupadas
    livres60 = cc.livres_puros(GRADE, [], MANOEL, 60, SEG, 1, AGORA, ocupados=ocupado)
    assert (livres60[0]["inicio"] - timedelta(hours=3)).strftime("%H:%M") == "08:30"
    # nada no passado
    tarde = _br(SEG, 15)
    assert all(x["inicio"] > tarde for x in cc.livres_puros(GRADE, [], MANOEL, 30, SEG, 1, tarde))
    # limite
    assert len(cc.livres_puros(GRADE, [], MANOEL, 30, SEG, 5, AGORA, limite=3)) == 3


def test_preco_em_centavos():
    assert cc.centavos("500") == 50000
    assert cc.centavos("500,00") == 50000
    assert cc.centavos("R$ 1.200,50") == 120050
    assert cc.centavos("") == 0
    assert cc.centavos("abc") is None
    assert cc.reais(50000) == "R$ 500" and cc.reais(0) == "sob consulta"
