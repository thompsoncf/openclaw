"""O PDF do DRE do mês (finance/empresa.dre_pdf), pedido da Iris (cliente da
Manoel Soares) via WhatsApp em 29/09/2026: "No caso da DRE pode configurar pra
imprimir em PDF na mesma estrutura?" — reaproveita o motor de PDF que já existe
pra clínica (finance/clinica_documentos.render_pdf), então o teste cobre só o
que é NOVO: o conteúdo bate com a estrutura da tela (grupo por grupo, subtotal,
total), o fallback pra conta sem plano de contas, e a rota devolve o PDF certo.

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import os
from pathlib import Path

import pytest

from tests.relogio_fixo import HOJE

# O "hoje" é o de Brasília, com o processo parado às 23h (02h UTC do dia
# seguinte) — ver tests/relogio_fixo.py. O lançamento nasce no dia de Brasília
# (finance/relogio.py) e o teste pegava o mês com `date.today()`: no último dia
# do mês, das 21h à meia-noite, eram meses diferentes (CI do #917, 01/10/2026).
pytestmark = pytest.mark.usefixtures("servidor_as_23h")
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import plano_contas as pc
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql")


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
            "insert into contas (tipo, nome) values ('pj', 'Teste DRE PDF') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _pdf_texto(pdf_bytes: bytes) -> str:
    import pymupdf
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(pg.get_text() for pg in doc)


def test_o_pdf_tem_a_mesma_estrutura_da_tela_grupo_subtotal_e_total(pool, conta_id):
    hoje = HOJE
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    liv = LivroCaixa(pool, conta_id)
    liv.adicionar(Lancamento(tipo=Tipo.RECEITA, valor_centavos=500000, categoria="Vendas",
                             descricao="Venda", natureza="empresa",
                             plano_conta_id=ids["1.1.02"]), forcar=True)
    liv.adicionar(Lancamento(tipo=Tipo.DESPESA, valor_centavos=120000, categoria="Pessoal",
                             descricao="Salário", natureza="empresa",
                             plano_conta_id=ids["4.1.01"]), forcar=True)

    pdf = emp.dre_pdf(pool, conta_id, hoje.year, hoje.month, "Empresa Teste")
    assert pdf is not None and pdf.startswith(b"%PDF")
    texto = _pdf_texto(pdf)

    assert "Empresa Teste" in texto
    assert "DRE do mês" in texto
    assert "Receita Operacional Bruta" in texto  # grupo 1
    assert "(–) Despesas com Pessoal" in texto   # grupo 4, um lado só vira "(–)"
    assert "= Receita Líquida" in texto          # subtotal
    assert "= Resultado do Mês" in texto         # total
    assert "R$ 5.000,00" in texto
    assert "R$ -1.200,00" in texto                # despesa sai negativa


def test_o_pdf_avisa_o_que_ficou_a_classificar(pool, conta_id):
    hoje = HOJE
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento(tipo=Tipo.DESPESA, valor_centavos=30000, categoria="Outros",
                  descricao="Sem plano"), forcar=True)  # natureza null

    pdf = emp.dre_pdf(pool, conta_id, hoje.year, hoje.month, "Empresa Teste")
    texto = _pdf_texto(pdf)
    # "classificar" sai com ligadura tipográfica ("classiﬁcar") no PDF — o
    # resto da frase não tem essa letra e escapa da armadilha.
    assert "não entraram neste DRE" in texto
    assert "R$ 300,00" in texto


def test_o_pdf_cai_no_resumo_simples_quando_nao_tem_plano_de_contas(monkeypatch, pool, conta_id):
    """Conta sem nenhum lançamento classificado no plano: `_dre_estrutura` ainda
    assim monta a linha 'A classificar', então o fallback só é visível de fato
    quando a estrutura vem vazia (banco tolerante, migração 132 ausente) — é
    o que este teste força, sem precisar de outro pool."""
    hoje = HOJE
    liv = LivroCaixa(pool, conta_id)
    liv.adicionar(Lancamento(tipo=Tipo.RECEITA, valor_centavos=80000, categoria="Vendas",
                             descricao="Venda", natureza="empresa"), forcar=True)

    dre_original = emp.dre_mes

    def sem_estrutura(*a, **k):
        r = dict(dre_original(*a, **k))
        r["estrutura"] = {"linhas": [], "disponivel": False}
        return r

    monkeypatch.setattr(emp, "dre_mes", sem_estrutura)
    pdf = emp.dre_pdf(pool, conta_id, hoje.year, hoje.month, "Empresa Teste")
    texto = _pdf_texto(pdf)
    assert "Receitas" in texto and "R$ 800,00" in texto
    assert "= Resultado" in texto


def test_a_rota_devolve_o_pdf_inline_com_o_nome_do_arquivo(pool, conta_id, monkeypatch):
    import web.portal as pt
    hoje = HOJE
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento(tipo=Tipo.RECEITA, valor_centavos=100000, categoria="Vendas",
                  descricao="Venda", natureza="empresa",
                  plano_conta_id=ids["1.1.02"]), forcar=True)

    conta = (conta_id, "pj", "Empresa Teste")
    monkeypatch.setattr(pt, "_guard_pj", lambda req: (conta, pool))
    resp = pt.empresa_dre_pdf(object(), ano=hoje.year, mes=hoje.month)

    assert resp.media_type == "application/pdf"
    assert resp.body.startswith(b"%PDF")
    assert f'dre_{hoje.year}_{hoje.month:02d}.pdf' in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].startswith("inline")


def test_a_rota_sem_modulo_pj_manda_pro_painel(monkeypatch):
    import web.portal as pt
    monkeypatch.setattr(pt, "_guard_pj", lambda req: None)
    resp = pt.empresa_dre_pdf(object())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/painel"
