"""Relatórios da Outlet Chic: o vendedor vem da reserva do stand.

Relato do dono, 02/10/2026: "o relatório de vendas da outlet chic não tá
aparecendo o vendedor". No app de estandes a proposta nasce pela página dos
stands (`criado_por = 'pagina_stands'`) e o recebimento pela baixa do título,
sem membro — quem vendeu está em `prospeccao.vendedor_id` (o link da vendedora).
Só a conta com o app de estandes lê esse caminho; as demais seguem como eram.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text, comissao_pct numeric);
create table orcamentos (id bigserial primary key, conta_id bigint, numero int,
  cliente text, empresa text, token text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, primeiro_ano_centavos bigint,
  status text default 'fechado', criado_por text, aprovada_em timestamptz,
  sinal_pago_em timestamptz,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table contratos (id bigserial primary key, conta_id bigint, numero int,
  orcamento_id bigint, status text default 'enviado', valor_centavos bigint,
  assinado_em timestamptz, enviado_em timestamptz, substitui_id bigint, token text,
  criado_em timestamptz default now());
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  orcamento_id bigint, status text default 'novo', vendedor_id bigint);
create table evento_stands (id bigserial primary key, conta_id bigint, codigo text,
  status text, orcamento_id bigint, prospeccao_id bigint);
create table plano_contas (id bigserial primary key, grupo text);
create table clientes (id bigserial primary key, nome text);
create table titulos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  lancamento_id bigint, contraparte text);
create table lancamentos (id bigserial primary key, conta_id bigint, data date,
  descricao text, categoria text, origem text, membro_id bigint, valor_centavos bigint,
  plano_conta_id bigint, cliente_id bigint, tipo text, natureza text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_vendedor_estande_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool, monkeypatch):
    monkeypatch.setattr(rel, "get_pool", lambda: pool, raising=False)
    with pool.connection() as c:
        c.execute("truncate contas, membros, orcamentos, contratos, prospeccao, evento_stands, "
                  "plano_contas, clientes, titulos, lancamentos restart identity")
        conta = c.execute("insert into contas (nome) values ('Outlet Chic') returning id").fetchone()[0]
        outra = c.execute("insert into contas (nome) values ('Outra') returning id").fetchone()[0]
        eduarda = c.execute("insert into membros (conta_id, nome, comissao_pct) "
                            "values (%s,'Eduarda',5) returning id", (conta,)).fetchone()[0]
        rui = c.execute("insert into membros (conta_id, nome) values (%s,'Rui') returning id",
                        (conta,)).fetchone()[0]
        intrusa = c.execute("insert into membros (conta_id, nome) values (%s,'De Outra Conta') "
                            "returning id", (outra,)).fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, numero, empresa, setup_centavos, criado_por, token) "
                        "values (%s, 56, 'Anna Sabino', 370000, 'pagina_stands', 't1') returning id",
                        (conta,)).fetchone()[0]
        oid2 = c.execute("insert into orcamentos (conta_id, numero, empresa, setup_centavos, criado_por, token) "
                         "values (%s, 57, 'Outra Loja', 400000, 'pagina_stands', 't2') returning id",
                         (conta,)).fetchone()[0]
        pid = c.execute("insert into prospeccao (conta_id, empresa, vendedor_id) values (%s,'Anna Sabino',%s) "
                        "returning id", (conta, eduarda)).fetchone()[0]
        pid2 = c.execute("insert into prospeccao (conta_id, empresa, vendedor_id) values (%s,'Outra',%s) "
                         "returning id", (conta, intrusa)).fetchone()[0]
        c.execute("insert into evento_stands (conta_id, codigo, status, orcamento_id, prospeccao_id) "
                  "values (%s,'S150','vendido',%s,%s), (%s,'S151','vendido',%s,%s)",
                  (conta, oid, pid, conta, oid2, pid2))
        c.execute("insert into contratos (conta_id, numero, orcamento_id, valor_centavos, token) "
                  "values (%s, 77, %s, 370000, 'ct1')", (conta, oid))
        lid = c.execute("insert into lancamentos (conta_id, data, descricao, categoria, origem, "
                        "valor_centavos, tipo, natureza) values (%s, current_date, "
                        "'Evento — Anna Sabino · Sinal', 'Serviços', 'titulo', 200000, 'receita', "
                        "'empresa') returning id", (conta,)).fetchone()[0]
        c.execute("insert into titulos (conta_id, orcamento_id, lancamento_id, contraparte) "
                  "values (%s,%s,%s,'Anna Sabino')", (conta, oid, lid))
        c.commit()
    return {"conta": conta, "eduarda": eduarda, "rui": rui}


def _vendedores(dados):
    return {(l.get("cliente") or l.get("numero")): l["vendedor"] for l in dados["linhas"]}


def test_com_o_app_de_estandes_o_vendedor_vem_da_reserva(pool, cen, monkeypatch):
    monkeypatch.setattr(rel, "_tem_estande", lambda p, c: True)
    vendas = rel._dados_vendas(pool, cen["conta"], "todos")
    assert [l["vendedor"] for l in vendas["linhas"]] == ["Eduarda"]
    orc = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert _vendedores(orc)["Anna Sabino"] == "Eduarda"
    # o vendedor de outra conta nunca aparece aqui (o join cita a conta)
    assert _vendedores(orc)["Outra Loja"] == "—"
    ct = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert [l["vendedor"] for l in ct["linhas"]] == ["Eduarda"]
    # o filtro "Vendedor" também acha pela reserva
    so_ela = rel._dados_orcamentos(pool, cen["conta"], "todos", "", str(cen["eduarda"]), "")
    assert [l["cliente"] for l in so_ela["linhas"]] == ["Anna Sabino"]
    assert rel._dados_orcamentos(pool, cen["conta"], "todos", "", str(cen["rui"]), "")["linhas"] == []
    assert len(rel._dados_contratos(pool, cen["conta"], "todos", "", str(cen["eduarda"]), "")["linhas"]) == 1


def test_sem_o_app_de_estandes_segue_como_era(pool, cen, monkeypatch):
    monkeypatch.setattr(rel, "_tem_estande", lambda p, c: False)
    vendas = rel._dados_vendas(pool, cen["conta"], "todos")
    assert [l["vendedor"] for l in vendas["linhas"]] == ["-"]
    orc = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert _vendedores(orc)["Anna Sabino"] == "—"
    assert rel._dados_orcamentos(pool, cen["conta"], "todos", "", str(cen["eduarda"]), "")["linhas"] == []


def test_a_comissao_do_app_de_estandes_e_da_vendedora_da_reserva(pool, cen, monkeypatch):
    from datetime import date
    from finance import comissao as com
    monkeypatch.setattr(com, "_tem_estande", lambda p, c: True)
    hoje = date.today()
    linhas = com.por_vendedor(pool, cen["conta"], hoje, hoje)
    assert [(l["vendedor"], l["recebido_centavos"], l["comissao_centavos"], l["sem_vendedor"])
            for l in linhas] == [("Eduarda", 200000, 10000, False)]
    assert com.de_um(pool, cen["conta"], cen["eduarda"], hoje, hoje)["comissao_centavos"] == 10000
    # sem o app de estandes: como era (o recebimento sem membro fica "sem vendedor")
    monkeypatch.setattr(com, "_tem_estande", lambda p, c: False)
    linhas = com.por_vendedor(pool, cen["conta"], hoje, hoje)
    assert [(l["sem_vendedor"], l["comissao_centavos"]) for l in linhas] == [(True, 0)]
