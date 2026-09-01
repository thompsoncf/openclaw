"""Vendas mostra o que o negócio VENDEU — produto ou serviço, não caixa.

O relato do dono em 01/09/2026: "os lançamentos estão nascendo lá e o correto é
contas pagas; vendas vem de produtos ou serviços". Conferido na Prime (conta 34),
a aba somava R$ 31.020,05 onde a empresa tinha vendido R$ 6.100,00 — cinco vezes
mais. O que inflava eram três coisas diferentes:

    R$ 21.470,05  12 recebimentos importados do extrato do banco
    R$  2.700,00   2 aportes de sócio
    R$    750,00   1 sinal contado duas vezes (o mesmo dinheiro entrou pela baixa
                   do título E pela foto do comprovante)

O que este teste protege:

  * **os três portões de exclusão, um por um** — e o quarto caso, que é o mais
    traiçoeiro: o aporte de R$ 2.500 da Prime tem categoria "Outros" (operacional)
    E plano "1.2.03 Outras Receitas" (grupo 1, operacional). Os dois primeiros
    portões passam batido; só o texto pega. Se alguém "simplificar" tirando a
    checagem de texto, esse dinheiro volta a contar como faturamento;
  * **a venda registrada à mão continua sendo venda.** É o teste que impede a
    régua de virar "só o que passou pelo funil": em produção a ZAQ e a Doce Mell
    não têm NENHUMA venda pelo funil — 46 das 50 receitas delas são foto/manual.
    Uma régua estrita zeraria a aba das duas;
  * **a linha repetida some da soma, mas a do TÍTULO é a que fica** — ela sabe o
    cliente e o orçamento; a foto do comprovante é o eco;
  * **nada sai calado.** O bloco `fora` leva quantidade, valor e motivo. Tirar
    dinheiro de uma tela sem dizer pra onde foi é a mesma família de erro que
    esconder o dinheiro (regra 0 do CLAUDE.md);
  * o cenário completo da Prime, com os 19 lançamentos reais reproduzidos, tem
    que fechar em R$ 6.100,00 e 4 vendas.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table clientes (id bigserial primary key, dono_id bigint, nome text);
create table plano_contas (id bigserial primary key, codigo text, nome text,
  grupo int, natureza text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, membro_id bigint,
  cliente_id bigint, plano_conta_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  origem text not null default 'manual', natureza text);
create table titulos (id bigserial primary key, conta_id bigint, lancamento_id bigint,
  contraparte text, tipo text, status text);
"""

HOJE = date.today()
ONTEM = HOJE - timedelta(days=1)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_vendas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        # o plano real da casa, nas duas pontas que importam pra esta régua
        c.execute("""insert into plano_contas (id, codigo, nome, grupo, natureza) values
                     (1,'1.2.03','Outras Receitas',1,'receita'),
                     (2,'1.2.04','Receita de Locação de Espaço',1,'receita'),
                     (3,'7.1.05','Aporte de Sócios',7,'receita')""")
        c.commit()
    yield p
    p.close()


@pytest.fixture
def conta(pool):
    with pool.connection() as c:
        c.execute("truncate contas, membros, clientes, lancamentos, titulos restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _rec(pool, conta_id, *, valor=10000, categoria="Vendas", descricao="Venda",
         data=None, origem="foto", natureza="empresa", plano=None,
         cliente_id=None, membro_id=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, membro_id, cliente_id, plano_conta_id,
                 tipo, valor_centavos, categoria, descricao, data, origem, natureza)
               values (%s,%s,%s,%s,'receita',%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, membro_id, cliente_id, plano, valor, categoria, descricao,
             data or HOJE, origem, natureza)).fetchone()[0]
        c.commit()
    return lid


def _vendas(pool, conta_id):
    return rel._dados_vendas(pool, conta_id, "todos")


# ── portão 1: a perna bancária ───────────────────────────────────────────────
def test_o_extrato_nao_e_venda(pool, conta):
    """Um Pix que caiu é o PAGAMENTO de uma venda, não uma venda nova — e a mesma
    venda costuma já estar registrada pelo funil ou pela foto."""
    _rec(pool, conta, valor=500000, origem="extrato", descricao="Recebimento Pix Jonas")
    _rec(pool, conta, valor=35000, origem="foto", descricao="Contrato Drinks")
    d = _vendas(pool, conta)
    assert [r["descricao"] for r in d["linhas"]] == ["Contrato Drinks"]
    assert d["total_centavos"] == 35000
    assert d["fora"]["centavos"] == 500000


# ── portão 2: não é receita do negócio ───────────────────────────────────────
def test_aporte_pela_categoria(pool, conta):
    _rec(pool, conta, valor=20000, categoria="Aporte", origem="manual",
         descricao="Dinheiro do sócio")
    d = _vendas(pool, conta)
    assert d["linhas"] == []
    assert d["fora"]["centavos"] == 20000


def test_aporte_pelo_plano_de_contas(pool, conta):
    """Categoria operacional, mas classificado em 7.1.05 Aporte de Sócios."""
    _rec(pool, conta, valor=50000, categoria="Vendas", plano=3, descricao="Entrada")
    d = _vendas(pool, conta)
    assert d["linhas"] == []
    assert d["fora"]["centavos"] == 50000


def test_aporte_que_so_o_texto_pega(pool, conta):
    """O caso real da Prime: R$ 2.500 com categoria "Outros" (operacional) e plano
    "1.2.03 Outras Receitas" (grupo 1, operacional). Os dois portões anteriores
    passam batido. Tirar a checagem de texto devolve esse dinheiro pro
    faturamento."""
    _rec(pool, conta, valor=250000, categoria="Outros", plano=1, origem="foto",
         descricao="Aporte sócio")
    d = _vendas(pool, conta)
    assert d["linhas"] == [], "aporte não é venda, mesmo mal classificado"
    assert d["fora"]["centavos"] == 250000


def test_emprestimo_tambem_sai(pool, conta):
    _rec(pool, conta, valor=100000, categoria="Outros", descricao="Empréstimo do banco")
    assert _vendas(pool, conta)["linhas"] == []


def test_a_palavra_no_meio_da_frase_conta(pool, conta):
    _rec(pool, conta, valor=100, categoria="Outros",
         descricao="Transferência entre contas da empresa")
    assert _vendas(pool, conta)["linhas"] == []


# ── portão 3: o mesmo dinheiro duas vezes ────────────────────────────────────
def test_o_sinal_contado_duas_vezes_conta_uma(pool, conta):
    """Bianca Oliveira, 28/08: um sinal de R$ 750 entrou pela baixa do título e
    pela foto do comprovante. A aba somava R$ 1.500."""
    _rec(pool, conta, valor=75000, origem="titulo", data=ONTEM,
         descricao="Evento — Bianca Oliveira · Sinal", categoria="Servicos")
    _rec(pool, conta, valor=75000, origem="foto", data=ONTEM,
         descricao="Sinal 50% locação espaço - Bianca Oliveira", categoria="Aluguel")
    d = _vendas(pool, conta)
    assert len(d["linhas"]) == 1
    assert d["total_centavos"] == 75000, "o mesmo dinheiro não pode contar duas vezes"


def test_a_linha_que_fica_e_a_do_titulo(pool, conta):
    """Ela sabe o cliente e o orçamento; a foto é o eco."""
    _rec(pool, conta, valor=75000, origem="titulo", data=ONTEM, descricao="Do título")
    _rec(pool, conta, valor=75000, origem="foto", data=ONTEM, descricao="Da foto")
    assert [r["descricao"] for r in _vendas(pool, conta)["linhas"]] == ["Do título"]


def test_mesmo_valor_em_dia_diferente_sao_duas_vendas(pool, conta):
    """Duas parcelas iguais em dias diferentes são duas vendas de verdade —
    apagar a segunda seria perder receita."""
    _rec(pool, conta, valor=75000, origem="titulo", data=HOJE, descricao="Parcela 1")
    _rec(pool, conta, valor=75000, origem="foto", data=ONTEM, descricao="Parcela 2")
    d = _vendas(pool, conta)
    assert len(d["linhas"]) == 2
    assert d["total_centavos"] == 150000


def test_dois_titulos_iguais_no_mesmo_dia_nao_se_apagam(pool, conta):
    """A regra só descarta a linha de OUTRA porta. Dois títulos legítimos de mesmo
    valor no mesmo dia (duas parcelas de eventos diferentes) continuam valendo."""
    _rec(pool, conta, valor=75000, origem="titulo", descricao="Evento A")
    _rec(pool, conta, valor=75000, origem="titulo", descricao="Evento B")
    assert len(_vendas(pool, conta)["linhas"]) == 2


# ── o que TEM que continuar entrando ─────────────────────────────────────────
def test_a_venda_registrada_a_mao_continua_sendo_venda(pool, conta):
    """O teste que impede a régua de virar "só o que passou pelo funil". Em
    produção a ZAQ e a Doce Mell não têm nenhuma venda pelo funil — uma régua
    estrita zeraria a aba das duas."""
    _rec(pool, conta, valor=300000, origem="foto", categoria="Aluguel", plano=2,
         descricao="Locação de espaço - Pedro Ribeiro (Parc. 2/3)")
    _rec(pool, conta, valor=240000, origem="manual", categoria="Vendas",
         descricao="Consultoria fechada no boca a boca")
    d = _vendas(pool, conta)
    assert len(d["linhas"]) == 2
    assert d["total_centavos"] == 540000
    assert {r["canal"] for r in d["linhas"]} == {"Manual"}


@pytest.mark.parametrize("origem,canal,cor", [
    ("titulo", "Funil", "ok"),
    ("balcao", "Balcão", "ok"),
    ("foto", "Manual", "neutro"),
    ("manual", "Manual", "neutro"),
])
def test_a_origem_vira_canal_na_tela(pool, conta, origem, canal, cor):
    _rec(pool, conta, origem=origem)
    linha = _vendas(pool, conta)["linhas"][0]
    assert (linha["canal"], linha["canal_cor"]) == (canal, cor)


def test_categoria_de_servico_nao_e_confundida_com_nao_operacional(pool, conta):
    """"Serviços" não está na lista oficial de categorias de receita, então
    canoniza pra "Outros" — que é operacional. Se algum dia virar não-operacional
    por engano, toda venda de serviço do funil some da aba."""
    _rec(pool, conta, valor=200000, categoria="Serviços", origem="titulo",
         descricao="Evento — Beatriz · Sinal")
    assert _vendas(pool, conta)["total_centavos"] == 200000


def test_pessoal_e_a_definir_nao_entram(pool, conta):
    _rec(pool, conta, valor=1000, natureza="empresa", descricao="Da empresa")
    _rec(pool, conta, valor=9900, natureza="pessoal", descricao="Do dono")
    _rec(pool, conta, valor=7700, natureza=None, descricao="Sem definir")
    d = _vendas(pool, conta)
    assert [r["descricao"] for r in d["linhas"]] == ["Da empresa"]


# ── quem é o cliente ─────────────────────────────────────────────────────────
def test_o_cliente_vem_do_titulo(pool, conta):
    lid = _rec(pool, conta, origem="titulo", descricao="Evento · Sinal")
    with pool.connection() as c:
        c.execute("insert into titulos (conta_id, lancamento_id, contraparte, tipo, "
                  "status) values (%s,%s,'Beatriz do Carmo Brito','receber','pago')",
                  (conta, lid))
        c.commit()
    assert _vendas(pool, conta)["linhas"][0]["cliente"] == "Beatriz do Carmo Brito"


def test_o_cliente_vem_do_cadastro_no_balcao(pool, conta):
    with pool.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Bianca "
                        "Oliveira') returning id", (conta,)).fetchone()[0]
        c.commit()
    _rec(pool, conta, origem="balcao", cliente_id=cli)
    assert _vendas(pool, conta)["linhas"][0]["cliente"] == "Bianca Oliveira"


def test_sem_cliente_a_tela_nao_inventa(pool, conta):
    """Venda lançada à mão quase nunca tem cliente ligado. Melhor o traço honesto
    que um nome chutado da descrição."""
    _rec(pool, conta, origem="foto", descricao="Locação - Pedro Ribeiro")
    assert _vendas(pool, conta)["linhas"][0]["cliente"] == "—"


# ── o bloco que devolve o dinheiro ───────────────────────────────────────────
def test_o_bloco_fora_traz_quantidade_valor_e_motivo(pool, conta):
    _rec(pool, conta, valor=100000, origem="extrato", descricao="Pix 1")
    _rec(pool, conta, valor=200000, origem="extrato", descricao="Pix 2")
    _rec(pool, conta, valor=20000, categoria="Aporte", descricao="Sócio")
    d = _vendas(pool, conta)
    itens = {i["texto"]: i for i in d["fora"]["itens"]}
    extrato = [v for k, v in itens.items() if "extrato" in k][0]
    assert extrato["n"] == 2 and extrato["centavos"] == 300000
    assert d["fora"]["centavos"] == 320000
    assert all(i["porque"] and i["aba"] for i in d["fora"]["itens"]), \
        "cada motivo precisa dizer o porquê e pra qual aba o dinheiro foi"


def test_sem_exclusao_nao_aparece_bloco(pool, conta):
    _rec(pool, conta, valor=1000)
    assert "fora" not in _vendas(pool, conta)


def test_aba_vazia_nao_quebra(pool, conta):
    d = _vendas(pool, conta)
    assert d["linhas"] == [] and d["total_centavos"] == 0
    assert ("Ticket médio", "R$ 0,00") in d["metricas"]


# ── o cenário inteiro, como estava em produção ───────────────────────────────
def test_o_cenario_da_prime_fecha_em_6100(pool, conta):
    """Os 19 lançamentos reais da conta 34 em 01/09/2026, reproduzidos. A aba
    somava R$ 31.020,05; a empresa vendeu R$ 6.100,00."""
    # as 4 vendas de verdade
    _rec(pool, conta, valor=200000, origem="titulo", categoria="Serviços",
         data=HOJE, descricao="Evento — Beatriz do Carmo Brito · Sinal")
    _rec(pool, conta, valor=75000, origem="titulo", categoria="Serviços",
         data=ONTEM, descricao="Evento — Bianca Oliveira · Sinal")
    _rec(pool, conta, valor=35000, origem="foto", categoria="Vendas", data=ONTEM,
         descricao="50% contrato Drinks - Evento 12/09/2026")
    _rec(pool, conta, valor=300000, origem="foto", categoria="Aluguel", plano=2,
         data=ONTEM, descricao="Locação de espaço - Pedro Ribeiro (Parc. 2/3)")
    # a repetida: o mesmo sinal da Bianca, pela foto
    _rec(pool, conta, valor=75000, origem="foto", categoria="Aluguel", data=ONTEM,
         descricao="Sinal 50% locação espaço - Bianca Oliveira")
    # os dois aportes, cada um mal classificado de um jeito
    _rec(pool, conta, valor=20000, origem="manual", categoria="Aporte", plano=1,
         descricao="Aporte de sócio - Espaço Pelle")
    _rec(pool, conta, valor=250000, origem="foto", categoria="Outros", plano=1,
         descricao="Aporte sócio")
    # os 12 do extrato, R$ 21.470,05
    for cent in (218290, 80000, 25000, 150000, 201000, 150000, 80000, 500000,
                 120000, 216000, 56715, 350000):
        _rec(pool, conta, valor=cent, origem="extrato", categoria="Outros",
             descricao="Recebimento Pix")

    d = _vendas(pool, conta)
    assert len(d["linhas"]) == 4, "quatro vendas de produto ou serviço"
    assert d["total_centavos"] == 610000, "R$ 6.100,00"
    assert ("Total vendido", "R$ 6.100,00") in d["metricas"]
    assert ("Ticket médio", "R$ 1.525,00") in d["metricas"]
    fora = {i["n"]: i["centavos"] for i in d["fora"]["itens"]}
    assert fora == {12: 2147005, 2: 270000, 1: 75000}
    assert d["fora"]["centavos"] == 2492005, "R$ 24.920,05 saíram da soma"
    # e o que entrou mais o que saiu tem que dar o total antigo — nada evaporou
    assert d["total_centavos"] + d["fora"]["centavos"] == 3102005


# ── a tabela ─────────────────────────────────────────────────────────────────
def test_a_elastica_e_o_que_foi_vendido(pool, conta):
    flex = [c["chave"] for c in _vendas(pool, conta)["colunas"] if c["flex"]]
    assert flex == ["descricao"]


def test_saiu_forma_de_pagamento_e_categoria(pool, conta):
    """Na Prime, Forma estava vazia em 15 das 19 linhas e Categoria era "Outros"
    em 13 — nenhuma das duas separava nada. Origem e Cliente entraram no lugar."""
    chaves = [c["chave"] for c in _vendas(pool, conta)["colunas"]]
    assert "forma" not in chaves and "categoria" not in chaves
    assert chaves == ["data", "descricao", "cliente", "canal", "vendedor",
                      "valor_centavos"]


# ── a régua isolada, sem banco ───────────────────────────────────────────────
@pytest.mark.parametrize("origem,cat,desc,grupo,esperado", [
    ("extrato", "Vendas", "Pix", 1, "extrato"),
    ("foto", "Aporte", "x", None, "nao_venda"),
    ("foto", "Vendas", "x", 7, "nao_venda"),
    ("foto", "Outros", "Aporte sócio", 1, "nao_venda"),
    ("foto", "Vendas", "Bolo de festa", 1, None),
    ("titulo", "Serviços", "Evento — Sinal", None, None),
    ("balcao", "Vendas", "Venda de balcao", None, None),
])
def test_a_regua_por_fora(origem, cat, desc, grupo, esperado):
    assert rel._fora_de_vendas(origem, cat, desc, grupo) == esperado
