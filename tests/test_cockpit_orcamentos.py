"""Carteira de propostas do Cockpit (finance.cockpit): escopo e movimentação.

A aba Propostas mostra a MESMA tabela `orcamentos` pras duas pontas, com alcance
diferente — é a regra que painel_servicos já aplica por `criado_por`:

  * vendedor  -> só as propostas que ELE criou;
  * dono/gestor -> a carteira toda, com filtro por vendedor.

O que precisa estar amarrado: o vendedor não pode ver nem mover a proposta de
outro pela URL (o id vem da tela, e tela não é fonte confiável).

Banco dedicado e descartável, no mesmo padrão de tests/test_cockpit.py.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, endereco text, bairro text, cidade text, uf text, logo_url text,
  banner_cor text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, status text default 'novo', estagio text default 'lead',
  orcamento_id bigint, whatsapp text, telefone text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, cnpj text, segmento text, whatsapp text, telefone text, email text,
  cidade text, uf text, itens jsonb, token text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  status text default 'rascunho', canal text, criado_por text,
  aprovada_em timestamptz, aprovada_por text, aprovada_doc text,
  criado_em timestamptz default now(), atualizado_em timestamptz default now());
create table titulos (id bigserial primary key, conta_id bigint not null,
  tipo text not null check (tipo in ('pagar','receber')), descricao text not null,
  contraparte text not null default '', valor_centavos int not null check (valor_centavos > 0),
  vencimento date not null, status text not null default 'aberto',
  recorrente boolean not null default false, categoria text not null default '',
  criado_por bigint, criado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_orcamentos_test"
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
def cenario(pool):
    """Uma conta, dois vendedores, uma proposta de cada. Limpa antes de cada teste."""
    with pool.connection() as c:
        c.execute("truncate orcamentos, prospeccao, membros, contas, titulos restart identity")
        conta = c.execute("insert into contas (nome) values ('Studio Vega') returning id").fetchone()[0]
        ana = c.execute("insert into membros (conta_id, nome, email, papel) "
                        "values (%s,'Ana','ana@x.com','vendedor') returning id", (conta,)).fetchone()[0]
        bruno = c.execute("insert into membros (conta_id, nome, email, papel) "
                          "values (%s,'Bruno','bruno@x.com','vendedor') returning id", (conta,)).fetchone()[0]

        def prop(autor, empresa, status, setup, mensal, token):
            return c.execute(
                """insert into orcamentos (conta_id, cliente, empresa, whatsapp, itens,
                     setup_centavos, mensal_centavos, status, criado_por, canal, token)
                   values (%s,%s,%s,'5586999990000',%s::jsonb,%s,%s,%s,%s,'cockpit',%s)
                   returning id""",
                (conta, "Contato " + empresa, empresa, json.dumps(
                    [{"nome": "Implantação", "setup": setup // 100, "mensal": mensal // 100}]),
                 setup, mensal, status, str(autor), token)).fetchone()[0]

        da_ana = prop(ana, "Pet Shop", "enviado", 180000, 39000, "tok-ana")
        do_bruno = prop(bruno, "Odonto", "negociando", 400000, 120000, "tok-bruno")
        # a da Ana nasceu de um lead; a do Bruno não
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, orcamento_id)
                            values (%s,%s,'Pet Shop',%s) returning id""",
                         (conta, ana, da_ana)).fetchone()[0]
        c.commit()
    return {"conta": conta, "ana": ana, "bruno": bruno,
            "da_ana": da_ana, "do_bruno": do_bruno, "lead": lead}


# ------------------------------------------------------------------ escopo da lista
def test_vendedor_ve_so_o_que_ele_fez(pool, cenario):
    da_ana = ck.orcamentos(pool, cenario["conta"], membro_id=cenario["ana"])
    assert [o["id"] for o in da_ana] == [cenario["da_ana"]]


def test_gestao_ve_a_carteira_toda(pool, cenario):
    todas = ck.orcamentos(pool, cenario["conta"])
    assert {o["id"] for o in todas} == {cenario["da_ana"], cenario["do_bruno"]}


def test_gestao_filtra_por_vendedor(pool, cenario):
    so_bruno = ck.orcamentos(pool, cenario["conta"], vendedor_id=cenario["bruno"])
    assert [o["id"] for o in so_bruno] == [cenario["do_bruno"]]


def test_filtra_por_status(pool, cenario):
    assert [o["id"] for o in ck.orcamentos(pool, cenario["conta"], status="negociando")] \
        == [cenario["do_bruno"]]
    assert ck.orcamentos(pool, cenario["conta"], status="fechado") == []


def test_status_invalido_nao_vira_filtro(pool, cenario):
    # texto solto na querystring não pode virar SQL nem esconder a lista
    assert len(ck.orcamentos(pool, cenario["conta"], status="'; drop table orcamentos; --")) == 2


# ------------------------------------------------------------------ o que a tela usa
def test_traz_link_publico_e_lead_de_origem(pool, cenario):
    o = ck.orcamentos(pool, cenario["conta"], membro_id=cenario["ana"])[0]
    assert o["link"].endswith("/proposta/tok-ana")
    assert o["lead_id"] == cenario["lead"]          # dá pra mandar na conversa
    assert o["zap"].startswith("https://wa.me/")    # e dá pra mandar no WhatsApp
    assert o["titulo"] == "Pet Shop"                # empresa na frente
    assert o["vendedor"] == "Ana"


def test_proposta_sem_lead_nao_inventa_um(pool, cenario):
    o = ck.orcamentos(pool, cenario["conta"], vendedor_id=cenario["bruno"])[0]
    assert o["lead_id"] is None


def test_proposta_sem_token_ganha_um(pool, cenario):
    """As antigas foram salvas antes do token existir — sem token não há link."""
    with pool.connection() as c:
        c.execute("update orcamentos set token=null where id=%s", (cenario["da_ana"],))
        c.commit()
    o = ck.orcamentos(pool, cenario["conta"], membro_id=cenario["ana"])[0]
    assert o["token"] and o["link"].endswith("/proposta/" + o["token"])


# ------------------------------------------------------------------ detalhe
def test_vendedor_nao_abre_proposta_de_outro(pool, cenario):
    assert ck.orcamento(pool, cenario["conta"], cenario["do_bruno"], membro_id=cenario["ana"]) is None
    # mas a gestão abre
    assert ck.orcamento(pool, cenario["conta"], cenario["do_bruno"]) is not None


def test_detalhe_traz_os_itens(pool, cenario):
    o = ck.orcamento(pool, cenario["conta"], cenario["da_ana"], membro_id=cenario["ana"])
    assert o["itens"] and o["itens"][0]["nome"] == "Implantação"
    assert o["setup_centavos"] == 180000 and o["mensal_centavos"] == 39000


def test_outra_conta_nao_enxerga(pool, cenario):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    assert ck.orcamento(pool, outra, cenario["da_ana"]) is None
    assert ck.orcamentos(pool, outra) == []


# ------------------------------------------------------------------ mover no funil
def test_vendedor_move_a_propria(pool, cenario):
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["da_ana"], "negociando",
                                  membro_id=cenario["ana"])
    assert r["ok"]
    o = ck.orcamento(pool, cenario["conta"], cenario["da_ana"], membro_id=cenario["ana"])
    assert o["status"] == "negociando" and o["status_rot"] == "Negociando"


def test_vendedor_nao_move_a_de_outro(pool, cenario):
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["do_bruno"], "perdido",
                                  membro_id=cenario["ana"])
    assert not r["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["do_bruno"])["status"] == "negociando"


def test_gestao_move_a_de_qualquer_um(pool, cenario):
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["do_bruno"], "aprovada")
    assert r["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["do_bruno"])["status"] == "aprovada"


def test_status_invalido_e_recusado(pool, cenario):
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["da_ana"], "arquivado")
    assert not r["ok"] and "inválido" in r["erro"].lower()
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "enviado"


def test_seletor_nao_fecha_contrato_pela_porta_dos_fundos(pool, cenario):
    """'fechado' não é status manual: marcar por aqui deixaria a proposta fechada
    SEM gerar os títulos a receber, e ninguém perceberia."""
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["da_ana"], "fechado")
    assert not r["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "enviado"
    assert "fechado" not in ck.STATUS_MANUAIS


# ------------------------------------------------------------------ fechar contrato
def _titulos(pool, conta):
    with pool.connection() as c:
        return c.execute("select descricao, valor_centavos, recorrente, tipo from titulos "
                         "where conta_id=%s order by id", (conta,)).fetchall()


def test_fechar_gera_entrada_e_mensalidade(pool, cenario):
    r = ck.fechar_contrato(pool, cenario["conta"], cenario["da_ana"], membro_id=cenario["ana"])
    assert r["ok"], r
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "fechado"
    tit = _titulos(pool, cenario["conta"])
    assert len(tit) == 2
    entrada, mensal = tit
    assert entrada[1] == 180000 and entrada[2] is False and entrada[3] == "receber"
    assert mensal[1] == 39000 and mensal[2] is True          # mensalidade é recorrente
    assert "Pet Shop" in entrada[0] and "Pet Shop" in mensal[0]


def test_duplo_clique_nao_gera_titulo_em_dobro(pool, cenario):
    ck.fechar_contrato(pool, cenario["conta"], cenario["da_ana"], membro_id=cenario["ana"])
    r2 = ck.fechar_contrato(pool, cenario["conta"], cenario["da_ana"], membro_id=cenario["ana"])
    assert not r2["ok"]
    assert len(_titulos(pool, cenario["conta"])) == 2


def test_vendedor_nao_fecha_contrato_de_outro(pool, cenario):
    """vendas.fechar_orcamento só escopa por conta — sem a checagem de dono, a
    Ana fecharia o contrato do Bruno (e geraria os títulos) pela URL."""
    r = ck.fechar_contrato(pool, cenario["conta"], cenario["do_bruno"], membro_id=cenario["ana"])
    assert not r["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["do_bruno"])["status"] == "negociando"
    assert _titulos(pool, cenario["conta"]) == []


def test_gestao_fecha_de_qualquer_um(pool, cenario):
    r = ck.fechar_contrato(pool, cenario["conta"], cenario["do_bruno"])
    assert r["ok"] and len(_titulos(pool, cenario["conta"])) == 2


def test_fechada_nao_volta_atras(pool, cenario):
    """Reabrir e fechar de novo geraria o contrato em dobro."""
    ck.fechar_contrato(pool, cenario["conta"], cenario["da_ana"])
    r = ck.mudar_status_orcamento(pool, cenario["conta"], cenario["da_ana"], "negociando")
    assert not r["ok"] and "contrato" in r["erro"].lower()
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "fechado"
    assert len(_titulos(pool, cenario["conta"])) == 2


def test_nao_fecha_de_outra_conta(pool, cenario):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    assert not ck.fechar_contrato(pool, outra, cenario["da_ana"])["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "enviado"
    assert _titulos(pool, outra) == []


def test_proposta_so_com_entrada_gera_um_titulo(pool, cenario):
    with pool.connection() as c:
        c.execute("update orcamentos set mensal_centavos=0 where id=%s", (cenario["da_ana"],))
        c.commit()
    r = ck.fechar_contrato(pool, cenario["conta"], cenario["da_ana"])
    assert r["ok"] and "1 título" in r["msg"]
    assert len(_titulos(pool, cenario["conta"])) == 1


def test_nao_move_proposta_de_outra_conta(pool, cenario):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    assert not ck.mudar_status_orcamento(pool, outra, cenario["da_ana"], "perdido")["ok"]
    assert ck.orcamento(pool, cenario["conta"], cenario["da_ana"])["status"] == "enviado"
