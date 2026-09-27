"""O dia da semana que a IA não pode chutar (finance/calendario.py).

O CASO (27/09/2026): no "Testar comigo" do resgate, o dono respondeu "06 07 2028" e a
IA escreveu "06/07/2028 (sábado)". É quinta-feira — e na Prime o dia da semana escolhe
o pacote ("Segunda a Quinta" × "Sábado"). Estes testes fixam as duas pontas: o
calendário que entra no prompt e a correção do que sai pro cliente.
"""
from datetime import date

import pytest

from finance import calendario as cal

HOJE = date(2026, 9, 27)       # domingo


@pytest.mark.parametrize("txt, esperado", [
    ("06 07 2028", [date(2028, 7, 6)]),
    ("06/07/2028", [date(2028, 7, 6)]),
    ("6-7-28", [date(2028, 7, 6)]),
    ("10102026 às 9", [date(2026, 10, 10)]),
    ("6 de julho de 2028", [date(2028, 7, 6)]),
    ("dia 15/11", [date(2026, 11, 15)]),              # sem ano: a próxima
    ("dia 10/01", [date(2027, 1, 10)]),               # já passou este ano
    ("meu número é 86 99999 0001", []),
    ("200 convidados às 19h", []),
    ("31/02/2027", []),                               # não existe
])
def test_acha_as_datas_da_conversa(txt, esperado):
    assert cal.datas(txt, HOJE) == esperado


def test_o_bloco_do_prompt_traz_o_dia_certo():
    b = cal.bloco("06 07 2028\nmedicina", "visita 10102026", hoje=HOJE)
    assert "06/07/2028 é quinta-feira" in b and "10/10/2026 é sábado" in b
    assert "nunca calcule de cabeça" in b
    assert cal.bloco("sem data nenhuma aqui", hoje=HOJE) == ""


@pytest.mark.parametrize("errado, certo", [
    ("🗓️ 06/07/2028 (sábado)", "🗓️ 06/07/2028 (quinta-feira)"),
    ("06/07/2028, sábado", "06/07/2028, quinta-feira"),
    ("a festa 10/10/2026 - Sexta-feira", "a festa 10/10/2026 - Sábado"),
    ("Sábado, 06/07/2028", "Quinta-feira, 06/07/2028"),
    ("quinta dia 10/10/2026", "sábado dia 10/10/2026"),
])
def test_corrige_o_dia_errado_colado_na_data(errado, certo):
    assert cal.corrigir(errado, HOJE) == certo


def test_o_dia_certo_e_o_texto_sem_data_ficam_como_vieram():
    assert cal.corrigir("06/07/2028 (quinta-feira)", HOJE) == "06/07/2028 (quinta-feira)"
    txt = "Sábado é o dia mais procurado! Quer conhecer o espaço?"
    assert cal.corrigir(txt, HOJE) == txt


def test_o_dia_certo_mantem_a_forma_de_quem_escreveu():
    """A lista de horários da visita escreve "segunda 28/09": certo, fica como está."""
    txt = "A) segunda 28/09 às 17h\nB) terça 29/09 às 9h\nC) quarta 30/09 às 9h"
    assert cal.corrigir(txt, HOJE) == txt
    assert cal.corrigir("Terça, 29/09", HOJE) == "Terça, 29/09"
    assert cal.corrigir("sexta 28/09", HOJE) == "segunda-feira 28/09"
