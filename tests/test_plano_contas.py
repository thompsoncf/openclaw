"""Plano de contas GLOBAL + centros de custo (por conta) + DRE estruturada.

Cobre o recurso da migração 132:
  - plano de contas é único/do sistema; cada conta só LIGA/DESLIGA (default-on);
  - centros de custo são por conta (CRUD isolado por conta_id);
  - a DRE ganha estrutura por grupo (reconciliando com o resultado legado) e um
    corte por centro de custo;
  - lançamento sem classificação continua contando (retrocompatível).

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import plano_contas as pc, empresa as emp
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

# 018 (chave) + 057 (natureza) fazem o insert do livro-caixa rodar; 132 traz o
# plano/centros e as colunas de classificação.
_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "132_plano_contas_centros_custo.sql")


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste PC') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _reais(pool, conta_id, tipo, valor, cod=None, centro=None, natureza="empresa"):
    """Atalho: cria um lançamento (opcionalmente classificado)."""
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento.criar(tipo, valor, "x", natureza=natureza,
                         plano_conta_id=(ids[cod] if cod else None),
                         centro_custo_id=centro), forcar=True)


# ── Plano de contas (global + liga/desliga por conta) ─────────────────────────
def test_seed_global_31_contas_em_7_grupos(pool):
    plano = pc.listar_plano(pool)
    assert len(plano) == 31
    grupos = {}
    for c in plano:
        grupos[c["grupo"]] = grupos.get(c["grupo"], 0) + 1
    assert grupos == {1: 6, 2: 3, 3: 3, 4: 5, 5: 8, 6: 3, 7: 3}


def test_default_on_tudo_habilitado(pool, conta_id):
    arv = pc.arvore_habilitada(pool, conta_id)
    assert len(arv) == 7
    assert sum(g["n_total"] for g in arv) == 31
    # sem nenhuma linha em plano_conta_habilitada, TUDO vem habilitado
    assert all(g["n_ativas"] == g["n_total"] for g in arv)
    assert all(c["habilitada"] for c in pc.habilitadas(pool, conta_id))


def test_liga_desliga_e_opcoes(pool, conta_id):
    alvo = pc.listar_plano(pool)[0]  # 1.1.01
    pc.habilitar(pool, conta_id, alvo["id"], False)
    estado = {c["codigo"]: c["habilitada"] for c in pc.habilitadas(pool, conta_id)}
    assert estado[alvo["codigo"]] is False
    # a desligada some das opções do lançamento
    ids_ops = {c["id"] for g in pc.opcoes_lancamento(pool, conta_id) for c in g["contas"]}
    assert alvo["id"] not in ids_ops
    # religa
    pc.habilitar(pool, conta_id, alvo["id"], True)
    ids_ops = {c["id"] for g in pc.opcoes_lancamento(pool, conta_id) for c in g["contas"]}
    assert alvo["id"] in ids_ops


def test_plano_conta_valido(pool, conta_id):
    plano = pc.listar_plano(pool)
    ativa, outra = plano[0], plano[1]
    pc.habilitar(pool, conta_id, ativa["id"], False)
    assert pc.plano_conta_valido(pool, conta_id, ativa["id"]) is None      # desligada
    assert pc.plano_conta_valido(pool, conta_id, outra["id"]) == outra["id"]
    assert pc.plano_conta_valido(pool, conta_id, "") is None               # vazio
    assert pc.plano_conta_valido(pool, conta_id, "abc") is None            # lixo
    assert pc.plano_conta_valido(pool, conta_id, 999999) is None           # inexistente


def test_liga_desliga_isolado_por_conta(pool, conta_id):
    alvo = pc.listar_plano(pool)[0]
    pc.habilitar(pool, conta_id, alvo["id"], False)
    # OUTRA conta continua com a mesma conta habilitada (default-on independente)
    with pool.connection() as c:
        outra = c.execute(
            "insert into contas (tipo, nome) values ('pj','Outra') returning id"
        ).fetchone()[0]
        c.commit()
    estado_outra = {x["codigo"]: x["habilitada"] for x in pc.habilitadas(pool, outra)}
    assert estado_outra[alvo["codigo"]] is True


# ── Centros de custo (por conta) ──────────────────────────────────────────────
def test_centros_crud_isolado(pool, conta_id):
    a = pc.criar_centro(pool, conta_id, "Unidade Centro", "matriz")
    b = pc.criar_centro(pool, conta_id, "Delivery")
    nomes = {c["nome"] for c in pc.listar_centros(pool, conta_id)}
    assert nomes == {"Unidade Centro", "Delivery"}
    # editar
    assert pc.editar_centro(pool, conta_id, a["id"], "Loja Centro", "matriz nova")
    a2 = [c for c in pc.listar_centros(pool, conta_id) if c["id"] == a["id"]][0]
    assert a2["nome"] == "Loja Centro" and a2["descricao"] == "matriz nova"
    # desativar some da lista padrão, mas segue no incluir_inativos
    pc.desativar_centro(pool, conta_id, b["id"])
    assert b["id"] not in {c["id"] for c in pc.listar_centros(pool, conta_id)}
    assert b["id"] in {c["id"] for c in pc.listar_centros(pool, conta_id, incluir_inativos=True)}
    # validação de defesa
    assert pc.centro_custo_valido(pool, conta_id, a["id"]) == a["id"]
    assert pc.centro_custo_valido(pool, conta_id, b["id"]) is None          # inativo
    assert pc.centro_custo_valido(pool, 999999, a["id"]) is None            # outra conta
    # multi-tenant: não edita/desativa centro de outra conta
    assert pc.editar_centro(pool, 999999, a["id"], "Hackeado") is False
    assert pc.desativar_centro(pool, 999999, a["id"]) is False


def test_nome_obrigatorio(pool, conta_id):
    with pytest.raises(ValueError):
        pc.criar_centro(pool, conta_id, "   ")


# ── DRE estruturada + por centro ──────────────────────────────────────────────
def test_dre_estrutura_reconcilia_e_a_classificar(pool, conta_id):
    hoje = date.today()
    _reais(pool, conta_id, Tipo.RECEITA, 1000, "1.1.01")   # Venda Produtos
    _reais(pool, conta_id, Tipo.RECEITA, 500, "1.1.02")    # Serviços
    _reais(pool, conta_id, Tipo.DESPESA, 100, "2.1.01")    # Impostos (dedução)
    _reais(pool, conta_id, Tipo.DESPESA, 300, "3.1.01")    # CMV (custo)
    _reais(pool, conta_id, Tipo.DESPESA, 200, "5.1.03")    # Marketing (operacional)
    _reais(pool, conta_id, Tipo.DESPESA, 50, None)         # empresa SEM conta contábil

    dre = emp.dre_mes(pool, conta_id, hoje.year, hoje.month)
    # totais legados intactos
    assert dre["receitas_centavos"] == 150000
    assert dre["despesas_centavos"] == 65000
    assert dre["resultado_centavos"] == 85000

    est = dre["estrutura"]
    assert est["disponivel"] is True
    L = {l["chave"]: l for l in est["linhas"]}
    assert L["grupo_1"]["valor_centavos"] == 150000        # receita bruta
    assert L["receita_liquida"]["valor_centavos"] == 140000  # 1500 - 100
    assert L["lucro_bruto"]["valor_centavos"] == 110000      # 1400 - 300
    # o lançamento de 50 sem conta cai em "A classificar"
    assert L["a_classificar"]["valor_centavos"] == -5000
    assert L["a_classificar"]["n"] == 1
    # o Resultado da estrutura BATE com o legado (reconciliação)
    assert L["resultado"]["valor_centavos"] == dre["resultado_centavos"]
    # as contas analíticas do grupo 1 aparecem
    assert {c["codigo"] for c in L["grupo_1"]["contas"]} == {"1.1.01", "1.1.02"}


def test_dre_por_centro_colunas_e_valores(pool, conta_id):
    hoje = date.today()
    A = pc.criar_centro(pool, conta_id, "Unidade Centro")["id"]
    B = pc.criar_centro(pool, conta_id, "Delivery")["id"]
    _reais(pool, conta_id, Tipo.RECEITA, 1000, "1.1.01", centro=A)
    _reais(pool, conta_id, Tipo.RECEITA, 500, "1.1.02", centro=B)
    _reais(pool, conta_id, Tipo.RECEITA, 80, "1.2.02", centro=None)   # sem centro
    _reais(pool, conta_id, Tipo.DESPESA, 200, "5.1.03", centro=A)

    d = emp.dre_por_centro(pool, conta_id, hoje.year, hoje.month)
    assert d["disponivel"] is True
    # colunas: os centros presentes (ordem, nome) + "Sem centro"
    nomes = [c["nome"] for c in d["centros"]]
    assert nomes == ["Delivery", "Unidade Centro", "Sem centro"]
    lin = {l["nome"]: l for l in d["linhas"]}
    rb = lin["Receita Operacional Bruta"]["por_centro"]
    assert rb[A] == 100000 and rb[B] == 50000 and rb["sem"] == 8000
    res = lin["= Resultado do Mês"]
    assert res["por_centro"][A] == 80000       # 1000 receita - 200 marketing
    assert res["por_centro"][B] == 50000
    assert res["por_centro"]["sem"] == 8000
    assert res["total_centavos"] == 138000


# ── Retrocompat: sem classificação e sem a migração ───────────────────────────
def test_setter_grava_limpa_e_multitenant(pool, conta_id):
    hoje = date.today()
    livro = LivroCaixa(pool, conta_id)
    s = livro.adicionar(Lancamento.criar(Tipo.DESPESA, 10, "x", natureza="empresa"),
                        forcar=True)
    it = [l for l in livro.lancamentos_recentes(hoje.year, hoje.month)
          if l["id"] == s.id][0]
    assert it["plano_conta_id"] is None and it["centro_custo_id"] is None
    pid = pc.listar_plano(pool)[0]["id"]
    assert livro.definir_plano_conta(s.id, pid)
    assert livro.definir_centro_custo(s.id, None)      # limpar centro (idempotente)
    it2 = [l for l in livro.lancamentos_recentes(hoje.year, hoje.month)
           if l["id"] == s.id][0]
    assert it2["plano_conta_id"] == pid
    # multi-tenant: setter não mexe em lançamento de outra conta
    assert LivroCaixa(pool, 999999).definir_plano_conta(s.id, pid) is False


def test_lancamento_sem_classificacao_ainda_conta(pool, conta_id):
    """Retrocompat: empresa sem conta contábil segue nos totais e some em
    'A classificar' — nada desaparece."""
    hoje = date.today()
    _reais(pool, conta_id, Tipo.RECEITA, 400, None)   # empresa, sem plano
    dre = emp.dre_mes(pool, conta_id, hoje.year, hoje.month)
    assert dre["receitas_centavos"] == 40000
    assert dre["resultado_centavos"] == 40000
    L = {l["chave"]: l for l in dre["estrutura"]["linhas"]}
    assert L["a_classificar"]["valor_centavos"] == 40000
    assert L["resultado"]["valor_centavos"] == 40000


def test_id_por_codigo_e_centro_por_nome(pool, conta_id):
    """Resolvedores usados pelo bot: código -> id (habilitado) e nome -> centro."""
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    assert pc.id_por_codigo(pool, conta_id, "5.1.03") == ids["5.1.03"]
    # nome colado no código (como o bot pode mandar) -> pega o código
    assert pc.id_por_codigo(pool, conta_id, "5.1.03 Marketing e Publicidade") == ids["5.1.03"]
    assert pc.id_por_codigo(pool, conta_id, "9.9.99") is None      # inexistente
    assert pc.id_por_codigo(pool, conta_id, "") is None            # vazio
    pc.habilitar(pool, conta_id, ids["5.1.03"], False)
    assert pc.id_por_codigo(pool, conta_id, "5.1.03") is None      # desabilitada
    a = pc.criar_centro(pool, conta_id, "Unidade Centro")["id"]
    assert pc.centro_por_nome(pool, conta_id, "unidade centro") == a   # sem caixa
    assert pc.centro_por_nome(pool, conta_id, "  Unidade Centro  ") == a
    assert pc.centro_por_nome(pool, conta_id, "não existe") is None


def test_lancamentos_a_classificar(pool, conta_id):
    """Lista só os lançamentos de EMPRESA sem conta contábil (o que alimenta o
    painel 'A classificar' da aba Empresa)."""
    hoje = date.today()
    livro = LivroCaixa(pool, conta_id)
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    # empresa COM conta contábil -> NÃO aparece
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 100, "x", natureza="empresa",
                                     plano_conta_id=ids["5.1.03"]), forcar=True)
    # empresa SEM conta contábil -> aparece
    s = livro.adicionar(Lancamento.criar(Tipo.DESPESA, 200, "Servicos",
                                         descricao="Anúncios", natureza="empresa"),
                        forcar=True)
    # pessoal e "a definir" -> NÃO aparecem
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 50, "x", natureza="pessoal"), forcar=True)
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 30, "x"), forcar=True)

    lst = livro.lancamentos_a_classificar(hoje.year, hoje.month)
    assert [l["id"] for l in lst] == [s.id]
    assert lst[0]["descricao"] == "Anúncios"
    assert lst[0]["valor"] == 20000
    assert lst[0]["centro_custo_id"] is None


def test_classificar_por_categoria_do(pool, conta_id):
    """'Aplicar a todos iguais': põe a conta nos de empresa do mesmo mês+categoria
    sem conta — sem tocar no já classificado, em outra categoria ou no pessoal."""
    livro = LivroCaixa(pool, conta_id)
    pid = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}["5.1.03"]

    def add(cat, nat="empresa"):
        return livro.adicionar(Lancamento.criar(Tipo.DESPESA, 10, cat, natureza=nat),
                               forcar=True).id
    ref = add("Servicos")
    s1, s2 = add("Servicos"), add("Servicos")
    outra = add("Mercado")               # categoria diferente
    pes = add("Servicos", nat="pessoal")  # pessoal

    livro.definir_plano_conta(ref, pid)   # ref classificado primeiro (como na UI)
    n = livro.classificar_por_categoria_do(ref, pid)
    assert n == 2                         # só s1 e s2

    def conta_de(i):
        with pool.connection() as c:
            return c.execute("select plano_conta_id from lancamentos where id=%s",
                             (i,)).fetchone()[0]
    assert conta_de(s1) == pid and conta_de(s2) == pid
    assert conta_de(outra) is None and conta_de(pes) is None
