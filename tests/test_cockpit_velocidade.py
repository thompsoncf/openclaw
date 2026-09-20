"""Velocidade do app, medida no celular de quem usa (migração 302).

O servidor já se cronometrava desde 19/09/2026, mas o número ia pro log e morria
lá: não dava pra comparar a semana passada com hoje, nem separar "o servidor
demorou" de "a rede do vendedor estava ruim" — e o log não sabe o que acontece
DEPOIS da resposta, quando o aparelho ainda precisa desenhar a tela.

Aqui se prova o que a tela do dono promete: a régua é a MEDIANA (a média esconde
o dia ruim), a quebra do tempo soma 100%, e telemetria nenhuma derruba a tela de
ninguém.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import velocidade as vel

_SQL = """
create table contas (id bigserial primary key, nome text);
create table tempo_tela (
    id bigserial primary key,
    conta_id bigint not null references contas(id) on delete cascade,
    membro_id bigint,
    tela text not null,
    servidor_ms integer not null default 0,
    banco_ms integer not null default 0,
    consultas integer not null default 0,
    conexoes integer not null default 0,
    conexao_ms integer not null default 0,
    espera_ms integer not null default 0,
    render_ms integer not null default 0,
    total_ms integer not null default 0,
    rede text not null default '',
    criado_em timestamptz not null default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_velocidade_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _conta(pool, nome="Emp"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (nome) values (%s) returning id", (nome,)).fetchone()[0]
        c.commit()
    return cid


def _grava(pool, conta, tela, total, **extra):
    d = {"tela": tela, "total": total}
    d.update(extra)
    return vel.gravar(pool, conta, None, d)


# ---------------------------------------------------------------- o caminho


def test_a_tela_perde_o_id_porque_e_a_mesma_tela():
    """/cockpit/lead/812 e /cockpit/lead/90 são a MESMA tela — comparar uma com a
    outra é o ponto. Mesma regra do middleware que mede o servidor."""
    assert vel.limpar_tela("/cockpit/lead/812/ficha?q=x") == "/cockpit/lead/{id}/ficha"
    assert vel.limpar_tela("/cockpit") == "/cockpit"


def test_o_que_nao_e_do_app_nao_entra():
    """A rota é aberta a qualquer sessão do cockpit: sem este corte, qualquer
    caminho viraria linha de telemetria."""
    assert vel.limpar_tela("/painel/prospeccao") == ""
    assert vel.limpar_tela("") == ""


def test_grava_e_resume_pela_mediana_nao_pela_media(pool):
    """Nove aberturas boas e uma péssima: a média diria 1,2 s (parece ok) e a
    mediana diz 400 ms — mas o p95 denuncia os 9 s, que é o toque repetido."""
    conta = _conta(pool)
    for _ in range(9):
        _grava(pool, conta, "/cockpit", 400, servidor=200, conexao=50, espera=50, render=100)
    _grava(pool, conta, "/cockpit", 9000, servidor=200, conexao=50, espera=50, render=8700)

    r = vel.resumo(pool, conta)
    assert r["n"] == 10
    assert r["mediana"] == 400, "a mediana não pode ser puxada pelo caso isolado"
    assert r["p95"] > 4000, "e o p95 tem que mostrar o dia ruim"
    assert r["ruins"] == 1, "passou de 2,5 s: é onde o vendedor toca de novo"
    assert r["pct_servidor"] + r["pct_rede"] + r["pct_render"] == 100


def test_a_lista_por_tela_vem_da_mais_lenta(pool):
    conta = _conta(pool)
    for _ in range(3):
        _grava(pool, conta, "/cockpit", 300, servidor=200)
        _grava(pool, conta, "/cockpit/lead/7", 2000, servidor=1800, banco=1500,
               consultas=12, conexoes=4)

    telas = vel.por_tela(pool, conta)
    assert [t["tela"] for t in telas] == ["/cockpit/lead/{id}", "/cockpit"]
    lead = telas[0]
    assert lead["mediana"] == 2000 and lead["servidor"] == 1800
    assert lead["banco"] == 1500 and lead["consultas"] == 12.0
    assert lead["conexoes"] == 4.0, "conexão tem conserto diferente de consulta"


def test_tela_com_poucas_amostras_nao_vira_veredito(pool):
    """Uma abertura só não é medida: seria uma tela "lentíssima" por causa de um
    toque num elevador."""
    conta = _conta(pool)
    _grava(pool, conta, "/cockpit/agenda", 8000)
    assert vel.por_tela(pool, conta) == []


def test_conta_nao_enxerga_a_medida_da_outra(pool):
    a, b = _conta(pool, "A"), _conta(pool, "B")
    for _ in range(3):
        _grava(pool, a, "/cockpit", 300)
    assert vel.resumo(pool, b)["n"] == 0
    assert vel.por_tela(pool, b) == []


# ---------------------------------------------------------------- o lixo


def test_numero_maluco_entra_zerado_em_vez_de_envenenar_a_mediana(pool):
    """A aba que dormiu a noite inteira devolve "total: 40 milhões de ms". Medir
    não pode inventar problema que não existe."""
    conta = _conta(pool)
    assert _grava(pool, conta, "/cockpit", 40_000_000, servidor="não é número")
    with pool.connection() as c:
        total, srv = c.execute("select total_ms, servidor_ms from tempo_tela").fetchone()
    assert total == 0 and srv == 0


def test_sem_tela_nao_grava_nada(pool):
    conta = _conta(pool)
    assert vel.gravar(pool, conta, None, {}) is False
    assert vel.gravar(pool, conta, None, {"tela": "/painel/x", "total": 10}) is False
    with pool.connection() as c:
        assert c.execute("select count(*) from tempo_tela").fetchone()[0] == 0


def test_o_periodo_vazio_responde_zero_e_nao_explode(pool):
    """A tela abre no dia seguinte a uma limpeza, ou numa conta que nunca usou o
    app: dividir a quebra por zero derrubaria justamente a tela do diagnóstico."""
    conta = _conta(pool)
    r = vel.resumo(pool, conta)
    assert r == {"n": 0, "mediana": 0, "p95": 0, "ruins": 0,
                 "pct_servidor": 0, "pct_rede": 0, "pct_render": 0}
