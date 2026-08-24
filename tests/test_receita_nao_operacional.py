"""Aporte de sócio não é faturamento.

A tela "Receitas por categoria" era uma lista só, ordenada por valor, e somava
dinheiro que o sócio pôs do bolso junto com venda. O número que o dono usa pra
julgar o negócio subia e descia por um motivo que não era o negócio.

Medido em produção em 24/08/2026, todas as contas: dos R$ 249 mil somados como
receita, os três lançamentos com "aporte" na descrição tinham caído em TRÊS
categorias diferentes, porque não existia a certa e o `canonizar_categoria` nunca
cria categoria nova:

    Outros         Aporte sócio                                     R$ 2.500,00
    Investimentos  Aporte da conta pessoal — T C Fernandes Ltda      R$   500,00
    Presentes      Aporte de sócio — Espaço Pelle Clínica Derm.      R$   200,00

Três camadas foram mexidas, e cada uma tem caso aqui:

  1. as categorias (Aporte, Emprestimo, Transferencia) e quais são não operacionais;
  2. a separação em dois blocos, com os totais;
  3. o sinal da DRE, que vinha do papel do GRUPO e passa a vir da natureza da CONTA
     — sem isso um aporte entraria negativo no grupo 7.

O histórico NÃO é reclassificado por este trabalho: os lançamentos já feitos ficam
como estão, pras empresas corrigirem na mão.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance.livro_caixa import LivroCaixa
from finance.models import (CATEGORIAS_RECEITA, RECEITA_NAO_OPERACIONAL,
                            Lancamento, Tipo, canonizar_categoria,
                            receita_e_operacional)

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


# ── camada 1: as categorias ──────────────────────────────────────────────────
def test_aporte_virou_categoria_de_verdade():
    """Antes, 'Aporte' não existia e o canonizar jogava tudo em 'Outros' — ou o
    lançador escolhia à mão a menos errada, que foi como um aporte foi parar em
    'Presentes'."""
    assert "Aporte" in CATEGORIAS_RECEITA
    for grafia in ("Aporte", "aporte", "APORTE", "Aporte "):
        assert canonizar_categoria(grafia, "receita") == "Aporte"


def test_as_tres_entradas_que_nao_sao_faturamento():
    assert RECEITA_NAO_OPERACIONAL == {"Aporte", "Emprestimo", "Transferencia",
                                       "Reembolso"}
    for cat in ("Aporte", "Emprestimo", "Transferencia", "Reembolso"):
        assert receita_e_operacional(cat) is False, cat


def test_o_faturamento_de_verdade_continua_operacional():
    for cat in ("Vendas", "Honorarios", "Freela", "Consultoria", "Aluguel",
                "Salario", "Beneficio", "Presentes", "Investimentos", "Outros"):
        assert receita_e_operacional(cat) is True, cat


def test_investimentos_fica_de_fora_de_proposito():
    """O nome é ambíguo — rendimento que a empresa ganhou vs. dinheiro que
    puseram nela — e são R$ 64.542 em produção. Mover a categoria antiga
    reclassificaria histórico sozinho; quem decide é a empresa."""
    assert "Investimentos" not in RECEITA_NAO_OPERACIONAL


def test_categoria_desconhecida_nao_vira_nao_operacional():
    """'Outros' é o fundo do funil e tem receita de verdade dentro. Cair lá não
    pode significar sair do faturamento."""
    assert receita_e_operacional("xpto que ninguem cadastrou") is True


# ── camada 2: os dois blocos ─────────────────────────────────────────────────
@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    yield p
    p.close()


@pytest.fixture()
def livro(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Doce Mell') "
                        "returning id").fetchone()[0]
        c.commit()
    lc = LivroCaixa(pool, cid)
    yield lc
    with pool.connection() as c:
        c.execute("delete from lancamentos where conta_id=%s", (cid,))
        c.commit()


def _lancar(livro, categoria, reais, dia):
    from datetime import date
    livro.adicionar(Lancamento.criar(Tipo.RECEITA, reais, categoria,
                                     descricao=categoria + " de teste",
                                     data=date(2026, 8, dia)))


def test_os_dois_blocos_separam_o_que_a_lista_misturava(livro):
    # o retrato de produção, em miniatura
    _lancar(livro, "Vendas", 139485.75, 5)
    _lancar(livro, "Honorarios", 15000.00, 6)
    _lancar(livro, "Aporte", 2500.00, 7)       # era "Outros"
    _lancar(livro, "Aporte", 200.00, 8)        # era "Presentes"
    _lancar(livro, "Reembolso", 1097.38, 9)

    r = livro.receitas_em_dois_blocos(2026, 8)

    assert dict(r["operacional"]) == {"Vendas": 13948575, "Honorarios": 1500000}
    assert dict(r["nao_operacional"]) == {"Aporte": 270000, "Reembolso": 109738}
    assert r["total_operacional"] == 15448575
    assert r["total_nao_operacional"] == 379738


def test_o_total_do_negocio_nao_se_move_quando_o_socio_poe_dinheiro(livro):
    """O ponto inteiro da mudança: um aporte não pode alterar o número que mede
    o negócio."""
    _lancar(livro, "Vendas", 10000.00, 5)
    antes = livro.receitas_em_dois_blocos(2026, 8)["total_operacional"]

    _lancar(livro, "Aporte", 50000.00, 6)
    depois = livro.receitas_em_dois_blocos(2026, 8)

    assert depois["total_operacional"] == antes == 1000000
    assert depois["total_nao_operacional"] == 5000000


def test_os_dois_blocos_somam_o_mesmo_que_a_lista_antiga(livro):
    """A separação é de APRESENTAÇÃO — nenhum centavo pode sumir no caminho."""
    _lancar(livro, "Vendas", 300.00, 5)
    _lancar(livro, "Aporte", 200.00, 6)
    _lancar(livro, "Transferencia", 100.00, 7)

    r = livro.receitas_em_dois_blocos(2026, 8)
    antiga = sum(v for _, v in livro.receitas_por_categoria(2026, 8))
    assert r["total_operacional"] + r["total_nao_operacional"] == antiga == 60000


def test_mes_sem_receita_nenhuma_nao_estoura(livro):
    r = livro.receitas_em_dois_blocos(2026, 8)
    assert r == {"operacional": [], "nao_operacional": [],
                 "total_operacional": 0, "total_nao_operacional": 0}


def test_so_faturamento_deixa_o_segundo_bloco_vazio(livro):
    """A tela esconde o bloco quando ele está vazio — a maioria dos meses é assim,
    e uma seção vazia toda vez viraria ruído."""
    _lancar(livro, "Vendas", 100.00, 5)
    r = livro.receitas_em_dois_blocos(2026, 8)
    assert r["nao_operacional"] == [] and r["total_nao_operacional"] == 0
    assert r["total_operacional"] == 10000


# ── camada 3: o sinal da DRE ─────────────────────────────────────────────────
def test_a_conta_de_aporte_entrou_no_plano():
    """7.1.05 no grupo 7, com natureza RECEITA — o contrário do 7.1.02
    (Distribuição de Lucros), que é o sócio tirando."""
    sql = (BASE / "186_plano_aporte_socios.sql").read_text(encoding="utf-8")
    assert "'7.1.05'" in sql and "Aporte de Sócios" in sql
    assert "7, 'receita'" in sql, "tem que ser grupo 7 com natureza receita"
    assert "on conflict (codigo) do nothing" in sql, "migração tem que ser idempotente"


def test_o_sinal_da_dre_vem_da_conta_e_nao_do_grupo():
    """Trava de leitura do fonte. `sinal = 1 if GRUPOS_DRE[grupo]["papel"]...`
    fazia o grupo 7 subtrair TUDO — e um aporte entraria negativo."""
    src = (Path(__file__).resolve().parent.parent / "finance" / "empresa.py"
           ).read_text(encoding="utf-8")
    assert 'sinal = 1 if cta_natureza == "receita" else -1' in src
    assert 'sinal = 1 if GRUPOS_DRE[grupo]["papel"] == "receita" else -1' not in src, \
        "o sinal voltou a sair do grupo — aporte volta a entrar negativo"


def test_grupo_de_mao_dupla_nao_leva_o_prefixo_de_menos():
    """O grupo 7 passa a ter conta dos dois lados; prefixar '(–)' seria mentir na
    metade. Grupo de mão única continua prefixado."""
    src = (Path(__file__).resolve().parent.parent / "finance" / "empresa.py"
           ).read_text(encoding="utf-8")
    assert "um_lado_so" in src and 'not tem_receita[gr]' in src
