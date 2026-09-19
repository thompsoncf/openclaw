"""A FAIXA DO ANO — onde ainda dá pra vender, mês a mês.

POR QUE EXISTE. O calendário mostra UM mês e a Prime vende de agosto de 2026 a
fevereiro de 2028: dezenove meses de seta para doze meses com evento, e sete
cliques caindo em mês vazio. Julho de 2027 ficava a dez cliques da tela inicial.

E ela conta SÁBADO, não "dia livre". Medido na Prime em 19/09/2026: dos 42 dias
vendidos, VINTE E QUATRO são sábado — 57%. Sexta 7, quinta 5, segunda 3,
domingo 3. Terça vaga não é estoque; sábado vago é.

Banco dedicado e descartável, no padrão de tests/test_agenda_marcacao.py.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag

CONTA = 11
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_faixa"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Espaço')", (CONTA,))
        c.commit()
    yield p
    p.close()


#: Uma quinta e um sábado de um mês tranquilo, longe da virada do ano.
_DE = datetime(2027, 3, 1, 9, 0, tzinfo=ag.BRT)


def _marcar(pool, quando, titulo="Casamento — Fulana", **kw):
    kw.setdefault("tipo", "empresa")
    kw.setdefault("tipo_evento", "Casamento")
    return ag.criar_evento(pool, CONTA, titulo, quando, **kw)


def _mes(faixa, rotulo):
    return next(f for f in faixa if f["rotulo"] == rotulo)


# ------------------------------------------------------------- a moldura

def test_a_faixa_cobre_treze_meses_a_partir_do_mes_atual(pool):
    faixa = ag.ocupacao_por_mes(pool, CONTA, de=_DE)
    assert len(faixa) == 13
    assert faixa[0]["mes"] == "2027-03" and faixa[0]["rotulo"] == "mar/27"
    assert faixa[-1]["mes"] == "2028-03"


def test_a_faixa_atravessa_a_virada_do_ano_sem_tropecar(pool):
    """Dezembro -> janeiro é onde conta de mês costuma quebrar."""
    faixa = ag.ocupacao_por_mes(pool, CONTA, de=datetime(2027, 12, 1, 9, 0, tzinfo=ag.BRT))
    assert [f["mes"] for f in faixa[:3]] == ["2027-12", "2028-01", "2028-02"]


def test_conta_vazia_devolve_a_faixa_inteira_zerada(pool):
    """A faixa é a moldura do ano; ela não some por falta de evento."""
    faixa = ag.ocupacao_por_mes(pool, 999, de=_DE)
    assert len(faixa) == 13
    assert all(f["ocupados"] == 0 for f in faixa)
    assert _mes(faixa, "abr/27")["sabados"] == 4


# ------------------------------------------------------- o sábado é o produto

def test_sabado_vendido_sai_da_conta_de_livres(pool):
    """03/04/2027 é um sábado. Vendido, sobram 3 dos 4 de abril."""
    antes = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "abr/27")
    assert antes["sabados"] == 4 and antes["sabados_livres"] == 4
    _marcar(pool, datetime(2027, 4, 3, 19, 0, tzinfo=ag.BRT))
    depois = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "abr/27")
    assert depois["sabados"] == 4 and depois["sabados_livres"] == 3
    assert depois["ocupados"] == 1


def test_visita_no_sabado_nao_tira_o_sabado_do_estoque(pool):
    """A regra do dono inteira, medida em número: 08/05/2027 é sábado, e uma
    visita marcada nele NÃO pode sumir da lista do que dá pra vender."""
    antes = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "mai/27")
    _marcar(pool, datetime(2027, 5, 8, 10, 0, tzinfo=ag.BRT),
            titulo="Visita — Andressa", tipo_evento=None)
    depois = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "mai/27")
    assert depois["sabados_livres"] == antes["sabados_livres"]
    assert depois["ocupados"] == antes["ocupados"]


def test_data_segurada_tira_o_sabado_do_estoque(pool):
    """Segurada ocupa HOJE: oferecê-la seria vender a mesma data duas vezes."""
    antes = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "jun/27")
    _marcar(pool, datetime(2027, 6, 5, 19, 0, tzinfo=ag.BRT), titulo="Casamento — Denise",
            pre_reserva_ate=_DE + timedelta(days=3))
    depois = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "jun/27")
    assert depois["sabados_livres"] == antes["sabados_livres"] - 1


def test_compromisso_pessoal_no_sabado_nao_ocupa(pool):
    """Aniversário do sócio é agenda, não venda de data."""
    antes = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "jul/27")
    _marcar(pool, datetime(2027, 7, 3, 12, 0, tzinfo=ag.BRT),
            titulo="Almoço em família", tipo="pessoal", tipo_evento=None)
    depois = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "jul/27")
    assert depois["sabados_livres"] == antes["sabados_livres"]


def test_o_que_ninguem_classificou_aparece_separado(pool):
    """Não entra em "ocupados" nem é esquecido: a faixa conta à parte pra a tela
    poder pedir a resposta em vez de chutar."""
    _marcar(pool, datetime(2027, 8, 12, 15, 0, tzinfo=ag.BRT),
            titulo="REUNIÃO COM ENGENHEIRA", tipo_evento=None)
    ago = _mes(ag.ocupacao_por_mes(pool, CONTA, de=_DE), "ago/27")
    assert ago["a_conferir"] == 1 and ago["ocupados"] == 0


# ------------------------------------------------------- ninguém vende ontem

def test_sabado_que_ja_passou_nao_conta_como_livre(pool):
    """"Livre" é o que dá pra vender HOJE. Em 2027-03-15 (segunda) já passaram os
    sábados 6 e 13 de março — a faixa não pode oferecê-los."""
    faixa = ag.ocupacao_por_mes(pool, CONTA, de=datetime(2027, 3, 15, 9, 0, tzinfo=ag.BRT))
    mar = _mes(faixa, "mar/27")
    # março/2027 tem sábados em 6, 13, 20 e 27 — só dois estão à frente
    assert mar["sabados"] == 2 and mar["sabados_livres"] == 2


# ------------------------------------------------- a régua mora num lugar só

def test_a_faixa_usa_a_MESMA_regua_do_calendario(pool):
    """`ocupacao_por_mes` não pode ter a sua própria ideia do que ocupa: ela
    chama `estado_do_dia`, a mesma função que pinta a célula. Reescrever a
    pergunta em SQL criaria a segunda cópia que um dia discorda da primeira —
    é o que o comentário de `_E_VISITA` já avisava em Relatórios."""
    import inspect
    fonte = inspect.getsource(ag.ocupacao_por_mes)
    assert "estado_do_dia" in fonte
    assert "ilike" not in fonte.lower()
