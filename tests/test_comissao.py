"""Comissão: uma conta só, e a atribuição chegando em quem vendeu.

O bug que isto trava: a comissão era calculada em dois lugares diferentes e os
números não batiam. O relatório do dono somava `lancamentos`; o Cockpit somava
`prospeccao.valor_estimado_centavos` (o palpite do vendedor). Pior: quase tudo
caía em "Sem vendedor", porque a atribuição se perdia em quatro pontos —

  1. o PDV não passava `membro_id` (venda de balcão saía sem dono);
  2. a baixa do título creditava QUEM CLICOU em "pago", não quem vendeu;
  3. o título recorrente nascia sem `criado_por` (comissão só no 1º mês);
  4. fechar contrato creditava quem apertou o botão, não quem fez a proposta.

Regra combinada: a comissão é sobre o RECEBIDO. Lead ganho sem contrato não
conta — é previsão, não dinheiro.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import comissao as com

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, comissao_pct numeric(5,2));
create table lancamentos (id bigserial primary key, conta_id bigint, membro_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null default '',
  descricao text not null default '', data date not null, pagamento text default '',
  forma_pagamento text default '', origem text default 'manual', comprovante text default '',
  chave text, natureza text default 'empresa', plano_conta_id bigint, centro_custo_id bigint,
  criado_em timestamptz default now());
create table titulos (id bigserial primary key, conta_id bigint, tipo text not null,
  descricao text not null, contraparte text not null default '',
  valor_centavos int not null, vencimento date not null, status text default 'aberto',
  recorrente boolean default false, categoria text default '', lancamento_id bigint,
  cliente_id bigint, cobranca_link_url text, pago_em date, criado_por bigint,
  criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, itens jsonb, token text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, status text default 'rascunho', canal text,
  criado_por text, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
"""

HOJE = date.today()
INI, FIM = HOJE - timedelta(days=30), HOJE


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_comissao_test"
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
def cen(pool):
    with pool.connection() as c:
        c.execute("truncate contas, membros, lancamentos, titulos, orcamentos restart identity")
        conta = c.execute("insert into contas (nome) values ('Studio Vega') returning id").fetchone()[0]
        ana = c.execute("insert into membros (conta_id, nome, email, comissao_pct) "
                        "values (%s,'Ana','ana@x.com',10) returning id", (conta,)).fetchone()[0]
        bruno = c.execute("insert into membros (conta_id, nome, email, comissao_pct) "
                          "values (%s,'Bruno','bruno@x.com',null) returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "ana": ana, "bruno": bruno}


def _receita(pool, conta, membro_id, valor, *, natureza="empresa", quando=None):
    with pool.connection() as c:
        c.execute("""insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                     categoria, data, natureza) values (%s,%s,'receita',%s,'vendas',%s,%s)""",
                  (conta, membro_id, valor, quando or HOJE, natureza))
        c.commit()


# ------------------------------------------------------------------ a conta
def test_comissao_e_percentual_do_recebido(pool, cen):
    _receita(pool, cen["conta"], cen["ana"], 100000)      # R$ 1.000
    _receita(pool, cen["conta"], cen["ana"], 50000)       # R$   500
    linha = com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)
    assert linha["recebido_centavos"] == 150000
    assert linha["n_vendas"] == 2
    assert linha["comissao_centavos"] == 15000           # 10% de R$ 1.500


def test_sem_percentual_configurado_nao_inventa(pool, cen):
    _receita(pool, cen["conta"], cen["bruno"], 80000)
    linha = com.de_um(pool, cen["conta"], cen["bruno"], INI, FIM)
    assert linha["recebido_centavos"] == 80000
    assert linha["configurada"] is False
    assert linha["comissao_centavos"] == 0


def test_despesa_e_receita_pessoal_ficam_de_fora(pool, cen):
    _receita(pool, cen["conta"], cen["ana"], 70000, natureza="pessoal")
    with pool.connection() as c:
        c.execute("""insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                     categoria, data, natureza) values (%s,%s,'despesa',999999,'x',%s,'empresa')""",
                  (cen["conta"], cen["ana"], HOJE))
        c.commit()
    assert com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)["recebido_centavos"] == 0


def test_fora_do_periodo_nao_conta(pool, cen):
    _receita(pool, cen["conta"], cen["ana"], 90000, quando=HOJE - timedelta(days=90))
    assert com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)["recebido_centavos"] == 0


def test_venda_sem_vendedor_aparece_separada(pool, cen):
    """Não pode sumir: é venda entrando sem dono, e comissão que ninguém recebe."""
    _receita(pool, cen["conta"], None, 40000)
    linhas = com.por_vendedor(pool, cen["conta"], INI, FIM)
    orfa = [l for l in linhas if l["sem_vendedor"]]
    assert len(orfa) == 1 and orfa[0]["recebido_centavos"] == 40000
    assert orfa[0]["comissao_centavos"] == 0


def test_outra_conta_nao_vaza(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    _receita(pool, cen["conta"], cen["ana"], 100000)
    assert com.por_vendedor(pool, outra, INI, FIM) == []


# ------------------------------------------------------ as duas telas concordam
def test_relatorio_do_dono_e_tela_do_vendedor_dao_o_mesmo(pool, cen, monkeypatch):
    """O coração do bug: dono e vendedor viam números diferentes pra mesma venda."""
    _receita(pool, cen["conta"], cen["ana"], 250000)
    import db.conexao as conexao
    monkeypatch.setattr(conexao, "get_pool", lambda *a, **k: pool)
    import web.painel_relatorios as rel
    monkeypatch.setattr(rel, "_intervalo", lambda periodo: (INI, FIM))

    do_dono = rel._dados_comissao(pool, cen["conta"], "mes")
    linha_ana = next(l for l in do_dono["linhas"] if l["vendedor"] == "Ana")
    do_vendedor = com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)

    assert linha_ana["comissao_centavos"] == do_vendedor["comissao_centavos"] == 25000
    assert linha_ana["vendas_centavos"] == do_vendedor["recebido_centavos"]


# ------------------------------------------------------------------ atribuição
def test_baixa_credita_quem_vendeu_e_nao_quem_clicou(pool, cen):
    """Era o furo mais caro: o dono dava baixa e a comissão ia pra ele (ou pra
    ninguém), enquanto quem fechou a venda não aparecia no relatório."""
    from finance import empresa as emp
    with pool.connection() as c:
        tid = c.execute("""insert into titulos (conta_id, tipo, descricao, contraparte,
                           valor_centavos, vencimento, categoria, criado_por)
                           values (%s,'receber','Mensalidade','Cliente',60000,%s,'vendas',%s)
                           returning id""", (cen["conta"], HOJE, cen["ana"])).fetchone()[0]
        c.commit()
    # quem dá a baixa é OUTRA pessoa (o Bruno, do financeiro)
    r = emp.dar_baixa_titulo(pool, cen["conta"], tid, membro_id=cen["bruno"])
    assert r["ok"]
    with pool.connection() as c:
        dono_lanc = c.execute("select membro_id from lancamentos where id=%s",
                              (r["lancamento_id"],)).fetchone()[0]
    assert dono_lanc == cen["ana"], "a venda é da Ana, não de quem clicou"
    assert com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)["comissao_centavos"] == 6000
    assert com.de_um(pool, cen["conta"], cen["bruno"], INI, FIM)["recebido_centavos"] == 0


def test_recorrente_carrega_o_vendedor_pro_mes_seguinte(pool, cen):
    """Sem isso o vendedor recebia comissão no 1º mês e a recorrência virava órfã."""
    from finance import empresa as emp
    with pool.connection() as c:
        tid = c.execute("""insert into titulos (conta_id, tipo, descricao, contraparte,
                           valor_centavos, vencimento, categoria, recorrente, criado_por)
                           values (%s,'receber','Mensalidade','Cliente',30000,%s,'vendas',true,%s)
                           returning id""", (cen["conta"], HOJE, cen["ana"])).fetchone()[0]
        c.commit()
    r = emp.dar_baixa_titulo(pool, cen["conta"], tid)
    with pool.connection() as c:
        prox = c.execute("select criado_por from titulos where id=%s",
                         (r["proximo_titulo_id"],)).fetchone()[0]
    assert prox == cen["ana"]


def test_titulo_sem_origem_fica_sem_dono(pool, cen):
    """Melhor sem dono do que com o dono errado: chutar em quem clicou era o bug."""
    from finance import empresa as emp
    with pool.connection() as c:
        tid = c.execute("""insert into titulos (conta_id, tipo, descricao, contraparte,
                           valor_centavos, vencimento, categoria)
                           values (%s,'receber','Avulso','X',20000,%s,'vendas')
                           returning id""", (cen["conta"], HOJE)).fetchone()[0]
        c.commit()
    r = emp.dar_baixa_titulo(pool, cen["conta"], tid, membro_id=cen["bruno"])
    with pool.connection() as c:
        assert c.execute("select membro_id from lancamentos where id=%s",
                         (r["lancamento_id"],)).fetchone()[0] is None


def test_fechar_contrato_credita_quem_fez_a_proposta(pool, cen):
    """Um gestor pode fechar o contrato de um vendedor — a comissão é do vendedor."""
    from finance import cockpit as ck
    with pool.connection() as c:
        oid = c.execute("""insert into orcamentos (conta_id, cliente, empresa,
                           setup_centavos, mensal_centavos, status, criado_por, canal)
                           values (%s,'Contato','Cliente SA',100000,20000,'enviado',%s,'cockpit')
                           returning id""", (cen["conta"], str(cen["ana"]))).fetchone()[0]
        c.commit()
    # quem fecha é a gestão (membro_id=None), não a Ana
    r = ck.fechar_contrato(pool, cen["conta"], oid)
    assert r["ok"], r
    with pool.connection() as c:
        donos = c.execute("select distinct criado_por from titulos where conta_id=%s",
                          (cen["conta"],)).fetchall()
    assert [d[0] for d in donos] == [cen["ana"]]


# ------------------------------------------------ ganho sem contrato não conta
def test_lead_ganho_sem_contrato_nao_vira_comissao(pool, cen):
    """Decisão de produto: o valor estimado do lead é palpite do próprio vendedor.
    Só vira comissão depois de virar contrato e o cliente pagar."""
    assert com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)["recebido_centavos"] == 0
    assert com.de_um(pool, cen["conta"], cen["ana"], INI, FIM)["comissao_centavos"] == 0
