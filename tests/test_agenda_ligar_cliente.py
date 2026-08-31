"""Ligar um compromisso antigo ao cadastro — o botão que esgota a camada 2.

A leitura do título tira 51 linhas da Prime do "—", mas ela é PALPITE. Sem um
jeito de confirmar, o palpite ficaria pra sempre na tela fingindo ser dado; e sem
um jeito de dizer "este não tem cliente", as linhas que legitimamente não têm dono
(reunião interna, visita batizada com o nome do vendedor) cobrariam atenção
eternamente — e lista que nunca esvazia é lista que ninguém mais abre.

Esta tela resolve as duas pontas: confirma o palpite (vira `cliente_id`) ou
declara a ausência (`sem_cliente`, migração 193).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from finance import clientes as cli
from web import painel_relatorios as rel

CONTA = 7
OUTRA = 8
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_MIGRACOES = ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
              "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
              "130_evento_desfecho.sql", "131_evento_link_online.sql",
              "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
              "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
              "163_evento_sinal_esperado.sql", "179_agenda_tipo_e_hora_sugerida.sql",
              "064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql", "149_cliente_cidade_uf.sql",
              "182_clientes_papel.sql", "192_evento_cliente.sql",
              "193_evento_sem_cliente.sql")


@pytest.fixture
def http(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_ligar_cliente"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, tipo text, nome text)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text, ativo boolean default true)")
        c.execute("create table lancamentos (id bigserial primary key, conta_id bigint)")
        for nome in _MIGRACOES:
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("alter table clientes add column if not exists endereco text")
        c.execute("alter table clientes add column if not exists cep text")
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Prime')", (CONTA,))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Vizinha')", (OUTRA,))
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'PEDRO YAN PRIME','vendedor')",
                  (CONTA,))
        c.commit()

    monkeypatch.setattr(rel, "get_pool", lambda: pool)
    monkeypatch.setattr(rel, "_pode_ver",
                        lambda request: ([CONTA, "pj", "Prime"] + [None] * 13, None))
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(rel.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _evento(http, titulo, *, tipo_evento=None, conta=CONTA):
    with http.pool.connection() as c:
        eid = c.execute(
            "insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) "
            "values (%s,%s,%s,%s) returning id",
            (conta, titulo, ag.agora_brt() + timedelta(days=10), tipo_evento)).fetchone()[0]
        c.commit()
    return eid


def _estado(http, eid):
    with http.pool.connection() as c:
        return c.execute("select cliente_id, sem_cliente from eventos_agenda where id=%s",
                         (eid,)).fetchone()


def _quantos_clientes(http):
    with http.pool.connection() as c:
        return c.execute("select count(*) from clientes where dono_id=%s and ativo",
                         (CONTA,)).fetchone()[0]


# ------------------------------------------------------------------ a tela

def test_a_tela_abre_com_o_palpite_ja_preenchido(http):
    """Redigitar o nome que a tela acabou de mostrar é o tipo de trabalho que faz
    ninguém usar a ferramenta."""
    eid = _evento(http, "Casamento — Eva da Silva Fontoura", tipo_evento="Casamento")
    html = http.get(f"/painel/relatorios/agenda/{eid}/cliente").text
    assert 'value="Eva da Silva Fontoura"' in html
    assert "Casamento — Eva da Silva Fontoura" in html
    assert 'name="sem_cliente" value="1"' in html


def test_a_tela_nao_sugere_o_nome_do_vendedor(http):
    eid = _evento(http, "VISITA TÉCNICA - PEDRO")
    html = http.get(f"/painel/relatorios/agenda/{eid}/cliente").text
    assert 'value=""' in html or 'name="cliente_nome" value=""' in html
    assert "PEDRO" not in html.split('name="cliente_nome"')[1][:120]


def test_compromisso_de_outra_conta_nao_abre(http):
    alheio = _evento(http, "Locação — Fulano", conta=OUTRA)
    r = http.get(f"/painel/relatorios/agenda/{alheio}/cliente")
    assert r.status_code == 303 and "tipo=agenda" in r.headers["location"]


# ------------------------------------------------------------------ ligar

def test_ligar_num_cadastro_escolhido_da_lista(http):
    eid = _evento(http, "Locação — Jonas Barreto", tipo_evento="Locação")
    cid = cli.criar_cliente(http.pool, CONTA, "Jonas Barreto")
    r = http.post(f"/painel/relatorios/agenda/{eid}/cliente",
                  data={"cliente_id": str(cid), "cliente_nome": "Jonas Barreto"})
    assert r.status_code == 303
    assert _estado(http, eid) == (cid, False)
    assert _quantos_clientes(http) == 1


def test_confirmar_o_palpite_cadastra_quando_nao_existe(http):
    eid = _evento(http, "Casamento — Eva da Silva Fontoura", tipo_evento="Casamento")
    r = http.post(f"/painel/relatorios/agenda/{eid}/cliente",
                  data={"cliente_nome": "Eva da Silva Fontoura"})
    assert r.status_code == 303
    cid, sem = _estado(http, eid)
    assert cid and sem is False
    assert cli.obter_cliente(http.pool, CONTA, cid)["nome"] == "Eva da Silva Fontoura"


def test_confirmar_o_palpite_reusa_a_ficha_que_ja_existe(http):
    """Sem isto, confirmar o palpite em 36 compromissos cunharia 36 fichas
    repetidas — o problema que este trabalho inteiro veio fechar."""
    cid = cli.criar_cliente(http.pool, CONTA, "Zenilda Rosa Silva")
    e1 = _evento(http, "Locação — Zenilda Rosa Silva", tipo_evento="Locação")
    e2 = _evento(http, "Buffet — Zenilda Rosa Silva", tipo_evento="Buffet")
    for eid in (e1, e2):
        http.post(f"/painel/relatorios/agenda/{eid}/cliente",
                  data={"cliente_nome": "Zenilda Rosa Silva"})
    assert _estado(http, e1)[0] == cid and _estado(http, e2)[0] == cid
    assert _quantos_clientes(http) == 1, "cunhou ficha repetida ao confirmar"


def test_homonimo_pede_pra_escolher_em_vez_de_chutar(http):
    cli.criar_cliente(http.pool, CONTA, "Maria Souza", telefone="8695000011")
    cli.criar_cliente(http.pool, CONTA, "Maria Souza", telefone="8695000022")
    eid = _evento(http, "Locação — Maria Souza", tipo_evento="Locação")
    r = http.post(f"/painel/relatorios/agenda/{eid}/cliente",
                  data={"cliente_nome": "Maria Souza"})
    assert r.status_code == 303 and str(eid) in r.headers["location"]
    assert _estado(http, eid) == (None, False), "chutou um dos homônimos"
    assert _quantos_clientes(http) == 2, "cunhou um terceiro"


def test_ligar_sem_nome_nenhum_pede_de_volta(http):
    eid = _evento(http, "REUNIÃO COM ENGENHEIRA")
    r = http.post(f"/painel/relatorios/agenda/{eid}/cliente", data={"cliente_nome": ""})
    assert r.status_code == 303 and str(eid) in r.headers["location"]
    assert _estado(http, eid) == (None, False)


def test_ligar_ignora_id_de_outra_conta_e_cai_no_nome(http):
    alheio = cli.criar_cliente(http.pool, OUTRA, "Cliente da vizinha")
    eid = _evento(http, "Locação — Fulano de Tal", tipo_evento="Locação")
    http.post(f"/painel/relatorios/agenda/{eid}/cliente",
              data={"cliente_id": str(alheio), "cliente_nome": "Fulano de Tal"})
    cid, _sem = _estado(http, eid)
    assert cid and cid != alheio
    assert cli.obter_cliente(http.pool, CONTA, cid)["nome"] == "Fulano de Tal"


# -------------------------------------------------------- "não tem cliente"

def test_marcar_sem_cliente_cala_a_linha_sem_inventar_vinculo(http):
    eid = _evento(http, "VISITA TÉCNICA - PEDRO")
    r = http.post(f"/painel/relatorios/agenda/{eid}/cliente", data={"sem_cliente": "1"})
    assert r.status_code == 303
    assert _estado(http, eid) == (None, True)
    assert _quantos_clientes(http) == 0, "não pode cadastrar ninguém"


def test_ligar_depois_desfaz_o_sem_cliente(http):
    """Dizer de quem é responde a pergunta que o "não tem" tinha silenciado —
    deixar as duas marcas de pé faria a linha mentir dos dois jeitos."""
    eid = _evento(http, "Locação — Fulano de Tal", tipo_evento="Locação")
    http.post(f"/painel/relatorios/agenda/{eid}/cliente", data={"sem_cliente": "1"})
    assert _estado(http, eid)[1] is True
    http.post(f"/painel/relatorios/agenda/{eid}/cliente",
              data={"cliente_nome": "Fulano de Tal"})
    cid, sem = _estado(http, eid)
    assert cid and sem is False


def test_nao_da_pra_marcar_compromisso_de_outra_conta(http):
    alheio = _evento(http, "Locação — Fulano", conta=OUTRA)
    r = http.post(f"/painel/relatorios/agenda/{alheio}/cliente", data={"sem_cliente": "1"})
    assert r.status_code == 303
    assert _estado(http, alheio) == (None, False)
