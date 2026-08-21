"""O selo do canal no card do funil vira BOTÃO — mas só quando existe conversa
de verdade, não só telefone/e-mail cadastrado.

POR QUE ISTO EXISTE. O selo 💬 já existia no card, mas só avisava "tem WhatsApp
cadastrado" — não abria nada, e não sabia se alguém já tinha escrito. Clicar em
qualquer parte do card abria a FICHA do lead, nunca o chat. Isto testa a regra
nova: o selo só clica quando `conversas` tem uma linha de verdade pra aquele
lead+canal — buscada em LOTE (`= any(%s)`) na mesma consulta do kanban, uma
query pro board inteiro, não uma por card.
"""
import os

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams
from types import SimpleNamespace

from web import painel_prospeccao as pp

CONTA = 11

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, segmento text, cidade text, uf text, telefone text, whatsapp text,
  email text, instagram text, temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, proximo_contato_em date,
  enriquecido_em timestamptz, estagio text default 'lead', status text default 'novo',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint references prospeccao(id), canal text,
  criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_kanban_chat_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
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


def _lead(pool, *, empresa, whatsapp="", email="", instagram=""):
    with pool.connection() as c:
        lid = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, email, instagram)
               values (%s,%s,%s,%s,%s) returning id""",
            (CONTA, empresa, whatsapp or None, email or None, instagram or None)
        ).fetchone()[0]
        c.commit()
    return lid


def _conversa(pool, lead_id, canal):
    with pool.connection() as c:
        cid = c.execute(
            "insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,%s) returning id",
            (CONTA, lead_id, canal)).fetchone()[0]
        c.commit()
    return cid


def _kanban_html(monkeypatch, pool) -> str:
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, vendedor="")
    assert r.status_code == 200
    return bytes(r.body).decode("utf-8")


def test_lead_com_conversa_de_verdade_vira_botao(monkeypatch, pool):
    lid = _lead(pool, empresa="Padaria Bom Pão", whatsapp="86999998888")
    cid = _conversa(pool, lid, "whatsapp")
    html = _kanban_html(monkeypatch, pool)
    assert f"kbAbrirChat(event,{cid},'conversas',this)" in html, (
        "o selo de quem já tem conversa não virou botão")


def test_lead_so_com_telefone_cadastrado_continua_span_inerte(monkeypatch, pool):
    """O caso que motivou a regra: número cadastrado não é conversa. Sem isso o
    selo clicaria em algo que não existe (nenhum conversa_id pra abrir)."""
    _lead(pool, empresa="Distribuidora Rio Poti", whatsapp="86988887777")
    html = _kanban_html(monkeypatch, pool)
    assert "kbAbrirChat" not in html.split("Distribuidora Rio Poti")[1].split("</div>")[0], (
        "virou botão sem ter conversa nenhuma")
    assert '<span title="WhatsApp">💬</span>' in html


def test_os_tres_canais_viram_botao_cada_um_com_a_conversa_certa(monkeypatch, pool):
    lid = _lead(pool, empresa="Grupo Cerrado", whatsapp="86977776666",
               email="contato@cerrado.com", instagram="@cerrado")
    c_wa = _conversa(pool, lid, "whatsapp")
    c_em = _conversa(pool, lid, "email")
    c_ig = _conversa(pool, lid, "instagram")
    html = _kanban_html(monkeypatch, pool)
    assert f"kbAbrirChat(event,{c_wa},'conversas',this)" in html
    assert f"kbAbrirChat(event,{c_em},'emails',this)" in html
    assert f"kbAbrirChat(event,{c_ig},'conversas',this)" in html


def test_conversa_de_outra_conta_nao_vaza_pro_selo(monkeypatch, pool):
    """Isolamento multi-tenant: uma conversa com o MESMO prospeccao_id só é
    válida dentro da própria conta — a query já filtra por conta_id, mas o teste
    trava que ninguém tire esse filtro num refactor futuro."""
    lid = _lead(pool, empresa="Papelaria Central", whatsapp="86966665555")
    with pool.connection() as c:
        c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (999,%s,'whatsapp')",
                  (lid,))
        c.commit()
    html = _kanban_html(monkeypatch, pool)
    assert "kbAbrirChat" not in html.split("Papelaria Central")[1].split("</div>")[0]


def test_uma_unica_query_de_conversas_pro_board_inteiro(monkeypatch, pool):
    """N+1: 20 leads não podem virar 20 idas ao banco só pra saber quem tem chat.
    Conta as chamadas de `execute` que mencionam a tabela `conversas`."""
    for i in range(20):
        lid = _lead(pool, empresa=f"Lead {i}", whatsapp=f"8690000{i:04d}")
        if i % 2 == 0:
            _conversa(pool, lid, "whatsapp")
    chamadas = []
    real_connection = pool.connection
    class _ConnSpy:
        def __init__(self, conn):
            self._conn = conn
        def __enter__(self):
            self._c = self._conn.__enter__()
            real_execute = self._c.execute
            def execute_espiao(sql, *a, **k):
                if "from conversas" in sql or "conversas c" in sql:
                    chamadas.append(sql)
                return real_execute(sql, *a, **k)
            self._c.execute = execute_espiao
            return self._c
        def __exit__(self, *a):
            return self._conn.__exit__(*a)
    monkeypatch.setattr(pool, "connection", lambda: _ConnSpy(real_connection()))
    _kanban_html(monkeypatch, pool)
    assert len(chamadas) == 1, f"esperava 1 query em lote, teve {len(chamadas)}: {chamadas}"
