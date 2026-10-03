"""O inventário rotativo do CD, a curva ABC e os indicadores (finance/obra_inventario.py;
PR 3b do desenho docs/mockups/obras_cd_almoxarifado.html, aprovado em 03/10/2026).

`test_clique_duplo_nao_ajusta_duas_vezes` é o contrato: o sistema é lido na hora,
com o material travado — a segunda contagem igual vê o estoque já ajustado e bate.
"""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_conferencia as conf
from finance import obra_inventario as inv
from finance import obra_material as omat
from finance import obra_pedidos as op
from finance import obras as ob
from web import painel_deposito as pd

_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_MIGRACOES = ["016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql", "019_codigo_gtin.sql",
              "032_catalogo_estoque.sql", "053_modulo_pj.sql", "057_natureza_lancamento.sql",
              "058_dados_empresa.sql", "060_unidade_generica.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql", "072_membro_login_web.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql", "367_pix_da_empresa.sql", "369_obra_fotos.sql",
              "371_obra_etapa_pagamentos.sql", "478_obra_quadras.sql", "484_obra_material.sql",
              "550_obra_mestre_e_campo.sql", "670_obra_pedidos_cd.sql"]
# as migrações com prefixo de data (db/nova_migracao.py): acha pelo nome
for _nome in ("obra_ferramentas", "obra_conferencia_nota", "obra_inventario_rotativo"):
    _MIGRACOES += [p.name for p in sorted(_BASE.glob(f"*_{_nome}.sql"))]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_inventario_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute("create table if not exists precos_observados (id bigserial primary key, item_id bigint)")
        c.commit()
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


def _nota(pool, conta, itens, fornecedor="Constrular", conferir=True):
    """Uma nota de material lida pelo leitor e absorvida no CD: [(descrição, qtd, unidade, centavos)].
    Conferida ("bateu") por padrão — material com nota sem conferir espera a conferência."""
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                               descricao, data, natureza)
                           values (%s,'despesa',100000,'Insumos',%s,%s,'empresa') returning id""",
                        (conta, fornecedor, date.today())).fetchone()[0]
        for desc, q, un, preco in itens:
            c.execute("""insert into itens_lancamento (lancamento_id, descricao, quantidade,
                             valor_unitario_centavos, valor_total_centavos, unidade)
                         values (%s,%s,%s,%s,0,%s)""", (lid, desc, q, preco, un))
        c.commit()
    omat.absorver_lancamento(pool, conta, lid)
    if conferir:
        conf.conferir(pool, conta, lid)
    return lid


def _pid(pool, conta, nome):
    return omat.achar_produto(pool, conta, nome)["id"]


def _cd(pool, conta, nome):
    return omat.saldo_local(pool, conta, _pid(pool, conta, nome), None)


def _levei(pool, conta, obra_id, nome, q):
    omat.mover(pool, conta, acao="levei", produto_id=_pid(pool, conta, nome), quantidade=q, obra_id=obra_id)


def _cd_padrao(pool, conta):
    """Cimento, ferro, areia e prego no CD; saem 60/20/5/2 pra uma casa."""
    _nota(pool, conta, [("CIMENTO CP II 50KG", 100, "sc", 3290), ("FERRO 8MM", 50, "barra", 4000),
                        ("AREIA MEDIA", 10, "m3", 9000), ("PREGO 17X21", 10, "kg", 1500)])
    o = ob.criar_obra(pool, conta, "Casa 1", "casa")
    for nome, q in (("cimento", 60), ("ferro", 20), ("areia", 5), ("prego", 2)):
        _levei(pool, conta, o["id"], nome, q)
    return o


# ── a curva ABC ───────────────────────────────────────────────────────────
def test_curva_abc_pelo_valor_que_saiu(pool, conta):
    _cd_padrao(pool, conta)
    # saiu: cimento R$ 1.974, ferro R$ 800, areia R$ 450, prego R$ 30 (total R$ 3.254)
    classes, ordem = inv.curva(pool, conta)
    nomes = {_pid(pool, conta, n): n for n in ("cimento", "ferro", "areia", "prego")}
    assert {nomes[p]: k for p, k in classes.items()} == {"cimento": "A", "ferro": "A", "areia": "B", "prego": "C"}
    assert [nomes[p] for p in ordem] == ["cimento", "ferro", "areia", "prego"]


def test_cd_novo_sem_saida_usa_o_valor_parado_e_sem_preco_e_c(pool, conta):
    _nota(pool, conta, [("TELHA CERAMICA", 1000, "un", 250), ("TINTA ACRILICA 18L", 2, "lata", 28000),
                        ("FIO 2,5MM", 3, "rolo", 0)])
    classes, _ = inv.curva(pool, conta)
    # parado: telha R$ 2.500 (82%), tinta R$ 560, fio sem preço
    assert classes[_pid(pool, conta, "telha")] == "A" and classes[_pid(pool, conta, "tinta")] == "B"
    assert classes[_pid(pool, conta, "fio")] == "C"


# ── a contagem do dia ─────────────────────────────────────────────────────
def test_contagem_do_dia_a_primeiro_e_quem_ja_foi_contado_espera(pool, conta):
    _cd_padrao(pool, conta)
    dia = inv.contagem_do_dia(pool, conta, n=3)
    assert [i["classe"] for i in dia["itens"]] == ["A", "A", "B"] and dia["faltam"] == 3
    assert dia["faltam_a"] == 2 and dia["vencidos"] == 4                 # nunca contados
    # o cimento foi contado há 3 dias (A conta toda semana): sai da lista
    with pool.connection() as c:
        c.execute("""insert into obra_contagens (conta_id, produto_id, sistema, contado, contado_em)
                     values (%s,%s,40,40, now() - interval '3 days')""", (conta, _pid(pool, conta, "cimento")))
        c.commit()
    nomes = [i["nome"] for i in inv.contagem_do_dia(pool, conta, n=5)["itens"]]
    assert not any("CIMENTO" in n for n in nomes) and len(nomes) == 3
    # contar hoje: fica na lista, com o resultado
    inv.contar(pool, conta, _pid(pool, conta, "ferro"), "30")
    dia = inv.contagem_do_dia(pool, conta, n=5)
    feito = [i for i in dia["itens"] if i["contagem"]]
    assert len(feito) == 1 and feito[0]["contagem"]["rotulo_dif"] == "ok" and dia["faltam"] == 2


# ── contar ────────────────────────────────────────────────────────────────
def test_contar_bateu_e_nao_bateu_com_motivo(pool, conta):
    _nota(pool, conta, [("TELHA CERAMICA", 320, "un", 180)])
    pid = _pid(pool, conta, "telha")
    with pytest.raises(ValueError, match="Escolha o motivo"):
        inv.contar(pool, conta, pid, "306")
    with pytest.raises(ValueError, match="achou a mais"):
        inv.contar(pool, conta, pid, "306", motivo="achado")
    with pytest.raises(ValueError, match="MAIS"):
        inv.contar(pool, conta, pid, "330", motivo="quebra")
    assert _cd(pool, conta, "telha") == 320 and inv.contagem_do_dia(pool, conta)["recentes"] == []
    r = inv.contar(pool, conta, pid, "306", motivo="quebra")
    assert "ajuste de −14 (quebra)" in r["frase"] and _cd(pool, conta, "telha") == 306
    k = inv.contagem_do_dia(pool, conta)["recentes"][0]
    assert k["dif"] == -14 and k["valor"] == -14 * 180 and k["motivo"] == "quebra"
    assert "já contado hoje" in inv.contar(pool, conta, pid, "306")["frase"]   # não grava de novo
    ind = inv.indicadores(pool, conta)
    assert ind["contagens"] == 1 and ind["acuracidade"] == 0
    assert ind["perdas_valor"] == 14 * 180 and ind["perdas"][0]["qtd"] == 14
    assert ind["perdas"][0]["motivo"] == "quebra"


def test_clique_duplo_nao_ajusta_duas_vezes(pool, conta):
    _nota(pool, conta, [("TELHA CERAMICA", 320, "un", 180)])
    pid = _pid(pool, conta, "telha")
    inv.contar(pool, conta, pid, "306", motivo="quebra")
    r = inv.contar(pool, conta, pid, "306", motivo="quebra")     # o mesmo formulário de novo
    assert "já contado hoje" in r["frase"] and _cd(pool, conta, "telha") == 306
    # nem grava: a acuracidade não sobe com o clique duplo e a lista mostra a quebra
    dia = inv.contagem_do_dia(pool, conta)
    assert len(dia["recentes"]) == 1 and inv.indicadores(pool, conta)["acuracidade"] == 0
    assert dia["itens"][0]["contagem"]["rotulo_dif"] == "−14 · quebra"


def test_achou_a_mais_e_mil_como_se_digita(pool, conta):
    _nota(pool, conta, [("TIJOLO 8 FUROS", 900, "un", 90)])
    pid = _pid(pool, conta, "tijolo")
    inv.contar(pool, conta, pid, "1.000", motivo="achado")       # "1.000" é mil
    assert _cd(pool, conta, "tijolo") == 1000
    assert "já contado hoje" in inv.contar(pool, conta, pid, Decimal("1000.000"))["frase"]   # número ≠ texto
    assert inv.indicadores(pool, conta)["perdas"] == []           # achado não é perda
    for ruim in ("nan", "-3", "", "1e12"):
        with pytest.raises(ValueError):
            inv.contar(pool, conta, pid, ruim, motivo="erro")


def test_outra_conta_e_ferramenta_nao_contam(pool, conta):
    _nota(pool, conta, [("CAL HIDRATADA", 10, "sc", 1200)])
    pid = _pid(pool, conta, "cal")
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        fer = c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade, categoria, disponivel)
                           values (%s,'Betoneira 400 L','unidade','ferramenta',false) returning id""",
                        (conta,)).fetchone()[0]
        c.commit()
    with pytest.raises(ValueError, match="não encontrado"):
        inv.contar(pool, outra, pid, "9", motivo="perda")
    with pytest.raises(ValueError, match="não encontrado"):
        inv.contar(pool, conta, fer, "1", motivo="achado")
    inv.contar(pool, conta, pid, "9", motivo="perda")
    kid = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    with pytest.raises(ValueError, match="não encontrada"):
        inv.desfazer(pool, outra, kid)
    assert _cd(pool, conta, "cal") == 9


def test_nota_sem_conferir_espera_a_conferencia(pool, conta):
    # a nota diz 60, a prateleira tem 58: contar E conferir tiraria 4
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc", 3290)], conferir=False)
    pid = _pid(pool, conta, "cimento")
    with pytest.raises(ValueError, match="nota sem conferir"):
        inv.contar(pool, conta, pid, "58", motivo="perda")
    dia = inv.contagem_do_dia(pool, conta)
    assert dia["itens"] == [] and dia["esperando"][0]["fornecedor"] == "Constrular"
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    conf.conferir(pool, conta, lid, chegou={mov: "58"})
    assert "bateu" in inv.contar(pool, conta, pid, "58")["frase"] and _cd(pool, conta, "cimento") == 58
    assert inv.indicadores(pool, conta)["perdas"] == []                  # a falta conta uma vez só


# ── desfazer ──────────────────────────────────────────────────────────────
def test_desfazer_so_a_ultima_de_hoje(pool, conta):
    _nota(pool, conta, [("TELHA CERAMICA", 320, "un", 180)])
    pid = _pid(pool, conta, "telha")
    inv.contar(pool, conta, pid, "360", motivo="achado")             # digitou errado
    primeira = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    inv.contar(pool, conta, pid, "306", motivo="erro")               # contou de novo
    with pytest.raises(ValueError, match="contado de novo"):
        inv.desfazer(pool, conta, primeira)
    segunda = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    assert "desfeita" in inv.desfazer(pool, conta, segunda)
    assert _cd(pool, conta, "telha") == 360                           # voltou ao da primeira
    assert "desfeita" in inv.desfazer(pool, conta, primeira)          # agora ela é a última
    assert _cd(pool, conta, "telha") == 320
    # a de ontem não desfaz mais
    with pool.connection() as c:
        c.execute("""insert into obra_contagens (conta_id, produto_id, sistema, contado, contado_em)
                     values (%s,%s,320,320, now() - interval '2 days')""", (conta, pid))
        c.commit()
    velha = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    with pytest.raises(ValueError, match="de hoje"):
        inv.desfazer(pool, conta, velha)


def test_desfazer_achado_que_ja_saiu_recusa(pool, conta):
    _nota(pool, conta, [("TIJOLO 8 FUROS", 100, "un", 90)])
    pid = _pid(pool, conta, "tijolo")
    inv.contar(pool, conta, pid, "110", motivo="achado")               # +10
    o = ob.criar_obra(pool, conta, "Casa T", "casa")
    omat.mover(pool, conta, acao="levei", produto_id=pid, quantidade=110, obra_id=o["id"])
    kid = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    with pytest.raises(ValueError, match="já saiu do CD"):
        inv.desfazer(pool, conta, kid)
    assert _cd(pool, conta, "tijolo") == 0


# ── os indicadores ────────────────────────────────────────────────────────
def test_indicadores_divergencia_pedido_e_giro(pool, conta):
    o = _cd_padrao(pool, conta)
    # giro do cimento: entrou 100 e saíram 60 no mês → saldo 40, começo 0, médio 20 → 3,0×
    ind = inv.indicadores(pool, conta)
    assert ind["giro"]["vezes"] == Decimal("3.0") and "CIMENTO" in ind["giro"]["nome"].upper()
    assert ind["acuracidade"] is None and ind["pedido_recebido"] is None and ind["divergentes"] == 0
    # uma nota com diferença
    lid = _nota(pool, conta, [("BRITA 1", 6, "m3", 11000)], fornecedor="Pedreira Boa", conferir=False)
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    conf.conferir(pool, conta, lid, chegou={mov: "5"})
    # um pedido que levou 5 horas pra chegar
    ped = op.criar(pool, conta, o["id"], itens=[("cimento", 5)])
    op.receber(pool, conta, ped["id"])
    with pool.connection() as c:
        c.execute("update obra_pedidos set criado_em = recebido_em - interval '5 hours' where id=%s and conta_id=%s",
                  (ped["id"], conta))
        c.commit()
    ind = inv.indicadores(pool, conta)
    assert ind["divergentes"] == 1 and ind["fornecedor_divergente"] == "Pedreira Boa"
    assert ind["pedido_recebido"] == {"pedidos": 1, "media": "5 h"}


# ── a tela ────────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch, papel="almoxarife"):
    monkeypatch.setattr(pd, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(pd, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(pd, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pd.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = papel
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_tela_conta_e_mostra(pool, conta, monkeypatch):
    _nota(pool, conta, [("TELHA CERAMICA", 320, "un", 180), ("BOMBA D'AGUA <b>X</b>", 1, "un", 50000)])
    c = _painel(pool, conta, monkeypatch)
    geral = c.get("/painel/obras/deposito").text
    assert "Contagem do dia" in geral and "Acuracidade" in geral and "nenhuma contagem ainda" in geral
    assert "<b>X</b>" not in geral
    html = c.get("/painel/obras/deposito?aba=inventario").text
    assert "Contagem de hoje" in html and "Pedido → recebido" in html and "<b>X</b>" not in html
    assert "BOMBA D&#39;AGUA &lt;b&gt;X&lt;/b&gt;" in html
    pid = _pid(pool, conta, "telha")
    r = c.post("/painel/obras/deposito/contar", data={"produto_id": str(pid), "contado": "306", "motivo": ""})
    assert "erro=" in r.headers["location"] and _cd(pool, conta, "telha") == 320
    r = c.post("/painel/obras/deposito/contar", data={"produto_id": str(pid), "contado": "306", "motivo": "quebra"})
    assert "ok=" in r.headers["location"] and _cd(pool, conta, "telha") == 306
    html = c.get("/painel/obras/deposito?aba=inventario").text
    assert "−14 · quebra" in html and "desfazer" in html and "R$" not in html.split("Perdas no mês")[1][:300]
    estoque = c.get("/painel/obras/deposito?aba=estoque").text
    assert '<span class="dp-chip c">A</span>' in estoque
    kid = inv.contagem_do_dia(pool, conta)["recentes"][0]["id"]
    r = c.post(f"/painel/obras/deposito/contagem/{kid}/desfazer")
    assert "ok=" in r.headers["location"] and _cd(pool, conta, "telha") == 320


def test_dono_ve_a_perda_em_reais(pool, conta, monkeypatch):
    _nota(pool, conta, [("TELHA CERAMICA", 320, "un", 180)])
    inv.contar(pool, conta, _pid(pool, conta, "telha"), "306", motivo="quebra")
    html = _painel(pool, conta, monkeypatch, papel="dono").get("/painel/obras/deposito?aba=inventario").text
    assert "R$ 25,20" in html.split("Perdas no mês")[1][:300]
